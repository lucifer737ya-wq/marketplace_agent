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

    async def test_ozon_accrual_by_day_reconcile_with_cash_flow(self):
        """Финансы Ozon из /v1/finance/accrual/by-day (+сверка с cash-flow)."""
        period = {"begin": "2026-11-18", "end": "2026-11-18"}
        accrual = {
            "accrual_id": "11",
            "date": "2026-11-18",
            "accrued_category": "POSTING",
            "unit_number": "123-1",
            "posting": {
                "products": [
                    {
                        "sku": 123,
                        "commission": {
                            "seller_price": {"amount": "300", "currency": "RUB"},
                            "sale_price": {"amount": "330", "currency": "RUB"},
                            "sale_commission": {"amount": "-66", "currency": "RUB"},
                        },
                        "delivery": {
                            "total_accrued": {"amount": "-39", "currency": "RUB"},
                            "services": [
                                {"type_id": 32, "accrued": {"amount": "-39"}}
                            ],
                        },
                    }
                ]
            },
        }
        ad = {
            "accrual_id": "12",
            "date": "2026-11-18",
            "accrued_category": "ITEM",
            "item_fees": {
                "fees": [
                    {
                        "sku": 123,
                        "fees": [
                            {"type_id": 41, "accrued": {"amount": "-6"}},
                            {"type_id": 1, "accrued": {"amount": "-2.1"}},
                        ],
                    }
                ]
            },
        }
        details = {
            "period": period,
            "delivery": {
                "delivery_services": {
                    "items": [{"name": "MarketplaceServiceItemDelivToCustomer", "price": -39}]
                }
            },
            "services": {
                "items": [{"name": "MarketplaceServiceCostPerClick", "price": -6}]
            },
            "others": {"items": [{"name": "Acquiring", "price": -2.1}]},
        }
        flow = {
            "period": period,
            "orders_amount": 300,
            "returns_amount": 0,
            "commission_amount": -66,
        }

        async def request(method, path, payload):
            if path == "/v1/finance/accrual/types":
                return {
                    "accrual_types": [
                        {"id": 32, "name": "Logistic"},
                        {"id": 41, "name": "PayPerClick"},
                        {"id": 1, "name": "Acquiring"},
                    ]
                }
            if path == "/v1/finance/accrual/by-day":
                self.assertEqual(payload["date"], "2026-11-18")
                return {"accruals": [accrual, ad], "last_id": ""}
            self.assertEqual(path, "/v1/finance/cash-flow-statement/list")
            self.assertIs(payload["with_details"], True)
            return {
                "result": {"cash_flows": [flow], "details": details},
                "page_count": 1,
            }

        self.oz._request = AsyncMock(side_effect=request)
        result = await self.oz.fetch_real_finance_breakdown(
            custom_date_from="2026-11-18", custom_date_to="2026-11-18"
        )
        self.assertIn("cash-flow", result["source"])
        self.assertNotIn("transaction/list", result["source"])
        self.assertEqual((result["orders_count"], result["deliv_total"]), (1, 39))
        self.assertEqual((result["ads_total"], result["billed_skus"]), (6, ["123"]))
        self.assertEqual(result["acquiring"], 2.1)
        self.assertEqual(result["total_payout"], 300 - 66 - 39 - 6 - 2.1)

    async def test_ozon_accrual_pagination_and_unknown_types(self):
        """Курсор last_id прокручивается, незнакомый type_id не теряет деньги."""
        seen = []

        async def request(method, path, payload):
            if path == "/v1/finance/accrual/types":
                raise APIStatusError(400, "types unavailable")
            if path == "/v1/finance/accrual/by-day":
                seen.append(payload.get("last_id", ""))
                if not payload.get("last_id"):
                    return {
                        "accruals": [
                            {
                                "accrual_id": "1",
                                "date": "2026-11-18",
                                "non_item_fee": {
                                    "type_id": 777,
                                    "accrued": {"amount": "-10"},
                                },
                            }
                        ],
                        "last_id": "page-2",
                    }
                return {
                    "accruals": [
                        {
                            "accrual_id": "2",
                            "date": "2026-11-18",
                            "non_item_fee": {
                                "type_id": 41,
                                "accrued": {"amount": "-5"},
                            },
                        }
                    ],
                    "last_id": "",
                }
            raise APIStatusError(400, "cash-flow window")

        self.oz._request = AsyncMock(side_effect=request)
        result = await self.oz.fetch_real_finance_breakdown(
            custom_date_from="2026-11-18", custom_date_to="2026-11-18"
        )
        self.assertEqual(seen, ["", "page-2"])
        self.assertEqual(result["ads_total"], 5)
        # Книга расходов ведётся положительными суммами (как в cash-flow)
        self.assertEqual(result["other_fines"], 10)
        self.assertIn("777", result["source"])
        self.assertIn("HTTP 400", result["source"])

    async def test_ozon_legacy_type_id_inside_accrual_is_not_a_fee(self):
        """Старая схема: type_id у самого начисления не должен считаться услугой."""
        accrual = {
            "type_id": 55,
            "date": "2026-11-18",
            "accrued": {"amount": "100"},
            "posting": {
                "products": [
                    {
                        "sku": 5,
                        "commission": {
                            "seller_price": {"amount": "100"},
                            "sale_commission": {"amount": "-10"},
                        },
                    }
                ]
            },
        }

        async def request(method, path, payload):
            if path == "/v1/finance/accrual/types":
                return {"accrual_types": []}
            if path == "/v1/finance/accrual/by-day":
                return {"accruals": [accrual], "last_id": ""}
            raise APIStatusError(400, "cash-flow window")

        self.oz._request = AsyncMock(side_effect=request)
        result = await self.oz.fetch_real_finance_breakdown(
            custom_date_from="2026-11-18", custom_date_to="2026-11-18"
        )
        self.assertEqual(result["sales_total"], 100)
        self.assertEqual(result["commission"], 10)
        self.assertEqual(result["other_fines"], 0)
        self.assertEqual(result["orders_count"], 1)

    async def test_ozon_return_accrual_reduces_commission(self):
        """Возврат: отрицательная продажа возвращает и комиссию."""
        row = {
            "accrual_id": "9",
            "date": "2026-11-18",
            "posting": {
                "posting_number": "123-1",
                "products": [
                    {
                        "sku": 7,
                        "commission": {
                            "seller_price": {"amount": "-100"},
                            "sale_commission": {"amount": "-10"},
                        },
                    }
                ],
            },
        }

        async def request(method, path, payload):
            if path == "/v1/finance/accrual/types":
                return {"accrual_types": []}
            if path == "/v1/finance/accrual/by-day":
                return {"accruals": [row], "last_id": ""}
            raise APIStatusError(400, "cash-flow window")

        self.oz._request = AsyncMock(side_effect=request)
        result = await self.oz.fetch_real_finance_breakdown(
            custom_date_from="2026-11-18", custom_date_to="2026-11-18"
        )
        self.assertEqual(result["sales_total"], -100)
        self.assertEqual(result["commission"], -10)

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
            self.assertTrue(
                url.endswith("/analytics/v1/order-feed")
                or url.endswith("/supplier/orders")
            )
            return [] if url.endswith("/supplier/orders") else {"orders": [], "snapshotTime": "t"}

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

    async def test_wb_finance_api_details_returns_and_acquiring(self):
        """finance-api WB: суммы строками, camelCase, пагинация по rrdId."""
        sale = {
            "rrdId": 19,
            "supplierOperName": "Продажа",
            "quantity": 1,
            "retailPriceWithDisc": "300",
            "retailAmount": "280",
            "forPay": "215.8",
            "acquiringBank": "Bank",
            "vendorCode": "1001",
            "saleDt": "2026-11-17T00:00:00Z",
        }
        ret = dict(
            sale,
            rrdId=20,
            supplierOperName="Возврат",
            deliveryService="30",
            returnAmount=1,
        )

        async def request(method, url, params=None, json_data=None):
            if "/upd" in url:
                return []
            self.assertTrue(url.endswith("/api/finance/v1/sales-reports/detailed"))
            self.assertNotIn("reportDetailByPeriod", url)
            return {0: [sale], 19: [ret], 20: [], 999: []}[json_data["rrdId"]]

        self.wb._request = AsyncMock(side_effect=request)
        report = await self.wb.fetch_real_finance_breakdown()
        self.assertEqual(report["returns_amount"], 300)
        self.assertEqual(report["revenue_seller"], 0)
        self.assertEqual(report["total_payout"], -30)
        self.assertTrue(report["acquiring_estimated"])
        self.assertEqual(report["orders"][0]["acquiring"], 4.2)
        self.assertEqual(report["orders"][0]["created_at"], "2026-11-17")
        self.assertIn("finance-api", report["source"])

    async def test_wb_finance_defaults_to_closed_report_summaries(self):
        """Если детализации нет, берутся итоги закрытых отчётов из /sales-reports/list."""
        summary = {
            "reportId": 1,
            "dateFrom": "2026-11-10",
            "dateTo": "2026-11-16",
            "retailAmountSum": "1000",
            "forPaySum": "700",
            "deliveryServiceSum": "100",
            "paidStorageSum": "20",
            "paidAcceptanceSum": "5",
            "deductionSum": "10",
            "penaltySum": "15",
            "cashbackCommissionChangeSum": "0",
        }

        async def request(method, url, params=None, json_data=None):
            if "/upd" in url:
                return []
            if url.endswith("/sales-reports/detailed"):
                return []
            self.assertTrue(url.endswith("/sales-reports/list"))
            return [summary]

        self.wb._request = AsyncMock(side_effect=request)
        report = await self.wb.fetch_real_finance_breakdown(
            custom_date_from="2026-11-10", custom_date_to="2026-11-16"
        )
        self.assertTrue(report["summary_only"])
        self.assertEqual(report["revenue_seller"], 1000)
        self.assertEqual(report["for_pay"], 700)
        self.assertNotIn("total_payout", report)
        self.assertNotIn("total_deductions", report)
        self.assertEqual(report["summary_reports"][0]["for_pay_sum"], 700)
        self.assertIn("закрытые", report["source"])

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
            ["promotion/count", "https://advert-api.wildberries.ru/api/advert/v3/adverts", "upd"],
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

    async def test_accrual_orders_feed_pnl_once(self):
        """Начисления → P&L: комиссия, логистика, эквайринг, COGS и налог считаются один раз."""
        accrual = {
            "accrual_id": "11",
            "date": "2026-11-15",
            "unit_number": "P1",
            "posting": {
                "products": [
                    {
                        "sku": 123,
                        "commission": {
                            "seller_price": {"amount": "300"},
                            "sale_price": {"amount": "330"},
                            "sale_commission": {"amount": "-66"},
                        },
                        "delivery": {
                            "total_accrued": {"amount": "-39"},
                            "services": [{"type_id": 32, "accrued": {"amount": "-39"}}],
                        },
                    }
                ]
            },
        }

        async def request(method, path, payload):
            if path == "/v1/finance/accrual/types":
                return {"accrual_types": [{"id": 32, "name": "Logistic"}]}
            if path == "/v1/finance/accrual/by-day":
                return {"accruals": [accrual], "last_id": ""}
            raise APIStatusError(400, "statement window")

        self.oz._request = request
        result = await self.oz.fetch_real_finance_breakdown(
            custom_date_from="2026-11-15", custom_date_to="2026-11-15"
        )
        self.assertEqual(
            (result["orders_count"], result["deliv_total"], result["total_payout"]),
            (1, 39, 195),
        )
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
        monthly = await sync_and_generate_orders_report(oz, wb)
        weekly = await generate_weekly_top_and_unprofitable_report(oz, wb)
        # 300 − 66 − 39 − эквайринг 1.5% (4.50) − себестоимость 10 − налог 18 = 162.50
        self.assertIn("Чистая прибыль Ozon:</b> <code>162.50 ₽</code>", monthly)
        self.assertIn("162.50 ₽", weekly)

    async def test_signed_wb_adjustments_and_explicit_zero_acquiring(self):
        row = {
            "rrdId": 1,
            "supplierOperName": "Продажа",
            "quantity": 1,
            "retailPriceWithDisc": "100",
            "forPay": "105",
            "acquiringFee": "0",
            "vendorCode": "1001",
            "saleDt": "2026-11-13T00:00:00Z",
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

    async def test_wb_finance_deduplicates_repeated_rows(self):
        row = {
            "rrdId": 39,
            "supplierOperName": "Доставка",
            "deliveryService": "40",
        }
        self.wb._request = AsyncMock(
            side_effect=[[row, row], [], [], []]
        )
        report = await self.wb.fetch_real_finance_breakdown()
        self.assertEqual(report["logistics_direct"], 40)
        detail_calls = [
            c
            for c in self.wb._request.await_args_list
            if c.args and str(c.args[1]).endswith("/sales-reports/detailed")
        ]
        self.assertEqual(
            [c.kwargs["json_data"]["rrdId"] for c in detail_calls], [0, 39]
        )


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
