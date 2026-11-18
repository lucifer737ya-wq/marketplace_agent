import ast
import html
import json
import re
import shutil
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional, Tuple

import aiohttp

from config import BACKUPS_DIR, BASE_DIR, settings
from services.common.ai_advisory_board import (
    PROXY_CANDIDATES,
    _extract_model_name,
    _get_available_models,
    _is_supported_text_model,
)

BACKUPS_DIR.mkdir(parents=True, exist_ok=True)

PROJECT_FILES_DESCRIPTIONS: Dict[str, str] = {
    "supervisor.py": "Супервизор перезапуска и мониторинга worker.py",
    "worker.py": "Главный планировщик задач и обработчик Telegram-бота",
    "config.py": "Настройки окружения, API-ключи и пути",
    "products_catalog.py": "Матрица гелей, баз и топов (связка артикулов Ozon и WB)",
    "bot/keyboards.py": "Клавиатура главного меню Telegram-бота",
    "bot/handlers/callbacks.py": "Обработка кнопок «Подтвердить/Отклонить» для цен Ozon и WB",
    "bot/handlers/patch_fsm.py": "Диалог редактирования кода через Telegram",
    "services/ozon/seller_api.py": "API Ozon: остатки FBO/FBS, цены, финансы и акции",
    "services/ozon/browser_reviews.py": "Playwright-автоответчик на отзывы и вопросы Ozon",
    "services/wb/statistics_api.py": "API Wildberries: остатки FBO/FBS, цены, эквайринг и отчеты",
    "services/common/orders_report.py": "Сводный финансовый P&L отчет Ozon + WB",
    "services/common/price_auditor.py": "Аудит и расчет оптимальных цен (Ozon 52% > 300₽ и WB FBO/FBS)",
    "services/common/ad_recommendations.py": "Рекомендации и еженедельный отчет по рекламе Ozon и WB",
    "services/common/buyer_questions.py": "Генератор ИИ-ответов технолога на отзывы и вопросы",
    "services/common/ai_advisory_board.py": "Маршрутизатор запросов к моделям Gemini и Совет ИИ",
}


def _resolve_safe_project_path(rel_path: str) -> Optional[Path]:
    """Безопасно преобразует относительный путь проекта без привязки к ОС и path traversal."""
    raw = str(rel_path or "").strip()
    if not raw:
        return None
    base_resolved = BASE_DIR.resolve()
    if raw.startswith("/") or re.match(r"^[a-zA-Z]:[\\/]", raw):
        try:
            abs_candidate = Path(raw).resolve()
        except OSError:
            return None
        if abs_candidate != base_resolved and abs_candidate.is_relative_to(base_resolved):
            return abs_candidate
        return None
    normalized = raw.replace("\\", "/")
    try:
        candidate = (base_resolved / normalized).resolve()
    except OSError:
        return None
    if candidate == base_resolved or not candidate.is_relative_to(base_resolved):
        return None
    return candidate


def get_project_tree_overview() -> str:
    lines = []
    for rel_path, desc in PROJECT_FILES_DESCRIPTIONS.items():
        resolved = _resolve_safe_project_path(rel_path)
        exists_mark = "🟢" if (resolved is not None and resolved.exists()) else "⚪️"
        lines.append(f"{exists_mark} <code>{html.escape(rel_path)}</code> — {html.escape(desc)}")
    return "\n".join(lines)


def _guess_target_file_by_instruction(instruction: str) -> Optional[Path]:
    """Автоматически определяет файл по тексту жалобы пользователя, если путь не указан."""
    low = instruction.lower()
    if any(k in low for k in ("отзыв", "вопрос", "шторк", "крестик", "браузер", "playwright")):
        return BASE_DIR / "services" / "ozon" / "browser_reviews.py"
    if any(k in low for k in ("кнопк", "подтверд", "external_id", "callback")):
        return BASE_DIR / "bot" / "handlers" / "callbacks.py"
    if any(k in low for k in ("вб", "wb", "эквайринг", "скидк")) and "отчет" not in low:
        return BASE_DIR / "services" / "wb" / "statistics_api.py"
    if any(k in low for k in ("отчет", "p&l", "прибыл", "себестоим")):
        return BASE_DIR / "services" / "common" / "orders_report.py"
    if any(k in low for k in ("реклам", "трафарет", "арк", "кампан")):
        return BASE_DIR / "services" / "common" / "ad_recommendations.py"
    if any(k in low for k in ("аудит цен", "52%", "300", "оптимальн")):
        return BASE_DIR / "services" / "common" / "price_auditor.py"
    return None


def _extract_python_code(raw_text: str) -> str:
    """Извлекает код Python из markdown-блока (игнорируя вводный текст до ```)."""
    text = (raw_text or "").strip()
    if "```" not in text:
        return text

    parts = text.split("```")
    fenced_blocks = parts[1::2]
    for block in fenced_blocks:
        stripped = block.strip()
        if re.match(r"^(?:python|py)\b", stripped, re.IGNORECASE):
            return re.sub(r"^(?:python|py)\b[^\n]*\n?", "", stripped, count=1, flags=re.IGNORECASE).strip()

    for block in fenced_blocks:
        stripped = block.strip()
        if any(tok in stripped for tok in ("import ", "from ", "def ", "class ", "async def ")):
            return re.sub(r"^[a-zA-Z0-9_-]+\n", "", stripped, count=1).strip() if "\n" in stripped else stripped

    if fenced_blocks:
        return fenced_blocks[0].strip()
    return text


async def apply_code_patch(instruction: str, target_file_rel: Optional[str] = None) -> Tuple[bool, str]:
    api_key = str(getattr(settings, "GEMINI_API_KEY", "") or "").strip()
    if not api_key:
        return False, "GEMINI_API_KEY не задан в .env"

    target_file: Optional[Path] = None
    if target_file_rel:
        cand = _resolve_safe_project_path(target_file_rel)
        if cand is not None and cand.exists() and cand.is_file():
            target_file = cand
        elif cand is None:
            return False, "Недопустимый путь к файлу за пределами проекта."

    if not target_file:
        guessed = _guess_target_file_by_instruction(instruction)
        if guessed and guessed.exists():
            target_file = guessed

    if not target_file:
        return False, "Не удалось автоматически угадать файл. Пожалуйста, укажите путь в начале сообщения (например: services/wb/statistics_api.py ...)"

    current_code = target_file.read_text(encoding="utf-8")
    prompt = (
        f"Ты — Senior Python разработчик проекта marketplace_agent.\n"
        f"ЦЕЛЕВОЙ ФАЙЛ: {target_file.name}\n"
        f"ЗАДАЧА ПОЛЬЗОВАТЕЛЯ:\n{instruction}\n\n"
        f"ТЕКУЩИЙ КОД ФАЙЛА:\n```python\n{current_code}\n```\n\n"
        f"ТРЕБОВАНИЯ:\n"
        f"1. Верни ПОЛНЫЙ исправленный код файла {target_file.name} целиком без пропусков и сокращений.\n"
        f"2. Не пиши никаких вступлений — только валидный блок ```python ... ```."
    )

    raw_response = ""
    last_err = "Нет ответа от моделей Gemini"
    timeout = aiohttp.ClientTimeout(total=120, connect=15, sock_connect=15, sock_read=100)

    for _, proxy_url, trust_env in PROXY_CANDIDATES:
        connector = aiohttp.TCPConnector(limit=4, ssl=True, enable_cleanup_closed=True)
        try:
            async with aiohttp.ClientSession(
                connector=connector,
                connector_owner=True,
                trust_env=trust_env,
                timeout=timeout,
            ) as session:
                models = await _get_available_models(session, api_key, proxy_url)
                model_names = list(
                    dict.fromkeys(
                        _extract_model_name(m)
                        for m in models
                        if _is_supported_text_model(_extract_model_name(m))
                    )
                )
                for m_str in model_names:
                    url = f"https://generativelanguage.googleapis.com/v1beta/models/{m_str}:generateContent?key={api_key}"
                    payload = {
                        "contents": [{"parts": [{"text": prompt}]}],
                        "generationConfig": {"temperature": 0.15, "maxOutputTokens": 16384},
                    }
                    try:
                        async with session.post(url, json=payload, proxy=proxy_url) as resp:
                            body_text = await resp.text(encoding="utf-8", errors="replace")
                            if resp.status == 200:
                                data = json.loads(body_text)
                                cands = data.get("candidates") or []
                                if cands:
                                    parts = (cands[0].get("content") or {}).get("parts") or []
                                    txt = "".join(
                                        str(p.get("text") or "")
                                        for p in parts
                                        if isinstance(p, dict) and not p.get("thought", False)
                                    ).strip()
                                    if txt:
                                        raw_response = txt
                                        break
                            else:
                                last_err = f"{m_str}: HTTP {resp.status}"
                                if resp.status in (401, 403):
                                    break
                    except Exception as e:
                        last_err = f"{m_str}: {e}"
                        continue
                if raw_response:
                    break
        except Exception as e:
            last_err = str(e)
            continue
        finally:
            if not connector.closed:
                await connector.close()

    if not raw_response:
        return False, f"Модели Gemini не ответили ({last_err})"

    clean_code = _extract_python_code(raw_response)
    if not clean_code or len(clean_code.strip()) < 20:
        return False, "ИИ вернул пустой или слишком короткий код."

    try:
        tree = ast.parse(clean_code, filename=str(target_file))
        compile(tree, filename=str(target_file), mode="exec")
    except SyntaxError as e:
        return False, f"Синтаксическая ошибка в сгенерированном коде (строка {e.lineno}): {e.msg}"

    backup_path = BACKUPS_DIR / f"{target_file.stem}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.bak"
    shutil.copy2(target_file, backup_path)
    tmp_target = target_file.with_suffix(".tmp")
    tmp_target.write_text(clean_code, encoding="utf-8")
    tmp_target.replace(target_file)
    return (
        True,
        f"Файл <code>{html.escape(target_file.name)}</code> успешно обновлен!\n"
        f"Резервная копия: <code>{html.escape(backup_path.name)}</code>",
    )
