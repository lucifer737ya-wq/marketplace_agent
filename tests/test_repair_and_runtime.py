"""Runtime tests use a temporary source tree and synthetic diagnostics only."""

import asyncio
import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

from tests import offline
from config import BASE_DIR, settings
from maintenance import ai_inspector as inspector
from maintenance import supervisor as repair
from services.common.gemini import _extract_model_name
from services.common.routes import proxy_routes, remember_route


class InspectorAndRepair(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.state = self.root / "state"
        self.state.mkdir()
        self.path = self.root / "services" / "example.py"
        self.path.parent.mkdir()
        self.original = "def calculate(price):\n    return price + typo\n"
        self.path.write_text(self.original)
        self.snapshots = self.root / "snapshots"
        for attr, val in [("BASE_DIR", self.root), ("BACKUPS_DIR", self.snapshots)]:
            self.enterContext(patch.object(repair, attr, val))

    def test_static_and_incremental_log_checks_without_module_execution(self):
        (self.root / "worker.py").write_text(
            "from services.example import missing\nimport nonexistent_dependency\n"
            "import services.example as svc\nsvc.missing()\n"
            'raise RuntimeError("must never be imported")\n'
        )
        (self.root / "supervisor.py").write_text("def broken(:\n")
        file = self.state / "runtime.log"
        file.write_text(
            f'Traceback (most recent call last):\n  File "{self.path}", line 2, in calculate\n'
            "AttributeError: 'Product' object has no attribute 'field' (private payload)\n"
            "WB HTTP 429\nWB HTTP 429\n"
        )
        stale = self.state / "old.log"
        stale.write_text("Ozon HTTP 503\nOzon HTTP 503\n")
        os.utime(stale, (time.time() - 2 * inspector.DAY,) * 2)
        issues = inspector.inspect_project(self.root, self.state)
        self.assertEqual(
            {i.kind for i in issues},
            {"SyntaxError", "NameError", "ImportError", "AttributeError", "API"},
        )
        attrs = next(
            i for i in issues if i.kind == "AttributeError" and i.file == self.path
        )
        self.assertTrue(any("нет атрибута missing" in i.detail for i in issues))
        self.assertEqual((attrs.file, attrs.line), (self.path, 2))
        self.assertNotIn("private payload", attrs.detail)
        self.assertEqual(
            next(i for i in issues if i.kind == "API").detail,
            "Повторяющийся сбой: WB HTTP 429",
        )
        self.assertTrue(any("нет missing" in i.detail for i in issues))
        self.assertEqual(
            inspector.recent_log_issues(self.root, self.state, time.time()), []
        )

    async def test_patch_rejects_truncation_syntax_and_outside_targets(self):
        link = self.root / "services/link.py"
        link.symlink_to(self.path)
        with patch.object(repair, "generate", AsyncMock()) as llm:
            for path in [link, self.root / "private", "../worker.py", "state/test.py"]:
                self.assertFalse((await repair.repair_file(path, "test"))[0])
            llm.assert_not_awaited()
        for code in [
            "def calculate(:\n",
            "unrelated = 2\n" * 5,
            "def calculate(price):\n    pass",
        ]:
            with patch.object(
                repair, "generate", AsyncMock(return_value=(code, "model"))
            ):
                self.assertFalse((await repair.repair_file(self.path, "NameError"))[0])
            self.assertEqual(self.path.read_text(), self.original)

    async def test_patch_full_file_backup_and_post_install_rollback(self):
        fixed = "def calculate(price):\n    return round(price, 2)\n"
        verify = AsyncMock(side_effect=RuntimeError("no heartbeat"))
        restart = AsyncMock()
        with patch.object(
            repair, "generate", AsyncMock(return_value=(fixed, "model"))
        ) as llm:
            ok, detail = await repair.repair_file(
                self.path, "NameError: typo", verify=verify, after_rollback=restart
            )
            self.assertFalse(ok)
            self.assertIn("откат", detail)
            self.assertEqual(self.path.read_text(), self.original)
            self.assertEqual(
                next(self.snapshots.glob("example.py_*.bak")).read_text(), self.original
            )
            self.assertEqual(llm.await_args.kwargs["max_tokens"], 16384)
            self.assertIn(self.original, llm.await_args.args[0])
            self.assertIn("NameError: typo", llm.await_args.args[0])
            restart.assert_awaited_once()
            self.assertTrue((await repair.repair_file(self.path, "NameError: typo"))[0])
        self.assertEqual(self.path.read_text(), fixed)

    async def test_patch_rechecks_source_after_generation(self):
        async def edit(*args, **kwargs):
            self.path.write_text("def calculate(price):\n    return float(price)\n")
            return "def calculate(price):\n    return round(price, 2)\n", "model"

        with patch.object(repair, "generate", edit):
            ok, detail = await repair.repair_file(self.path, "NameError")
        self.assertFalse(ok)
        self.assertIn("изменился", detail)
        self.assertIn("float(price)", self.path.read_text())


class Runtime(unittest.IsolatedAsyncioTestCase):
    def test_project_sources_and_imports(self):
        issues = inspector.inspect_project(BASE_DIR, offline.STATE)
        self.assertEqual(issues, [])

    def test_long_telegram_lines_keep_entities_and_balanced_tags(self):
        from xml.etree.ElementTree import fromstring
        from worker import _split_html_chunks

        text = "<b>Заголовок\n<i>" + "РК 🟢 &amp; WB" * 400 + "</i></b>"
        chunks = _split_html_chunks(text)
        self.assertGreater(len(chunks), 1)
        visible = [
            "".join(fromstring("<root>" + c + "</root>").itertext()) for c in chunks
        ]
        self.assertTrue(all(len(s.encode("utf-16-le")) // 2 <= 3900 for s in visible))
        self.assertEqual("".join(visible), "".join(fromstring(text).itertext()))

    def test_routes(self):
        alternate = "http://127.0.0.1:3067"
        with patch.object(settings, "LOCAL_PROXY_FALLBACK_URL", alternate):
            remember_route("fixture", alternate)
            self.assertEqual(proxy_routes("fixture")[0], alternate)
        self.assertEqual(proxy_routes("fixture"), [settings.LOCAL_PROXY_URL])
        with patch.object(settings, "LOCAL_PROXY_URL", ""):
            with self.assertRaises(ValueError):
                proxy_routes("gemini")
        self.assertEqual(_extract_model_name("models/gemini-test"), "gemini-test")

    async def test_startup_interval_router_lock_and_publish_mode(self):
        import worker
        from bot.handlers.callbacks import callbacks_router

        self.assertIn(callbacks_router, worker.dp.sub_routers)
        self.assertIn("callback_query", worker.dp.resolve_used_update_types())
        job = worker.configure_scheduler().get_job("ozon_rev_q_30m")
        self.assertEqual(job.trigger.interval.total_seconds(), 1800)
        self.assertLess(abs(job.next_run_time.timestamp() - time.time()), 2)
        with (
            patch.object(
                worker,
                "auto_reply_unanswered_reviews",
                AsyncMock(return_value={"status": "success"}),
            ) as r,
            patch.object(
                worker,
                "auto_reply_unanswered_questions",
                AsyncMock(return_value={"status": "success"}),
            ) as q,
        ):
            await worker.scheduled_reviews_and_questions_job()
            for handler in (r, q):
                self.assertFalse(handler.await_args.kwargs["dry_run"])
            async with worker._BROWSER_JOB_LOCK:
                await worker.scheduled_reviews_and_questions_job()
            self.assertEqual(r.await_count, 1)
        with patch.object(settings, "WORKER_LOCK_PORT", 0):
            self.assertTrue(worker.acquire_worker_single_instance_lock())
            try:
                with patch.object(
                    settings,
                    "WORKER_LOCK_PORT",
                    worker._WORKER_LOCK_SOCKET.getsockname()[1],
                ):
                    self.assertFalse(worker.acquire_worker_single_instance_lock())
            finally:
                worker.release_worker_single_instance_lock()

    async def test_aiogram_getupdates_conflict_stops_actual_polling(self):
        from aiogram import Bot
        from aiogram.methods import DeleteWebhook, GetMe, GetUpdates
        from aiogram.types import User
        from aiogram.exceptions import TelegramConflictError
        import worker

        fake = Bot(token=settings.TG_BOT_TOKEN)
        self.addAsyncCleanup(fake.session.close)
        ready = asyncio.Event()
        calls = []

        async def telegram(bot, method, timeout=None):
            if isinstance(method, GetMe):
                return User(
                    id=123456, is_bot=True, first_name="Agent", username="fixture_bot"
                )
            if isinstance(method, DeleteWebhook):
                return True
            self.assertIsInstance(method, GetUpdates)
            calls.append(method)
            if len(calls) == 1:
                self.assertFalse(ready.is_set())
                return []
            self.assertTrue(ready.is_set())
            raise TelegramConflictError(method, "fixture: conflict")

        with (
            patch.object(worker, "bot", fake),
            patch.object(fake.session, "make_request", telegram),
            patch.object(worker, "notify_admin", AsyncMock()) as notify,
        ):
            async with asyncio.timeout(8):
                with self.assertRaises(SystemExit) as stopped:
                    await worker._run_polling_with_conflict_guard(ready)
            self.assertEqual(stopped.exception.code, 75)
            self.assertEqual(len(calls), 2)
            notify.assert_awaited_once()

    def test_watchdog_does_not_respawn_409(self):
        import supervisor

        watchdog = supervisor.WorkerSupervisor()
        watchdog.process = Mock()
        watchdog.process.poll.return_value = 75
        with (
            patch.object(watchdog, "acquire_single_instance_lock", return_value=True),
            patch.object(watchdog, "shutdown") as shutdown,
            patch.object(watchdog, "start_worker") as start,
            patch.object(supervisor.atexit, "register"),
            patch.object(supervisor.signal, "signal"),
            patch.object(supervisor.time, "sleep"),
            patch.object(supervisor, "notify", AsyncMock()) as notify,
        ):
            watchdog.run_forever()
            start.assert_called_once()
            shutdown.assert_called_once()
            notify.assert_awaited_once()

    async def test_verifier_requires_fresh_running_heartbeat_from_new_pid(self):
        import supervisor

        monitor = supervisor.WorkerSupervisor()
        hb = offline.STATE / "verify.json"

        def child_heartbeat(status, pid, age):
            monitor.process = Mock(pid=321)
            monitor.process.poll.return_value = None
            hb.write_text(
                json.dumps(
                    {"pid": pid, "status": status, "timestamp": time.time() - age}
                )
            )

        with (
            patch.object(settings, "HEARTBEAT_FILE", hb),
            patch.object(supervisor, "POST_PATCH_VERIFY_SEC", 0.01),
            patch.object(supervisor.asyncio, "sleep", AsyncMock()),
        ):
            for args in [
                ("starting", 321, 0),
                ("running", 322, 0),
                ("running", 321, 1),
                ("running", 321, 0),
            ]:
                with patch.object(
                    monitor, "start_worker", lambda: child_heartbeat(*args)
                ):
                    if args == ("running", 321, 0):
                        await monitor.verify_patch()
                    else:
                        with self.assertRaisesRegex(RuntimeError, "готовность"):
                            await monitor.verify_patch()
