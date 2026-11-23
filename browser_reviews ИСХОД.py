import os
import re
import json
import time
import asyncio
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional, Set, Tuple
from config import BASE_DIR, LOGS_DIR  # noqa: F401

# =============================================================================
# Константы и калиброванные координаты (viewport 1440x900)
# =============================================================================
BROWSER_DATA_DIR = LOGS_DIR / "ozon_browser_profile"
BROWSER_DATA_DIR.mkdir(parents=True, exist_ok=True)
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

def _is_date_from_nov_13_2026(text: str) -> bool:
    """
    Проверяет дату отзыва или вопроса в строке/шторке.
    Берет в работу все записи начиная с 13.11.2026 (включая сегодняшние).
    """
    # Формат ДД.ММ.ГГГГ
    m_full = re.search(r"\b(\d{2})\.(\d{2})\.(20\d{2})\b", text)
    if m_full:
        d, mo, y = int(m_full.group(1)), int(m_full.group(2)), int(m_full.group(3))
        try:
            return datetime(y, mo, d) >= MIN_TARGET_DATE
        except Exception:
            return True

    # Формат таблицы Ozon ДД.ММ ЧЧ:ММ
    m_short = re.search(r"\b(\d{2})\.(\d{2})\s+\d{2}:\d{2}\b", text)
    if m_short:
        d, mo = int(m_short.group(1)), int(m_short.group(2))
        try:
            return datetime(2026, mo, d) >= MIN_TARGET_DATE
        except Exception:
            return True

    return True

REVIEW_SUBTABS = [("Новые", ["Новые", "Новое"]), ("Просмотренные", ["Просмотренные", "Просмотрено"])]
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
    today_str = datetime.now().strftime("%Y-%m-%d")
    if TRACKER_FILE.exists():
        try:
            data = json.loads(TRACKER_FILE.read_text(encoding="utf-8"))
            if data.get("date") == today_str:
                data.setdefault("processed_review_ids", [])
                data.setdefault("processed_question_ids", [])
                return data
        except Exception:
            pass
    return {
        "date": today_str,
        "replied_count": 0,
        "processed_review_ids": [],
        "processed_question_ids": [],
        "bot_start_time": time.time(),
    }


def _save_tracker(data: Dict[str, Any]) -> None:
    try:
        TRACKER_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass


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


async def _blur_active(page) -> None:
    try:
        await page.evaluate("() => { const a = document.activeElement; if (a && a.blur) a.blur(); }")
    except Exception:
        pass


def _sector_points(cx: int, cy: int, radius: int = 36, step: int = 8) -> List[Tuple[int, int]]:
    x_min, x_max = 8, VIEWPORT["width"] - 18
    y_min, y_max = 8, VIEWPORT["height"] - 8
    seen: set = set()
    pts: List[Tuple[int, int]] = []

    def _add(x: int, y: int) -> None:
        x = min(max(x, x_min), x_max)
        y = min(max(y, y_min), y_max)
        if (x, y) not in seen:
            seen.add((x, y))
            pts.append((x, y))

    _add(cx, cy)
    r = step
    while r <= radius:
        ring: List[Tuple[int, int]] = []
        dx = -r
        while dx <= r:
            dy = -r
            while dy <= r:
                if max(abs(dx), abs(dy)) == r:
                    ring.append((dx, dy))
                dy += step
            dx += step
        ring.sort(key=lambda d: d[0] * d[0] + d[1] * d[1])
        for dx, dy in ring:
            _add(cx + dx, cy + dy)
        r += step
    return pts


async def _confirm_discard_if_shown(page) -> None:
    try:
        await page.evaluate(r"""() => {
            const btns = document.querySelectorAll('button');
            for (const b of btns) {
                const t = (b.innerText || '').trim().toLowerCase();
                if (['да', 'закрыть', 'отменить', 'не сохранять', 'выйти'].includes(t) ||
                    t.includes('без сохранения') || t.includes('не сохранять') || t.includes('отменить')) {
                    if (!b.disabled) { b.click(); return; }
                }
            }
        }""")
    except Exception:
        pass


async def _open_review_row(page, row, click_x: int, click_y: int) -> bool:
    try:
        cells = row.locator("td")
        if await cells.count() >= 4:
            rev_cell = cells.nth(3)
            await rev_cell.scroll_into_view_if_needed(timeout=1500)
            target = rev_cell.locator("span, div, a, p").first
            if await target.count() > 0 and await target.is_visible():
                await target.click()
            else:
                await rev_cell.click()
            await asyncio.sleep(1.2)
            if await _js_is_drawer_open(page):
                return True
    except Exception:
        pass

    pts = _sector_points(click_x, click_y, radius=24, step=8)
    for (px, py) in pts:
        try:
            await page.mouse.click(px, py)
            await asyncio.sleep(0.35)
            if await _js_is_drawer_open(page):
                return True
        except Exception:
            pass
    return await _js_is_drawer_open(page)


async def _click_reply_to_review_button(page) -> bool:
    try:
        btn = page.locator('button, [role="button"]').filter(has_text=re.compile(r"^Ответить на отзыв$", re.I)).first
        if await btn.count() > 0 and await btn.is_visible():
            await btn.click()
            print("👉 [Нажата кнопка «Ответить на отзыв» через селектор]")
            await asyncio.sleep(1.2)
            return True
    except Exception:
        pass

    try:
        clicked = await page.evaluate(r"""() => {
            const btns = Array.from(document.querySelectorAll('button, [role="button"], span, div'));
            for (const b of btns) {
                const t = (b.innerText || '').trim().toLowerCase();
                if (t === 'ответить на отзыв' || t === 'ответить') {
                    b.click();
                    return true;
                }
            }
            return false;
        }""")
        if clicked:
            print("👉 [Нажата кнопка «Ответить на отзыв» через JS]")
            await asyncio.sleep(1.2)
            return True
    except Exception:
        pass

    print("👉 [Нажата кнопка «Ответить на отзыв» по координатам (280, 595)]")
    await page.mouse.click(280, 595)
    await asyncio.sleep(1.2)
    return True


async def _submit_review_reply(page) -> bool:
    """
    Отправка ответа на отзыв:
    1. Нажатие Control+Enter прямо в активном поле ввода.
    2. Поиск кнопки «Отправить» / «Сохранить» в DOM.
    3. Клик по координатам кнопки (1147, 739) и сектору вокруг.
    4. Нажатие Enter на фокусе.
    """
    # 1. Горячие клавиши Control+Enter
    try:
        print("👉 [Отправка ответа: комбинация Control+Enter]")
        await page.keyboard.press("Control+Enter")
        await asyncio.sleep(1.5)
        ta = page.locator("textarea").last
        if await ta.count() == 0 or (await ta.input_value()).strip() == "":
            return True
    except Exception:
        pass

    # 2. Клик по кнопке отправки в DOM
    try:
        send_btn = page.locator(
            f"{PORTAL_SEL} button, {DIALOG_SEL} button, button"
        ).filter(has_text=re.compile(r"^Отправить( ответ)?$|^Сохранить$", re.I)).last
        if await send_btn.count() > 0 and await send_btn.is_visible():
            await send_btn.click()
            print("👉 [Отправка ответа: клик по кнопке «Отправить»]")
            await asyncio.sleep(2.0)
            return True
    except Exception:
        pass

    # 3. JS-клик
    try:
        clicked_js = await page.evaluate(r"""() => {
            const btns = Array.from(document.querySelectorAll('button, [role="button"]'));
            for (let i = btns.length - 1; i >= 0; i--) {
                const t = (btns[i].innerText || '').trim().toLowerCase();
                if (t === 'отправить' || t === 'отправить ответ' || t === 'сохранить') {
                    btns[i].click();
                    return true;
                }
            }
            return false;
        }""")
        if clicked_js:
            print("👉 [Отправка ответа: JS-клик по кнопке]")
            await asyncio.sleep(2.0)
            return True
    except Exception:
        pass

    # 4. Клик по калиброванным координатам (1147, 739)
    print(f"👉 [Отправка ответа: клик по координатам ({REV_SEND_PT[0]}, {REV_SEND_PT[1]})]")
    for (sx, sy) in _sector_points(REV_SEND_PT[0], REV_SEND_PT[1], radius=28, step=7):
        try:
            await page.mouse.click(sx, sy)
            await asyncio.sleep(0.1)
        except Exception:
            pass

    # 5. Резервный Enter
    try:
        await page.keyboard.press("Enter")
    except Exception:
        pass

    await asyncio.sleep(3.0)
    return True


async def _close_review_drawer(page) -> bool:
    """Блокирующее закрытие шторки отзыва."""
    if not await _js_is_drawer_open(page):
        return True

    for round_no in range(1, 6):
        await _blur_active(page)
        await _confirm_discard_if_shown(page)

        # 1. Клик по крестику через DOM
        try:
            await page.evaluate(r"""() => {
                const all = Array.from(document.querySelectorAll('button, svg, [role="button"]'));
                for (const el of all) {
                    const r = el.getBoundingClientRect();
                    if (r.top < 160 && r.left > 1100 && r.left < 1250) {
                        (el.closest('button') || el).click();
                        return;
                    }
                }
            }""")
        except Exception:
            pass
        await asyncio.sleep(0.25)
        if not await _js_is_drawer_open(page):
            print("✅ [Шторка отзыва закрыта через DOM-крестик]")
            return True

        # 2. Клик по крестику окна (1165, 125) и (1179, 23)
        for (cx, cy) in [(REV_MODAL_CLOSE_PT[0], REV_MODAL_CLOSE_PT[1]), (REV_CLOSE_PT[0], REV_CLOSE_PT[1])]:
            try:
                await page.mouse.click(cx, cy)
                await asyncio.sleep(0.1)
            except Exception:
                pass
            if not await _js_is_drawer_open(page):
                print(f"✅ [Шторка отзыва закрыта кликом в крестик ({cx}, {cy})]")
                return True

        # 3. Escape x 3
        for _ in range(3):
            await page.keyboard.press("Escape")
            await asyncio.sleep(0.12)
            if not await _js_is_drawer_open(page):
                print("✅ [Шторка отзыва закрыта клавишей Escape]")
                return True

        # 4. Клик по подложке слева
        for bx in (50, 100, 150):
            try:
                await page.mouse.click(bx, 450)
                await asyncio.sleep(0.1)
            except Exception:
                pass
            if not await _js_is_drawer_open(page):
                print("✅ [Шторка отзыва закрыта кликом по подложке]")
                return True

        await asyncio.sleep(0.3)

    return not await _js_is_drawer_open(page)


async def _close_question_drawer(page) -> bool:
    if not await _js_is_drawer_open(page):
        return True
    try:
        await page.mouse.click(Q_CLOSE_PT[0], Q_CLOSE_PT[1])
        await asyncio.sleep(0.4)
    except Exception:
        pass
    if not await _js_is_drawer_open(page):
        return True
    for dx, dy in [(-6, 0), (6, 0), (0, -6), (0, 6)]:
        try:
            await page.mouse.click(Q_CLOSE_PT[0] + dx, Q_CLOSE_PT[1] + dy)
            await asyncio.sleep(0.15)
            if not await _js_is_drawer_open(page):
                return True
        except Exception:
            pass
    for _ in range(3):
        await page.keyboard.press("Escape")
        await asyncio.sleep(0.2)
        if not await _js_is_drawer_open(page):
            return True
    try:
        await page.mouse.click(100, 400)
    except Exception:
        pass
    await asyncio.sleep(0.4)
    return not await _js_is_drawer_open(page)


async def _apply_reviews_content_filter(page) -> None:
    print("👉 [Фильтр Ozon] Выставляем фильтр: «С текстом» + «С медиа»...")
    try:
        filter_btn = page.locator("button, [role='button'], div").filter(has_text=re.compile(r"^Фильтры$", re.I)).first
        if await filter_btn.count() > 0 and await filter_btn.is_visible():
            await filter_btn.click()
        else:
            for (fx, fy) in _sector_points(355, 425, radius=25, step=8):
                await page.mouse.click(fx, fy)
                await asyncio.sleep(0.15)
                if await page.locator("text=Контент").count() > 0:
                    break

        await asyncio.sleep(1.0)

        content_tab = page.locator("div, span, li, button").filter(has_text=re.compile(r"^Контент$", re.I)).last
        if await content_tab.count() > 0 and await content_tab.is_visible():
            await content_tab.click()
            await asyncio.sleep(0.6)

        await page.evaluate(r"""() => {
            const labels = [...document.querySelectorAll('label, div, span')];
            const toggleOption = (textRegex, shouldCheck) => {
                for (const el of labels) {
                    const txt = (el.innerText || '').trim();
                    if (!textRegex.test(txt)) continue;
                    const row = el.closest('label') || el.parentElement;
                    if (!row) continue;
                    const inp = row.querySelector('input[type="checkbox"]');
                    if (inp) {
                        if (Boolean(inp.checked) !== shouldCheck) {
                            row.click();
                        }
                        return true;
                    }
                }
                return false;
            };
            toggleOption(/^Только с оценкой$/i, false);
            toggleOption(/^С текстом$/i, true);
            toggleOption(/^С медиа$/i, true);
        }""")
        await asyncio.sleep(0.5)

        for opt_name in ("С текстом", "С медиа"):
            opt_loc = page.locator("label, div, span").filter(has_text=re.compile(rf"^{opt_name}$", re.I)).last
            if await opt_loc.count() > 0 and await opt_loc.is_visible():
                try:
                    is_checked = await opt_loc.evaluate(r"""el => {
                        const row = el.closest('label') || el.parentElement;
                        const inp = row ? row.querySelector('input[type="checkbox"]') : null;
                        return inp ? Boolean(inp.checked) : false;
                    }""")
                    if not is_checked:
                        await opt_loc.click()
                        await asyncio.sleep(0.2)
                except Exception:
                    pass

        apply_btn = page.locator("button").filter(has_text=re.compile(r"^Применить$", re.I)).last
        if await apply_btn.count() > 0 and await apply_btn.is_visible():
            await apply_btn.click()
        else:
            await page.evaluate(r"""() => {
                const btns = [...document.querySelectorAll('button')];
                for (const b of btns) {
                    if ((b.innerText || '').trim().toLowerCase() === 'применить') {
                        b.click();
                        break;
                    }
                }
            }""")

        await asyncio.sleep(2.5)
        await page.keyboard.press("Escape")
        await asyncio.sleep(0.5)
        print("✅ [Фильтр Ozon] Фильтр «С текстом» и «С медиа» успешно применен!")
    except Exception as exc:
        print(f"⚠️ [Фильтр Ozon] Ошибка применения фильтра: {exc}")


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


async def _extract_rating_accurately(page, drawer_text: str, row_text: str) -> int:
    try:
        dom_stars = await page.evaluate(r"""() => {
            const all = Array.from(document.querySelectorAll('*'));
            for (const el of all) {
                const txt = (el.innerText || '').trim();
                const m = txt.match(/^Оценка\s*([1-5])(\s*★)?$/i);
                if (m) return parseInt(m[1], 10);
            }
            return null;
        }""")
        if dom_stars:
            return int(dom_stars)
    except Exception:
        pass

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

    return 5


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
        context = await p.chromium.launch_persistent_context(
            user_data_dir=str(BROWSER_DATA_DIR),
            headless=False,
            args=DIRECT_BROWSER_ARGS + ["--start-maximized"],
            env=_get_direct_env(),
            viewport=None,
        )
        try:
            page = context.pages[0] if context.pages else await context.new_page()
            await page.goto(REVIEWS_URL, wait_until="domcontentloaded", timeout=60000)
            while len(context.pages) > 0:
                await asyncio.sleep(1)
        finally:
            try:
                await context.close()
            except Exception:
                pass


# =============================================================================
# МОДУЛЬ: ОТЗЫВЫ (ПРЯМОЙ ПЕРЕХОД ПО КНОПКЕ «ОТВЕТИТЬ НА ОТЗЫВ»)
# =============================================================================
async def auto_reply_unanswered_reviews(
    headless: bool = True,
    dry_run: bool = False,
    max_per_run: int = 1,
) -> Dict[str, Any]:
    from playwright.async_api import async_playwright
    from services.common.buyer_questions import generate_review_reply

    tracker = _load_tracker()
    if tracker.get("replied_count", 0) >= MAX_DAILY_REPLIES:
        return {"status": "limit_reached", "replied": 0, "message": f"Достигнут дневной лимит ({MAX_DAILY_REPLIES})."}

    replied_in_session = 0
    reviews_found: List[Dict[str, Any]] = []
    processed_row_keys: Set[str] = set()

    print("[Ozon Reviews] Запуск браузерного модуля...")
    try:
        async with async_playwright() as p:
            context = await p.chromium.launch_persistent_context(
                user_data_dir=str(BROWSER_DATA_DIR),
                headless=headless,
                args=DIRECT_BROWSER_ARGS,
                env=_get_direct_env(),
                viewport=VIEWPORT,
            )
            try:
                page = context.pages[0] if context.pages else await context.new_page()
                await page.goto(REVIEWS_URL, wait_until="domcontentloaded", timeout=60000)
                await asyncio.sleep(3.5)

                if "login" in page.url.lower():
                    return {"status": "need_auth", "replied": 0, "message": "Сессия устарела. Запустите авторизацию."}

                for sub_title, sub_variants in REVIEW_SUBTABS:
                    if tracker.get("replied_count", 0) >= MAX_DAILY_REPLIES or replied_in_session >= max_per_run:
                        break

                    await _close_review_drawer(page)
                    print(f"[Ozon Reviews] Переход во вкладку «{sub_title}»...")
                    await _click_subtab(page, sub_variants)

                    # Выставляем фильтр Ozon «С текстом» + «С медиа»
                    await _apply_reviews_content_filter(page)

                    no_new_rows_count = 0
                    while no_new_rows_count < 3:
                        if tracker.get("replied_count", 0) >= MAX_DAILY_REPLIES or replied_in_session >= max_per_run:
                            break

                        rows = page.locator("tbody tr")
                        row_count = await rows.count()
                        print(f"[Ozon Reviews] Отфильтрованных отзывов в таблице: {row_count}")
                        new_rows_in_pass = 0

                        for i in range(row_count):
                            if tracker.get("replied_count", 0) >= MAX_DAILY_REPLIES or replied_in_session >= max_per_run:
                                break

                            # Закрываем предыдущую шторку, если она осталась открыта
                            if await _js_is_drawer_open(page):
                                print("⚠️ Предыдущая шторка открыта! Дожимаем закрытие...")
                                while await _js_is_drawer_open(page):
                                    await _close_review_drawer(page)
                                    await asyncio.sleep(0.5)

                            row = rows.nth(i)
                            row_text = (await row.inner_text()).strip()
                            if not row_text or "нет отзывов" in row_text.lower():
                                continue

                            row_key = row_text[:120]
                            if row_key in processed_row_keys:
                                continue

                            processed_row_keys.add(row_key)
                            new_rows_in_pass += 1

                            cells = row.locator("td")
                            cell_count = await cells.count()
                            if cell_count >= 8:
                                ans_str = (await cells.nth(cell_count - 3).inner_text()).strip()
                                if ans_str.isdigit() and int(ans_str) > 0:
                                    continue

                            # Превью отзыва и оценка из строки таблицы
                            row_comment_preview = ""
                            if cell_count > 3:
                                row_comment_preview = _clean_review_text(await cells.nth(3).inner_text())

                            row_stars = 5
                            if cell_count > 4:
                                st_txt = await cells.nth(4).inner_text()
                                m_st = re.search(r"([1-5])", st_txt)
                                if m_st:
                                    row_stars = int(m_st.group(1))

                            await row.scroll_into_view_if_needed(timeout=2000)
                            row_box = await row.bounding_box()
                            if not row_box:
                                continue

                            click_y = int(row_box["y"] + row_box["height"] / 2)
                            print(f"\n👉 [Открытие отзыва #{i} в точке ({REV_CLICK_X}, {click_y})]")

                            if not await _open_review_row(page, row, REV_CLICK_X, click_y):
                                print("⚠️ Окно отзыва не открылось, пропускаю строку")
                                continue

                            await asyncio.sleep(1.8)
                            drawer_text = await page.evaluate(r"""() => {
                                const all = Array.from(document.querySelectorAll('div, [role="dialog"], #ui-kit-side-page-portal-container'));
                                for (const d of all) {
                                    if ((d.innerText || '').includes('Отзыв на товар')) return d.innerText;
                                }
                                return document.body.innerText;
                            }""")

                            # Считывание артикула, оценки и текста отзыва
                            raw_sku_point = await _read_text_at_point(page, REV_SKU_PT[0], REV_SKU_PT[1])
                            sku_detected = _extract_sku(raw_sku_point, drawer_text, row_text)

                            stars = await _extract_rating_accurately(page, drawer_text, row_text)
                            if not stars:
                                stars = row_stars

                            full_comment = _extract_review_comment(drawer_text)
                            if not full_comment or len(full_comment) < 2:
                                full_comment = row_comment_preview

                            rev_id = f"{full_comment[:35]}_{stars}_{sku_detected or 'any'}"
                            if rev_id in tracker.get("processed_review_ids", []):
                                print(f"[Ozon Reviews] Отзыв уже обрабатывался: «{full_comment[:30]}...»")
                                await _close_review_drawer(page)
                                continue

                            prompt_text = full_comment if len(full_comment.strip()) >= 2 else "Покупатель поставил высокую оценку и прикрепил фото/видео маникюра к отзыву."
                            reply_text = await generate_review_reply(review_text=prompt_text, rating=stars, sku=sku_detected)
                            cleaned_reply = _clean_text_for_input(reply_text)

                            print(f"📦 [Артикул]: {sku_detected or 'YaSoKo'}")
                            print(f"⭐ [Оценка]: {stars}★ | 📝 [Текст отзыва]: «{full_comment or '[Фото/Видео маникюра]'}»")
                            print(f"💬 [Сгенерированный ответ ИИ]:\n{cleaned_reply}\n")

                            reviews_found.append({
                                "subtab": sub_title,
                                "text": full_comment,
                                "sku": sku_detected,
                                "stars": stars,
                                "reply": cleaned_reply,
                            })

                            # 1. Нажимаем кнопку «Ответить на отзыв» (Ozon сам откроет и сфокусирует поле ввода!)
                            await _click_reply_to_review_button(page)

                            # 2. Просто вставляем текст ответа (нажимать на экран не нужно!)
                            print("👉 [Вставка текста ответа в активное поле]")
                            field = page.locator("textarea, [contenteditable='true']").last
                            if await field.count() > 0:
                                try:
                                    await field.fill(cleaned_reply)
                                except Exception:
                                    await page.keyboard.insert_text(cleaned_reply)
                            else:
                                await page.keyboard.insert_text(cleaned_reply)

                            await asyncio.sleep(1.0)

                            if dry_run:
                                print("ℹ️ [Dry Run]: Ответ успешно вставлен! Закрываю окно...")
                                await asyncio.sleep(1.2)
                            else:
                                # 3. Надежная отправка ответа (Ctrl+Enter + DOM + координаты + Enter)
                                await _submit_review_reply(page)
                                print("✅ [Успех] Ответ на отзыв опубликован!")
                                tracker["replied_count"] = tracker.get("replied_count", 0) + 1
                                replied_in_session += 1
                                tracker.setdefault("processed_review_ids", []).append(rev_id)
                                _save_tracker(tracker)

                            # 4. Закрытие шторки с гарантированным контролем
                            print("👉 [Закрытие шторки отзыва...]")
                            await _close_review_drawer(page)
                            await asyncio.sleep(0.8)

                            # Если окно все еще видно — не идем дальше, а дожимаем закрытие!
                            retry_close = 0
                            while await _js_is_drawer_open(page) and retry_close < 6:
                                retry_close += 1
                                print(f"⚠️ Окно отзыва все еще открыто (попытка {retry_close}/6)...")
                                await _close_review_drawer(page)
                                await asyncio.sleep(0.8)

                        table_scrolled = await _scroll_main_table_down(page)
                        if new_rows_in_pass == 0 and not table_scrolled:
                            no_new_rows_count += 1
                        else:
                            no_new_rows_count = 0

                try:
                    await page.screenshot(path=str(LOGS_DIR / "ozon_reviews_run.png"))
                except Exception:
                    pass
            finally:
                try:
                    await context.close()
                except Exception:
                    pass

            return {
                "status": "success",
                "replied": replied_in_session,
                "reviews": reviews_found,
                "message": f"Опубликовано ответов: {replied_in_session}. Найдено по фильтру: {len(reviews_found)}.",
            }
    except Exception as exc:
        print(f"❌ [Ozon Reviews Error]: {exc}")
        return {"status": "error", "replied": replied_in_session, "message": str(exc)}


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
            context = await p.chromium.launch_persistent_context(
                user_data_dir=str(BROWSER_DATA_DIR),
                headless=headless,
                args=DIRECT_BROWSER_ARGS,
                env=_get_direct_env(),
                viewport=VIEWPORT,
            )
            try:
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

                            click_y = int(row_box["y"] + row_box["height"] / 2)
                            print(f"\n👉 [Вопрос] X={Q_CLICK_X}, Y={click_y}: «{q_cell_text[:50]}...»")
                            await page.mouse.click(Q_CLICK_X, click_y)
                            await asyncio.sleep(2.0)

                            if not await _js_is_drawer_open(page):
                                await page.mouse.click(Q_CLICK_X, click_y)
                                await asyncio.sleep(2.0)

                            drawer = page.locator(f"{PORTAL_SEL}, {DIALOG_SEL}").first
                            drawer_text = await drawer.inner_text() if await drawer.count() > 0 else ""

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
                            except Exception:
                                pass

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
                except Exception:
                    pass
            finally:
                try:
                    await context.close()
                except Exception:
                    pass

            return {
                "status": "success",
                "replied": replied_count,
                "questions": questions_found,
                "message": f"Найдено вопросов: {len(questions_found)}, отправлено ответов: {replied_count}.",
            }
    except Exception as exc:
        print(f"❌ [Ozon Questions Error]: {exc}")
        return {"status": "error", "replied": replied_count, "message": str(exc)}