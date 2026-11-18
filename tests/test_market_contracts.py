import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from tests import offline
from aiogram import Bot
from aiogram.methods import AnswerCallbackQuery, SendMessage
from aiogram.types import Update
from bot.handlers import callbacks
from config import settings
from database.crud import get_pending_action, upsert_product_costs
from database.models import close_db, init_db
from services.common.ad_recommendations import analyze_ozon_campaigns
from services.common.market_api import APIStatusError, read_versions
from services.common.price_auditor import run_daily_price_audit
from services.common.orders_report import sync_and_generate_orders_report
from services.common.financial_pnl import generate_weekly_top_and_unprofitable_report
from services.common.storage import DiskCache
from services.ozon.seller_api import OzonSellerAPI
from services.wb.statistics_api import WBStatisticsAPI


class Adapters(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.oz, self.wb = OzonSellerAPI(), WBStatisticsAPI()
        self.oz._cache = DiskCache(Path(tmp.name) / "oz")
        self.wb._cache = DiskCache(Path(tmp.name) / "wb")

    async def test_version_errors_are_typed_and_read_only(self):
        for status in [400, 404, 429]:
            read = AsyncMock(side_effect=[APIStatusError(status, "not available"), []])
            with patch("services.common.market_api.asyncio.sleep", AsyncMock()):
                self.assertEqual(await read_versions(["v5", "v4"], read), [])
            self.assertEqual([c.args for c in read.await_args_list], [("v5",), ("v4",)])
        read = AsyncMock(side_effect=APIStatusError(403, "scope"))
        with self.assertRaises(APIStatusError):
            await read_versions(["v5", "v4"], read)
        self.assertEqual(read.await_count, 1)

    async def test_ozon_v4_restart_pagination_and_disk_cache(self):
        async def request(method, path, payload):
            self.assertEqual(method, "POST")
            if path.startswith("/v5/"):
                if payload["cursor"] == "":
                    return {
                        "items": [{"offer_id": "discard-partial"}],
                        "cursor": "second",
                    }
                raise APIStatusError(400, "old version")
            if path == "/v4/product/info/prices":
                self.assertEqual(payload["last_id"], "")
                return {
                    "result": {
                        "items": [
                            {
                                "offer_id": "1001",
                                "product_id": 19,
                                "price": {"price": "299"},
                                "commissions": {
                                    "fbs_first_mile": 8,
                                    "sales_percent_fbs": 23,
                                    "sales_percent_fbo": 22,
                                },
                                "marketing_actions": {"actions": [{"title": "Акция"}]},
                            }
                        ]
                    }
                }
            if path == "/v4/product/info/stocks":
                self.assertIn("cursor", payload)
                raise APIStatusError(404, "version")
            if path == "/v3/product/info/stocks":
                self.assertEqual(payload["last_id"], "")
                return {
                    "result": {
                        "items": [
                            {
                                "offer_id": "1001",
                                "stocks": [
                                    {"type": "fbs", "present": 4, "reserved": 1},
                                    {"type": "fbo", "present": 5},
                                ],
                            }
                        ]
                    }
                }
            self.assertEqual(path, "/v1/analytics/data")
            return {
                "result": {
                    "data": [{"dimensions": [{"id": "3405669329"}], "metrics": [14]}]
                }
            }

        self.oz._request = AsyncMock(side_effect=request)
        rows = await self.oz.fetch_stocks_and_prices()
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(
            [
                row[k]
                for k in [
                    "stock_fbo",
                    "stock_fbs",
                    "daily_sales",
                    "fbs_pvz_dropoff_rub",
                    "promo_names",
                ]
            ],
            [5, 3, 2, 8, "Акция"],
        )
        second = OzonSellerAPI()
        second._cache = self.oz._cache
        second._request = AsyncMock(side_effect=AssertionError("should use disk"))
        self.assertEqual(await second.fetch_stocks_and_prices(), rows)

    async def test_ozon_transactions_statement_reconcile_not_sum(self):
        period = {"begin": "2026-11-18", "end": "2026-11-18"}
        sale = {
            "operation_id": 11,
            "operation_type": "OperationAgentDeliveredToCustomer",
            "operation_date": "2026-11-18",
            "sale_commission": -66,
            "accruals_for_sale": 300,
            "amount": 195,
            "type": "orders",
            "items": [{"sku": 123}],
            "posting": {"posting_number": "123-1"},
            "services": [
                {"name": "MarketplaceServiceItemDirectFlowLogisticSum", "price": -39}
            ],
        }
        ad = {
            "operation_id": 12,
            "operation_type": "MarketplaceServiceCostPerClick",
            "amount": -6,
            "items": [{"sku": 123}],
        }
        details = {
            "period": period,
            "delivery": {"delivery_services": {"items": sale["services"]}},
            "services": {
                "items": [{"name": "MarketplaceServiceCostPerClick", "price": -6}]
            },
        }
        flow = {
            "period": period,
            "orders_amount": 300,
            "returns_amount": 0,
            "commission_amount": -66,
        }

        async def request(method, path, payload):
            if path.endswith("transaction/list"):
                return {
                    "result": {
                        "operations": [sale] if payload["page"] == 1 else [ad],
                        "page_count": 2,
                    }
                }
            self.assertEqual(path, "/v1/finance/cash-flow-statement/list")
            self.assertIs(payload["with_details"], True)
            return {
                "result": {"cash_flows": [flow], "details": details},
                "page_count": 1,
            }

        self.oz._request = AsyncMock(side_effect=request)
        for start, source in [
            ("2026-11-18", "cash-flow"),
            ("2026-11-17", "транзакции"),
        ]:
            result = await self.oz.fetch_real_finance_breakdown(
                custom_date_from=start, custom_date_to="2026-11-18"
            )
            self.assertIn(source, result["source"])
            self.assertEqual(result["deliv_total"] / result["orders_count"], 39)
            self.assertEqual(
                (result["ads_total"], result["billed_skus"], result["total_payout"]),
                (6, ["123"], 189),
            )

    async def test_performance_campaigns_real_membership_billing_not_organic(self):
        self.oz._perf_token = AsyncMock(return_value="test")

        async def performance(method, url, headers, *, params):
            if url.endswith("/campaign"):
                return {
                    "list": [
                        {"id": c, "state": "CAMPAIGN_STATE_RUNNING"} for c in (10, 20)
                    ]
                    + [{"id": 30, "state": "CAMPAIGN_STATE_INACTIVE"}]
                }
            if url.endswith("/v2/products"):
                skus = [1001, 1002, 1003, 1004] if "/10/" in url else [1005, 1006]
                return {"products": [{"sku": s} for s in skus]}
            self.assertTrue(url.endswith("/campaign/product/json"))
            return {
                "rows": [
                    {
                        "moneySpent": "63,0"
                        if params["campaignIds"] == "10"
                        else "154",
                        "ordersMoney": "1000",
                    }
                ]
            }

        self.oz.request_json = AsyncMock(side_effect=performance)
        campaigns = await self.oz.fetch_active_ad_campaigns()
        self.assertEqual(
            [(r["products_count"], r["drr_in_promo_pct"]) for r in campaigns],
            [(4, 6.3), (2, 15.4)],
        )
        items = [{"sku": str(s), "stock_fbo": 1} for s in range(1001, 1009)]
        classified = analyze_ozon_campaigns(campaigns, items, {"billed_skus": ["1007"]})
        self.assertEqual([r["sku"] for r in classified["organic_items"]], ["1008"])
        self.assertEqual([r["sku"] for r in classified["unclassified_items"]], ["1007"])

    async def test_wb_sources_and_no_duplicate_stock(self):
        async def request(method, url, params=None, json_data=None):
            if url.endswith("/goods/filter"):
                goods = {
                    "nmID": 99,
                    "vendorCode": "1001",
                    "sizes": [{"discountedPrice": 299}],
                }
                return {"data": {"listGoods": [goods, goods]}}
            if url.endswith("/commission"):
                return {
                    "report": [
                        {"subjectID": 7, "paidStorageKgvp": 20, "kgvpMarketplace": 25}
                    ]
                }
            if url.endswith("/box"):
                return {
                    "response": {
                        "data": {
                            "warehouseList": [
                                {
                                    "warehouseName": "A",
                                    "boxDeliveryBase": "53,2",
                                    "boxDeliveryMarketplaceBase": "60",
                                }
                            ]
                        }
                    }
                }
            if url.endswith("/cards/list"):
                return {
                    "cards": [
                        {
                            "nmID": 99,
                            "subjectID": 7,
                            "sizes": [{"chrtID": 11, "skus": ["barcode"]}],
                        }
                    ]
                }
            if url.endswith("/warehouses"):
                return [{"id": 1}, {"id": 1}]
            if url.endswith("/stocks/1"):
                self.assertEqual(json_data, {"chrtIds": [11]})
                return {"stocks": [{"chrtId": 11, "amount": 4}] * 2}
            if url.endswith("/supplier/stocks"):
                return [
                    {
                        "nmId": 99,
                        "warehouseName": "A",
                        "barcode": "barcode",
                        "quantity": 3,
                    }
                ] * 2
            self.assertTrue(url.endswith("/supplier/orders"))
            return []

        self.wb._request = AsyncMock(side_effect=request)
        rows = await self.wb.fetch_stocks_and_prices()
        self.assertEqual(len(rows), 1)
        self.assertEqual(
            [
                rows[0][k]
                for k in ("nm_id", "stock_fbo", "stock_fbs", "fbo_logistics_cost")
            ],
            [99, 3, 4, 53.2],
        )

    async def test_wb_report_rrdid_fallback_returns_and_acquiring(self):
        sale = {
            "rrd_id": 19,
            "supplier_oper_name": "Продажа",
            "quantity": 1,
            "retail_price_withdisc_rub": 300,
            "retail_amount": 280,
            "ppvz_for_pay": 215.8,
            "acquiring_bank": "Bank",
            "sa_name": "1001",
            "sale_dt": "2026-11-17",
        }
        ret = dict(
            sale,
            rrd_id=20,
            supplier_oper_name="Возврат",
            delivery_rub=30,
            return_amount=1,
        )

        async def request(method, url, params=None):
            if "/v5/" in url or "/v3/" in url:
                raise APIStatusError(404, "version")
            if "/upd" in url:
                return []
            self.assertTrue(url.endswith("/v1/supplier/reportDetailByPeriod"))
            return {0: [sale], 19: [ret], 20: None}[params["rrdid"]]

        self.wb._request = AsyncMock(side_effect=request)
        report = await self.wb.fetch_real_finance_breakdown()
        self.assertEqual(report["returns_amount"], 300)
        self.assertEqual(report["revenue_seller"], 0)
        self.assertEqual(report["total_payout"], -30)
        self.assertTrue(report["acquiring_estimated"])
        self.assertEqual(report["orders"][0]["acquiring"], 4.2)
        self.assertEqual(report["orders"][0]["created_at"], "2026-11-17")

    async def test_wb_campaign_sequence_and_join_by_id(self):
        self.wb._request = AsyncMock(
            side_effect=[
                {"adverts": [{"status": 9, "advert_list": [{"advertId": 11}]}]},
                [
                    {
                        "advertId": 11,
                        "status": 9,
                        "name": "А",
                        "autoParams": {"nms": [99]},
                    }
                ],
                [{"advertId": 11, "updSum": 10}, {"advertId": 12, "updSum": 3}],
            ]
        )
        result = await self.wb.fetch_active_ad_campaigns()
        self.assertEqual(
            [c.args[1].split("/adv/v1/")[-1] for c in self.wb._request.await_args_list],
            ["promotion/count", "promotion/adverts", "upd"],
        )
        self.assertEqual(
            [(c["nms"], c["spend_rub"]) for c in result], [(["99"], 10), ([], 3)]
        )

    async def test_cpo_read_does_not_use_a_bid_as_attributed_drr(self):
        self.oz._perf_token = AsyncMock(return_value="fixture")

        async def performance(method, url, headers, **query):
            if url.endswith("/campaign"):
                return {
                    "list": [
                        {
                            "state": "CAMPAIGN_STATE_RUNNING",
                            "id": 11,
                            "advObjectType": "SEARCH_PROMO",
                        }
                    ]
                }
            self.assertTrue(url.endswith("/search_promo/v2/products"))
            self.assertEqual(
                (method, query), ("POST", {"body": {"page": 1, "pageSize": 100}})
            )
            return {
                "products": [
                    {"sku": "90000", "bid": 15, "searchPromoStatus": True},
                    {"sku": "80000", "bid": 9, "searchPromoStatus": False},
                ]
            }

        self.oz.request_json = performance
        data = await self.oz.fetch_active_ad_campaigns()
        self.assertEqual(data[0]["skus_inside"], ["90000"])
        items = [
            {"sku": "brush", "ozon_skus": ["90000"], "stock_fbs": 1},
            {"sku": "gel", "stock_fbo": 1},
        ]
        report = analyze_ozon_campaigns(
            data, items, {"ads_total": 70, "billed_skus": []}
        )
        self.assertEqual(report["promoted_skus"], ["brush"])
        self.assertIsNone(report["campaigns"][0]["drr_in_promo_pct"])
        self.assertIsNone(report["campaigns"][0]["spend_7d_rub"])
        self.assertEqual(report["organic_items"], [])
        self.assertEqual([r["sku"] for r in report["unclassified_items"]], ["gel"])

    async def test_transactions_when_statement_rejects_window_and_zero_acquiring_is_explicit(
        self,
    ):
        operations = [
            {
                "operation_id": 1,
                "operation_type": "OperationAgentDeliveredToCustomer",
                "type": "orders",
                "items": [{"sku": 123}] * 2,
                "posting": {"posting_number": "P1"},
                "operation_date": "2026-11-15",
                "sale_commission": -66,
                "accruals_for_sale": 300,
                "amount": 195,
                "services": [
                    {"name": "MarketplaceServiceItemDelivToCustomer", "price": -39}
                ],
            },
            {
                "operation_id": 2,
                "operation_type": "MarketplaceRedistributionOfAcquiringOperation",
                "amount": 0,
            },
        ]

        async def request(method, path, body):
            if path.endswith("/transaction/list"):
                return {"result": {"operations": operations, "page_count": 1}}
            raise APIStatusError(400, "statement window")

        self.oz._request = request
        result = await self.oz.fetch_real_finance_breakdown(
            custom_date_from="2026-11-15", custom_date_to="2026-11-15"
        )
        self.assertEqual(
            (result["orders_count"], result["deliv_total"], result["total_payout"]),
            (1, 39, 195),
        )
        self.assertTrue(result["acquiring_reported"])
        self.assertEqual(result["orders"][0]["posting_units"], 2)
        self.assertIn("HTTP 400", result["source"])
        await init_db()
        self.addAsyncCleanup(close_db)
        await upsert_product_costs("123", "ozon", 10, 0)
        oz, wb = AsyncMock(), AsyncMock()
        oz.fetch_real_finance_breakdown.return_value = result
        wb.fetch_real_finance_breakdown.return_value = {}
        oz.fetch_stocks_and_prices.return_value = (
            wb.fetch_stocks_and_prices.return_value
        ) = []
        # 300 - 66 - 39 - 2*10 - 18 = 157. No second delivery on the two-unit posting.
        monthly = await sync_and_generate_orders_report(oz, wb)
        weekly = await generate_weekly_top_and_unprofitable_report(oz, wb)
        self.assertIn("157 ₽", monthly)
        self.assertIn("157 ₽", weekly)

    async def test_signed_wb_adjustments_and_explicit_zero_acquiring(self):
        row = {
            "rrd_id": 1,
            "supplier_oper_name": "Продажа",
            "quantity": 1,
            "retail_price_withdisc_rub": 100,
            "ppvz_for_pay": 105,
            "acquiring_fee": 0,
            "sa_name": "1001",
            "sale_dt": "2026-11-13",
        }
        wb_fin = self.wb._finance_rows([row], 0, "2026-11-13", "2026-11-13")
        self.assertEqual(
            (wb_fin["total_payout"], wb_fin["commission"], wb_fin["acquiring"]),
            (105, -5, 0),
        )
        self.assertFalse(wb_fin["acquiring_estimated"])
        await init_db()
        self.addAsyncCleanup(close_db)
        await upsert_product_costs("1001", "wb", 10, 0)
        oz, wb = AsyncMock(), AsyncMock()
        oz.fetch_real_finance_breakdown.return_value = {}
        wb.fetch_real_finance_breakdown.return_value = wb_fin
        oz.fetch_stocks_and_prices.return_value = (
            wb.fetch_stocks_and_prices.return_value
        ) = []
        monthly = await sync_and_generate_orders_report(oz, wb)
        weekly = await generate_weekly_top_and_unprofitable_report(oz, wb)
        self.assertIn("+5 ₽", monthly)
        self.assertIn("89 ₽", monthly)
        self.assertIn("89 ₽", weekly)

    async def test_wb_throttled_continuation_keeps_rrdid(self):
        self.wb._request = AsyncMock(
            side_effect=[
                [{"rrd_id": 39, "operation": "Доставка", "delivery_rub": 40}],
                APIStatusError(429, "wait", 2),
                None,
                [],
            ]
        )
        with patch("services.wb.statistics_api.asyncio.sleep", AsyncMock()) as wait:
            report = await self.wb.fetch_real_finance_breakdown()
        wait.assert_awaited_once_with(2)
        self.assertEqual(
            [c.kwargs["params"]["rrdid"] for c in self.wb._request.await_args_list[:3]],
            [0, 39, 39],
        )
        self.assertEqual(report["logistics_direct"], 40)


class ApprovalChain(unittest.IsolatedAsyncioTestCase):
    async def test_card_to_api_and_callback_errors(self):
        self.assertTrue(offline.STATE.is_dir())
        await init_db()
        self.addAsyncCleanup(close_db)
        await upsert_product_costs("1001", "ozon", 60, 5)
        await upsert_product_costs("1001", "wb", 60, 5)
        bot, oz, wb = AsyncMock(), AsyncMock(), AsyncMock()
        oz.fetch_real_finance_breakdown.return_value = {
            "orders_count": 10,
            "deliv_total": 390,
        }
        oz.fetch_active_ad_campaigns.return_value = []
        base = {
            "sku": "1001",
            "current_price": 299,
            "stock_fbs": 2,
            "commission_rate": 0.23,
            "daily_sales": 1,
        }
        oz.fetch_stocks_and_prices.return_value = [
            dict(base, product_id=33, fbs_pvz_dropoff_rub=10)
        ]
        wb.fetch_real_finance_breakdown.return_value = {"orders_count": 1}
        wb.fetch_stocks_and_prices.return_value = [
            dict(base, nm_id=99, fbs_logistics_cost=39)
        ]
        await run_daily_price_audit(bot, settings.TG_ADMIN_ID, oz, wb)
        ozcard, wbcard = [
            c.kwargs["reply_markup"].inline_keyboard
            for c in bot.send_message.await_args_list
        ]
        self.assertEqual(len(ozcard), 3)
        self.assertIn("299 ₽", ozcard[0][0].text)
        self.assertIn("прибыль", ozcard[1][0].text)
        from worker import dp

        transport = Bot(token=settings.TG_BOT_TOKEN)
        self.addAsyncCleanup(transport.session.close)
        sent = []

        async def telegram(bot, method, timeout=None):
            self.assertIsInstance(method, (AnswerCallbackQuery, SendMessage))
            sent.append(method)
            return True

        self.enterContext(patch.object(transport.session, "make_request", telegram))

        async def press(data):
            update = Update.model_validate(
                {
                    "update_id": 123,
                    "callback_query": {
                        "id": "query",
                        "from": {
                            "id": settings.TG_ADMIN_ID,
                            "is_bot": False,
                            "first_name": "Admin",
                        },
                        "chat_instance": "chat",
                        "data": data,
                        "message": {
                            "message_id": 1,
                            "chat": {"id": settings.TG_ADMIN_ID, "type": "private"},
                            "date": 1,
                            "text": "price audit",
                        },
                    },
                }
            )
            await dp.feed_update(transport, update)

        oz_data, wb_data = ozcard[1][0].callback_data, wbcard[0][0].callback_data
        action_id = int(oz_data.rsplit(":", 1)[-1])
        with patch.object(
            callbacks.ozon_api,
            "_request",
            AsyncMock(
                return_value={
                    "result": [
                        {
                            "product_id": 33,
                            "offer_id": "1001",
                            "updated": False,
                            "errors": [{"message": "bad <price>"}],
                        }
                    ]
                }
            ),
        ) as req:
            await press(oz_data)
            item = req.await_args.args[2]["prices"][0]
            self.assertEqual(
                (item["product_id"], item["offer_id"], item["auto_action_enabled"]),
                (33, "1001", "UNKNOWN"),
            )
            self.assertGreater(float(item["price"]), 300)
            self.assertIn("bad &lt;price&gt;", sent[-1].text)
            self.assertEqual((await get_pending_action(action_id)).status, "pending")

        with patch.object(
            callbacks.wb_api,
            "_request",
            AsyncMock(
                side_effect=[
                    {"data": {"listGoods": [{"nmID": 99, "discount": 25}]}},
                    {"error": True, "errorText": "Ошибка <скидки>"},
                ]
            ),
        ) as req:
            await press(wb_data)
            self.assertEqual(req.await_args.kwargs["json_data"]["data"][0]["nmID"], 99)
            self.assertIn("Ошибка &lt;скидки&gt;", sent[-1].text)

        # Accept the retry; the sibling button, even on an old Telegram card, cannot write again.
        with patch.object(
            callbacks.ozon_api,
            "_request",
            AsyncMock(
                return_value={
                    "result": [
                        {
                            "offer_id": "1001",
                            "product_id": 33,
                            "updated": True,
                            "errors": [],
                        }
                    ]
                }
            ),
        ) as req:
            await press(oz_data)
            self.assertEqual((await get_pending_action(action_id)).status, "executed")
            await press(ozcard[0][0].callback_data)
            req.assert_awaited_once()
        with patch.object(
            callbacks.wb_api,
            "_request",
            AsyncMock(
                side_effect=[
                    {"data": {"listGoods": [{"nmID": 99, "discount": 25}]}},
                    {"error": False, "data": {"id": 475}},
                ]
            ),
        ) as req:
            await press(wb_data)
            saved = await get_pending_action(int(wb_data.split(":")[-1]))
            self.assertEqual(saved.status, "submitted")
            target = saved.payload["new_price"]
            self.assertEqual(
                req.await_args.kwargs["json_data"]["data"][0]["price"],
                round(target / 0.75),
            )
            self.assertIn(f"{int(round(0.92 * target))} ₽", sent[-1].text)
