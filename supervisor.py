import runtime_bootstrap

runtime_bootstrap.install_excepthook("supervisor.log")

import asyncio
import atexit
from collections import deque
import html
import json
import logging
import os
import signal
import socket
import subprocess
import sys
import time
from datetime import datetime
from typing import Optional

from aiogram import Bot
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.exceptions import TelegramAPIError

from config import BASE_DIR, LOGS_DIR, prepare_runtime_dirs, settings
from maintenance.self_healer import run_full_self_check_and_heal
from maintenance.supervisor import apply_code_patch
from services.common.routes import (
    proxy_for_route,
    remember_route,
    telegram_routes,
)

def _ensure_stdio_streams() -> None:
    """Перенаправляет stdout/stderr в лог-файл при скрытом запуске через pythonw.exe."""
    runtime_bootstrap.redirect_stdio("supervisor.log")


HEARTBEAT_TIMEOUT_SEC = 150
POST_PATCH_VERIFY_SEC = 120
TELEGRAM_POLLING_COOLDOWN_SEC = 2.5


async def notify(text: str) -> None:
    """Уведомление администратору; сбой Telegram не должен ронять супервизор."""
    try:
        routes = telegram_routes("telegram")
    except ValueError as exc:
        print(f"[Supervisor] Telegram: {exc}", flush=True)
        return
    for route in routes:
        proxy = proxy_for_route(route)
        try:
            async with Bot(
                token=settings.TG_BOT_TOKEN,
                session=AiohttpSession(proxy=proxy, timeout=15),
            ).context() as bot:
                await bot.send_message(
                    settings.TG_ADMIN_ID, text, parse_mode="HTML"
                )
        except (TelegramAPIError, asyncio.TimeoutError, OSError, ValueError) as exc:
            print(
                f"[Supervisor] Telegram {route or 'direct'}: {type(exc).__name__}",
                flush=True,
            )
            continue
        remember_route("telegram", route)
        return
    print("[Supervisor] Telegram недоступен по всем маршрутам", flush=True)


def _is_local_port_in_use(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        try:
            probe.bind(('127.0.0.1', port))
        except OSError:
            return True
    return False


def _kill_pid_tree(pid: int) -> None:
    if pid <= 0 or pid == os.getpid():
        return
    try:
        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/PID", str(pid), "/T", "/F"],
                capture_output=True,
                timeout=8,
            )
        else:
            os.kill(pid, signal.SIGTERM)
    except OSError as exc:
        logging.getLogger(__name__).warning('ОС: %s', type(exc).__name__)


class WorkerSupervisor:
    def __init__(self) -> None:
        self.process: Optional[subprocess.Popen] = None
        self._lock_socket: Optional[socket.socket] = None
        self._stopping: bool = False
        self._exits = deque(maxlen=3)
        self._next_crash_check = 0.0
        self._restart_failures = 0
        self._next_restart_at = 0.0

    def acquire_single_instance_lock(self) -> bool:
        """Гарантирует единственный экземпляр supervisor.py в ОС (защита от TelegramConflictError в start_hidden.vbs)."""
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            sock.setsockopt(socket.SOL_SOCKET, getattr(socket, "SO_EXCLUSIVEADDRUSE"), 1)
        try:
            sock.bind(("127.0.0.1", settings.SUPERVISOR_LOCK_PORT))
            sock.listen(1)
        except OSError:
            sock.close()
            return False

        self._lock_socket = sock
        try:
            settings.SUPERVISOR_PID_FILE.write_text(str(os.getpid()), encoding="utf-8")
        except OSError as exc:
            logging.getLogger(__name__).warning('ОС: %s', type(exc).__name__)
        return True

    def release_single_instance_lock(self) -> None:
        if self._lock_socket is not None:
            try:
                settings.SUPERVISOR_PID_FILE.unlink(missing_ok=True)
            finally:
                self._lock_socket.close()
                self._lock_socket = None

    def cleanup_orphan_worker(self) -> None:
        """Не завершать процессы по устаревшему PID; очищать только свободную блокировку."""
        if not _is_local_port_in_use(settings.WORKER_LOCK_PORT) and not settings.WORKER_PID_FILE.exists():
            return

        # PID-файл может устареть и указывать на посторонний процесс. Нельзя
        # завершать его без достоверной проверки владельца и времени запуска.
        if _is_local_port_in_use(settings.WORKER_LOCK_PORT):
            raise RuntimeError("Порт worker занят: остановите старый worker вручную")
        settings.WORKER_PID_FILE.unlink(missing_ok=True)

    def start_worker(self) -> None:
        self.stop_worker()
        self.cleanup_orphan_worker()
        self.write_fresh_heartbeat()

        creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
        self.process = subprocess.Popen(
            [sys.executable, str(BASE_DIR / "worker.py")],
            cwd=str(BASE_DIR),
            creationflags=creation_flags,
            env={**os.environ, "MARKETPLACE_SUPERVISED": "1"},
        )
        self._restart_failures = 0
        self._next_restart_at = 0.0
        stamp = datetime.now().strftime("%d.%m %H:%M:%S")
        print(
            f"[Supervisor] {stamp} Запущен worker.py (PID={self.process.pid})",
            flush=True,
        )

    def stop_worker(self) -> None:
        was_running = False
        if self.process is not None:
            pid = self.process.pid
            if self.process.poll() is None:
                was_running = True
                self.process.terminate()
                try:
                    self.process.wait(timeout=8)
                except subprocess.TimeoutExpired:
                    _kill_pid_tree(pid)
                    try:
                        self.process.kill()
                        self.process.wait(timeout=8)
                    except OSError as exc:
                        logging.getLogger(__name__).warning('ОС: %s', type(exc).__name__)
            self.process = None

        deadline = time.time() + 8.0
        while time.time() < deadline and _is_local_port_in_use(settings.WORKER_LOCK_PORT):
            time.sleep(0.4)

        if was_running:
            # Пауза для гарантированного закрытия старого getUpdates long-polling на сервере Telegram
            time.sleep(TELEGRAM_POLLING_COOLDOWN_SEC)

    def write_fresh_heartbeat(self) -> None:
        settings.HEARTBEAT_FILE.write_text(
            json.dumps({"timestamp": time.time(), "status": "starting"}),
            encoding="utf-8",
        )

    def get_heartbeat_age(self) -> float:
        if not settings.HEARTBEAT_FILE.exists():
            return float("inf")
        try:
            data = json.loads(settings.HEARTBEAT_FILE.read_text(encoding="utf-8"))
            return time.time() - float(data.get("timestamp", 0.0))
        except Exception:
            return float("inf")

    def is_worker_healthy(self) -> bool:
        if self.process is None or self.process.poll() is not None:
            return False
        return 0 <= self.get_heartbeat_age() <= HEARTBEAT_TIMEOUT_SEC

    async def verify_patch(self) -> None:
        started = time.time()
        await asyncio.to_thread(self.start_worker)
        deadline = time.monotonic() + POST_PATCH_VERIFY_SEC
        while time.monotonic() < deadline and self.process.poll() is None:
            try:
                hb = json.loads(settings.HEARTBEAT_FILE.read_bytes())
                if (hb.get('status') == 'running' and hb.get('pid') == self.process.pid
                        and started <= float(hb.get('timestamp', 0)) <= time.time()):
                    return
            except (OSError, ValueError, TypeError):
                logging.getLogger(__name__).debug("Ожидаю heartbeat нового worker")
            await asyncio.sleep(1)
        raise RuntimeError('Новый worker не подтвердил готовность')

    async def diagnose(self, silent_if_healthy: bool) -> None:
        result = await run_full_self_check_and_heal(verify=self.verify_patch,
            after_rollback=lambda: asyncio.to_thread(self.start_worker))
        if result['total_issues'] or not silent_if_healthy:
            await notify(f"Диагностика: ошибок {result['total_issues']}, исправлено {result['healed_count']}")
            for line in result['details']:
                await notify(line)

    def _schedule_restart_backoff(self, exc: BaseException) -> None:
        """Не молотить перезапусками при неустранимой ошибке (битый .env, занятый порт)."""
        self._restart_failures += 1
        delay = min(5 * (2 ** min(self._restart_failures - 1, 6)), 300)
        self._next_restart_at = time.monotonic() + delay
        print(
            f"[Supervisor] Перезапуск не удался ({type(exc).__name__}: {exc}). "
            f"Следующая попытка через {delay} с.",
            flush=True,
        )

    def recover_worker(self) -> None:
        # A syntax error in worker.py prevents even its scheduled /heal from starting.
        now = time.monotonic()
        if now < self._next_restart_at:
            return
        self._exits.append(now)
        if len(self._exits) == 3 and now - self._exits[0] < 600 and now >= self._next_crash_check:
            self._next_crash_check = now + 3600
            try:
                asyncio.run(self.diagnose(silent_if_healthy=False))
            except (OSError, RuntimeError, ValueError) as exc:
                print(f"[Supervisor] Диагностика недоступна: {type(exc).__name__}", flush=True)
        if self.is_worker_healthy():
            return
        try:
            self.start_worker()
        except Exception as exc:  # noqa: BLE001 — супервизор обязан жить дальше
            self._schedule_restart_backoff(exc)

    async def process_requests(self) -> None:
        for path in sorted((LOGS_DIR / 'maintenance_queue').glob('*.json')):
            if path.is_symlink():
                continue
            data = json.loads(path.read_bytes())
            path.unlink()  # a crash must never replay an already applied patch
            checks = {'verify': self.verify_patch, 'after_rollback': lambda: asyncio.to_thread(self.start_worker)}
            if data['kind'] == 'patch':
                ok, message = await apply_code_patch(data['instruction'], data.get('target'), **checks)
                await notify(('✅ ' if ok else '❌ ') + html.escape(message))
            elif data['kind'] in ('heal', 'night'):
                await self.diagnose(silent_if_healthy=data['kind'] == 'night')

    def shutdown(self) -> None:
        if self._stopping:
            return
        self._stopping = True
        print("[Supervisor] Корректное завершение работы...", flush=True)
        try:
            self.stop_worker()
        finally:
            self.release_single_instance_lock()

    def run_forever(self) -> None:
        if not self.acquire_single_instance_lock():
            print(
                f"[Supervisor] Другой экземпляр supervisor.py уже удерживает порт {settings.SUPERVISOR_LOCK_PORT}. "
                "Повторный запуск отменен для защиты от TelegramConflictError.",
                flush=True,
            )
            return

        atexit.register(self.shutdown)
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                signal.signal(sig, lambda *_: sys.exit(0))
            except OSError as exc:
                logging.getLogger(__name__).warning('ОС: %s', type(exc).__name__)

        try:
            self.start_worker()
            while not self._stopping:
                time.sleep(5)

                if self.process is not None and self.process.poll() == 75:
                    asyncio.run(notify('Polling остановлен: токен используется другим процессом. После его остановки запустите supervisor заново.'))
                    return
                try:
                    asyncio.run(self.process_requests())
                except (OSError, KeyError, ValueError, RuntimeError) as exc:
                    print(f'[Supervisor] Ошибка обслуживания: {type(exc).__name__}', flush=True)

                if settings.RESTART_SIGNAL_FILE.exists():
                    settings.RESTART_SIGNAL_FILE.unlink(missing_ok=True)
                    stamp = datetime.now().strftime("%d.%m %H:%M:%S")
                    print(f"[Supervisor] {stamp} Получен сигнал перезапуска worker.py.", flush=True)
                    try:
                        self.start_worker()
                    except Exception as exc:  # noqa: BLE001
                        self._schedule_restart_backoff(exc)

                if not self.is_worker_healthy():
                    stamp = datetime.now().strftime("%d.%m %H:%M:%S")
                    print(f"[Supervisor] {stamp} Воркер не отвечает или завершился. Перезапуск...", flush=True)
                    self.recover_worker()
        finally:
            self.shutdown()


if __name__ == "__main__":
    prepare_runtime_dirs()
    _ensure_stdio_streams()
    supervisor = WorkerSupervisor()
    supervisor.run_forever()
