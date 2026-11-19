"""Контракты отказоустойчивости: резервные маршруты и деградация без падения.

Проверяются именно запасные пути: мёртвый прокси для Telegram, недоступные
тарифы WB, неполное покрытие финансового отчёта Ozon, отсутствие Playwright,
недоступный диск для HTML-отчёта и сбой записи статуса действия в БД.
"""

import asyncio
import tempfile
import time
import unittest
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from tests import offline

offline.require_isolation()

from config import settings
from services.common import routes
from services.common.market_api import APIStatusError
from services.common.storage import DiskCache
from services.ozon.seller_api import OzonSellerAPI, _cash_flow_windows
from services.wb.statistics_api import WBStatisticsAPI


class TelegramRouting(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        routes.reset_routes()

    def tearDown(self):
        routes.reset_routes()

    def test_direct_is_first_and_proxy_is_fallback(self):
        listed = routes.telegram_routes("telegram-test")
        self.assertEqual(listed[0], routes.DIRECT)
        self.assertIn("http://127.0.0.1:3066", listed)
        self.assertIsNone(routes.proxy_for_route(routes.DIRECT))
        self.assertEqual(
            routes.proxy_for_route("http://127.0.0.1:3066"),
            "http://127.0.0.1:3066",
        )

    async def test_bot_starts_through_proxy_when_direct_route_is_dead(self):
        import worker

        attempts = []

        class FakeSession:
            def __init__(self, proxy=None, timeout=None):
                self.proxy = proxy

            async def close(self):
                return None

        class FakeBot:
            def __init__(self, token=None, session=None):
                self.session = session

            async def get_me(self):
                attempts.append(self.session.proxy)
                if self.session.proxy is None:
                    raise OSError("direct route blocked")
                return SimpleNamespace(username="yasoko_bot")

        previous = worker.bot
        worker.bot = None
        try:
            with patch.object(worker, "telegram_routes", return_value=[routes.DIRECT, "http://127.0.0.1:3066"]), \
                 patch.object(worker, "Bot", FakeBot), \
                 patch.object(worker, "AiohttpSession", FakeSession):
                await worker.ensure_working_telegram_route()
            self.assertEqual(attempts, [None, "http://127.0.0.1:3066"])
            self.assertIsInstance(worker.bot, FakeBot)
        finally:
            worker.bot = previous

    async def test_no_routes_gives_actionable_error(self):
        import worker

        with patch.object(worker, "telegram_routes", side_effect=ValueError("нет маршрута")):
            with self.assertRaises(RuntimeError) as ctx:
                await worker.ensure_working_telegram_route()
        self.assertIn("ALLOW_DIRECT_CONNECTIONS", str(ctx.exception))


class OzonFinanceFallbacks(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.oz = OzonSellerAPI()
        self.oz._cache = DiskCache(Path(self.tmp.name) / "oz")

    def test_cash_flow_windows_follow_ozon_half_months(self):
        self.assertEqual(
            _cash_flow_windows(date(2026, 11, 10), date(2026, 11, 19)),
            [(date(2026, 11, 10), date(2026, 11, 15)), (date(2026, 11, 16), date(2026, 11, 19))],
        )
        self.assertEqual(
            _cash_flow_windows(date(2026, 11, 18), date(2026, 11, 18)),
            [(date(2026, 11, 18), date(2026, 11, 18))],
        )
        self.assertEqual(
            _cash_flow_windows(date(2026, 11, 28), date(2026, 12, 2)),
            [(date(2026, 11, 28), date(2026, 11, 30)), (date(2026, 12, 1), date(2026, 12, 2))],
        )

    async def test_incomplete_cash_flow_never_overstates_revenue(self):
        """Отчёт за чужие дни не должен подменять суточные начисления."""
        accrual = {
            "accrual_id": "1",
            "date": "2026-11-10",
            "unit_number": "U-1",
            "posting": {
                "products": [
                    {
                        "sku": 555,
                        "commission": {
                            "seller_price": {"amount": "100"},
                            "sale_price": {"amount": "120"},
                            "sale_commission": {"amount": "-10"},
                        },
                        "delivery": {
                            "total_accrued": {"amount": "-5"},
                            "services": [{"type_id": 32, "accrued": {"amount": "-5"}}],
                        },
                    }
                ]
            },
        }

        async def request(method, path, payload):
            if path == "/v1/finance/accrual/types":
                return {"accrual_types": [{"id": 32, "name": "Logistic"}]}
            if path == "/v1/finance/accrual/by-day":
                return {"accruals": [accrual if payload["date"] == "2026-11-10" else {}], "last_id": ""}
            # Cash-flow отвечает только за первую половину месяца.
            return {
                "result": {
                    "cash_flows": [
                        {
                            "period": {"begin": "2026-11-01", "end": "2026-11-15"},
                            "orders_amount": 999,
                            "returns_amount": 0,
                            "commission_amount": -99,
                        }
                    ],
                    "details": None,
                },
                "page_count": 1,
            }

        self.oz._request = AsyncMock(side_effect=request)
        result = await self.oz.fetch_real_finance_breakdown(
            custom_date_from="2026-11-10", custom_date_to="2026-11-19"
        )
        self.assertEqual(result["sales_total"], 100.0)
        self.assertEqual(result["total_payout"], 85.0)
        self.assertEqual(result["orders_count"], 1)
        self.assertIn("неполное покрытие", result["source"])
        self.assertNotIn("999", str(result["sales_total"]))

    async def test_cash_flow_error_is_reported_and_accrual_used(self):
        async def request(method, path, payload):
            if path == "/v1/finance/accrual/types":
                return {"accrual_types": []}
            if path == "/v1/finance/accrual/by-day":
                return {"accruals": [], "last_id": ""}
            raise APIStatusError(400, "period")

        self.oz._request = AsyncMock(side_effect=request)
        result = await self.oz.fetch_real_finance_breakdown(
            custom_date_from="2026-11-18", custom_date_to="2026-11-18"
        )
        self.assertIn("HTTP 400", result["source"])
        self.assertIn("начисления", result["source"].lower())


class WildberriesDegradation(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.wb = WBStatisticsAPI()
        self.wb._cache = DiskCache(Path(self.tmp.name) / "wb")

    async def test_tariff_failure_returns_empty_shape_instead_of_crashing(self):
        self.wb._request = AsyncMock(side_effect=RuntimeError("tariffs down"))
        result = await self.wb.fetch_wb_tariffs()
        self.assertEqual(result, {"subjects": {}, "warehouses": {}})

    async def test_tariff_partial_response_keeps_what_is_valid(self):
        async def request(method, url, params=None):
            if url.endswith("/tariffs/commission"):
                return {"report": [{"subjectID": 1, "paidStorageKgvp": 20, "kgvpMarketplace": 22}, "bad"]}
            return {"response": {"data": {"warehouseList": [{"warehouseName": "Коледино", "boxDeliveryBase": "50"}, {}]}}}

        self.wb._request = AsyncMock(side_effect=request)
        result = await self.wb.fetch_wb_tariffs()
        self.assertEqual(result["subjects"], {"1": {"fbo": 20, "fbs": 22}})
        self.assertIn("Коледино", result["warehouses"])

    async def test_ad_spend_ignores_malformed_rows(self):
        self.wb._request = AsyncMock(
            return_value=[{"updSum": "10"}, {}, {"advertId": 3, "updSum": "abc"}, {"updSum": 2.5}]
        )
        self.assertEqual(await self.wb.fetch_wb_ad_spend("2026-11-01", "2026-11-18"), 12.5)

    async def test_finance_falls_back_to_legacy_v5_when_finance_api_is_down(self):
        legacy_row = {
            "rrd_id": 5,
            "supplier_oper_name": "Продажа",
            "quantity": 1,
            "retail_price_withdisc_rub": "200",
            "ppvz_for_pay": "150",
            "delivery_rub": "10",
            "acquiring_fee": "0",
            "sa_name": "1001",
            "sale_dt": "2026-11-10T00:00:00Z",
        }

        async def request(method, url, params=None, json_data=None):
            if "/sales-reports/detailed" in url or "/sales-reports/list" in url:
                raise APIStatusError(404, "method moved")
            if url.endswith("/api/v5/supplier/reportDetailByPeriod"):
                return [legacy_row] if params["rrdid"] == 0 else []
            if url.endswith("/adv/v1/upd"):
                return []
            raise AssertionError(url)

        self.wb._request = AsyncMock(side_effect=request)
        report = await self.wb.fetch_real_finance_breakdown(
            custom_date_from="2026-11-10", custom_date_to="2026-11-16"
        )
        self.assertEqual(report["revenue_seller"], 200)
        self.assertEqual(report["for_pay"], 150)
        self.assertIn("legacy", report["source"])
        self.assertIn("404", report["source"])

    async def test_dead_legacy_fbo_endpoint_is_not_hammered(self):
        """После 404 legacy-остатков WB повторный запрос не уходит в сеть 6 часов."""
        self.wb._request = AsyncMock()
        self.wb._fbo_legacy_retry_at = time.monotonic() + 60
        with self.assertRaises(APIStatusError):
            await self.wb._fetch_fbo("supplier")
        self.wb._request.assert_not_awaited()

    async def test_campaign_list_survives_missing_fields(self):
        async def request(method, url, params=None, json_data=None):
            if url.endswith("/promotion/count"):
                return {
                    "adverts": [
                        {"status": 9, "advert_list": [{"advertId": 5}, {}]},
                        {"status": 8, "advert_list": [{"advertId": 6}]},
                        "broken",
                    ]
                }
            if url.endswith("/promotion/adverts"):
                return [{"advertId": 5, "params": [{"nms": [100]}]}, "bad", {"id": None}]
            return [{"advertId": 5, "updSum": "7"}, {"updSum": "3"}]

        self.wb._request = AsyncMock(side_effect=request)
        campaigns = await self.wb.fetch_active_ad_campaigns(days=7)
        self.assertEqual([c["id"] for c in campaigns], [5])
        self.assertEqual(campaigns[0]["spend_rub"], 7.0)


class ReportDegradation(unittest.IsolatedAsyncioTestCase):
    async def test_missing_playwright_returns_message_not_import_error(self):
        import services.ozon.browser_reviews as reviews

        with patch.object(reviews, "PLAYWRIGHT_AVAILABLE", False):
            for func in (
                reviews.auto_reply_unanswered_reviews,
                reviews.auto_reply_unanswered_questions,
            ):
                result = await func(headless=True, dry_run=True, max_per_run=1)
                self.assertEqual(result["status"], "error")
                self.assertIn("Playwright", result["message"])

    async def test_ai_board_returns_none_when_html_cannot_be_written(self):
        import services.common.ai_advisory_board as advisory

        broken_logs = Path(self.enterContext(tempfile.TemporaryDirectory())) / "not-a-dir"
        broken_logs.write_text("file, not directory", encoding="utf-8")
        with patch.object(advisory, "LOGS_DIR", broken_logs), \
             patch.object(advisory, "sync_and_generate_orders_report", AsyncMock(return_value="P&L")), \
             patch.object(advisory, "generate_weekly_top_and_unprofitable_report", AsyncMock(return_value="TOP5")), \
             patch.object(advisory, "get_all_products", AsyncMock(return_value=[])), \
             patch.object(advisory, "_call_gemini_advisory", AsyncMock(return_value="Итог")):
            oz = MagicMock()
            oz.fetch_stocks_and_prices = AsyncMock(return_value=[])
            wb = MagicMock()
            wb.fetch_stocks_and_prices = AsyncMock(return_value=[])
            summary, path = await advisory.generate_monday_ai_board_report(oz, wb)
        self.assertIn("Итог", summary)
        self.assertIsNone(path)


class SupervisorResilience(unittest.TestCase):
    def test_failed_restart_backs_off_instead_of_crashing(self):
        import supervisor
        import time as time_module

        watchdog = supervisor.WorkerSupervisor()
        watchdog.process = None
        with patch.object(watchdog, "start_worker", side_effect=RuntimeError("port busy")) as start, \
             patch.object(supervisor.time, "sleep"):
            watchdog.recover_worker()
            self.assertEqual(start.call_count, 1)
            self.assertGreater(watchdog._next_restart_at, time_module.monotonic())
            # Повторный вызов в окне ожидания не должен снова пытаться стартовать.
            watchdog.recover_worker()
            self.assertEqual(start.call_count, 1)

    def test_stdio_bootstrap_is_safe_when_streams_exist(self):
        import runtime_bootstrap

        path = runtime_bootstrap.redirect_stdio("bootstrap-test.log")
        self.assertIsNotNone(path)
        self.assertTrue(path.parent.exists())


class StatusMessages(unittest.IsolatedAsyncioTestCase):
    async def test_edit_of_deleted_message_falls_back_to_new_message(self):
        """«message to edit not found» больше не скрывает исходную ошибку."""
        import worker

        status = MagicMock()
        status.edit_text = AsyncMock(
            side_effect=RuntimeError("Bad Request: message to edit not found")
        )
        status.answer = AsyncMock()
        await worker._show_status_result(status, "<b>Ошибка P&L</b>")
        status.answer.assert_awaited_once()
        self.assertIn("Ошибка P&L", status.answer.await_args.args[0])

    async def test_delete_of_missing_status_does_not_raise(self):
        import worker

        status = MagicMock()
        status.delete = AsyncMock(side_effect=RuntimeError("message to delete not found"))
        await worker._safe_delete_message(status)
        status.delete.assert_awaited_once()

    async def test_unchanged_status_is_not_duplicated(self):
        import worker

        status = MagicMock()
        status.edit_text = AsyncMock(
            side_effect=RuntimeError("Bad Request: message is not modified")
        )
        status.answer = AsyncMock()
        await worker._show_status_result(status, "тот же текст")
        status.answer.assert_not_awaited()


class SchedulerBudget(unittest.IsolatedAsyncioTestCase):
    async def test_browser_job_stops_after_budget(self):
        """Цикл автоответов не занимает 30 минут и не блокирует следующий запуск."""
        import worker

        async def slow(*args, **kwargs):
            await asyncio.sleep(5)

        notify = AsyncMock()
        with patch.object(worker, "auto_reply_unanswered_reviews", slow), \
             patch.object(worker, "auto_reply_unanswered_questions", AsyncMock(
                 return_value={"status": "success"}
             )), \
             patch.object(worker, "notify_admin", notify), \
             patch.object(worker, "BROWSER_JOB_BUDGET_SEC", 0.05):
            await worker.scheduled_reviews_and_questions_job()

        messages = [str(call.args[0]) for call in notify.await_args_list if call.args]
        self.assertTrue(any("остановлен" in text for text in messages), messages)


class CallbackPersistence(unittest.IsolatedAsyncioTestCase):
    async def test_action_reply_survives_database_failure(self):
        from bot.handlers import callbacks

        action = SimpleNamespace(
            id=1,
            action_type="update_price",
            marketplace="ozon",
            sku="1001",
            payload={"new_price": 299, "offer_id": "1001", "product_id": "123"},
        )
        call = MagicMock()
        call.from_user = SimpleNamespace(id=settings.TG_ADMIN_ID)
        call.data = "act:app:1"
        call.answer = AsyncMock()
        call.message.reply = AsyncMock()

        with patch.object(callbacks, "claim_price_action", AsyncMock(return_value=action)), \
             patch.object(callbacks, "finish_price_action", AsyncMock(side_effect=RuntimeError("db locked"))), \
             patch.object(callbacks.ozon_api, "update_price", AsyncMock(return_value={
                 "result": [{"offer_id": "1001", "product_id": 123, "updated": True}]
             })):
            await callbacks.cb_approve_action(call)

        call.message.reply.assert_awaited_once()
        text = call.message.reply.await_args.args[0]
        self.assertIn("299", text)
        self.assertIn("не сохранён", text)


if __name__ == "__main__":
    unittest.main()
