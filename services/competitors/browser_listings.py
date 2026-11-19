"""Разбор витрин WB и Ozon через реальный браузер (резерв для парсера конкурентов).

Прямой HTTP к витринам закрыт антиботом: с октября 2026 `search.wb.ru`,
`card.wb.ru` и `catalog.wb.ru` отвечают 403/498, а `composer-api.bx` Ozon
отдаёт 307-петлю без прогретой сессии. Рабочая схема (та же, что в открытых
парсерах и в MCP-серверах на GitHub):

* WB — браузер решает JVO1-челлендж, после чего перехватывается собственный
  ответ сайта на `search.wb.ru/.../v18/search` (replay токена в curl даёт 498:
  токен привязан к браузеру, который решил челлендж). Резерв — DOM-разбор.
* Ozon — один Chromium открывает главную и остаётся открытым; все запросы
  выполняются из контекста этой страницы (`fetch` из документа ozon.ru), иначе
  CORS/403. Из ответа достаются `widgetStates`.

Модуль сознательно не блокирует изображения/шрифты/стили: скрипты антибота
грузятся через них, и блокировка приводит к 403.
"""

import asyncio
import json
import logging
import re
from typing import Any, Dict, List, Optional, Tuple

from config import LOGS_DIR, settings

logger = logging.getLogger(__name__)

LISTING_PROFILE_DIR = LOGS_DIR / "listings_browser_profile"
VIEWPORT = {"width": 1440, "height": 900}

BROWSER_ARGS = [
    "--no-proxy-server",
    "--proxy-server=direct://",
    "--proxy-bypass-list=*",
    "--disable-blink-features=AutomationControlled",
    "--disable-infobars",
    "--disable-dev-shm-usage",
    "--disable-features=IsolateOrigins,site-per-process",
]

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

WB_SEARCH_RE = re.compile(r"search\.wb\.ru/.*/search\?")
WB_CARD_RE = re.compile(r"card\.wb\.ru/.*/detail")
OZON_ITEM_RE = re.compile(r"/product/[^\"\\?#]*-([1-9]\d{5,})(?:/|[?#]|$)")

PRICE_NUM_RE = re.compile(r"\d[\d\s\u00a0\u2009]*(?:[.,]\d+)?")

NAV_TIMEOUT_MS = 45000
FIRST_RESULT_TIMEOUT_MS = 25000
POST_CHALLENGE_PAUSE_SEC = 5.0
def _budget() -> float:
    """Бюджет из настроек (можно снизить на слабом ПК), но не меньше 20 с."""
    try:
        return max(float(getattr(settings, "LISTINGS_BROWSER_BUDGET_SEC", 90)), 20.0)
    except (TypeError, ValueError):
        return 90.0


CACHE_TTL_SEC = 600.0
CACHE_MAX_ENTRIES = 32

# Витрины отвечают медленно (первый запрос Ozon ~12 с), поэтому результат
# придерживается в памяти процесса: отчёт не должен ждать браузер дважды.
_CACHE: Dict[str, Tuple[float, List[Dict[str, Any]]]] = {}


def _cache_get(key: str) -> Optional[List[Dict[str, Any]]]:
    import time

    entry = _CACHE.get(key)
    if not entry:
        return None
    stamp, items = entry
    if time.monotonic() - stamp > CACHE_TTL_SEC:
        _CACHE.pop(key, None)
        return None
    return [dict(it) for it in items]


def _cache_put(key: str, items: List[Dict[str, Any]]) -> None:
    import time

    if not items:
        return
    if len(_CACHE) >= CACHE_MAX_ENTRIES:
        oldest = min(_CACHE, key=lambda k: _CACHE[k][0])
        _CACHE.pop(oldest, None)
    _CACHE[key] = (time.monotonic(), [dict(it) for it in items])


def _direct_env() -> Dict[str, str]:
    """Окружение без прокси: витрины не работают через DC-прокси."""
    import os

    env = os.environ.copy()
    for key in (
        "http_proxy",
        "https_proxy",
        "all_proxy",
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "ALL_PROXY",
    ):
        env.pop(key, None)
    return env


def _num(value: Any) -> float:
    if value is None or isinstance(value, bool):
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    match = PRICE_NUM_RE.search(str(value))
    if not match:
        return 0.0
    cleaned = (
        match.group(0).replace("\u00a0", "").replace("\u2009", "").replace(" ", "").replace(",", ".")
    )
    try:
        return float(cleaned)
    except ValueError:
        return 0.0


# =============================================================================
# Общий разбор
# =============================================================================


def parse_wb_products(products: Any, limit: int = 15) -> List[Dict[str, Any]]:
    """Приводит карточки выдачи WB (v18 search / card detail) к общему виду."""
    items: List[Dict[str, Any]] = []
    seen: set[str] = set()
    for product in products if isinstance(products, list) else []:
        if not isinstance(product, dict):
            continue
        sku = str(product.get("id") or product.get("nmId") or "").strip()
        if not sku or sku in seen:
            continue
        buyer_price = 0.0
        sizes = product.get("sizes")
        if isinstance(sizes, list):
            for size in sizes:
                if not isinstance(size, dict):
                    continue
                price_obj = size.get("price")
                if isinstance(price_obj, dict):
                    for field in ("total", "product", "basic"):
                        cents = _num(price_obj.get(field))
                        if cents > 0.0:
                            buyer_price = round(cents / 100.0, 2)
                            break
                if buyer_price > 0.0:
                    break
        if buyer_price <= 0.0:
            for legacy in ("salePriceU", "priceU", "salePrice"):
                cents = _num(product.get(legacy))
                if cents > 0.0:
                    buyer_price = round(cents / 100.0, 2)
                    break
        if buyer_price <= 0.0:
            continue
        raw_rating = (
            product.get("reviewRating")
            if product.get("reviewRating") is not None
            else product.get("nmReviewRating")
        )
        seen.add(sku)
        items.append(
            {
                "mp": "WB",
                "sku": sku,
                "name": str(product.get("name") or "").strip() or f"Товар WB {sku}",
                "brand": str(product.get("brand") or "").strip() or "Без бренда",
                "rating": round(min(max(_num(raw_rating), 0.0), 5.0), 1),
                "feedbacks": max(int(_num(product.get("feedbacks"))), 0),
                "buyer_price": buyer_price,
            }
        )
        if len(items) >= max(int(limit), 1):
            break
    return items


def parse_ozon_widget_states(
    payload: Any, limit: int = 15
) -> List[Dict[str, Any]]:
    """Достаёт карточки из `widgetStates` (значения — JSON-строки разной формы).

    Пути к товарным массивам у Ozon меняются, поэтому вместо фиксированных
    ключей используется рекурсивный обход с проверкой обязательных полей.
    """
    items: List[Dict[str, Any]] = []
    seen: set[str] = set()

    def emit(node: Dict[str, Any]) -> None:
        if len(items) >= max(int(limit), 1):
            return
        sku = str(
            node.get("sku") or node.get("productId") or node.get("id") or ""
        ).strip()
        price = 0.0
        for field in ("price", "cardPrice", "finalPrice", "originalPrice", "priceV2"):
            raw = node.get(field)
            if isinstance(raw, dict):
                price = _num(raw.get("price") or raw.get("text") or raw.get("value"))
            else:
                price = _num(raw)
            if price > 0.0:
                break
        if not sku or price <= 0.0 or sku in seen:
            return
        name = str(node.get("name") or node.get("title") or "").strip()
        if not name:
            return
        seen.add(sku)
        items.append(
            {
                "mp": "OZON",
                "sku": sku,
                "name": name[:180],
                "brand": str(node.get("brand") or "").strip() or "Без бренда",
                "rating": round(min(max(_num(node.get("rating")), 0.0), 5.0), 1),
                "feedbacks": max(int(_num(node.get("feedbackCount") or node.get("reviewsCount"))), 0),
                "buyer_price": round(price, 2),
            }
        )

    def walk(node: Any, depth: int = 0) -> None:
        if depth > 12 or len(items) >= max(int(limit), 1):
            return
        if isinstance(node, str):
            stripped = node.strip()
            if stripped[:1] in ("{", "["):
                try:
                    walk(json.loads(stripped), depth + 1)
                except (ValueError, TypeError):
                    return
            return
        if isinstance(node, list):
            for child in node:
                walk(child, depth + 1)
            return
        if not isinstance(node, dict):
            return
        emit(node)
        for key in ("items", "tiles", "products", "searchResultsV2", "skuGrid", "grid"):
            if key in node:
                walk(node[key], depth + 1)
        if any(k in node for k in ("sku", "productId")):
            return
        for value in node.values():
            if isinstance(value, (dict, list, str)):
                walk(value, depth + 1)

    walk(payload)
    return items


def parse_ozon_dom_cards(cards: List[Dict[str, Any]], limit: int = 15) -> List[Dict[str, Any]]:
    """DOM-резерв: берёт текст карточек и вытаскивает цену и ссылку на товар."""
    items: List[Dict[str, Any]] = []
    seen: set[str] = set()
    for card in cards if isinstance(cards, list) else []:
        if not isinstance(card, dict):
            continue
        href = str(card.get("href") or "")
        match = OZON_ITEM_RE.search(href)
        if not match:
            continue
        sku = match.group(1)
        if sku in seen:
            continue
        text = str(card.get("text") or "").replace("\n", " ")
        # Не выбираем минимум из произвольных чисел: размер/баллы/скидка
        # попадали в предыдущую выдачу как цена товара. Несколько разных
        # цен без маркировки неоднозначны и исключаются из статистики.
        price_re = r"(?<![\d])([\d][\d\s\u00a0\u2009]*(?:[.,]\d{1,2})?)\s*₽"
        prices = {_num(m.group(1)) for m in re.finditer(price_re, text)}
        prices = {v for v in prices if v > 0}
        if len(prices) != 1:
            continue
        name = re.sub(r"\s+", " ", text).strip()[:180] or f"Товар Ozon {sku}"
        seen.add(sku)
        items.append(
            {
                "mp": "OZON",
                "sku": sku,
                "name": name,
                "brand": "Без бренда",
                "rating": 0.0,
                "feedbacks": 0,
                "buyer_price": round(next(iter(prices)), 2),
            }
        )
        if len(items) >= max(int(limit), 1):
            break
    return items


# =============================================================================
# Браузерные сценарии
# =============================================================================


class _BrowserUnavailable(RuntimeError):
    """Playwright или его браузер не установлен на этом хосте."""


async def _launch(p):
    LISTING_PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    return await p.chromium.launch_persistent_context(
        user_data_dir=str(LISTING_PROFILE_DIR),
        headless=True,
        args=BROWSER_ARGS,
        env=_direct_env(),
        viewport=VIEWPORT,
        user_agent=USER_AGENT,
        locale="ru-RU",
        timezone_id="Europe/Moscow",
    )


async def _close(context) -> None:
    if context is None:
        return
    try:
        await asyncio.wait_for(context.close(), timeout=15)
    except Exception:  # noqa: BLE001 — закрытие не должно ронять парсер
        pass


async def wb_search_via_browser(
    query: str, limit: int = 15
) -> Tuple[List[Dict[str, Any]], Optional[str]]:
    """ Поиск WB в браузере: перехват ответа сайта, затем DOM-резерв. """
    key = f"wb:{str(query or '').strip().lower()}:{int(limit)}"
    cached = _cache_get(key)
    if cached:
        return cached, None
    try:
        items, err = await asyncio.wait_for(
            _wb_search_uncached(query, limit), timeout=_budget()
        )
    except asyncio.TimeoutError:
        return [], "WB: браузерный разбор не уложился в бюджет времени"
    if items:
        _cache_put(key, items)
    return items, err


async def _wb_search_uncached(
    query: str, limit: int = 15
) -> Tuple[List[Dict[str, Any]], Optional[str]]:
    try:
        from playwright.async_api import async_playwright
    except Exception as exc:  # noqa: BLE001
        raise _BrowserUnavailable(f"Playwright недоступен: {exc}") from exc

    captured: List[Any] = []

    async def on_response(response) -> None:
        if not WB_SEARCH_RE.search(response.url):
            return
        try:
            payload = await response.json()
        except Exception:  # noqa: BLE001 — не-JSON ответ антибота
            return
        products = (payload or {}).get("data") or payload
        if isinstance(products, dict) and isinstance(products.get("products"), list):
            captured.append(products["products"])

    context = None
    try:
        async with async_playwright() as p:
            try:
                context = await _launch(p)
            except Exception as exc:  # noqa: BLE001
                raise _BrowserUnavailable(
                    f"Браузер Playwright не запускается: {exc}"
                ) from exc
            page = context.pages[0] if context.pages else await context.new_page()
            page.on("response", lambda r: asyncio.create_task(on_response(r)))
            url = (
                "https://www.wildberries.ru/catalog/0/search.aspx?search="
                + _quote(query)
            )
            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=NAV_TIMEOUT_MS)
            except Exception as exc:  # noqa: BLE001
                return [], f"WB: страница поиска не открылась ({exc})"
            # После JVO1 нужно дать странице время выполнить свой поиск.
            for _ in range(int(FIRST_RESULT_TIMEOUT_MS / 500)):
                if captured:
                    break
                await asyncio.sleep(0.5)
            if not captured:
                await asyncio.sleep(POST_CHALLENGE_PAUSE_SEC)
            if captured:
                items = parse_wb_products(captured[0], limit)
                if items:
                    return items, None
            # DOM-резерв: карточки в разметке страницы.
            try:
                cards = await page.eval_on_selector_all(
                    "article.product-card, div.product-card",
                    """els => els.map(e => ({
                        text: (e.innerText || '').trim(),
                        href: (e.querySelector('a[href*="/catalog/"]') || {}).href || '',
                    }))""",
                )
            except Exception:  # noqa: BLE001
                cards = []
            dom_items: List[Dict[str, Any]] = []
            for card in cards or []:
                match = re.search(r"/catalog/(\d+)/detail", str(card.get("href") or ""))
                text = str(card.get("text") or "")
                price_re = r"(?<![\d])([\d][\d\s\u00a0\u2009]*(?:[.,]\d{1,2})?)\s*₽"
                prices = {_num(m.group(1)) for m in re.finditer(price_re, text)}
                prices = {v for v in prices if v > 0}
                if not match or len(prices) != 1:
                    continue
                dom_items.append(
                    {
                        "mp": "WB",
                        "sku": match.group(1),
                        "name": re.sub(r"\s+", " ", text).strip()[:180],
                        "brand": "Без бренда",
                        "rating": 0.0,
                        "feedbacks": 0,
                        "buyer_price": round(next(iter(prices)), 2),
                    }
                )
                if len(dom_items) >= max(int(limit), 1):
                    break
            if dom_items:
                return dom_items, None
            return [], "WB: витрина не отдала карточки (антибот JVO1 не пройден)"
    finally:
        await _close(context)


async def ozon_search_via_browser(
    query: str, limit: int = 15
) -> Tuple[List[Dict[str, Any]], Optional[str]]:
    """Поиск Ozon: in-page fetch из документа ozon.ru с разбором widgetStates."""
    key = f"ozon:{str(query or '').strip().lower()}:{int(limit)}"
    cached = _cache_get(key)
    if cached:
        return cached, None
    try:
        items, err = await asyncio.wait_for(
            _ozon_search_uncached(query, limit), timeout=_budget()
        )
    except asyncio.TimeoutError:
        return [], "Ozon: браузерный разбор не уложился в бюджет времени"
    if items:
        _cache_put(key, items)
    return items, err


async def _ozon_search_uncached(
    query: str, limit: int = 15
) -> Tuple[List[Dict[str, Any]], Optional[str]]:
    try:
        from playwright.async_api import async_playwright
    except Exception as exc:  # noqa: BLE001
        raise _BrowserUnavailable(f"Playwright недоступен: {exc}") from exc

    context = None
    try:
        async with async_playwright() as p:
            try:
                context = await _launch(p)
            except Exception as exc:  # noqa: BLE001
                raise _BrowserUnavailable(
                    f"Браузер Playwright не запускается: {exc}"
                ) from exc
            page = context.pages[0] if context.pages else await context.new_page()
            try:
                await page.goto(
                    "https://www.ozon.ru/", wait_until="domcontentloaded", timeout=NAV_TIMEOUT_MS
                )
            except Exception as exc:  # noqa: BLE001
                return [], f"Ozon: главная не открылась ({exc})"
            await asyncio.sleep(3.0)  # прогрев антибот-сессии

            search_path = (
                "https://www.ozon.ru/search/?text=" + _quote(query) + "&from_global=true"
            )
            # Запрос идёт из контекста страницы ozon.ru — иначе CORS и 403.
            payload = await page.evaluate(
                """async (url) => {
                    const r = await fetch(url, {
                        headers: {'x-o3-app-name': 'dweb_client'},
                        credentials: 'include',
                    });
                    if (!r.ok) return {__status: r.status};
                    const text = await r.text();
                    return {__text: text};
                }""",
                search_path,
            )
            body = payload.get("__text") if isinstance(payload, dict) else None
            status = payload.get("__status") if isinstance(payload, dict) else None
            if not body:
                return [], f"Ozon: поиск вернул HTTP {status or '—'} (антибот)"

            # 1) Пробуем JSON-ответ composer-api (в нём widgetStates).
            items: List[Dict[str, Any]] = []
            try:
                items = parse_ozon_widget_states(json.loads(body), limit)
            except (ValueError, TypeError):
                items = []
            if not items and ("__NUXT__" in body or "<html" in body.lower()):
                items = parse_ozon_widget_states(body, limit)
            if items:
                return items, None

            # 2) Резерв: ждём отрисовку выдачи и читаем DOM.
            try:
                await page.goto(search_path, wait_until="domcontentloaded", timeout=NAV_TIMEOUT_MS)
                await asyncio.sleep(4.0)
                cards = await page.eval_on_selector_all(
                    'a[href*="/product/"]',
                    """els => els.slice(0, 200).map(e => ({
                        href: e.href || '',
                        text: (e.closest('div') && e.closest('div').innerText) || e.innerText || '',
                    }))""",
                )
            except Exception:  # noqa: BLE001
                cards = []
            items = parse_ozon_dom_cards(cards or [], limit)
            if items:
                return items, None
            return [], "Ozon: витрина закрыта антиботом (нет ни widgetStates, ни карточек)"
    finally:
        await _close(context)


def _quote(value: str) -> str:
    import urllib.parse

    return urllib.parse.quote(str(value or "").strip())
