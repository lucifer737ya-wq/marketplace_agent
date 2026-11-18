"""Контрактные и регрессионные тесты этапа 4 без внешних сетевых запросов."""
import ast
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from aiohttp import web

from tests import offline

offline.require_isolation()  # before importing application settings

from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.fsm.context import FSMContext

import bot.keyboards as keyboards_mod
import bot.handlers.costs_fsm as costs_fsm_mod
import bot.handlers.logistics_fsm as logistics_fsm_mod
import bot.handlers.patch_fsm as patch_fsm_mod
import bot.handlers.questions_fsm as questions_fsm_mod
import bot.handlers.reviews_fsm as reviews_fsm_mod
import maintenance.api_inspector as api_inspector_mod
import services.common.gemini as gemini
import maintenance.supervisor as supervisor_mod
import services.common.ai_advisory_board as advisory_mod
import services.common.buyer_questions as buyer_questions_mod
import services.ozon.browser_reviews as browser_reviews_mod
from config import BASE_DIR
from database.models import close_db, init_db


def _make_message(user_id: int = 123, text: str = "", document=None) -> MagicMock:
    msg = MagicMock()
    msg.from_user = SimpleNamespace(id=user_id) if user_id is not None else None
    msg.chat = SimpleNamespace(id=user_id or 123)
    msg.text = text
    msg.document = document
    msg.answer = AsyncMock()
    wait_msg = MagicMock()
    wait_msg.delete = AsyncMock()
    wait_msg.edit_text = AsyncMock()
    msg.answer.return_value = wait_msg
    return msg


def _make_fsm_context() -> FSMContext:
    storage = MemoryStorage()
    return FSMContext(storage=storage, key=StorageKey(bot_id=1, chat_id=123, user_id=123))


class TestStage4BrowserReviews(unittest.IsolatedAsyncioTestCase):
    def test_is_date_from_nov_13_2026_formats(self) -> None:
        self.assertTrue(browser_reviews_mod._is_date_from_nov_13_2026("13.11.2026 09:00"))
        self.assertTrue(browser_reviews_mod._is_date_from_nov_13_2026("18.11.2026"))
        self.assertFalse(browser_reviews_mod._is_date_from_nov_13_2026("12.11.2026 23:59"))
        self.assertTrue(browser_reviews_mod._is_date_from_nov_13_2026("2026-11-18T10:15:00"))
        self.assertFalse(browser_reviews_mod._is_date_from_nov_13_2026("2026-11-12"))
        self.assertTrue(browser_reviews_mod._is_date_from_nov_13_2026("14.11.26"))
        self.assertFalse(browser_reviews_mod._is_date_from_nov_13_2026("10.11.26"))
        self.assertTrue(browser_reviews_mod._is_date_from_nov_13_2026("15 ноября 2026"))
        self.assertFalse(browser_reviews_mod._is_date_from_nov_13_2026("12 нояб 2026"))
        self.assertFalse(browser_reviews_mod._is_date_from_nov_13_2026("", require_date=True))
        self.assertFalse(browser_reviews_mod._is_date_from_nov_13_2026("неизвестно", require_date=True))
        self.assertTrue(browser_reviews_mod._is_date_from_nov_13_2026("", require_date=False))

    async def test_close_browser_context_safe_handles_hang_and_error(self) -> None:
        ctx_ok = MagicMock()
        ctx_ok.close = AsyncMock()
        await browser_reviews_mod._close_browser_context_safe(ctx_ok, timeout=1.0)
        ctx_ok.close.assert_awaited_once()

        ctx_err = MagicMock()
        ctx_err.close = AsyncMock(side_effect=RuntimeError("boom"))
        await browser_reviews_mod._close_browser_context_safe(ctx_err, timeout=1.0)

        await browser_reviews_mod._close_browser_context_safe(None, timeout=1.0)

    async def test_auto_reply_reviews_enforces_date_filter_and_closes_context(self) -> None:
        old_row = MagicMock()
        old_row.inner_text = AsyncMock(return_value="12.11.2026\nТовар хороший\n5")
        old_row.click = AsyncMock()
        old_row.count = AsyncMock(return_value=1)
        old_row.get_attribute = AsyncMock(return_value="old")
        old_row.evaluate = AsyncMock(return_value="12.11.2026")

        page = MagicMock()
        page.url = "https://seller.ozon.ru/app/reviews"
        page.goto = AsyncMock()
        page.get_by_role.return_value.first.is_visible = AsyncMock(return_value=False)
        page.wait_for_timeout = AsyncMock()
        page.locator = MagicMock()

        unanswered_tab = MagicMock()
        unanswered_tab.count = AsyncMock(return_value=0)
        rows_loc = MagicMock()
        rows_loc.count = AsyncMock(return_value=1)
        rows_loc.all_inner_texts = AsyncMock(return_value=["old"])
        rows_loc.nth = MagicMock(return_value=old_row)

        def locator_side_effect(selector: str):
            if "Ожидают" in selector or "Неотвечен" in selector:
                return unanswered_tab
            return rows_loc

        page.locator.side_effect = locator_side_effect

        context = MagicMock()
        context.pages = [page]
        context.new_page = AsyncMock(return_value=page)
        context.close = AsyncMock()

        chromium = MagicMock()
        chromium.launch_persistent_context = AsyncMock(return_value=context)
        pw_instance = MagicMock()
        pw_instance.chromium = chromium
        pw_cm = MagicMock()
        pw_cm.__aenter__ = AsyncMock(return_value=pw_instance)
        pw_cm.__aexit__ = AsyncMock(return_value=False)

        with patch("services.ozon.browser_reviews.BROWSER_DATA_DIR") as mock_dir, \
             patch.object(browser_reviews_mod, "_review_tab", AsyncMock(return_value=True)), \
             patch.object(browser_reviews_mod, "_scroll_main_table_down", AsyncMock(return_value=False)), \
             patch("playwright.async_api.async_playwright", return_value=pw_cm):
            mock_dir.exists.return_value = True
            mock_dir.mkdir = MagicMock()
            mock_dir.iterdir = MagicMock(return_value=["Singleton"])
            stats = await browser_reviews_mod.auto_reply_unanswered_reviews(dry_run=True, max_per_run=5)

        self.assertEqual(stats["status"], "success")
        self.assertEqual(stats["replied"], 0)
        self.assertEqual(stats["reviews"], [])
        old_row.click.assert_not_awaited()
        context.close.assert_awaited_once()


class TestStage4GeminiAndAdvisory(unittest.IsolatedAsyncioTestCase):
    def test_model_filtering_excludes_non_text_and_invalid_fallbacks(self) -> None:
        # fолбэк обязан начинаться с актуального поколения моделей Google
        self.assertIn("gemini-3.8-flash", gemini.FALLBACK_MODELS)
        self.assertNotIn("gemini-2.5-pro", gemini.FAST_MODELS)
        self.assertTrue(gemini._is_supported_text_model("gemini-3.8-flash"))
        self.assertTrue(gemini._is_supported_text_model("gemini-2.5-flash"))
        self.assertFalse(gemini._is_supported_text_model("gemini-2.5-flash-tts"))
        self.assertFalse(gemini._is_supported_text_model("imagen-3.0-generate-002"))
        self.assertFalse(gemini._is_supported_text_model("text-embedding-004"))
        self.assertFalse(gemini._is_supported_text_model(""))

    async def test_health_check_and_fast_gemini_against_local_mock_server(self) -> None:
        captured_payloads = []

        async def handle_models(request: web.Request) -> web.Response:
            return web.json_response(
                {
                    "models": [
                        {"name": "models/gemini-3.8-flash-tts", "supportedGenerationMethods": ["generateContent"]},
                        {"name": "models/gemini-3.8-flash", "supportedGenerationMethods": ["generateContent"]},
                    ]
                }
            )

        async def handle_generate(request: web.Request) -> web.Response:
            payload = await request.json()
            captured_payloads.append(payload)
            return web.json_response(
                {
                    "candidates": [
                        {
                            "finishReason": "STOP",
                            "content": {
                                "parts": [
                                    {"text": "internal thought", "thought": True},
                                    {"text": "OK", "thought": False},
                                ]
                            },
                        }
                    ]
                }
            )

        app = web.Application()
        app.router.add_get("/v1beta/models", handle_models)
        app.router.add_post("/v1beta/models/{model}:generateContent", handle_generate)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        port = site._server.sockets[0].getsockname()[1]

        # Прямой маршрут по умолчанию идёт на локальный фикстур-сервер,
        # локальный прокси остаётся резервом. Ни один запрос не уходит в Google.
        with patch.object(gemini, 'BASE_URL', f'http://127.0.0.1:{port}/v1beta/models'), \
             patch.object(gemini.settings, 'LOCAL_PROXY_URL', ''), \
             patch.object(gemini.settings, 'LOCAL_PROXY_FALLBACK_URL', ''), \
             patch.object(gemini.settings, 'LOCAL_PROXY_LIST', ''):
            try:
                self.assertTrue((await advisory_mod.check_gemini_api_health())['ok'])
                self.assertEqual(await buyer_questions_mod.call_fast_gemini('вопрос', True), 'OK')
                self.assertEqual(len(captured_payloads), 2)
            finally:
                await runner.cleanup()
        with patch.object(gemini, 'BASE_URL', 'http://127.0.0.1:1/v1beta/models'), \
             patch.object(gemini.settings, 'ALLOW_DIRECT_CONNECTIONS', False), \
             patch.object(gemini.settings, 'LOCAL_PROXY_URL', ''), \
             patch.object(gemini.settings, 'LOCAL_PROXY_FALLBACK_URL', ''), \
             patch.object(gemini.settings, 'LOCAL_PROXY_LIST', ''):
            with self.assertRaises(RuntimeError):
                await buyer_questions_mod.call_fast_gemini('test', raise_on_error=True)
            self.assertIn('товар скоро поступит', (await buyer_questions_mod.call_fast_gemini('test')).lower())
        with patch.object(buyer_questions_mod, 'call_fast_gemini', AsyncMock(side_effect=RuntimeError('down'))) as call, \
             patch.object(buyer_questions_mod.asyncio, 'sleep', AsyncMock()):
            with self.assertRaises(RuntimeError):
                await buyer_questions_mod.generate_review_reply('Супер', 5, sku='1001')
            self.assertEqual(call.await_count, 3)

    async def test_generate_monday_ai_board_report_escapes_html_and_no_fake_cogs(self) -> None:
        await init_db()
        try:
            oz_mock = AsyncMock()
            oz_mock.fetch_stocks_and_prices = AsyncMock(
                return_value=[
                    {"offer_id": "1001", "price": 299.0, "stock_fbo": 10, "stock_fbs": 5},
                ]
            )
            wb_mock = AsyncMock()
            wb_mock.fetch_stocks_and_prices = AsyncMock(return_value=[])

            with patch("services.common.orders_report.sync_and_generate_orders_report", AsyncMock(return_value="P&L")), \
                 patch("services.common.price_auditor.run_daily_price_audit", AsyncMock(side_effect=RuntimeError("audit err"))), \
                 patch("services.common.ai_advisory_board._call_gemini_advisory", AsyncMock(return_value="Вывод <script>alert(1)</script> & итог")):
                tg_summary, html_path = await advisory_mod.generate_monday_ai_board_report(oz_mock, wb_mock)

            self.assertNotIn("<i>", tg_summary)
            self.assertNotIn("</i>", tg_summary)
            html_content = html_path.read_text(encoding="utf-8")
            self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt; &amp; итог", html_content)
            self.assertNotIn("<script>alert(1)</script>", html_content)
        finally:
            await close_db()


class TestStage4MaintenanceModules(unittest.IsolatedAsyncioTestCase):
    def test_supervisor_path_safety_and_code_fence_extraction(self) -> None:
        resolved = supervisor_mod._resolve_safe_project_path(r"bot\keyboards.py")
        self.assertEqual(resolved, (BASE_DIR / "bot" / "keyboards.py").resolve())
        self.assertIsNone(supervisor_mod._resolve_safe_project_path("../../etc/passwd"))
        self.assertIsNone(supervisor_mod._resolve_safe_project_path("/etc/passwd"))

        prose_and_code = (
            "Here is how to import the module and fix def foo():\n"
            "```python\n"
            "def fixed() -> int:\n"
            "    return 42\n"
            "```\n"
            "Hope this helps!"
        )
        extracted = supervisor_mod._extract_python_code(prose_and_code)
        self.assertEqual(extracted, "def fixed() -> int:\n    return 42")
        ast.parse(extracted)

    def test_self_healer_no_corrupted_markdown_links_and_clean_verify(self) -> None:
        source_text = (BASE_DIR / "maintenance" / "self_healer.py").read_text(encoding="utf-8")
        self.assertNotIn("](http", source_text)

        target = BASE_DIR / 'bot' / 'keyboards.py'
        supervisor_mod._compile(target, 'def valid_func():\n    return 1\n')
        with self.assertRaises(SyntaxError):
            supervisor_mod._compile(target, 'def broken(:\n')

    async def test_api_inspector_reports_fast_gemini_failure_and_escapes_html(self) -> None:
        with patch("maintenance.api_inspector.OzonSellerAPI") as oz_cls, \
             patch("maintenance.api_inspector.WBStatisticsAPI") as wb_cls, \
             patch("maintenance.api_inspector.check_gemini_api_health", AsyncMock(return_value={"ok": False, "detail": "<bad&detail>"})), \
             patch("maintenance.api_inspector.call_fast_gemini", AsyncMock(side_effect=RuntimeError("<boom&err>"))):
            oz_cls.return_value.fetch_stocks_and_prices = AsyncMock(side_effect=RuntimeError("<oz&err>"))
            wb_cls.return_value.fetch_stocks_and_prices = AsyncMock(return_value=[])
            wb_cls.return_value.fetch_wb_tariffs = AsyncMock(return_value={"subjects": {}, "warehouses": {}})

            report = await api_inspector_mod.run_monthly_api_audit()

        self.assertIn("🔴 <b>СБОЙ:</b> <code>&lt;oz&amp;err&gt;</code>", report)
        self.assertIn("🔴 <b>СБОЙ:</b> <code>&lt;bad&amp;detail&gt;</code>", report)
        self.assertIn("🔴 <b>СБОЙ:</b> <code>&lt;boom&amp;err&gt;</code>", report)


class TestStage4BotKeyboardsAndHandlers(unittest.IsolatedAsyncioTestCase):
    def test_main_menu_keyboard_contains_bulk_costs(self) -> None:
        kb = keyboards_mod.get_main_menu_keyboard()
        labels = [btn.text for row in kb.keyboard for btn in row]
        self.assertIn("📑 Массовая загрузка себестоимости", labels)
        self.assertIn("🛠 Редактировать код", labels)

    async def test_fsm_handlers_block_unauthorized_users(self) -> None:
        unauth_msg = _make_message(user_id=999, text="ozon")
        state = _make_fsm_context()

        await costs_fsm_mod.cmd_bulk_costs_start(unauth_msg, state)
        await costs_fsm_mod.cmd_costs_start(unauth_msg, state)
        await logistics_fsm_mod.cmd_first_mile_start(unauth_msg, state)
        await patch_fsm_mod.cmd_patch_start(unauth_msg, state)
        await patch_fsm_mod.process_patch_instruction(unauth_msg, state)
        await questions_fsm_mod.cmd_question_start(unauth_msg, state)
        await questions_fsm_mod.process_question_text(unauth_msg, state)
        await reviews_fsm_mod.cmd_review_start(unauth_msg, state)
        await reviews_fsm_mod.process_review_text(unauth_msg, state)

        unauth_msg.answer.assert_not_awaited()

    async def test_costs_fsm_validation_preserves_sku_and_escapes_html(self) -> None:
        state = _make_fsm_context()
        await state.set_state(costs_fsm_mod.BulkCostsForm.waiting_for_data)

        msg = _make_message(user_id=123, text="ozon;SKU,1;100,50;10,25")
        with patch("bot.handlers.costs_fsm.bulk_upsert_costs", AsyncMock(return_value=1)) as bulk_mock:
            await costs_fsm_mod.process_bulk_costs_text(msg, state)
            bulk_mock.assert_awaited_once_with(
                [{"marketplace": "ozon", "sku": "SKU,1", "cost_price": 100.5, "packaging_cost": 10.25}]
            )
        self.assertIsNone(await state.get_state())

        # Document with file_name=None shouldn't crash
        bad_doc_msg = _make_message(user_id=123, document=SimpleNamespace(file_name=None, file_size=10))
        await costs_fsm_mod.process_bulk_costs_document(bad_doc_msg, state, MagicMock())
        bad_doc_msg.answer.assert_awaited_once()
        self.assertEqual(bad_doc_msg.answer.call_args.kwargs.get("parse_mode"), "HTML")

        # NaN / negative rejection in single costs FSM
        await state.set_state(costs_fsm_mod.CostsForm.cost_price)
        nan_msg = _make_message(user_id=123, text="nan")
        await costs_fsm_mod.process_costs_price(nan_msg, state)
        self.assertEqual(await state.get_state(), costs_fsm_mod.CostsForm.cost_price.state)

    async def test_logistics_fsm_cancel_and_validation(self) -> None:
        state = _make_fsm_context()
        await state.set_state(logistics_fsm_mod.FirstMileForm.transport_cost)

        nan_msg = _make_message(user_id=123, text="inf")
        await logistics_fsm_mod.process_supply_cost(nan_msg, state)
        self.assertEqual(await state.get_state(), logistics_fsm_mod.FirstMileForm.transport_cost.state)

        cancel_msg = _make_message(user_id=123, text="/cancel")
        await logistics_fsm_mod.cmd_cancel_first_mile(cancel_msg, state)
        self.assertIsNone(await state.get_state())

    async def test_questions_and_reviews_escape_html_and_fallback_cleanly(self) -> None:
        state = _make_fsm_context()
        q_msg = _make_message(user_id=123, text="1001 Есть ли в наличии?")
        with patch("bot.handlers.questions_fsm.generate_buyer_question_answer", AsyncMock(return_value="Ответ <ok> & тест")):
            await questions_fsm_mod.process_question_text(q_msg, state)
        sent_q = q_msg.answer.call_args_list[-1].args[0]
        self.assertIn("Ответ &lt;ok&gt; &amp; тест", sent_q)

        # Fallback without shade/color must not emit '# ()'
        meta = reviews_fsm_mod.get_product_context("UNKNOWN-999")
        fb = reviews_fsm_mod._build_review_fallback_reply(meta)
        self.assertNotIn("# ()", fb)

        # process_review_text escapes HTML and clears state
        await state.set_state(reviews_fsm_mod.ReviewForm.waiting_for_review_data)
        r_msg = _make_message(user_id=123, text="1001 Отзыв <script>")
        await init_db()
        try:
            with patch("bot.handlers.reviews_fsm.generate_review_reply_llm", return_value="Спасибо <client> & удачи!"):
                await reviews_fsm_mod.process_review_text(r_msg, state)
        finally:
            await close_db()
        sent_r = r_msg.answer.call_args_list[-1].args[0]
        self.assertIn("Спасибо &lt;client&gt; &amp; удачи!", sent_r)
        self.assertIsNone(await state.get_state())


if __name__ == "__main__":
    unittest.main()
