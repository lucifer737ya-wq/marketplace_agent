"""Резервные ярусы: артикулы для ТОП-5 и браузерный разбор витрин."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from tests import offline

assert offline.require_isolation() is None  # изоляция окружения обязательна
from database.crud import upsert_product_costs
from database.models import close_db, init_db
from services.common.financial_pnl import generate_weekly_top_and_unprofitable_report
from services.competitors.browser_listings import (
    parse_ozon_dom_cards,
    parse_ozon_widget_states,
    parse_wb_products,
)
from services.competitors.deep_parser import (
    _parse_ozon_competitors_with_status,
    _parse_wb_competitors_with_status,
)
from services.common.storage import DiskCache
from services.ozon.seller_api import OzonSellerAPI
from services.wb.statistics_api import WBStatisticsAPI


class SkuSalesFallback(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        await init_db()
        self.addAsyncCleanup(close_db)

    def _services(self, tmp: str):
        oz, wb = AsyncMock(), AsyncMock()
        # Начисления и закрытые отчёты за свежий период пусты — типичный случай.
        oz.fetch_real_finance_breakdown.return_value = {
            "sales_total": 300000.0,
            "orders_count": 1000,
            "orders": [],
            "commission": 66000.0,
            "deliv_total": 39000.0,
            "acquiring": 4500.0,
            "ads_total": 6000.0,
        }
        wb.fetch_real_finance_breakdown.return_value = {
            "revenue_seller": 200000.0,
            "orders_count": 0,
            "orders": [],
            "commission": 40000.0,
            "total_payout": 150000.0,
            "total_deductions": 50000.0,
            "summary_only": True,
        }
        oz.fetch_stocks_and_prices.return_value = []
        wb.fetch_stocks_and_prices.return_value = []
        wb.fetch_wb_tariffs.return_value = {
            "subjects": {},
            "warehouses": {"Коледино": {"boxDeliveryBase": "48,5"}},
        }
        oz.fetch_sales_by_sku.return_value = (
            [
                {
                    "order_id": "a1",
                    "marketplace": "ozon",
                    "sku": "1010",
                    "quantity": 40,
                    "price": 350.0,
                    "status": "sold",
                },
                {
                    "order_id": "a2",
                    "marketplace": "ozon",
                    "sku": "2222",
                    "quantity": 5,
                    "price": 299.0,
                    "status": "sold",
                },
            ],
            "аналитика Ozon по SKU",
        )
        wb.fetch_sales_by_sku.return_value = (
            [
                {
                    "order_id": "b1",
                    "marketplace": "wb",
                    "sku": "1010",
                    "quantity": 25,
                    "price": 300.0,
                    "buyer_price": 250.0,
                    "status": "sold",
                }
            ],
            "лента заказов WB",
        )
        return oz, wb

    async def test_top5_built_from_sku_fallback_with_real_numbers(self) -> None:
        await upsert_product_costs("1010", "ozon", 120, 10)
        await upsert_product_costs("1010", "wb", 120, 10)
        oz, wb = self._services(tempfile.mkdtemp())
        report = await generate_weekly_top_and_unprofitable_report(oz, wb)
        self.assertNotIn("заказов не найдено", report)
        self.assertIn("резервному источнику", report)
        self.assertIn("Продано: <b>40 шт.</b> на <b>14,000 ₽</b>", report)
        self.assertIn("Продано: <b>25 шт.</b> на <b>7,500 ₽</b>", report)
        # Ozon без подтверждённого факта доставки не получает выдуманную прибыль.
        self.assertIn("OZON:2222", report)
        self.assertIn("Без подтвержденной себестоимости", report)
        # WB: 300 − 130 себес − (логистика 48.5 + эквайринг 1.5% от 300) − УСН 15 = 102 ₽/шт.
        self.assertIn("+102.0 ₽/шт.", report)
        self.assertIn("2,550 ₽", report)

    async def test_no_fallback_data_keeps_honest_empty_message(self) -> None:
        oz, wb = self._services(tempfile.mkdtemp())
        oz.fetch_sales_by_sku.return_value = ([], "Ozon: артикулы недоступны (HTTP 403)")
        wb.fetch_sales_by_sku.return_value = ([], "WB: артикулы недоступны (HTTP 403)")
        report = await generate_weekly_top_and_unprofitable_report(oz, wb)
        self.assertIn("заказов не найдено", report)
        self.assertIn("HTTP 403", report)

    async def test_fallback_exception_does_not_break_report(self) -> None:
        oz, wb = self._services(tempfile.mkdtemp())
        oz.fetch_sales_by_sku.side_effect = RuntimeError("boom")
        report = await generate_weekly_top_and_unprofitable_report(oz, wb)
        self.assertIn("резервный источник артикулов недоступен", report)
        self.assertIn("Продано: <b>25 шт.</b>", report)


class SkuSalesSources(unittest.IsolatedAsyncioTestCase):
    def _caches(self):
        tmp = tempfile.mkdtemp()
        self.oz, self.wb = OzonSellerAPI(), WBStatisticsAPI()
        self.oz._cache = DiskCache(Path(tmp) / "oz")
        self.wb._cache = DiskCache(Path(tmp) / "wb")

    async def test_wb_sales_by_sku_uses_order_feed_when_report_empty(self) -> None:
        self._caches()
        self.wb._fetch_report_details = AsyncMock(return_value=[])
        self.wb._fetch_orders_7d = AsyncMock(
            return_value=[
                {"nmId": 555, "date": "2026-11-16T10:00:00Z", "finishedPrice": 250.0,
                 "priceWithDisc": 260.0, "isCancel": False},
                {"nmId": 555, "date": "2026-11-16T11:00:00Z", "finishedPrice": 250.0,
                 "priceWithDisc": 260.0, "isCancel": True},
            ]
        )
        rows, source = await self.wb.fetch_sales_by_sku(days=7)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["sku"], "555")
        self.assertEqual(rows[0]["price"], 250.0)
        self.assertIn("лента заказов", source)

    async def test_ozon_sales_by_sku_falls_back_across_metric_sets(self) -> None:
        self._caches()
        calls = []

        async def fake_request(method, endpoint, payload):
            calls.append(list(payload["metrics"]))
            if "revenue" in payload["metrics"]:
                raise RuntimeError("metric revenue unavailable")
            return {
                "result": {
                    "data": [
                        {"dimensions": [{"id": "9001"}], "metrics": ["14"]},
                        {"dimensions": [{"id": "9002"}], "metrics": [0]},
                    ]
                }
            }

        self.oz._request = AsyncMock(side_effect=fake_request)
        self.oz.fetch_stocks_and_prices = AsyncMock(
            return_value=[{"sku": "9001", "current_price": 350.0, "buyer_price": 340.0}]
        )
        rows, source = await self.oz.fetch_sales_by_sku(days=7)
        self.assertEqual(calls[0], ["ordered_units", "revenue"])
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["sku"], "9001")
        self.assertEqual(rows[0]["quantity"], 14)
        # Выручки нет — подставляется текущая цена ЛК, и это подписано в отчёте.
        self.assertEqual(rows[0]["price"], 350.0)
        self.assertIn("оценка", source)
        self.assertIn("аналитика Ozon", source)


class BrowserListingParsers(unittest.TestCase):
    def test_wb_products_decode_cents_and_skip_priceless(self) -> None:
        items = parse_wb_products(
            [
                {
                    "id": 1,
                    "name": "Гель",
                    "brand": "YaSoKo",
                    "reviewRating": 4.8,
                    "feedbacks": 42,
                    "sizes": [{"price": {"product": 35000, "basic": 50000}}],
                },
                {"id": 2, "name": "Без цены", "sizes": []},
            ],
            limit=5,
        )
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["buyer_price"], 350.0)
        self.assertEqual(items[0]["mp"], "WB")

    def test_ozon_widget_states_walk_nested_json_strings(self) -> None:
        import json

        payload = {
            "searchResultsV2-1": json.dumps(
                {
                    "items": [
                        {"sku": 111, "name": "Гель 15г", "brand": "YaSoKo",
                         "price": "350 ₽", "rating": 4.9, "feedbackCount": 88},
                        {"productId": 222, "title": "Топ", "price": {"price": "1 299 ₽"}},
                    ]
                }
            )
        }
        items = parse_ozon_widget_states(payload, limit=5)
        self.assertEqual([i["sku"] for i in items], ["111", "222"])
        self.assertEqual([i["buyer_price"] for i in items], [350.0, 1299.0])

    def test_ozon_widget_states_returns_nothing_for_antibot_html(self) -> None:
        self.assertEqual(parse_ozon_widget_states("<html>antibot</html>", limit=5), [])

    def test_ozon_dom_cards_extract_sku_and_price(self) -> None:
        items = parse_ozon_dom_cards(
            [{"href": "https://www.ozon.ru/product/gel-777/", "text": "Гель\n350 ₽"}],
            limit=5,
        )
        self.assertEqual(items[0]["sku"], "777")
        self.assertEqual(items[0]["buyer_price"], 350.0)


class CompetitorBrowserTier(unittest.IsolatedAsyncioTestCase):
    async def test_wb_http_failure_falls_back_to_browser(self) -> None:
        browser_items = [
            {"mp": "WB", "sku": "9", "name": "Гель", "brand": "B",
             "rating": 4.5, "feedbacks": 3, "buyer_price": 300.0}
        ]
        with patch(
            "services.competitors.deep_parser._fetch_json_direct",
            AsyncMock(return_value=(None, "HTTP 403", False)),
        ), patch(
            "services.competitors.browser_listings.wb_search_via_browser",
            AsyncMock(return_value=(browser_items, None)),
        ):
            items, err = await _parse_wb_competitors_with_status("гель", count=5)
        self.assertEqual(len(items), 1)
        self.assertIsNone(err)

    async def test_ozon_http_failure_falls_back_to_browser(self) -> None:
        browser_items = [
            {"mp": "OZON", "sku": "77", "name": "Гель", "brand": "B",
             "rating": 0.0, "feedbacks": 0, "buyer_price": 350.0}
        ]
        with patch(
            "services.competitors.deep_parser._fetch_json_direct",
            AsyncMock(return_value=(None, "HTTP 307", False)),
        ), patch(
            "services.competitors.browser_listings.ozon_search_via_browser",
            AsyncMock(return_value=(browser_items, None)),
        ):
            items, err = await _parse_ozon_competitors_with_status("гель", count=5)
        self.assertEqual(len(items), 1)
        self.assertIsNone(err)

    async def test_browser_unavailable_keeps_clear_status(self) -> None:
        with patch(
            "services.competitors.deep_parser._fetch_json_direct",
            AsyncMock(return_value=(None, "HTTP 498", False)),
        ), patch(
            "services.competitors.browser_listings.wb_search_via_browser",
            AsyncMock(side_effect=RuntimeError("Playwright недоступен")),
        ):
            items, err = await _parse_wb_competitors_with_status("гель", count=5)
        self.assertEqual(items, [])
        self.assertIn("антибот", err)
