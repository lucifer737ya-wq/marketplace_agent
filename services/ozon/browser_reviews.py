import logging
import hashlib
import os
import re
import json
import time
import asyncio
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Set, Tuple
from config import LOGS_DIR, settings
from services.common.storage import write_json
from zoneinfo import ZoneInfo

# =============================================================================
# Константы и калиброванные координаты (viewport 1440x900)
# =============================================================================
BROWSER_DATA_DIR = LOGS_DIR / "ozon_browser_profile"
TRACKER_FILE = LOGS_DIR / "replies_tracker.json"
MAX_DAILY_REPLIES = 50
VIEWPORT = {"width": 1440, "height": 900}

REVIEWS_URL = "https://seller.ozon.ru/app/reviews"
QUESTIONS_URL = "https://seller.ozon.ru/app/reviews/questions"

PORTAL_SEL = "#ui-kit-side-page-portal-container"
DIALOG_SEL = 'div[role="dialog"]'

# --- ОТЗЫВЫ ---
REV_CLICK_X = 698
REV_SKU_PT = (413, 161)
REV_STAR_PT = (271, 299)
REV_INPUT_PT = (377, 737)
REV_SEND_PT = (1147, 739)
REV_CLOSE_PT = (1179, 23)
REV_MODAL_CLOSE_PT = (1165, 125)

# --- ВОПРОСЫ (НЕ ТРОГАЕМ — РАБОТАЮТ ИДЕАЛЬНО) ---
Q_CLICK_X = 898
Q_INPUT_PT = (916, 636)
Q_CLOSE_PT = (1416, 21)

STATUS_WORDS = {
    "получен", "отменён", "отменен", "доставлен", "в пути",
    "новый", "просмотрен", "обработан", "виден всем"
}

DIRECT_BROWSER_ARGS = [
    "--no-proxy-server",
    "--proxy-server=direct://",
    "--proxy-bypass-list=*",
    "--disable-blink-features=AutomationControlled",
    "--no-sandbox",
    "--disable-infobars",
    "--disable-dev-shm-usage",
]

MIN_TARGET_DATE = datetime(2026, 11, 13, 0, 0, 0)

_RU_MONTH_PREFIXES: Tuple[Tuple[str, int], ...] = (
    ("янв", 1),
    ("фев", 2),
    ("мар", 3),
    ("апр", 4),
    ("мая", 5),
    ("май", 5),
    ("июн", 6),
    ("июл", 7),
    ("авг", 8),
    ("сен", 9),
    ("окт", 10),
    ("ноя", 11),
    ("дек", 12),
)


def _get_min_target_date() -> datetime:
    raw = str(getattr(settings, "OZON_REVIEWS_MIN_DATE", "") or "").strip()
    if raw:
        try:
            return max(datetime.strptime(raw, "%d.%m.%Y"), MIN_TARGET_DATE)
        except ValueError:
            return MIN_TARGET_DATE
    return MIN_TARGET_DATE


def _resolve_short_year(month: int, day: int, now_dt: datetime) -> int:
    try:
        cand = datetime(now_dt.year, month, day)
        if cand > now_dt + timedelta(days=1):
            return now_dt.year - 1
        return now_dt.year
    except ValueError:
        return now_dt.year


def _is_date_from_nov_13_2026(text: str, *, require_date: bool = False) -> bool:
    """
    Проверяет дату отзыва или вопроса в строке/шторке.
    Берет в работу все записи начиная с 13.11.2026 (включая сегодняшние).
    """
    min_date = _get_min_target_date()
    src = (text or "").strip()
    if not src:
        return not require_date

    now_dt = datetime.now(ZoneInfo(settings.TIMEZONE)).replace(tzinfo=None)

    # 1. Формат ДД.ММ.ГГГГ
    for m_full in re.finditer(r"\b(\d{1,2})\.(\d{1,2})\.(20\d{2})\b", src):
        d, mo, y = int(m_full.group(1)), int(m_full.group(2)), int(m_full.group(3))
        try:
            return datetime(y, mo, d) >= min_date
        except ValueError:
            continue

    # 2. ISO формат ГГГГ-ММ-ДД
    for m_iso in re.finditer(r"\b(20\d{2})-(\d{1,2})-(\d{1,2})(?=T|\b)", src):
        y, mo, d = int(m_iso.group(1)), int(m_iso.group(2)), int(m_iso.group(3))
        try:
            return datetime(y, mo, d) >= min_date
        except ValueError:
            continue

    # 3. Короткий год ДД.ММ.ГГ
    for m_yy in re.finditer(r"\b(\d{1,2})\.(\d{1,2})\.(\d{2})\b", src):
        d, mo, y = int(m_yy.group(1)), int(m_yy.group(2)), 2000 + int(m_yy.group(3))
        try:
            return datetime(y, mo, d) >= min_date
        except ValueError:
            continue

    # 4. Русские названия месяцев ("13 ноября 2026", "12 нояб.", "12 нояб 2026")
    ru_pattern = (
        r"\b(\d{1,2})\s+"
        r"(янв(?:ар[яь])?|фев(?:р(?:ал[яь])?)?|мар(?:та?)?|апр(?:ел[яь])?|ма[йя]|"
        r"июн[яь]?|июл[яь]?|авг(?:уст[а]?)?|сен(?:т(?:ябр[яь])?)?|окт(?:ябр[яь])?|"
        r"ноя(?:б(?:р[яь])?)?|дек(?:абр[яь])?)\.?(?:\s+(20\d{2}))?\b"
    )
    for m_ru in re.finditer(ru_pattern, src, re.IGNORECASE):
        d = int(m_ru.group(1))
        mon_word = m_ru.group(2).lower()
        mo = next((m_num for pref, m_num in _RU_MONTH_PREFIXES if mon_word.startswith(pref)), 0)
        if mo:
            y = int(m_ru.group(3)) if m_ru.group(3) else _resolve_short_year(mo, d, now_dt)
            try:
                return datetime(y, mo, d) >= min_date
            except ValueError:
                continue

    # 5. Формат таблицы Ozon ДД.ММ ЧЧ:ММ
    for m_short in re.finditer(r"\b(\d{1,2})\.(\d{1,2})\s+\d{2}:\d{2}\b", src):
        d, mo = int(m_short.group(1)), int(m_short.group(2))
        y = _resolve_short_year(mo, d, now_dt)
        try:
            return datetime(y, mo, d) >= min_date
        except ValueError:
            continue

    # 6. Относительные обозначения даты
    low = src.lower()
    today_dt = now_dt.replace(hour=0, minute=0, second=0, microsecond=0)
    relative = re.search(r"(\d+)\s+(минут|час)\w*\s+назад", low)
    if relative:
        n = int(relative[1])
        return now_dt - timedelta(minutes=n if relative[2] == 'минут' else n * 60) >= min_date
    if re.search(r"\bсегодня\b|\bтолько что\b", low):
        return today_dt >= min_date
    if re.search(r"\bвчера\b", low):
        return (today_dt - timedelta(days=1)) >= min_date

    return not require_date


async def _close_browser_context_safe(context: Any, timeout: float = 15.0) -> None:
    """Гарантированно закрывает страницы и persistent-контекст Playwright без зависаний."""
    if context is None:
        return
    try:
        await asyncio.wait_for(context.close(), timeout=timeout)
    except Exception as exc:
        logging.getLogger(__name__).warning('Не удалось закрыть контекст Playwright: %s', type(exc).__name__)

QUESTION_SUBTABS = [("Новые", ["Новые", "Новое"]), ("Просмотренные", ["Просмотренные", "Просмотрено"])]


def _clean_text_for_input(text: str) -> str:
    if not text:
        return ""
    paragraphs = [p.strip() for p in text.split("\n") if p.strip()]
    return "\n\n".join(paragraphs)


def _clean_review_text(text: str) -> str:
    t = (text or "").strip()
    if t.lower() in STATUS_WORDS:
        return ""
    for badge in ("Только с оценкой", "С медиа", "Без медиа", "Без комментария", "–", "—", "-"):
        t = t.replace(badge, " ").strip()
    if t.lower() in STATUS_WORDS:
        return ""
    return t


def _get_direct_env() -> Dict[str, str]:
    clean_env = os.environ.copy()
    for var in ("http_proxy", "https_proxy", "all_proxy", "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY"):
        clean_env.pop(var, None)
    return clean_env


def _load_tracker() -> Dict[str, Any]:
    today_str = datetime.now(ZoneInfo(settings.TIMEZONE)).date().isoformat()
    try:
        data = json.loads(TRACKER_FILE.read_bytes())
    except FileNotFoundError:
        data = {}
    if data.get('date') == today_str:
        if type(data.get('replied_count')) is not int or data['replied_count'] < 0:
            raise ValueError('Некорректный счётчик ответов; нужна проверка трекера')
        for key in ('processed_review_ids', 'processed_question_ids'):
            if not isinstance(data.setdefault(key, []), list):
                raise ValueError('Повреждён трекер ответов')
        return data
    return {
        "date": today_str,
        "replied_count": 0,
        "processed_review_ids": [],
        "processed_question_ids": [],
        "bot_start_time": time.time(),
    }


def _save_tracker(data: Dict[str, Any]) -> None:
    write_json(TRACKER_FILE, data)


async def _read_text_at_point(page, x: int, y: int, max_len: int = 260) -> str:
    try:
        text = await page.evaluate(
            r"""([px, py, maxLen]) => {
                const el = document.elementFromPoint(px, py);
                if (!el) return "";
                const cell = el.closest('td, [data-testid*="cell"], [class*="cell"]');
                const host = cell || el.parentElement || el;
                return (((el.innerText || "") + " " + (host.innerText || "")).replace(/\s+/g, " ").trim()).slice(0, maxLen);
            }""",
            [x, y, max_len],
        )
        return (text or "").strip()
    except Exception:
        return ""


async def _js_is_drawer_open(page) -> bool:
    """Безотказная проверка открытого всплывающего окна отзыва по тексту и DOM."""
    try:
        return bool(await page.evaluate(
            r"""() => {
                const isVis = (el) => {
                    if (!el) return false;
                    const s = window.getComputedStyle(el);
                    return s.display !== 'none' && s.visibility !== 'hidden' && Number(s.opacity) > 0.02;
                };

                const allHeadings = Array.from(document.querySelectorAll('h1, h2, h3, h4, div, span'));
                for (const el of allHeadings) {
                    if ((el.innerText || '').trim() === 'Отзыв на товар' && isVis(el)) {
                        const r = el.getBoundingClientRect();
                        if (r.height > 0) return true;
                    }
                }

                const btns = Array.from(document.querySelectorAll('button, [role="button"]'));
                for (const b of btns) {
                    if ((b.innerText || '').trim().toLowerCase().includes('ответить на отзыв') && isVis(b)) {
                        const r = b.getBoundingClientRect();
                        if (r.width > 0 && r.height > 0) return true;
                    }
                }

                const portal = document.querySelector('#ui-kit-side-page-portal-container');
                if (portal && isVis(portal) && portal.innerText && portal.innerText.trim().length > 10) {
                    return true;
                }

                const dialogs = document.querySelectorAll('div[role="dialog"]');
                for (const d of dialogs) {
                    if (isVis(d)) {
                        const r = d.getBoundingClientRect();
                        if (r.width >= 100 && r.height >= 100) return true;
                    }
                }

                return false;
            }"""))
    except Exception:
        return False


_REPLY_NAME = re.compile(r'^Ответить(?: на отзыв)?$', re.I)
_SELLER_ANSWER = '[data-testid*="seller-reply" i], [data-testid*="seller-answer" i], [data-testid*="brand-reply" i]'


async def _item_date(container, *, table_row: bool) -> str:
    """Use the publication date field, never a date mentioned in customer text."""
    return await container.evaluate(r'''(el, row) => {
        if (row) {
            const headings = Array.from(el.closest('table')?.querySelectorAll('thead th') || []);
            const idx = headings.findIndex(h => /^(дата|опубликован)/i.test(h.innerText.trim()));
            if (idx >= 0 && el.cells[idx]) return el.cells[idx].innerText;
        }
        const dates = el.querySelectorAll('time, [data-testid*="review-date" i], [data-testid*="question-date" i]');
        if (dates.length === 1) return dates[0].getAttribute('datetime') || dates[0].innerText;
        if (!row) {
            const label = [...el.querySelectorAll('span,dt,label,div')].find(n => /^Дата (отзыва|вопроса|публикации)[:：]?$/.test(n.innerText.trim()));
            return label?.nextElementSibling?.innerText || '';
        }
        const cells = Array.from(el.cells || []).filter((c, i) => i !== 3 &&
            /^(?:\d{1,2}[. ](?:\d|[а-я])|20\d\d-|сегодня|вчера)/i.test(c.innerText.trim()));
        return cells.length === 1 ? cells[0].innerText : '';
    }''', table_row)


async def _has_seller_reply(drawer) -> bool:
    marker = drawer.locator(f':is({_SELLER_ANSWER}):visible')
    label = drawer.get_by_text(re.compile(r'^(?:Ответ продавца|Ответ бренда|Ответ представителя бренда|Ваш ответ)[:：]?$', re.I))
    return await marker.first.is_visible() or await label.first.is_visible()


async def _submit_review_reply(page, drawer, field, reply: str) -> bool:
    """One send. A cleared input, closed drawer or newly hidden opener alone is not a receipt."""
    from playwright.async_api import TimeoutError as PlaywrightTimeout

    card = await drawer.element_handle()
    send = page.locator(f'{PORTAL_SEL} button:visible, {DIALOG_SEL} button:visible').filter(
        has_text=re.compile(r'^\s*(Отправить(?: ответ)?|Сохранить)\s*$', re.I))
    if await send.count() == 1:
        await send.click()
    elif await send.count() == 0:
        await field.press('Control+Enter')
    else:
        raise RuntimeError('Неоднозначная кнопка отправки ответа')
    try:
        await page.wait_for_function(r'''([card, text]) => {
            const visible = el => el.isConnected && el.getClientRects().length > 0 && getComputedStyle(el).visibility !== 'hidden';
            if (!visible(card) || !/Отзыв на товар/i.test(card.innerText)) return false;
            if ([...document.querySelectorAll('textarea,[contenteditable=true]')].some(visible)) return false;
            if (card.querySelector('[aria-busy=true], [role=alert]')) return false;
            const norm = s => s.replace(/\s+/g, ' ').trim();
            const published = [...card.querySelectorAll('div, span, p')].some(e =>
                visible(e) && norm(e.innerText) === norm(text));
            const opener = [...card.querySelectorAll('button,[role=button]')].some(e =>
                visible(e) && /^ответить( на отзыв)?$/i.test(norm(e.innerText)));
            return published || (!opener && /(?:^|\n)(ответ продавца|ответ бренда|ваш ответ)[:\s]*(?:\n|$)/i.test(card.innerText));
        }''', arg=[card, reply], timeout=8000)
        return True
    except PlaywrightTimeout:
        return False
    finally:
        await card.dispose()


async def _close_review_drawer(page) -> bool:
    drawer = page.locator(f'{DIALOG_SEL}:visible, {PORTAL_SEL}:visible').filter(has_text=re.compile('Отзыв на товар', re.I)).last
    for _ in range(3):
        if not await drawer.is_visible():
            return True
        button = drawer.get_by_role('button', name=re.compile('^(Закрыть|Close)$', re.I))
        if await button.first.is_visible():
            await button.first.click()
        else:
            await page.keyboard.press('Escape')
        await page.wait_for_timeout(250)
        discard = page.get_by_role('button', name=re.compile('^(Не сохранять|Выйти без сохранения)$', re.I))
        if await discard.first.is_visible():
            await discard.first.click()
    return not await drawer.is_visible()


async def _close_question_drawer(page) -> bool:
    if not await _js_is_drawer_open(page):
        return True
    try:
        await page.mouse.click(Q_CLOSE_PT[0], Q_CLOSE_PT[1])
        await asyncio.sleep(0.4)
    except Exception as exc:
        logging.getLogger(__name__).debug('Playwright: %s', type(exc).__name__)
    if not await _js_is_drawer_open(page):
        return True
    for dx, dy in [(-6, 0), (6, 0), (0, -6), (0, 6)]:
        try:
            await page.mouse.click(Q_CLOSE_PT[0] + dx, Q_CLOSE_PT[1] + dy)
            await asyncio.sleep(0.15)
            if not await _js_is_drawer_open(page):
                return True
        except Exception as exc:
            logging.getLogger(__name__).debug('Playwright: %s', type(exc).__name__)
    for _ in range(3):
        await page.keyboard.press("Escape")
        await asyncio.sleep(0.2)
        if not await _js_is_drawer_open(page):
            return True
    try:
        await page.mouse.click(100, 400)
    except Exception as exc:
        logging.getLogger(__name__).debug('Playwright: %s', type(exc).__name__)
    await asyncio.sleep(0.4)
    return not await _js_is_drawer_open(page)


async def _scroll_main_table_down(page) -> bool:
    try:
        prev_height = await page.evaluate("() => document.body.scrollHeight")
        await page.mouse.move(500, 500)
        await page.mouse.wheel(0, 1200)
        await asyncio.sleep(0.6)
        await page.keyboard.press("PageDown")
        await asyncio.sleep(0.8)
        new_height = await page.evaluate("() => document.body.scrollHeight")
        return new_height > prev_height
    except Exception:
        return False


def _extract_sku(*texts: str) -> Optional[str]:
    for t in texts:
        m = re.search(r"Артикул[:\s\n]*(\d{4,5})", t or "", re.I)
        if m:
            return m.group(1)
    for t in texts:
        m = re.search(r"\b(1[0-5]\d{2})\b", t or "")
        if m:
            return m.group(1)
    return None


async def _extract_rating_accurately(page, drawer_text: str, row_text: str) -> Optional[int]:
    m = re.search(r"Оценка[:\s\n]*([1-5])", drawer_text, re.I)
    if m:
        return int(m.group(1))

    m_star = re.search(r"\b([1-5])\s*★", drawer_text)
    if m_star:
        return int(m_star.group(1))

    raw_pt = await _read_text_at_point(page, REV_STAR_PT[0], REV_STAR_PT[1])
    m_pt = re.search(r"\b([1-5])\b", raw_pt)
    if m_pt:
        return int(m_pt.group(1))

    m_row = re.search(r"\b([1-5])\s*★", row_text)
    if m_row:
        return int(m_row.group(1))

    return None


def _extract_review_comment(drawer_text: str) -> str:
    for pat in (
        r"Комментарий[:\s\n]+([^\n]+(?:\n[^\n]+)*?)\n+(?:Фотографии|Видео|Оценки|Ответить|Написать|Ответ на отзыв|Полезно|$)",
        r"Комментарий[:\s\n]+([^\n]+)",
        r"Отзыв\s*\n+(?:Покупатель[^\n]+\n+)?([^\n]+(?:\n[^\n]+)*?)\n+(?:Фотографии|Видео|Оценки|Ответить|Написать|$)",
    ):
        m = re.search(pat, drawer_text)
        if m:
            cand = m.group(1).strip()
            if cand.lower() not in STATUS_WORDS and not re.fullmatch(r"[—–\-−\s·•.]+", cand):
                return cand
    return ""


def _extract_question_text(drawer_text: str, fallback: str) -> str:
    for pat in (
        r"Вопрос\s*\n+([^\n]+)\n+(?:Оценки|Ответ на вопрос|Ответить|Ваш ответ)",
        r"Вопрос(?:\s*о товаре)?\s*\n+([^\n]+)",
    ):
        m = re.search(pat, drawer_text)
        if m:
            cand = m.group(1).strip()
            if len(cand) > 5 and "о товаре" not in cand.lower():
                return cand
    return fallback


async def _click_subtab(page, names: List[str]) -> bool:
    for name in names:
        rx = re.compile(rf"^\s*{re.escape(name)}\b", re.I)
        cands = page.locator('button, [role="tab"], [role="radio"], a, span, div').filter(has_text=rx)
        try:
            count = await cands.count()
            for idx in range(count):
                el = cands.nth(idx)
                if not await el.is_visible():
                    continue
                box = await el.bounding_box()
                if box and box["y"] > 140 and box["height"] < 90:
                    await el.click()
                    await asyncio.sleep(2.5)
                    return True
        except Exception:
            continue
    return False


async def run_interactive_login() -> None:
    from playwright.async_api import async_playwright
    print("\n[Ozon Browser] Запуск браузера без VPN для авторизации...")
    async with async_playwright() as p:
        context = None
        try:
            context = await p.chromium.launch_persistent_context(
                user_data_dir=str(BROWSER_DATA_DIR),
                headless=False,
                args=DIRECT_BROWSER_ARGS + ["--start-maximized"],
                env=_get_direct_env(),
                viewport=None,
            )
            page = context.pages[0] if context.pages else await context.new_page()
            await page.goto(REVIEWS_URL, wait_until="domcontentloaded", timeout=60000)
            while len(context.pages) > 0:
                await asyncio.sleep(1)
        finally:
            await _close_browser_context_safe(context)


# =============================================================================
# МОДУЛЬ: ОТЗЫВЫ (ПРЯМОЙ ПЕРЕХОД ПО КНОПКЕ «ОТВЕТИТЬ НА ОТЗЫВ»)
# =============================================================================
async def _reset_review_filters(page) -> None:
    """The persistent profile may retain the old text/media-only content filter."""
    reset = page.get_by_role('button', name=re.compile(r'^Сбросить (?:все )?фильтры$', re.I)).first
    if await reset.is_visible():
        await reset.click()
        return
    filters = page.get_by_role('button', name=re.compile(r'^Фильтры(?:\s*\d+)?$', re.I)).first
    if not await filters.is_visible():
        return
    await filters.click()
    reset = page.get_by_role('button', name=re.compile(r'^(Сбросить|Очистить)(?: все| всё| фильтры| все фильтры)?$', re.I)).last
    if await reset.is_visible() and await reset.is_enabled():
        await reset.click()
    else:
        content = page.get_by_text('Контент', exact=True).last
        if await content.is_visible():
            await content.click()
        for name in ('Только с оценкой', 'С текстом', 'С медиа'):
            option = page.get_by_role('checkbox', name=name, exact=True)
            if await option.count() == 1 and await option.is_checked():
                await option.uncheck()
    apply = page.get_by_role('button', name='Применить', exact=True).last
    if await apply.is_visible() and await apply.is_enabled():
        await apply.click()
    else:
        await page.keyboard.press('Escape')


async def _review_tab(page, names: list[str]) -> bool:
    for name in names:
        rx = re.compile(r'^' + re.escape(name) + r'(?:\s*[(]?\d+[)]?)?$', re.I)
        for role in (('tab', 'radio') if name == 'Все' else ('tab', 'radio', 'button', 'link')):
            tab = page.get_by_role(role, name=rx)
            if await tab.count() == 1 and await tab.is_visible():
                await tab.click()
                await page.wait_for_timeout(400)
                return True
        if name == 'Все':
            continue  # a generic status label is not evidence of the complete reviews tab
        fallback = page.get_by_text(rx)
        if await fallback.count() == 1 and await fallback.is_visible():
            await fallback.click()
            await page.wait_for_timeout(400)
            return True
    return False


async def _scan_reviews(page, tracker, dry_run: bool, max_per_run: int) -> dict:
    from services.common.buyer_questions import generate_review_reply

    reviews, confirmed, failures = [], 0, []
    attempted = set()
    await _reset_review_filters(page)
    # Prefer the complete list. Otherwise process viewed first so a previous failure is retried.
    if await _review_tab(page, ['Все отзывы', 'Все']):
        tabs = [('Все', [])]
    else:
        tabs = [('Просмотренные', ['Просмотренные', 'Просмотрено']), ('Новые', ['Новые', 'Новое'])]
    for title, names in tabs:
        if names and not await _review_tab(page, names):
            failures.append(f'Не найдена вкладка «{title}»')
            continue
        visited = set()
        last_page = None
        idle = 0
        while idle < 2 and len(reviews) < max_per_run and tracker['replied_count'] < MAX_DAILY_REPLIES:
            rows = page.locator('tbody tr')
            progress = False
            for i in range(await rows.count()):
                if len(reviews) >= max_per_run or tracker['replied_count'] >= MAX_DAILY_REPLIES:
                    break
                row = rows.nth(i)
                if not await row.count():
                    break  # opening a new review may remove its row
                row_text = (await row.inner_text()).strip()
                if not row_text or 'нет отзывов' in row_text.lower():
                    continue
                ident = await row.get_attribute('data-review-id') or await row.get_attribute('data-row-key')
                # A text fingerprint is only a traversal hint, not proof that two reviews are identical.
                key = ident or (i, row_text)
                if key in visited or (ident and ('review:' + ident in tracker['processed_review_ids'] or ident in attempted)):
                    continue
                visited.add(key)
                stamp = await _item_date(row, table_row=True)
                if not _is_date_from_nov_13_2026(stamp):
                    continue
                cells = row.locator('td')
                if await cells.count() < 4:
                    continue
                comment = _clean_review_text(await cells.nth(3).inner_text())
                await cells.nth(3).click()
                drawer = page.locator(f'{PORTAL_SEL}:visible, {DIALOG_SEL}:visible').filter(
                    has_text=re.compile('Отзыв на товар', re.I)).last
                await drawer.wait_for(timeout=5000)
                try:
                    drawer_stamp = await _item_date(drawer, table_row=False)
                    if not _is_date_from_nov_13_2026(drawer_stamp or stamp, require_date=True):
                        continue
                    opener = drawer.get_by_role('button', name=_REPLY_NAME)
                    if not await opener.first.is_visible() or await _has_seller_reply(drawer):
                        continue
                    text = await drawer.inner_text()
                    sku = _extract_sku(text, row_text)
                    stars = await _extract_rating_accurately(page, text, row_text)
                    comment = _extract_review_comment(text) or comment
                    try:
                        reply = _clean_text_for_input(await generate_review_reply(comment or 'Без комментария', stars, sku))
                    except RuntimeError as exc:
                        failures.append(str(exc))
                        continue  # no tracker entry: eligible again under Viewed
                    reviews.append({'subtab': title, 'sku': sku, 'text': comment, 'stars': stars, 'reply': reply})
                    if not dry_run:
                        await opener.first.click()
                        field = page.locator(f'{PORTAL_SEL} textarea:visible, {DIALOG_SEL} textarea:visible, '
                                             f'{PORTAL_SEL} [contenteditable=true]:visible, {DIALOG_SEL} [contenteditable=true]:visible')
                        await field.fill(reply, timeout=5000)
                        if ident:
                            attempted.add(ident)
                        if not await _submit_review_reply(page, drawer, field, reply):
                            problem = 'Публикация не подтверждена в карточке; перепроверка в следующем цикле'
                            if not ident:
                                raise RuntimeError(problem)
                            failures.append(problem)
                            continue
                        confirmed += 1
                        tracker['replied_count'] += 1
                        tracker['processed_review_ids'].append('review:' + ident if ident else
                            'receipt:' + hashlib.sha256((row_text + reply).encode()).hexdigest())
                        _save_tracker(tracker)
                finally:
                    if not await _close_review_drawer(page):
                        raise RuntimeError('Не удалось закрыть карточку отзыва')
                progress = True
                break  # reacquire the live list; indices can shift
            if progress:
                idle = 0
                continue
            before = await rows.all_inner_texts()
            if await _scroll_main_table_down(page) or before != await rows.all_inner_texts():
                idle = 0
            else:
                idle += 1
                if idle == 2:
                    nxt = page.get_by_role('button', name=re.compile('^(Следующая страница|Далее)$', re.I)).first
                    if await nxt.is_visible() and await nxt.is_enabled():
                        page_key = tuple(await rows.all_inner_texts())
                        if page_key == last_page:
                            raise RuntimeError('Пагинация отзывов не продвигается')
                        last_page = page_key
                        await nxt.click()
                        await page.wait_for_timeout(500)
                        # Keep the current page's traversal hints until the next page actually appears.
                        if page_key != tuple(await rows.all_inner_texts()):
                            visited.clear()
                        idle = 0
    return {'status': 'partial' if failures else 'success', 'replied': confirmed, 'reviews': reviews,
            'message': f'Опубликовано ответов: {confirmed}. ' + '; '.join(dict.fromkeys(failures))}


async def auto_reply_unanswered_reviews(headless: bool = True, dry_run: bool = False, max_per_run: int = 1) -> Dict[str, Any]:
    from playwright.async_api import async_playwright

    tracker = _load_tracker()
    starting_count = tracker['replied_count']
    if starting_count >= MAX_DAILY_REPLIES:
        return {'status': 'limit_reached', 'replied': 0, 'message': 'Достигнут дневной лимит'}
    try:
        async with async_playwright() as pw:
            context = await pw.chromium.launch_persistent_context(user_data_dir=str(BROWSER_DATA_DIR),
                headless=headless, args=DIRECT_BROWSER_ARGS, env=_get_direct_env(), viewport=VIEWPORT)
            try:
                page = context.pages[0] if context.pages else await context.new_page()
                await page.goto(REVIEWS_URL, wait_until='domcontentloaded', timeout=60000)
                if 'login' in page.url.lower():
                    return {'status': 'need_auth', 'replied': 0, 'message': 'Необходима авторизация Ozon'}
                await page.wait_for_timeout(1500)
                return await _scan_reviews(page, tracker, dry_run, max_per_run)
            finally:
                await _close_browser_context_safe(context)
    except Exception as exc:
        return {'status': 'error', 'replied': tracker['replied_count'] - starting_count, 'message': str(exc)}


# =============================================================================
# МОДУЛЬ: ВОПРОСЫ (НЕ ТРОГАЕМ — РАБОТАЮТ ИДЕАЛЬНО)
# =============================================================================
async def auto_reply_unanswered_questions(
    headless: bool = True,
    dry_run: bool = False,
    max_per_run: int = 1,
) -> Dict[str, Any]:
    from playwright.async_api import async_playwright
    from services.common.buyer_questions import generate_buyer_question_answer

    tracker = _load_tracker()
    if tracker.get("replied_count", 0) >= MAX_DAILY_REPLIES:
        return {"status": "limit_reached", "replied": 0, "message": f"Достигнут дневной лимит ({MAX_DAILY_REPLIES})."}

    replied_count = 0
    questions_found: List[Dict[str, Any]] = []
    processed_q_keys: Set[str] = set()

    print("[Ozon Questions] Запуск браузерного модуля...")
    try:
        async with async_playwright() as p:
            context = None
            try:
                context = await p.chromium.launch_persistent_context(
                    user_data_dir=str(BROWSER_DATA_DIR),
                    headless=headless,
                    args=DIRECT_BROWSER_ARGS,
                    env=_get_direct_env(),
                    viewport=VIEWPORT,
                )
                page = context.pages[0] if context.pages else await context.new_page()

                print("[Ozon Questions] Открытие раздела вопросов...")
                await page.goto(REVIEWS_URL, wait_until="domcontentloaded", timeout=60000)
                await asyncio.sleep(2.5)

                q_tab = page.locator('a, button, span').filter(has_text=re.compile(r"Вопросы и ответы", re.I)).first
                if await q_tab.count() > 0:
                    await q_tab.click()
                    await asyncio.sleep(3.0)
                else:
                    await page.goto(QUESTIONS_URL, wait_until="domcontentloaded", timeout=45000)
                    await asyncio.sleep(3.0)

                if "login" in page.url.lower():
                    return {"status": "need_auth", "replied": 0, "message": "Сессия устарела. Запустите авторизацию."}

                for sub_title, sub_variants in QUESTION_SUBTABS:
                    if tracker.get("replied_count", 0) >= MAX_DAILY_REPLIES or replied_count >= max_per_run:
                        break

                    await _close_question_drawer(page)
                    print(f"[Ozon Questions] Переход во вкладку «{sub_title}»...")
                    await _click_subtab(page, sub_variants)

                    no_new_rows_count = 0
                    while no_new_rows_count < 3:
                        if tracker.get("replied_count", 0) >= MAX_DAILY_REPLIES or replied_count >= max_per_run:
                            break

                        rows = page.locator("tbody tr")
                        row_count = await rows.count()
                        new_rows_in_pass = 0

                        for i in range(row_count):
                            if tracker.get("replied_count", 0) >= MAX_DAILY_REPLIES or replied_count >= max_per_run:
                                break

                            await _close_question_drawer(page)

                            row = rows.nth(i)
                            row_text = await row.inner_text()
                            row_key = row_text[:120].strip()

                            if row_key in processed_q_keys:
                                continue

                            processed_q_keys.add(row_key)
                            new_rows_in_pass += 1

                            cells = row.locator("td")
                            cell_count = await cells.count()
                            if cell_count < 4:
                                continue

                            if cell_count >= 5:
                                ans_text = (await cells.nth(4).inner_text()).strip()
                                if ans_text != "0":
                                    continue

                            q_cell_text = (await cells.nth(3).inner_text()).strip()
                            if len(q_cell_text) < 4 or "выберите дату" in q_cell_text.lower():
                                continue

                            await row.scroll_into_view_if_needed(timeout=2000)
                            row_box = await row.bounding_box()
                            if not row_box:
                                continue

                            row_stamp = await _item_date(row, table_row=True)
                            if not _is_date_from_nov_13_2026(row_stamp):
                                continue
                            click_y = int(row_box["y"] + row_box["height"] / 2)
                            print(f"\n👉 [Вопрос] X={Q_CLICK_X}, Y={click_y}: «{q_cell_text[:50]}...»")
                            await page.mouse.click(Q_CLICK_X, click_y)
                            await asyncio.sleep(2.0)

                            if not await _js_is_drawer_open(page):
                                await page.mouse.click(Q_CLICK_X, click_y)
                                await asyncio.sleep(2.0)

                            drawer = page.locator(f"{PORTAL_SEL}:visible, {DIALOG_SEL}:visible").first
                            drawer_text = await drawer.inner_text() if await drawer.count() > 0 else ""

                            # Keep the existing question UI; require a publication date before generating/sending.
                            stamp = await _item_date(drawer, table_row=False) if await drawer.count() else ""
                            stamp = stamp or row_stamp
                            if not _is_date_from_nov_13_2026(stamp, require_date=True):
                                await _close_question_drawer(page)
                                continue
                            sku_detected = _extract_sku(drawer_text, q_cell_text)
                            q_full_text = _extract_question_text(drawer_text, fallback=q_cell_text)
                            q_id = f"{q_full_text[:35]}_{sku_detected or 'any'}"

                            if q_id in tracker.get("processed_question_ids", []):
                                print(f"[Ozon Questions] Вопрос уже обрабатывался: «{q_full_text[:30]}...»")
                                await _close_question_drawer(page)
                                continue

                            reply_raw = await generate_buyer_question_answer(question_text=q_full_text, sku=sku_detected)
                            cleaned_reply = _clean_text_for_input(reply_raw)

                            print(f"📦 [Товар / Артикул]: {sku_detected or 'YaSoKo'}")
                            print(f"❓ [Вопрос]: «{q_full_text}»")
                            print(f"💬 [Сгенерированный ответ]:\n{cleaned_reply}\n")

                            questions_found.append({
                                "subtab": sub_title,
                                "question": q_full_text,
                                "sku": sku_detected,
                                "reply": cleaned_reply,
                            })

                            try:
                                await page.mouse.move(750, 450)
                                for _ in range(6):
                                    await page.mouse.wheel(0, 600)
                                    await asyncio.sleep(0.1)
                                for _ in range(3):
                                    await page.keyboard.press("PageDown")
                                    await asyncio.sleep(0.1)
                            except Exception as exc:
                                logging.getLogger(__name__).debug('Playwright: %s', type(exc).__name__)

                            print(f"👉 [Клик в поле ввода вопроса ({Q_INPUT_PT[0]}, {Q_INPUT_PT[1]})]")
                            await page.mouse.click(Q_INPUT_PT[0], Q_INPUT_PT[1])
                            await asyncio.sleep(0.5)

                            field = page.locator(
                                f"{PORTAL_SEL} textarea, {PORTAL_SEL} [contenteditable='true'], "
                                f"{DIALOG_SEL} textarea, {DIALOG_SEL} [contenteditable='true']"
                            ).last
                            if await field.count() > 0 and await field.is_visible():
                                try:
                                    await field.fill(cleaned_reply)
                                except Exception:
                                    await page.keyboard.insert_text(cleaned_reply)
                            else:
                                await page.keyboard.insert_text(cleaned_reply)

                            await asyncio.sleep(1.0)

                            if dry_run:
                                print(f"ℹ️ [Dry Run] Предпросмотр завершён — закрываю шторку по крестику ({Q_CLOSE_PT[0]}, {Q_CLOSE_PT[1]})...")
                                await _close_question_drawer(page)
                                continue

                            send_rx = re.compile(r"Отправить ответ|Отправить|Сохранить", re.I)
                            btn = drawer.locator("button").filter(has_text=send_rx).first
                            if await btn.count() > 0 and await btn.is_visible():
                                await btn.click()
                                await asyncio.sleep(4.0)
                                print("✅ [Успех] Ответ на вопрос отправлен!")
                                tracker["replied_count"] = tracker.get("replied_count", 0) + 1
                                replied_count += 1
                                tracker.setdefault("processed_question_ids", []).append(q_id)
                                _save_tracker(tracker)
                            else:
                                print("⚠️ Не найдена кнопка отправки ответа на вопрос")

                            await _close_question_drawer(page)

                        table_scrolled = await _scroll_main_table_down(page)
                        if new_rows_in_pass == 0 and not table_scrolled:
                            no_new_rows_count += 1
                        else:
                            no_new_rows_count = 0

                try:
                    await page.screenshot(path=str(LOGS_DIR / "ozon_questions_run.png"))
                except Exception as exc:
                    logging.getLogger(__name__).debug('Playwright: %s', type(exc).__name__)
            finally:
                await _close_browser_context_safe(context)

            return {
                "status": "success",
                "replied": replied_count,
                "questions": questions_found,
                "message": f"Найдено вопросов: {len(questions_found)}, отправлено ответов: {replied_count}.",
            }
    except Exception as exc:
        print(f"❌ [Ozon Questions Error]: {exc}")
        return {"status": "error", "replied": replied_count, "message": str(exc)}