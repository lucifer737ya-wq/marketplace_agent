"""Регрессии по пользовательским примерам; только изолированные mock-данные."""
import asyncio
import unittest
from unittest.mock import AsyncMock, patch
from tests import offline

offline.require_isolation()
from sqlalchemy import delete
from database.models import Product, OrderRecord, init_db, close_db, get_db_session
from services.common.orders_report import sync_and_generate_orders_report
from services.wb.statistics_api import WBStatisticsAPI
from services.competitors.browser_listings import parse_ozon_dom_cards
from services.common import buyer_questions


class ReportRegression(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await init_db()
        async with get_db_session() as session:
            await session.execute(delete(OrderRecord))
            await session.execute(delete(Product))
            await session.commit()
        self.addAsyncCleanup(close_db)

    async def test_missing_cost_and_summary_do_not_claim_profit(self):
        oz, wb = AsyncMock(), AsyncMock()
        oz.fetch_real_finance_breakdown.return_value = {
            "source": "Ozon начисления by-day; незнакомые типы: 17, 72, 74, 76, 94, 116",
            "orders_count": 291,
            "sales_total": 43353,
            "total_payout": 20025.41,
            "total_deductions": 23327.59,
            "orders": [{"order_id": "x", "sku": "1010", "price": 149,
                        "quantity": 291, "status": "sold"}],
        }
        wb.fetch_real_finance_breakdown.return_value = {
            "summary_only": True,
            "summary_reports": [{"id": "1", "date_from": "2026-11-13", "date_to": "2026-11-19",
                                 "retail_amount_sum": 3012.82, "for_pay_sum": 2287.86}],
        }
        report = await sync_and_generate_orders_report(oz, wb)
        self.assertIn("291 шт.", report)
        self.assertIn("Чистая прибыль Ozon:</b> <b>н/д", report)
        self.assertIn("2026-11-13..2026-11-19", report)
        self.assertNotIn("Строк реализации: <b>0", report)
        self.assertNotIn("Вознаграждение WB", report)
        self.assertIn("ИТОГОВАЯ ЧИСТАЯ ПРИБЫЛЬ:</b> <b>н/д", report)

    async def test_october_example_and_complete_cogs_with_unknown_types(self):
        oz, wb = AsyncMock(), AsyncMock()
        oz.fetch_real_finance_breakdown.return_value = {
            "source": "Ozon начисления by-day; незнакомые типы: 17, 72, 74, 76, 94, 116",
            "orders_count": 2801, "sales_total": 469290,
            "total_deductions": 269468.13, "total_payout": 199821.87,
            "orders": [{"order_id": "m", "sku": "1010", "price": 167.54,
                        "quantity": 2801, "status": "sold"}],
        }
        wb.fetch_real_finance_breakdown.return_value = {"summary_only": True, "summary_reports": [
            {"id": "9", "date_from": "2026-10-01", "date_to": "2026-10-07",
             "retail_amount_sum": 17606.87, "for_pay_sum": 12680.63}]}
        report = await sync_and_generate_orders_report(oz, wb, custom_date_from="2026-10-01", custom_date_to="2026-10-31")
        self.assertIn("2801 шт.", report)
        self.assertIn("Чистая прибыль Ozon:</b> <b>н/д", report)
        self.assertIn("ИТОГОВАЯ ЧИСТАЯ ПРИБЫЛЬ:</b> <b>н/д", report)
        from database.crud import upsert_product_costs
        await upsert_product_costs("1010", "ozon", cost_price=50, packaging_cost=0)
        report = await sync_and_generate_orders_report(oz, wb)
        self.assertIn("Чистая прибыль Ozon:</b> <b>н/д", report)

    async def test_ozon_two_failed_finance_sources_are_not_zero_sales(self):
        from services.ozon.seller_api import OzonSellerAPI
        api = OzonSellerAPI()
        api._fetch_cash_flow = AsyncMock(side_effect=RuntimeError("not available"))
        api._fetch_accrual_by_day = AsyncMock(side_effect=RuntimeError("not available"))
        with patch.object(api, "_read_disk_cache", return_value=None), patch.object(api, "_write_disk_cache"):
            with self.assertRaisesRegex(RuntimeError, "финансы недоступны"):
                await api.fetch_real_finance_breakdown(custom_date_from="2026-11-13", custom_date_to="2026-11-19")

    async def test_wb_overlapping_week_refused_and_duplicates_deduped(self):
        api = WBStatisticsAPI()
        row = {"reportId": 1, "dateFrom": "2026-11-10", "dateTo": "2026-11-16",
               "retailAmountSum": "3012.82", "forPaySum": "2287.86"}
        api._fetch_report_positions = AsyncMock(return_value=[row])
        with self.assertRaisesRegex(RuntimeError, "выходит за запрошенный период"):
            await api._finance_from_summaries("2026-11-13", "2026-11-19", "weekly")
        api._fetch_report_positions.return_value = [row, dict(row)]
        result = await api._finance_from_summaries("2026-11-10", "2026-11-16", "weekly")
        self.assertEqual(result["revenue_seller"], 3012.82)
        self.assertEqual(len(result["summary_reports"]), 1)
        self.assertNotIn("commission", result)
        row2 = dict(row, reportId=2)
        api._fetch_report_positions.return_value = [row, row2]
        with self.assertRaisesRegex(RuntimeError, "пересекаются"):
            await api._finance_from_summaries("2026-11-10", "2026-11-16", "weekly")

    async def test_questions_and_reviews_never_fallback_to_stock_claim(self):
        with patch.object(buyer_questions, "generate", AsyncMock(side_effect=RuntimeError("down"))), patch.object(buyer_questions.asyncio, "sleep", AsyncMock()):
            with self.assertRaises(RuntimeError):
                await buyer_questions.generate_buyer_question_answer("Какая вязкость?", "1010")
            with self.assertRaises(RuntimeError):
                await buyer_questions.generate_review_reply("Густой", 3, "1010")

    async def test_gemini_400_keeps_only_safe_status_without_error_body(self):
        from services.common import gemini
        class Response:
            status = 400
            async def __aenter__(self): return self
            async def __aexit__(self, *args): pass
            async def json(self):
                return {"error": {"status": "INVALID_ARGUMENT", "message": "secret-value"}}
        class Session:
            def __init__(self, *args, **kwargs): pass
            async def __aenter__(self): return self
            async def __aexit__(self, *args): pass
            def post(self, *args, **kwargs): return Response()
        with patch.object(gemini.settings, "GEMINI_API_KEY", "secret-value"), \
             patch.object(gemini, "request_routes", return_value=[gemini.DIRECT]), \
             patch.object(gemini, "_get_available_models", AsyncMock(return_value=["gemini-3.1-flash-lite"])), \
             patch.object(gemini.aiohttp, "ClientSession", Session):
            with self.assertRaises(RuntimeError) as err:
                await gemini.generate("private prompt", max_tokens=50)
        self.assertIn("INVALID_ARGUMENT", str(err.exception))
        self.assertNotIn("secret-value", str(err.exception))
        self.assertNotIn("private prompt", str(err.exception))

    async def test_advice_filter_and_timeout_fairness(self):
        with patch.object(buyer_questions, "call_fast_gemini", AsyncMock(return_value="Добавьте спирт")):
            with self.assertRaises(RuntimeError):
                await buyer_questions.generate_buyer_question_answer("Загустел", "1010")
        import worker
        async def slow(**kwargs):
            await asyncio.sleep(1)
        question = AsyncMock(return_value={"status": "success"})
        with patch.object(worker, "auto_reply_unanswered_reviews", slow), patch.object(worker, "auto_reply_unanswered_questions", question), patch.object(worker, "notify_admin", AsyncMock()), patch.object(worker, "BROWSER_JOB_BUDGET_SEC", .02):
            await worker.scheduled_reviews_and_questions_job()
        question.assert_awaited_once()


class ListingRegression(unittest.TestCase):
    def test_ozon_id_is_full_numeric_suffix_and_ambiguous_prices_skipped(self):
        cards = [
            {"href": "https://www.ozon.ru/product/gel-12-02-123456789/?x=1", "text": "Гель 15 г 5% 699 ₽"},
            {"href": "https://www.ozon.ru/product/gel-12-987654321/", "text": "Скидка 15% 699 ₽ 999 ₽"},
            {"href": "https://www.ozon.ru/product/gel-12345678/", "text": "51 79 15"},
        ]
        result = parse_ozon_dom_cards(cards)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["sku"], "123456789")
        self.assertEqual(result[0]["buyer_price"], 699)
