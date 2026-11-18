import ast
import atexit
import json
import os
import py_compile
import shutil
import signal
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from config import BACKUPS_DIR, BASE_DIR, LOGS_DIR, settings


def _ensure_stdio_streams() -> None:
    """Перенаправляет stdout/stderr в лог-файл при скрытом запуске через pythonw.exe (start_hidden.vbs)."""
    log_path = LOGS_DIR / "supervisor.log"
    if sys.stdout is None:
        sys.stdout = open(log_path, "a", encoding="utf-8", buffering=1)
    if sys.stderr is None:
        sys.stderr = open(log_path, "a", encoding="utf-8", buffering=1)


_ensure_stdio_streams()

PATCH_REQUEST_FILE = LOGS_DIR / "patch_request.json"
ROUTE_CACHE_FILE = LOGS_DIR / "proxy_route_cache.json"
TEMP_PATCH_FILE = BASE_DIR / "temp_patch.py"
HEARTBEAT_TIMEOUT_SEC = 90
POST_PATCH_VERIFY_SEC = 45
TELEGRAM_POLLING_COOLDOWN_SEC = 2.5

ALLOWED_TARGET_FILES: Dict[str, Path] = {
    "config.py": BASE_DIR / "config.py",
    "products_catalog.py": BASE_DIR / "products_catalog.py",
    "worker.py": BASE_DIR / "worker.py",
    "models.py": BASE_DIR / "database" / "models.py",
    "crud.py": BASE_DIR / "database" / "crud.py",
    "seller_api.py": BASE_DIR / "services" / "ozon" / "seller_api.py",
    "browser_reviews.py": BASE_DIR / "services" / "ozon" / "browser_reviews.py",
    "statistics_api.py": BASE_DIR / "services" / "wb" / "statistics_api.py",
    "price_auditor.py": BASE_DIR / "services" / "common" / "price_auditor.py",
    "unit_economics.py": BASE_DIR / "services" / "common" / "unit_economics.py",
    "orders_report.py": BASE_DIR / "services" / "common" / "orders_report.py",
    "financial_pnl.py": BASE_DIR / "services" / "common" / "financial_pnl.py",
    "ad_recommendations.py": BASE_DIR / "services" / "common" / "ad_recommendations.py",
    "buyer_questions.py": BASE_DIR / "services" / "common" / "buyer_questions.py",
    "ai_advisory_board.py": BASE_DIR / "services" / "common" / "ai_advisory_board.py",
    "deep_parser.py": BASE_DIR / "services" / "competitors" / "deep_parser.py",
    "api_inspector.py": BASE_DIR / "maintenance" / "api_inspector.py",
    "self_healer.py": BASE_DIR / "maintenance" / "self_healer.py",
    "keyboards.py": BASE_DIR / "bot" / "keyboards.py",
    "callbacks.py": BASE_DIR / "bot" / "handlers" / "callbacks.py",
    "costs_fsm.py": BASE_DIR / "bot" / "handlers" / "costs_fsm.py",
    "logistics_fsm.py": BASE_DIR / "bot" / "handlers" / "logistics_fsm.py",
    "patch_fsm.py": BASE_DIR / "bot" / "handlers" / "patch_fsm.py",
    "questions_fsm.py": BASE_DIR / "bot" / "handlers" / "questions_fsm.py",
    "reviews_fsm.py": BASE_DIR / "bot" / "handlers" / "reviews_fsm.py",
}

_CACHED_ROUTE_NAME: Optional[str] = None


def _get_route_candidates() -> List[Tuple[str, urllib.request.OpenerDirector]]:
    candidates: List[Tuple[str, urllib.request.OpenerDirector]] = []
    proxy_url = (settings.LOCAL_PROXY_URL or "").strip()
    if proxy_url:
        proxy_handler = urllib.request.ProxyHandler({"http": proxy_url, "https": proxy_url})
        candidates.append(("local_proxy", urllib.request.build_opener(proxy_handler)))
    return candidates


def _load_cached_route_name() -> Optional[str]:
    global _CACHED_ROUTE_NAME
    if _CACHED_ROUTE_NAME:
        return _CACHED_ROUTE_NAME
    try:
        if ROUTE_CACHE_FILE.exists():
            data = json.loads(ROUTE_CACHE_FILE.read_text(encoding="utf-8"))
            route = data.get("route")
            if isinstance(route, str) and route:
                _CACHED_ROUTE_NAME = route
                return route
    except Exception:
        pass
    return None


def _save_cached_route_name(route_name: str) -> None:
    global _CACHED_ROUTE_NAME
    _CACHED_ROUTE_NAME = route_name
    try:
        ROUTE_CACHE_FILE.write_text(
            json.dumps({"route": route_name, "updated_at": time.time()}, ensure_ascii=False),
            encoding="utf-8",
        )
    except Exception:
        pass


def _open_via_cached_route(req: urllib.request.Request, timeout: int = 30) -> bytes:
    """Выполняет запрос к Telegram/Gemini через локальный прокси с кэшированием рабочего маршрута."""
    candidates = _get_route_candidates()
    cached_name = _load_cached_route_name()
    if cached_name:
        candidates.sort(key=lambda item: 0 if item[0] == cached_name else 1)

    last_exc: Optional[Exception] = None
    for route_name, opener in candidates:
        try:
            with opener.open(req, timeout=timeout) as resp:
                raw = resp.read()
                _save_cached_route_name(route_name)
                return raw
        except urllib.error.HTTPError as http_err:
            # HTTP-ответ получен — значит сетевой маршрут рабочий, пробрасываем код ответа вызывающему коду
            _save_cached_route_name(route_name)
            raise http_err
        except Exception as exc:
            last_exc = exc
            continue

    raise RuntimeError(f"Все сетевые маршруты недоступны: {last_exc}")


def send_telegram_sync(text: str) -> None:
    url = f"https://api.telegram.org/bot{settings.TG_BOT_TOKEN}/sendMessage"
    data = json.dumps(
        {"chat_id": settings.TG_ADMIN_ID, "text": text, "parse_mode": "HTML"}
    ).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    try:
        _open_via_cached_route(req, timeout=12)
    except Exception as exc:
        print(f"[Supervisor] Ошибка отправки TG уведомления: {exc}", flush=True)


def resolve_target_file(instruction: str) -> Path:
    clean_instr = instruction.strip()
    first_token = clean_instr.split(maxsplit=1)[0].strip().replace("\\", "/") if clean_instr else ""
    if first_token.endswith(".py"):
        candidate_rel = (BASE_DIR / first_token).resolve()
        if candidate_rel.exists() and candidate_rel.is_file() and BASE_DIR in candidate_rel.parents:
            return candidate_rel

    lower_instr = clean_instr.lower()
    for fname, path in ALLOWED_TARGET_FILES.items():
        if fname.lower() in lower_instr:
            return path

    if any(k in lower_instr for k in ("отзыв", "вопрос", "playwright", "шторк")):
        return ALLOWED_TARGET_FILES["browser_reviews.py"]
    if any(k in lower_instr for k in ("аудит цен", "52%", "299", "300")):
        return ALLOWED_TARGET_FILES["price_auditor.py"]
    if any(k in lower_instr for k in ("реклам", "кампан", "ддр", "дрр", "трафарет")):
        return ALLOWED_TARGET_FILES["ad_recommendations.py"]
    if "ozon" in lower_instr:
        return ALLOWED_TARGET_FILES["seller_api.py"]
    if "wb" in lower_instr or "wildberries" in lower_instr:
        return ALLOWED_TARGET_FILES["statistics_api.py"]
    if "цен" in lower_instr or "floor" in lower_instr or "тариф" in lower_instr:
        return ALLOWED_TARGET_FILES["unit_economics.py"]
    if "отчет" in lower_instr or "pnl" in lower_instr or "дебет" in lower_instr:
        return ALLOWED_TARGET_FILES["financial_pnl.py"]
    return ALLOWED_TARGET_FILES["worker.py"]


def _discover_gemini_text_models(api_key: str, primary_model: str) -> List[str]:
    excluded_tokens = ("tts", "audio", "imagen", "embedding", "aqa", "vision")
    discovered: List[str] = []
    list_url = f"https://generativelanguage.googleapis.com/v1beta/models?key={api_key}"
    try:
        req = urllib.request.Request(list_url, method="GET")
        raw = _open_via_cached_route(req, timeout=10)
        body = json.loads(raw.decode("utf-8"))
        for item in body.get("models") or []:
            methods = item.get("supportedGenerationMethods") or []
            name = str(item.get("name") or "").replace("models/", "").strip()
            if "generateContent" in methods and name and not any(tok in name.lower() for tok in excluded_tokens):
                discovered.append(name)
    except Exception:
        pass

    fallbacks = [
        primary_model,
        "gemini-2.5-pro",
        "gemini-2.5-flash",
        "gemini-2.0-flash",
        "gemini-1.5-pro",
        "gemini-1.5-flash",
    ]
    ordered = [m for m in [primary_model, *discovered, *fallbacks] if m and "tts" not in m.lower()]
    return list(dict.fromkeys(ordered))


def _strip_code_fences(raw_text: str) -> str:
    code = raw_text.strip()
    if "```" in code:
        blocks = code.split("```")
        for block in blocks:
            stripped = block.strip()
            if stripped.startswith("python"):
                return stripped[len("python"):].strip()
        for block in blocks:
            stripped = block.strip()
            if "import " in stripped or "def " in stripped or "class " in stripped:
                return stripped
    return code


def request_gemini_patch(instruction: str, target_file_path: Path, current_code: str) -> str:
    """Запрос на генерацию патча к Gemini API с кэшированием прокси-маршрута и перебором текстовых моделей."""
    raw_model = getattr(settings, "GEMINI_MODEL", "gemini-2.5-flash") or "gemini-2.5-flash"
    primary_model = raw_model.strip().replace("models/", "")
    api_key = (getattr(settings, "GEMINI_API_KEY", "") or "").strip()
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY не задан в .env")

    models_to_try = _discover_gemini_text_models(api_key, primary_model)

    prompt = (
        f"Файл: {target_file_path.name}\n"
        f"Задача: {instruction}\n\n"
        f"Текущий исходный код:\n```python\n{current_code}\n```\n\n"
        "Верни ПОЛНЫЙ рабочий валидный Python-код файла целиком без пропусков и без пояснений."
    )

    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0.15, "maxOutputTokens": 16384},
    }
    req_data = json.dumps(payload, ensure_ascii=False).encode("utf-8")

    last_error = "Нет доступных моделей"
    for model in models_to_try:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"
        req = urllib.request.Request(url, data=req_data, headers={"Content-Type": "application/json"})
        try:
            raw_resp = _open_via_cached_route(req, timeout=90)
            body = json.loads(raw_resp.decode("utf-8"))
            candidates = body.get("candidates") or []
            if not candidates:
                continue
            parts = candidates[0].get("content", {}).get("parts") or []
            text_out = "".join(p.get("text", "") for p in parts if not p.get("thought", False)).strip()
            clean_code = _strip_code_fences(text_out)
            if clean_code:
                return clean_code
        except urllib.error.HTTPError as http_err:
            last_error = f"{model}: HTTP {http_err.code}"
            if http_err.code in (400, 404, 429, 500, 502, 503, 504):
                continue
            raise
        except Exception as exc:
            last_error = f"{model}: {exc}"
            continue

    raise RuntimeError(f"Все доступные модели Gemini временно перегружены или недоступны ({last_error}).")


def _is_local_port_in_use(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.settimeout(0.4)
        return probe.connect_ex(("127.0.0.1", port)) == 0


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
    except Exception:
        pass


class WorkerSupervisor:
    def __init__(self) -> None:
        self.process: Optional[subprocess.Popen] = None
        self._lock_socket: Optional[socket.socket] = None
        self._stopping: bool = False

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
        except Exception:
            pass
        return True

    def release_single_instance_lock(self) -> None:
        if self._lock_socket is not None:
            try:
                self._lock_socket.close()
            except Exception:
                pass
            self._lock_socket = None
        try:
            if settings.SUPERVISOR_PID_FILE.exists():
                settings.SUPERVISOR_PID_FILE.unlink(missing_ok=True)
        except Exception:
            pass

    def cleanup_orphan_worker(self) -> None:
        """Гасит осиротевший worker.py от прошлых сессий и ждет освобождения его сокета перед новым polling."""
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
        )
        print(f"[Supervisor] Запущен worker.py (PID={self.process.pid})", flush=True)

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
                    except Exception:
                        pass
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
            return 999999.0
        try:
            data = json.loads(settings.HEARTBEAT_FILE.read_text(encoding="utf-8"))
            return time.time() - float(data.get("timestamp", 0.0))
        except Exception:
            return 999999.0

    def is_worker_healthy(self) -> bool:
        if self.process is None or self.process.poll() is not None:
            return False
        return self.get_heartbeat_age() <= HEARTBEAT_TIMEOUT_SEC

    def apply_patch_safely(self, instruction: str) -> None:
        target_file = resolve_target_file(instruction)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_path = BACKUPS_DIR / f"{target_file.name}.{timestamp}.bak"

        shutil.copy2(target_file, backup_path)
        current_code = target_file.read_text(encoding="utf-8")

        try:
            patched_code = request_gemini_patch(
                instruction=instruction,
                target_file_path=target_file,
                current_code=current_code,
            )
            if not patched_code:
                raise RuntimeError("Пустой ответ от модели")
            ast.parse(patched_code, filename=str(target_file))
            TEMP_PATCH_FILE.write_text(patched_code, encoding="utf-8")
        except Exception as exc:
            TEMP_PATCH_FILE.unlink(missing_ok=True)
            send_telegram_sync(f"❌ Ошибка при генерации патча LLM: <code>{exc}</code>. Патч отменен.")
            return

        compile_res = subprocess.run(
            [sys.executable, "-m", "py_compile", str(TEMP_PATCH_FILE)],
            capture_output=True,
            text=True,
        )
        if compile_res.returncode != 0:
            TEMP_PATCH_FILE.unlink(missing_ok=True)
            send_telegram_sync(
                "❌ Ошибка компиляции при проверке патча. Патч отменен, бот продолжает работу на прежней версии."
            )
            return

        shutil.move(str(TEMP_PATCH_FILE), str(target_file))
        pre_restart_ts = time.time()
        self.stop_worker()
        settings.HEARTBEAT_FILE.unlink(missing_ok=True)

        creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
        self.process = subprocess.Popen(
            [sys.executable, str(BASE_DIR / "worker.py")],
            cwd=str(BASE_DIR),
            creationflags=creation_flags,
        )

        deadline = time.time() + POST_PATCH_VERIFY_SEC
        heartbeat_updated = False
        while time.time() < deadline:
            time.sleep(2)
            if self.process.poll() is not None:
                break
            if settings.HEARTBEAT_FILE.exists():
                try:
                    hb_data = json.loads(settings.HEARTBEAT_FILE.read_text(encoding="utf-8"))
                    if float(hb_data.get("timestamp", 0.0)) > pre_restart_ts and hb_data.get("status") != "starting":
                        heartbeat_updated = True
                        break
                except Exception:
                    pass

        if heartbeat_updated:
            send_telegram_sync(
                f"✅ Патч успешно применен к файлу <code>{target_file.name}</code>.\n"
                f"Резервная копия: <code>{backup_path.name}</code>."
            )
        else:
            self.stop_worker()
            shutil.copy2(backup_path, target_file)
            self.start_worker()
            send_telegram_sync(
                f"⚠️ Воркер не подтвердил запуск после патча <code>{target_file.name}</code>. "
                f"Выполнен автоматический откат на <code>{backup_path.name}</code>."
            )

    def shutdown(self) -> None:
        if self._stopping:
            return
        self._stopping = True
        print("[Supervisor] Корректное завершение работы...", flush=True)
        self.stop_worker()
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
            except Exception:
                pass

        try:
            self.start_worker()
            while not self._stopping:
                time.sleep(5)

                if PATCH_REQUEST_FILE.exists():
                    try:
                        req_data = json.loads(PATCH_REQUEST_FILE.read_text(encoding="utf-8"))
                        PATCH_REQUEST_FILE.unlink(missing_ok=True)
                        instruction = str(req_data.get("instruction") or "").strip()
                        if instruction:
                            self.apply_patch_safely(instruction)
                    except Exception as exc:
                        print(f"[Supervisor] Ошибка чтения запроса патча: {exc}", flush=True)

                if settings.RESTART_SIGNAL_FILE.exists():
                    settings.RESTART_SIGNAL_FILE.unlink(missing_ok=True)
                    print("[Supervisor] Получен сигнал перезапуска worker.py.", flush=True)
                    self.start_worker()

                if not self.is_worker_healthy():
                    print("[Supervisor] Воркер не отвечает или завершился. Перезапуск...", flush=True)
                    self.start_worker()
        finally:
            self.shutdown()


if __name__ == "__main__":
    supervisor = WorkerSupervisor()
    supervisor.run_forever()
