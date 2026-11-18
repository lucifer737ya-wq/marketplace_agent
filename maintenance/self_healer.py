import ast
import html
import json
import py_compile
import shutil
import tempfile
import traceback
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

import aiohttp

from config import BACKUPS_DIR, BASE_DIR, LOGS_DIR, settings
from maintenance.supervisor import _extract_python_code
from services.common.ai_advisory_board import (
    PROXY_CANDIDATES,
    _extract_model_name,
    _get_available_models,
    _is_supported_text_model,
)

LOGS_DIR.mkdir(parents=True, exist_ok=True)
BACKUPS_DIR.mkdir(parents=True, exist_ok=True)


async def _ask_gemini_fix(prompt: str) -> str:
    api_key = str(getattr(settings, "GEMINI_API_KEY", "") or "").strip()
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY не задан в .env")

    timeout = aiohttp.ClientTimeout(total=120, connect=15, sock_connect=15, sock_read=100)
    last_error = "Нет ответа от доступных моделей Gemini"

    for route_label, proxy_url, trust_env in PROXY_CANDIDATES:
        connector = aiohttp.TCPConnector(limit=4, ssl=True, enable_cleanup_closed=True)
        try:
            async with aiohttp.ClientSession(
                connector=connector,
                connector_owner=True,
                trust_env=trust_env,
                timeout=timeout,
            ) as session:
                models = await _get_available_models(session, api_key, proxy_url)
                candidate_models = list(
                    dict.fromkeys(
                        _extract_model_name(m)
                        for m in models
                        if _is_supported_text_model(_extract_model_name(m))
                    )
                )
                for model_name in candidate_models:
                    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent?key={api_key}"
                    payload = {
                        "contents": [{"parts": [{"text": prompt}]}],
                        "generationConfig": {"temperature": 0.1, "maxOutputTokens": 16384},
                    }
                    try:
                        async with session.post(url, json=payload, proxy=proxy_url) as resp:
                            body_text = await resp.text(encoding="utf-8", errors="replace")
                            if resp.status == 200:
                                data = json.loads(body_text)
                                candidates = data.get("candidates") or []
                                if candidates:
                                    parts = (candidates[0].get("content") or {}).get("parts") or []
                                    text = "".join(
                                        str(p.get("text") or "")
                                        for p in parts
                                        if isinstance(p, dict) and not p.get("thought", False)
                                    ).strip()
                                    if text:
                                        return text
                            else:
                                last_error = f"[{route_label} | {model_name}] HTTP {resp.status}: {body_text[:120]}"
                                if resp.status in (401, 403):
                                    break
                    except Exception as exc:
                        last_error = f"[{route_label} | {model_name}] {type(exc).__name__}: {exc}"
                        continue
        except Exception as exc:
            last_error = f"[{route_label}] {type(exc).__name__}: {exc}"
            continue
        finally:
            if not connector.closed:
                await connector.close()

    raise RuntimeError(f"Не удалось связаться с Gemini API ({last_error})")


def _clean_markdown_code(raw_text: str) -> str:
    return _extract_python_code(raw_text)


def _verify_code_syntax_and_import(target_file: Path, new_code: str) -> Tuple[bool, str]:
    try:
        tree = ast.parse(new_code, filename=str(target_file))
        compile(tree, filename=str(target_file), mode="exec")
    except SyntaxError as syn_err:
        return False, f"Синтаксическая ошибка (строка {syn_err.lineno}): {syn_err.msg}"

    temp_test_file: Optional[Path] = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            suffix=".py",
            prefix="heal_check_",
            dir=LOGS_DIR,
            encoding="utf-8",
            delete=False,
        ) as tmp_f:
            tmp_f.write(new_code)
            temp_test_file = Path(tmp_f.name)
        py_compile.compile(str(temp_test_file), doraise=True)
    except Exception as exc:
        return False, f"Ошибка компиляции байт-кода: {exc}"
    finally:
        if temp_test_file is not None and temp_test_file.exists():
            temp_test_file.unlink(missing_ok=True)
    return True, "OK"


def _resolve_target_file(target_file: Union[Path, str]) -> Optional[Path]:
    raw_path = Path(str(target_file))
    base_resolved = BASE_DIR.resolve()
    try:
        resolved = raw_path.resolve() if raw_path.is_absolute() else (base_resolved / raw_path).resolve()
    except OSError:
        return None
    if resolved == base_resolved or not resolved.is_relative_to(base_resolved):
        return None
    return resolved


async def auto_repair_file(target_file: Union[Path, str], error_context: str) -> Tuple[bool, str]:
    resolved_target = _resolve_target_file(target_file)
    if resolved_target is None or not resolved_target.exists() or not resolved_target.is_file():
        return False, f"Файл не найден: {target_file}"

    original_code = resolved_target.read_text(encoding="utf-8")
    now_str = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_file = BACKUPS_DIR / f"{resolved_target.stem}_{now_str}.bak"
    shutil.copy2(resolved_target, backup_file)

    prompt = (
        "Ты — инженер автопочинки Python-кода в агенте маркетплейсов YaSoKo (Ozon + WB).\n"
        f"В модуле {resolved_target.name} произошел сбой.\n\n"
        f"ОШИБКА И СТЕК:\n{error_context}\n\n"
        f"ТЕКУЩИЙ КОД ФАЙЛА {resolved_target.name}:\n{original_code}\n\n"
        "ЗАДАЧА:\n"
        "1. Исправь ошибку, сохранив существующие сигнатуры функций.\n"
        "2. Верни ТОЛЬКО валидный полный код Python для этого файла без вводных слов и пояснений.\n"
    )
    try:
        raw_fix = await _ask_gemini_fix(prompt)
        clean_code = _clean_markdown_code(raw_fix)
        if not clean_code or len(clean_code) < 50:
            return False, "ИИ вернул пустой ответ."
        valid, check_msg = _verify_code_syntax_and_import(resolved_target, clean_code)
        if not valid:
            return False, f"Код не прошел валидацию: {check_msg}"
        tmp_target = resolved_target.with_suffix(".tmp")
        tmp_target.write_text(clean_code, encoding="utf-8")
        tmp_target.replace(resolved_target)
        return True, f"Файл успешно восстановлен. Резервная копия: {backup_file.name}"
    except Exception as exc:
        if backup_file.exists():
            shutil.copy2(backup_file, resolved_target)
        return False, f"Исключение при автопочинке: {exc}"


async def run_full_self_check_and_heal() -> Dict[str, Any]:
    from services.common.ai_advisory_board import check_gemini_api_health
    from services.common.orders_report import sync_and_generate_orders_report
    from services.ozon.seller_api import OzonSellerAPI
    from services.wb.statistics_api import WBStatisticsAPI

    ozon = OzonSellerAPI()
    wb = WBStatisticsAPI()
    issues: List[Dict[str, Any]] = []
    healed: List[str] = []
    healed_count = 0

    try:
        await ozon.fetch_stocks_and_prices()
    except Exception:
        issues.append(
            {
                "service": "Ozon Seller API",
                "file": BASE_DIR / "services" / "ozon" / "seller_api.py",
                "error": traceback.format_exc(),
            }
        )

    try:
        await wb.fetch_stocks_and_prices()
        await wb.fetch_wb_tariffs()
    except Exception:
        issues.append(
            {
                "service": "Wildberries API",
                "file": BASE_DIR / "services" / "wb" / "statistics_api.py",
                "error": traceback.format_exc(),
            }
        )

    try:
        ai_stat = await check_gemini_api_health()
        if not ai_stat.get("ok"):
            issues.append(
                {
                    "service": "Gemini AI API",
                    "file": BASE_DIR / "services" / "common" / "ai_advisory_board.py",
                    "error": f"Health check failed: {ai_stat.get('detail')}",
                }
            )
    except Exception:
        issues.append(
            {
                "service": "Gemini AI API",
                "file": BASE_DIR / "services" / "common" / "ai_advisory_board.py",
                "error": traceback.format_exc(),
            }
        )

    try:
        await sync_and_generate_orders_report(ozon, wb, days=1)
    except Exception:
        issues.append(
            {
                "service": "Orders Report Engine",
                "file": BASE_DIR / "services" / "common" / "orders_report.py",
                "error": traceback.format_exc(),
            }
        )

    for item in issues:
        target_path = Path(item["file"])
        success, msg = await auto_repair_file(target_path, item["error"])
        safe_service = html.escape(str(item["service"]))
        safe_name = html.escape(target_path.name)
        safe_msg = html.escape(str(msg))
        if success:
            healed_count += 1
            healed.append(f"🟢 {safe_service} ({safe_name}): {safe_msg}")
        else:
            healed.append(f"🔴 {safe_service} ({safe_name}): {safe_msg}")

    now_str = datetime.now().strftime("%d.%m.%Y %H:%M:%S")
    return {
        "timestamp": now_str,
        "total_issues": len(issues),
        "healed_count": healed_count,
        "details": healed,
    }
