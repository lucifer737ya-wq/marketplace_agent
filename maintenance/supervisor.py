import ast
import shutil
import aiohttp
from datetime import datetime
from pathlib import Path
from typing import Tuple, Optional, Dict
from config import settings, BASE_DIR
from services.common.ai_advisory_board import _get_available_models, PROXY_CANDIDATES

BACKUPS_DIR = BASE_DIR / "backups"
BACKUPS_DIR.mkdir(parents=True, exist_ok=True)

PROJECT_FILES_DESCRIPTIONS: Dict[str, str] = {
    "main.py": "Точка входа и запуск агента",
    "worker.py": "Главный планировщик задач (опрос цен, рекламы, отзывов каждые 30 мин)",
    "config.py": "Настройки окружения, API-ключи и пути",
    "products_catalog.py": "Матрица гелей, баз и топов (связка артикулов Ozon и WB)",
    "bot/handlers/callbacks.py": "Обработка кнопок «Подтвердить/Отклонить» для цен Ozon и WB",
    "bot/handlers/patch_fsm.py": "Диалог редактирования кода через Telegram",
    "services/ozon/seller_api.py": "API Ozon: остатки FBO/FBS, цены, финансы и акции",
    "services/ozon/browser_reviews.py": "Playwright-автоответчик на отзывы и вопросы Ozon",
    "services/wb/statistics_api.py": "API Wildberries: остатки FBO/FBS, цены, эквайринг и отчеты",
    "services/common/orders_report.py": "Сводный финансовый P&L отчет Ozon + WB",
    "services/common/price_auditor.py": "Аудит и расчет оптимальных цен (Ozon 52% > 300₽ и WB FBO/FBS)",
    "services/common/ad_recommendations.py": "Рекомендации и еженедельный отчет по рекламе Ozon и WB",
    "services/common/buyer_questions.py": "Генератор ИИ-ответов технолога на отзывы и вопросы",
    "services/common/ai_advisory_board.py": "Маршрутизатор запросов к моделям Gemini",
}


def get_project_tree_overview() -> str:
    lines = []
    for rel_path, desc in PROJECT_FILES_DESCRIPTIONS.items():
        exists_mark = "🟢" if (BASE_DIR / rel_path.replace("/", "\\")).exists() else "⚪️"
        lines.append(f"{exists_mark} <code>{rel_path}</code> — {desc}")
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


async def apply_code_patch(instruction: str, target_file_rel: Optional[str] = None) -> Tuple[bool, str]:
    api_key = str(getattr(settings, "GEMINI_API_KEY", "") or "").strip()
    if not api_key:
        return False, "GEMINI_API_KEY не задан в .env"

    target_file: Optional[Path] = None
    if target_file_rel:
        cand = BASE_DIR / target_file_rel.strip().replace("/", "\\")
        if cand.exists() and cand.is_file():
            target_file = cand

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

    clean_code = ""
    last_err = ""
    timeout = aiohttp.ClientTimeout(total=120)

    for _, proxy_url, trust_env in PROXY_CANDIDATES:
        try:
            async with aiohttp.ClientSession(trust_env=trust_env, timeout=timeout) as session:
                models = await _get_available_models(session, api_key, proxy_url)
                for m_item in models:
                    m_str = (m_item if isinstance(m_item, str) else getattr(m_item, "name", str(m_item))).replace("models/", "").strip()
                    url = f"https://generativelanguage.googleapis.com/v1beta/models/{m_str}:generateContent?key={api_key}"
                    payload = {
                        "contents": [{"parts": [{"text": prompt}]}],
                        "generationConfig": {"temperature": 0.15, "maxOutputTokens": 16384},
                    }
                    try:
                        async with session.post(url, json=payload, proxy=proxy_url) as resp:
                            if resp.status == 200:
                                data = await resp.json()
                                cands = data.get("candidates") or []
                                if cands:
                                    parts = cands[0].get("content", {}).get("parts") or []
                                    txt = "".join(p.get("text", "") for p in parts if not p.get("thought", False)).strip()
                                    if txt:
                                        clean_code = txt
                                        break
                            else:
                                last_err = f"HTTP {resp.status}"
                    except Exception as e:
                        last_err = str(e)
                        continue
                if clean_code:
                    break
        except Exception as e:
            last_err = str(e)
            continue

    if not clean_code:
        return False, f"Модели Gemini не ответили ({last_err})"

    if "```" in clean_code:
        blocks = clean_code.split("```")
        for b in blocks:
            if b.startswith("python"):
                clean_code = b[6:].strip()
                break
            elif "import " in b or "def " in b:
                clean_code = b.strip()
                break

    try:
        ast.parse(clean_code, filename=str(target_file))
    except SyntaxError as e:
        return False, f"Синтаксическая ошибка в сгенерированном коде (строка {e.lineno}): {e.msg}"

    backup_path = BACKUPS_DIR / f"{target_file.stem}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.bak"
    shutil.copy2(target_file, backup_path)
    target_file.write_text(clean_code, encoding="utf-8")
    return True, f"Файл <code>{target_file.name}</code> успешно обновлен!\nРезервная копия: <code>{backup_path.name}</code>"