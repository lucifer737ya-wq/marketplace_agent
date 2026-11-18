import os
import sys
import json
import time
import shutil
import subprocess
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Optional
from config import settings, BASE_DIR, BACKUPS_DIR, LOGS_DIR

PATCH_REQUEST_FILE = LOGS_DIR / "patch_request.json"
TEMP_PATCH_FILE = BASE_DIR / "temp_patch.py"
HEARTBEAT_TIMEOUT_SEC = 45
POST_PATCH_VERIFY_SEC = 30

ALLOWED_TARGET_FILES = {
    "config.py": BASE_DIR / "config.py",
    "models.py": BASE_DIR / "database" / "models.py",
    "crud.py": BASE_DIR / "database" / "crud.py",
    "seller_api.py": BASE_DIR / "services" / "ozon" / "seller_api.py",
    "statistics_api.py": BASE_DIR / "services" / "wb" / "statistics_api.py",
    "unit_economics.py": BASE_DIR / "services" / "common" / "unit_economics.py",
    "financial_pnl.py": BASE_DIR / "services" / "common" / "financial_pnl.py",
    "api_inspector.py": BASE_DIR / "maintenance" / "api_inspector.py",
    "costs_fsm.py": BASE_DIR / "bot" / "handlers" / "costs_fsm.py",
    "logistics_fsm.py": BASE_DIR / "bot" / "handlers" / "logistics_fsm.py",
    "callbacks.py": BASE_DIR / "bot" / "handlers" / "callbacks.py",
    "worker.py": BASE_DIR / "worker.py",
}


def send_telegram_sync(text: str) -> None:
    url = f"https://api.telegram.org/bot{settings.TG_BOT_TOKEN}/sendMessage"
    data = json.dumps(
        {"chat_id": settings.TG_ADMIN_ID, "text": text, "parse_mode": "HTML"}
    ).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    try:
        proxy_handler = urllib.request.ProxyHandler({
            'http': 'http://127.0.0.1:3066',
            'https': 'http://127.0.0.1:3066'
        })
        opener = urllib.request.build_opener(proxy_handler)
        with opener.open(req, timeout=10) as _:
            pass
    except Exception as exc:
        print(f"[Supervisor] Ошибка отправки TG уведомления: {exc}", flush=True)


def resolve_target_file(instruction: str) -> Path:
    lower_instr = instruction.lower()
    for fname, path in ALLOWED_TARGET_FILES.items():
        if fname.lower() in lower_instr:
            return path
    if "ozon" in lower_instr:
        return ALLOWED_TARGET_FILES["seller_api.py"]
    if "wb" in lower_instr or "wildberries" in lower_instr:
        return ALLOWED_TARGET_FILES["statistics_api.py"]
    if "цен" in lower_instr or "floor" in lower_instr or "тариф" in lower_instr:
        return ALLOWED_TARGET_FILES["unit_economics.py"]
    if "отчет" in lower_instr or "pnl" in lower_instr or "дебет" in lower_instr:
        return ALLOWED_TARGET_FILES["financial_pnl.py"]
    return ALLOWED_TARGET_FILES["worker.py"]


def request_gemini_patch(instruction: str, target_file_path: Path, current_code: str) -> str:
    """Запрос на генерацию патча с автоматическим перебором резервных моделей."""
    raw_model = getattr(settings, "GEMINI_MODEL", "gemini-3.5-flash") or "gemini-3.5-flash"
    primary_model = raw_model.strip().strip('"').strip("'").replace("models/", "")
    api_key = (getattr(settings, "GEMINI_API_KEY", "") or "").strip().strip('"').strip("'")

    models_to_try = list(dict.fromkeys([
        primary_model,
        "gemini-3.8-flash-tts",
        "gemini-3.8-flash-lite-tts",
        "gemini-3.1-flash-tts-preview",
        "gemini-3.5-flash-lite",
        "gemini-3.1-flash-lite-preview",
        "gemini-3.5-flash",
        "gemini-3.1-flash-lite",
        "gemini-3.7-flash",
        "gemini-3.1-pro-preview",
        "gemini-2.5-flash-lite",
    ]))

    prompt = (
        f"Файл: {target_file_path.name}\n"
        f"Задача: {instruction}\n\n"
        f"Текущий исходный код:\n```python\n{current_code}\n```\n\n"
        "Верни ПОЛНЫЙ рабочий валидный Python-код без markdown-разметки (без ```python)."
    )

    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0.2}
    }
    req_data = json.dumps(payload).encode("utf-8")

    proxy_handler = urllib.request.ProxyHandler({
        'http': '[http://127.0.0.1:3066](http://127.0.0.1:3066)',
        'https': '[http://127.0.0.1:3066](http://127.0.0.1:3066)'
    })
    opener = urllib.request.build_opener(proxy_handler)

    for model in models_to_try:
        url = f"[https://generativelanguage.googleapis.com/v1beta/models/](https://generativelanguage.googleapis.com/v1beta/models/){model}:generateContent?key={api_key}"
        req = urllib.request.Request(url, data=req_data, headers={"Content-Type": "application/json"})

        try:
            with opener.open(req, timeout=60) as resp:
                body = json.loads(resp.read().decode("utf-8"))

            code = body["candidates"][0]["content"]["parts"][0]["text"].strip()
            if code.startswith("```"):
                code = "\n".join(code.splitlines()[1:-1])
            return code
        except urllib.error.HTTPError as http_err:
            if http_err.code in (503, 429, 404, 500):
                continue
            raise
        except Exception:
            continue

    raise RuntimeError("Все доступные модели Gemini временно перегружены или недоступны.")


class WorkerSupervisor:
    def __init__(self) -> None:
        self.process: Optional[subprocess.Popen] = None

    def start_worker(self) -> None:
        self.stop_worker()
        self.write_fresh_heartbeat()
        self.process = subprocess.Popen(
            [sys.executable, str(BASE_DIR / "worker.py")],
            cwd=str(BASE_DIR),
        )
        print(f"[Supervisor] Запущен worker.py (PID={self.process.pid})", flush=True)

    def stop_worker(self) -> None:
        if self.process and self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=8)
            except subprocess.TimeoutExpired:
                self.process.kill()
        self.process = None

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

        # 1. Создание резервной копии
        shutil.copy2(target_file, backup_path)
        current_code = target_file.read_text(encoding="utf-8")

        # 2. Запрос к Gemini API и сохранение во временный файл
        try:
            patched_code = request_gemini_patch(current_code, instruction, target_file.name)
            if not patched_code:
                raise RuntimeError("Пустой ответ от модели")
            TEMP_PATCH_FILE.write_text(patched_code, encoding="utf-8")
        except Exception as exc:
            if TEMP_PATCH_FILE.exists():
                TEMP_PATCH_FILE.unlink()
            send_telegram_sync(f"❌ Ошибка при обращении к LLM: {exc}. Патч отменен.")
            return

        # 3. Проверка синтаксиса через python -m py_compile temp_patch.py
        compile_res = subprocess.run(
            [sys.executable, "-m", "py_compile", str(TEMP_PATCH_FILE)],
            capture_output=True,
            text=True,
        )
        if compile_res.returncode != 0:
            if TEMP_PATCH_FILE.exists():
                TEMP_PATCH_FILE.unlink()
            send_telegram_sync(
                "❌ Ошибка синтаксиса при генерации. Патч отменен, бот работает на прежней версии"
            )
            return

        # 4. Перезапись оригинала и перезапуск воркера
        shutil.move(str(TEMP_PATCH_FILE), str(target_file))
        pre_restart_ts = time.time()
        self.stop_worker()
        if settings.HEARTBEAT_FILE.exists():
            settings.HEARTBEAT_FILE.unlink()

        self.process = subprocess.Popen(
            [sys.executable, str(BASE_DIR / "worker.py")],
            cwd=str(BASE_DIR),
        )

        # 5. Ожидание обновления heartbeat в течение 30 секунд
        deadline = time.time() + POST_PATCH_VERIFY_SEC
        heartbeat_updated = False
        while time.time() < deadline:
            time.sleep(2)
            if self.process.poll() is not None:
                break
            if settings.HEARTBEAT_FILE.exists():
                try:
                    hb_data = json.loads(settings.HEARTBEAT_FILE.read_text(encoding="utf-8"))
                    if float(hb_data.get("timestamp", 0.0)) > pre_restart_ts:
                        heartbeat_updated = True
                        break
                except Exception:
                    pass

        if heartbeat_updated:
            send_telegram_sync(
                f"✅ Патч успешно применен к файлу <code>{target_file.name}</code>. "
                f"Резервная копия: <code>{backup_path.name}</code>."
            )
        else:
            # 6. Автоматический откат из .bak при падении или отсутствии heartbeat
            self.stop_worker()
            shutil.copy2(backup_path, target_file)
            self.start_worker()
            send_telegram_sync(
                "⚠️ Воркер упал после запуска. Выполнен откат на резервную копию"
            )

    def run_forever(self) -> None:
        self.start_worker()
        while True:
            time.sleep(5)
            if PATCH_REQUEST_FILE.exists():
                try:
                    req_data = json.loads(PATCH_REQUEST_FILE.read_text(encoding="utf-8"))
                    PATCH_REQUEST_FILE.unlink()
                    instruction = req_data.get("instruction", "").strip()
                    if instruction:
                        self.apply_patch_safely(instruction)
                except Exception as exc:
                    print(f"[Supervisor] Ошибка чтения запроса патча: {exc}", flush=True)

            if not self.is_worker_healthy():
                print("[Supervisor] Воркер не отвечает или завершился. Перезапуск...", flush=True)
                self.start_worker()


if __name__ == "__main__":
    supervisor = WorkerSupervisor()
    supervisor.run_forever()