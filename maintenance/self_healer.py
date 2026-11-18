import os
import ast
import time
import shutil
import asyncio
import aiohttp
import py_compile
import subprocess
import traceback
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple

from config import settings, BASE_DIR, LOGS_DIR

BACKUPS_DIR = BASE_DIR / "backups"
BACKUPS_DIR.mkdir(parents=True, exist_ok=True)

PROXY_CANDIDATES = [
    ("Karing HTTP 3066", "[http://127.0.0.1:3066](http://127.0.0.1:3066)", False),
    ("Системный прокси Windows", None, True),
    ("Прямое соединение", None, False),
]

FALLBACK_MODELS = [
    "gemini-3.1-flash-lite-preview",
    "gemini-3.8-flash",
    "gemini-3.1-pro-preview",
    "gemini-1.5-flash",
    "gemini-1.5-pro",
]


async def _get_active_gemini_model(session: aiohttp.ClientSession, api_key: str, proxy: Optional[str]) -> str:
    url = f"[https://generativelanguage.googleapis.com/v1beta/models?key=](https://generativelanguage.googleapis.com/v1beta/models?key=){api_key}"
    try:
        async with session.get(url, proxy=proxy, timeout=aiohttp.ClientTimeout(total=8)) as resp:
            if resp.status == 200:
                data = await resp.json()
                for m in data.get("models", []):
                    methods = m.get("supportedGenerationMethods", [])
                    if "generateContent" in methods:
                        name = m.get("name", "").replace("models/", "")
                        if "flash" in name or "pro" in name:
                            return name
    except Exception:
        pass
    return FALLBACK_MODELS[0]


async def _ask_gemini_fix(prompt: str) -> str:
    api_key = str(settings.GEMINI_API_KEY).strip()
    timeout = aiohttp.ClientTimeout(total=120, connect=15)
    for route_label, proxy_url, trust_env in PROXY_CANDIDATES:
        try:
            async with aiohttp.ClientSession(trust_env=trust_env, timeout=timeout) as session:
                model_name = await _get_active_gemini_model(session, api_key, proxy_url)
                url = f"[https://generativelanguage.googleapis.com/v1beta/models/](https://generativelanguage.googleapis.com/v1beta/models/){model_name}:generateContent?key={api_key}"
                payload = {
                    "contents": [{"parts": [{"text": prompt}]}],
                    "generationConfig": {"temperature": 0.1, "maxOutputTokens": 8192},
                }
                async with session.post(url, json=payload, proxy=proxy_url) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        candidates = data.get("candidates") or []
                        if candidates:
                            parts = candidates[0].get("content", {}).get("parts") or []
                            text = "".join(p.get("text", "") for p in parts if not p.get("thought", False)).strip()
                            if text:
                                return text
        except Exception:
            continue
    raise RuntimeError("Не удалось связаться с Gemini API через доступные сетевые маршруты.")


def _clean_markdown_code(raw_text: str) -> str:
    text = raw_text.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    return text


def _verify_code_syntax_and_import(target_file: Path, new_code: str) -> Tuple[bool, str]:
    try:
        ast.parse(new_code, filename=str(target_file))
    except SyntaxError as syn_err:
        return False, f"Синтаксическая ошибка (строка {syn_err.lineno}): {syn_err.msg}"
    temp_test_file = target_file.parent / f"__test_heal_{target_file.name}"
    try:
        temp_test_file.write_text(new_code, encoding="utf-8")
        py_compile.compile(str(temp_test_file), doraise=True)
    except Exception as exc:
        return False, f"Ошибка компиляции байт-кода: {exc}"
    finally:
        if temp_test_file.exists():
            temp_test_file.unlink(missing_ok=True)
        for p in target_file.parent.glob("__pycache__/__test_heal_*"):
            p.unlink(missing_ok=True)
    return True, "OK"


async def auto_repair_file(target_file: Path, error_context: str) -> Tuple[bool, str]:
    if not target_file.exists():
        return False, f"Файл не найден: {target_file}"
    original_code = target_file.read_text(encoding="utf-8")
    now_str = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_file = BACKUPS_DIR / f"{target_file.stem}_{now_str}.bak"
    shutil.copy2(target_file, backup_file)
    prompt = (
        "Ты — инженер автопочинки Python-кода в агенте маркетплейсов YaSoKo (Ozon + WB).\n"
        f"В модуле {target_file.name} произошел сбой.\n\n"
        f"ОШИБКА И СТЕК:\n{error_context}\n\n"
        f"ТЕКУЩИЙ КОД ФАЙЛА {target_file.name}:\n{original_code}\n\n"
        "ЗАДАЧА:\n"
        "1. Исправь ошибку, сохранив существующие сигнатуры функций.\n"
        "2. Верни ТОЛЬКО валидный полный код Python для этого файла без вводных слов и пояснений.\n"
    )
    try:
        raw_fix = await _ask_gemini_fix(prompt)
        clean_code = _clean_markdown_code(raw_fix)
        if not clean_code or len(clean_code) < 50:
            return False, "ИИ вернул пустой ответ."
        valid, check_msg = _verify_code_syntax_and_import(target_file, clean_code)
        if not valid:
            return False, f"Код не прошел валидацию: {check_msg}"
        tmp_target = target_file.with_suffix(".tmp")
        tmp_target.write_text(clean_code, encoding="utf-8")
        tmp_target.replace(target_file)
        return True, f"Файл успешно восстановлен. Резервная копия: {backup_file.name}"
    except Exception as exc:
        if backup_file.exists():
            shutil.copy2(backup_file, target_file)
        return False, f"Исключение при автопочинке: {exc}"


async def run_full_self_check_and_heal() -> Dict[str, Any]:
    from services.ozon.seller_api import OzonSellerAPI
    from services.wb.statistics_api import WBStatisticsAPI
    from services.common.orders_report import sync_and_generate_orders_report
    from services.common.ai_advisory_board import check_gemini_api_health

    ozon = OzonSellerAPI()
    wb = WBStatisticsAPI()
    issues = []
    healed = []

    try:
        await ozon.fetch_stocks_and_prices()
    except Exception:
        issues.append({
            "service": "Ozon Seller API",
            "file": BASE_DIR / "services" / "ozon" / "seller_api.py",
            "error": traceback.format_exc(),
        })

    try:
        await wb.fetch_stocks_and_prices()
        await wb.fetch_wb_tariffs()
    except Exception:
        issues.append({
            "service": "Wildberries API",
            "file": BASE_DIR / "services" / "wb" / "statistics_api.py",
            "error": traceback.format_exc(),
        })

    try:
        ai_stat = await check_gemini_api_health()
        if not ai_stat.get("ok"):
            issues.append({
                "service": "Gemini AI API",
                "file": BASE_DIR / "services" / "common" / "ai_advisory_board.py",
                "error": f"Health check failed: {ai_stat.get('detail')}",
            })
    except Exception:
        issues.append({
            "service": "Gemini AI API",
            "file": BASE_DIR / "services" / "common" / "ai_advisory_board.py",
            "error": traceback.format_exc(),
        })

    try:
        await sync_and_generate_orders_report(ozon, wb, days=1)
    except Exception:
        issues.append({
            "service": "Orders Report Engine",
            "file": BASE_DIR / "services" / "common" / "orders_report.py",
            "error": traceback.format_exc(),
        })

    for item in issues:
        target_path = Path(item["file"])
        success, msg = await auto_repair_file(target_path, item["error"])
        if success:
            healed.append(f"🟢 {item['service']} ({target_path.name}): {msg}")
        else:
            healed.append(f"🔴 {item['service']} ({target_path.name}): {msg}")

    now_str = datetime.now().strftime("%d.%m.%Y %H:%M:%S")
    return {
        "timestamp": now_str,
        "total_issues": len(issues),
        "healed_count": sum(1 for h in healed if "🟢" in h),
        "details": healed,
    }