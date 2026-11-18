import asyncio
import html
import json
import logging
import math
import re
import statistics
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Optional, Tuple

import aiohttp

from config import settings
from services.common.orders_report import _mul_rub, _round_pct, _round_rub, _to_dec
from services.common.ad_recommendations import (
    _discover_models_via_proxy,
    sanitize_telegram_html,
)

logger = logging.getLogger(__name__)

WB_HTTP_TIMEOUT = aiohttp.ClientTimeout(total=15, connect=6, sock_connect=6, sock_read=10)
OZON_HTTP_TIMEOUT = aiohttp.ClientTimeout(total=15, connect=6, sock_connect=6, sock_read=10)
GEMINI_HTTP_TIMEOUT = aiohttp.ClientTimeout(total=90, connect=15, sock_connect=15, sock_read=70)
NATIVE_SOCKET_TIMEOUT_SEC = 10
MAX_RESPONSE_BYTES = 2 * 1024 * 1024  # 2 МБ — защита памяти хоста (4 ГБ RAM)

DEFAULT_COMPETITOR_COGS_RUB = 75.0  # Оценочная себестоимость сырья + баночка 15 г + этикетка
DEFAULT_OZON_ACTUAL_LOGISTICS_RUB = 39.0  # Фактическая эталонная логистика Ozon (~39 ₽/заказ)
DEFAULT_OZON_UNDER300_COMM_RATE = 0.23  # Льготная комиссия Ozon до 300 ₽ (22–23%)
DEFAULT_OZON_COINVEST_FACTOR = 0.90  # Оценочный коэффициент витрины Ozon к цене ЛК
DEFAULT_WB_LOGISTICS_RUB = 55.0  # Базовый тариф логистики WB для баночки 15 г
DEFAULT_WB_COMM_RATE = 0.25  # Базовая комиссия категории ногтевого сервиса WB
DEFAULT_WB_SPP_FACTOR = 0.82  # Оценочный коэффициент СПП WB (buyer_price / seller_price)

_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
_PRICE_DIGITS_RE = re.compile(r"(\d[\d\s\u00a0\u2009]*(?:[.,]\d+)?)")


def _safe_float(value: Any, default: float = 0.0) -> float:
    if value is None or isinstance(value, bool):
        return default
    if isinstance(value, str):
        cleaned = (
            value.strip()
            .replace("\u00a0", "")
            .replace("\u2009", "")
            .replace(" ", "")
            .replace(",", ".")
        )
        if not cleaned:
            return default
        value = cleaned
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    return parsed if math.isfinite(parsed) else default


def _safe_int(value: Any, default: int = 0) -> int:
    parsed = _safe_float(value, float(default))
    return int(parsed) if math.isfinite(parsed) else default


def _extract_price_number(raw: Any) -> float:
    if isinstance(raw, (int, float)) and not isinstance(raw, bool):
        return float(raw) if math.isfinite(raw) and raw > 0.0 else 0.0
    if isinstance(raw, str):
        match = _PRICE_DIGITS_RE.search(raw)
        if match:
            val = _safe_float(match.group(1), 0.0)
            return val if val > 0.0 else 0.0
    return 0.0


async def _fetch_json_direct(
    url: str,
    headers: Dict[str, str],
    timeout: aiohttp.ClientTimeout,
) -> Tuple[Optional[Any], Optional[str], bool]:
    """
    Выполняет прямой HTTP GET (trust_env=False) с жесткими таймаутами, ограничением объема ответа
    и гарантированным закрытием aiohttp.ClientSession и TCPConnector при любом исходе.
    Возвращает: (json_data, error_message, is_transport_error).
    """
    if not url.startswith("https://"):
        return None, "Недопустимая схема URL (требуется https://)", False

    connector = aiohttp.TCPConnector(limit=4, ssl=True, enable_cleanup_closed=True)
    session: Optional[aiohttp.ClientSession] = None
    try:
        async with aiohttp.ClientSession(
            connector=connector,
            connector_owner=True,
            trust_env=False,
            timeout=timeout,
            headers=headers,
        ) as opened_session:
            session = opened_session
            async with session.get(url, allow_redirects=True, max_redirects=3) as resp:
                raw_bytes = await resp.content.read(MAX_RESPONSE_BYTES + 1)
                if len(raw_bytes) > MAX_RESPONSE_BYTES:
                    return (
                        None,
                        f"HTTP {resp.status}: превышен лимит ответа ({MAX_RESPONSE_BYTES // 1024} КБ)",
                        False,
                    )
                if resp.status == 429:
                    return None, "HTTP 429 (превышен лимит запросов)", False
                if resp.status == 403:
                    return None, "HTTP 403 (доступ ограничен антибот-защитой)", False
                if resp.status != 200:
                    return None, f"HTTP {resp.status}", resp.status >= 500

                text = raw_bytes.decode("utf-8", errors="replace").strip()
                if not text:
                    return None, "Пустое тело ответа HTTP 200", False
                try:
                    return json.loads(text), None, False
                except json.JSONDecodeError as exc:
                    return None, f"Некорректный JSON ответа ({exc.msg})", False
    except asyncio.TimeoutError:
        return None, f"Таймаут запроса ({timeout.total} сек.)", True
    except aiohttp.ClientError as exc:
        return None, f"Сетевая ошибка aiohttp ({type(exc).__name__}: {exc})", True
    except OSError as exc:
        return None, f"Ошибка сокета ({type(exc).__name__}: {exc})", True
    finally:
        if session is not None and not session.closed:
            await session.close()
        if not connector.closed:
            await connector.close()


def _fetch_wb_native_sync(url: str) -> Tuple[List[Dict[str, Any]], Optional[str]]:
    """
    Резервный прямой запрос к WB через стандартную библиотеку без системного прокси,
    с явным таймаутом сокета, ограничением размера ответа и корректной обработкой ошибок.
    """
    if not url.startswith("https://"):
        return [], "Недопустимая схема URL"

    headers = {
        "User-Agent": _USER_AGENT,
        "Accept": "application/json",
        "Accept-Language": "ru-RU,ru;q=0.9",
        "Origin": "https://www.wildberries.ru",
        "Referer": "https://www.wildberries.ru/",
    }
    req = urllib.request.Request(url, headers=headers, method="GET")
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(req, timeout=NATIVE_SOCKET_TIMEOUT_SEC) as response:
            status = getattr(response, "status", 200)
            raw_bytes = response.read(MAX_RESPONSE_BYTES + 1)
            if len(raw_bytes) > MAX_RESPONSE_BYTES:
                return [], f"HTTP {status}: превышен лимит ответа"
            if status != 200:
                return [], f"HTTP {status}"
            data = json.loads(raw_bytes.decode("utf-8", errors="replace"))
            if not isinstance(data, dict):
                return [], "Некорректная структура ответа WB"
            data_block = data.get("data")
            products = data_block.get("products") if isinstance(data_block, dict) else None
            if isinstance(products, list):
                return [p for p in products if isinstance(p, dict)], None
            return [], None
    except urllib.error.HTTPError as exc:
        return [], f"HTTP {exc.code}"
    except urllib.error.URLError as exc:
        return [], f"URLError: {exc.reason}"
    except TimeoutError:
        return [], f"Таймаут резервного сокета ({NATIVE_SOCKET_TIMEOUT_SEC} сек.)"
    except json.JSONDecodeError as exc:
        return [], f"Некорректный JSON резервного ответа ({exc.msg})"
    except (OSError, ValueError) as exc:
        return [], f"{type(exc).__name__}: {exc}"


def _extract_wb_product_price(product: Dict[str, Any]) -> float:
    sizes = product.get("sizes")
    if isinstance(sizes, list):
        for size in sizes:
            if not isinstance(size, dict):
                continue
            price_obj = size.get("price")
            if isinstance(price_obj, dict):
                for field in ("total", "product", "basic"):
                    cents = _safe_float(price_obj.get(field), 0.0)
                    if cents > 0.0:
                        return round(cents / 100.0, 2)
    for legacy_field in ("salePriceU", "priceU"):
        cents = _safe_float(product.get(legacy_field), 0.0)
        if cents > 0.0:
            return round(cents / 100.0, 2)
    return 0.0


async def _parse_wb_competitors_with_status(
    query: str,
    count: int = 15,
) -> Tuple[List[Dict[str, Any]], Optional[str]]:
    clean_query = str(query or "").strip()
    safe_count = max(_safe_int(count, 15), 0)
    if not clean_query or safe_count == 0:
        return [], None

    encoded_query = urllib.parse.quote(clean_query)
    url = (
        "https://catalog.wb.ru/catalog/common/v2/search"
        f"?ab_testing=false&appType=1&curr=rub&dest=-1257786&query={encoded_query}"
        "&resultset=catalog&sort=popular&spp=30&suppressSpellcheck=false"
    )
    headers = {
        "User-Agent": _USER_AGENT,
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "ru-RU,ru;q=0.9",
        "Origin": "https://www.wildberries.ru",
        "Referer": "https://www.wildberries.ru/",
    }

    raw_products: List[Dict[str, Any]] = []
    data, err_msg, is_transport_err = await _fetch_json_direct(url, headers, WB_HTTP_TIMEOUT)

    if err_msg is None and isinstance(data, dict):
        data_block = data.get("data")
        prods = data_block.get("products") if isinstance(data_block, dict) else None
        if isinstance(prods, list):
            raw_products = [p for p in prods if isinstance(p, dict)]
    elif is_transport_err:
        logger.warning("[Competitors WB] aiohttp error (%s), trying direct urllib fallback", err_msg)
        fallback_prods, fallback_err = await asyncio.to_thread(_fetch_wb_native_sync, url)
        if fallback_err is None:
            raw_products = fallback_prods
            err_msg = None
        else:
            err_msg = f"{err_msg}; fallback: {fallback_err}"
            logger.warning("[Competitors WB] fallback failed: %s", fallback_err)
    elif err_msg:
        logger.warning("[Competitors WB] request failed: %s", err_msg)

    items: List[Dict[str, Any]] = []
    seen_skus: set[str] = set()
    for prod in raw_products:
        if len(items) >= safe_count:
            break
        sku = str(prod.get("id") or "").strip()
        if not sku or sku in seen_skus:
            continue
        buyer_price = _extract_wb_product_price(prod)
        if buyer_price <= 0.0:
            continue

        raw_rating = (
            prod.get("reviewRating")
            if prod.get("reviewRating") is not None
            else prod.get("nmReviewRating")
        )
        rating = min(max(_safe_float(raw_rating, 0.0), 0.0), 5.0)
        feedbacks = max(_safe_int(prod.get("feedbacks"), 0), 0)
        name = str(prod.get("name") or "").strip() or f"Товар WB {sku}"
        brand = str(prod.get("brand") or "").strip() or "Без бренда"

        seen_skus.add(sku)
        items.append(
            {
                "mp": "WB",
                "sku": sku,
                "name": name,
                "brand": brand,
                "rating": round(rating, 1),
                "feedbacks": feedbacks,
                "buyer_price": buyer_price,
            }
        )

    # Не подмешиваем вымышленные карточки при неполной или недоступной выдаче WB.
    return items, err_msg


async def parse_wb_competitors(query: str, count: int = 15) -> List[Dict[str, Any]]:
    """Парсит реальные карточки конкурентов WB без подстановки фейковых товаров."""
    items, _ = await _parse_wb_competitors_with_status(query=query, count=count)
    return items


def _extract_ozon_items_from_payload(payload: Any, limit: int) -> List[Dict[str, Any]]:
    if not isinstance(payload, dict) or limit <= 0:
        return []

    widget_states = payload.get("widgetStates")
    if not isinstance(widget_states, dict):
        return []

    items: List[Dict[str, Any]] = []
    seen_skus: set[str] = set()

    for key, raw_widget in widget_states.items():
        if len(items) >= limit:
            break
        key_low = str(key).lower()
        if "searchresults" not in key_low and "tilegrid" not in key_low:
            continue

        if isinstance(raw_widget, str):
            try:
                widget = json.loads(raw_widget)
            except json.JSONDecodeError:
                continue
        elif isinstance(raw_widget, dict):
            widget = raw_widget
        else:
            continue

        raw_items = widget.get("items")
        if not isinstance(raw_items, list):
            continue

        for entry in raw_items:
            if len(items) >= limit:
                break
            if not isinstance(entry, dict):
                continue

            sku = str(entry.get("skuId") or entry.get("sku") or entry.get("id") or "").strip()
            if not sku or sku in seen_skus:
                continue

            buyer_price = _extract_price_number(entry.get("price") or entry.get("finalPrice"))
            title = str(entry.get("title") or entry.get("name") or "").strip()
            brand = str(entry.get("brand") or "").strip()
            rating = min(max(_safe_float(entry.get("rating"), 0.0), 0.0), 5.0)
            feedbacks = max(_safe_int(entry.get("reviewsCount") or entry.get("feedbacks"), 0), 0)

            for state in entry.get("mainState") or []:
                if not isinstance(state, dict):
                    continue
                atom = state.get("atom") if isinstance(state.get("atom"), dict) else state
                atom_type = str(atom.get("type") or "")
                if buyer_price <= 0.0 and "price" in atom_type.lower():
                    p_obj = atom.get("priceV2") or atom.get("price") or {}
                    if isinstance(p_obj, dict):
                        for p_el in p_obj.get("price") or []:
                            if isinstance(p_el, dict) and str(p_el.get("textStyle") or "") == "PRICE":
                                buyer_price = _extract_price_number(p_el.get("text"))
                                if buyer_price > 0.0:
                                    break
                        if buyer_price <= 0.0:
                            buyer_price = _extract_price_number(p_obj.get("text"))
                if not title and atom_type == "textAtom":
                    txt_atom = atom.get("textAtom")
                    if isinstance(txt_atom, dict) and txt_atom.get("text"):
                        title = str(txt_atom["text"]).strip()

            if buyer_price <= 0.0:
                continue

            seen_skus.add(sku)
            items.append(
                {
                    "mp": "OZON",
                    "sku": sku,
                    "name": title or f"Товар Ozon {sku}",
                    "brand": brand or "Без бренда",
                    "rating": round(rating, 1),
                    "feedbacks": feedbacks,
                    "buyer_price": round(buyer_price, 2),
                }
            )

    return items


async def _parse_ozon_competitors_with_status(
    query: str,
    count: int = 15,
) -> Tuple[List[Dict[str, Any]], Optional[str]]:
    clean_query = str(query or "").strip()
    safe_count = max(_safe_int(count, 15), 0)
    if not clean_query or safe_count == 0:
        return [], None

    search_path = f"/search/?text={urllib.parse.quote(clean_query)}&from_global=true"
    url = (
        "https://www.ozon.ru/api/entrypoint-api.bx/page/json/v2"
        f"?url={urllib.parse.quote(search_path, safe='')}"
    )
    headers = {
        "User-Agent": _USER_AGENT,
        "Accept": "application/json",
        "Accept-Language": "ru-RU,ru;q=0.9",
        "Referer": "https://www.ozon.ru/",
    }

    data, err_msg, _ = await _fetch_json_direct(url, headers, OZON_HTTP_TIMEOUT)
    if err_msg is not None:
        logger.warning("[Competitors Ozon] live search unavailable: %s", err_msg)
        return [], err_msg

    items = _extract_ozon_items_from_payload(data, limit=safe_count)
    if not items:
        return [], "Витрина Ozon не вернула товарных карточек в JSON-выдаче"
    return items, None


async def parse_ozon_competitors(query: str, count: int = 15) -> List[Dict[str, Any]]:
    """
    Парсит реальную поисковую выдачу Ozon через aiohttp с таймаутами и закрытием сессии.
    При блокировке антиботом или сетевой ошибке возвращает пустой список без фейковых SKU.
    """
    items, _ = await _parse_ozon_competitors_with_status(query=query, count=count)
    return items


def calculate_competitor_unit_economy(
    buyer_price: float,
    mp: str,
    *,
    cogs: float = DEFAULT_COMPETITOR_COGS_RUB,
    ozon_logistics_rub: float = DEFAULT_OZON_ACTUAL_LOGISTICS_RUB,
    wb_logistics_rub: float = DEFAULT_WB_LOGISTICS_RUB,
) -> Dict[str, float]:
    """
    Оценочный расчет юнит-экономики конкурента на основе эталонных констант config.py:
    - Эквайринг: settings.ACQUIRING_RATE (1.5%)
    - Налог УСН: settings.TAX_RATE (6%)
    - Ozon: льготная комиссия 23% до 300 ₽ включительно, 52% свыше 300 ₽; логистика ~39 ₽/заказ
    - WB: комиссия категории 25%, базовая логистика 55 ₽, УСН 6% с цены покупателя (СПП)
    - Безопасная обработка нулевой/некорректной цены без деления на ноль.
    """
    clean_bp = round(max(_safe_float(buyer_price, 0.0), 0.0), 2)
    if clean_bp <= 0.0:
        return {
            "seller_price": 0.0,
            "buyer_price": 0.0,
            "commission": 0.0,
            "commission_rate": 0.0,
            "acquiring": 0.0,
            "logistics": 0.0,
            "mp_expenses": 0.0,
            "tax": 0.0,
            "cogs": 0.0,
            "profit": 0.0,
            "ros": 0.0,
        }

    acq_rate = max(_safe_float(getattr(settings, "ACQUIRING_RATE", 0.015), 0.015), 0.0)
    tax_rate = max(_safe_float(getattr(settings, "TAX_RATE", 0.06), 0.06), 0.0)
    oz_low_limit = max(_safe_float(getattr(settings, "OZON_LOW_PRICE_LIMIT", 300.0), 300.0), 0.0)
    oz_high_comm = max(_safe_float(getattr(settings, "OZON_HIGH_COMMISSION_RATE", 0.52), 0.52), 0.0)
    clean_cogs = round(max(_safe_float(cogs, DEFAULT_COMPETITOR_COGS_RUB), 0.0), 2)

    mp_norm = str(mp or "").strip().upper()
    if mp_norm == "WB":
        raw_seller = clean_bp / DEFAULT_WB_SPP_FACTOR
        seller_price = float(round(raw_seller)) if raw_seller >= 1.0 else _round_rub(raw_seller)
        comm_rate = DEFAULT_WB_COMM_RATE
        comm = _mul_rub(seller_price, comm_rate)
        logistics = _round_rub(max(_safe_float(wb_logistics_rub, DEFAULT_WB_LOGISTICS_RUB), 0.0))
        acq = _mul_rub(max(clean_bp, seller_price), acq_rate)
        tax_usn = _mul_rub(clean_bp, tax_rate)
    else:
        raw_seller = clean_bp / DEFAULT_OZON_COINVEST_FACTOR
        seller_price = float(round(raw_seller)) if raw_seller >= 1.0 else _round_rub(raw_seller)
        comm_rate = (
            DEFAULT_OZON_UNDER300_COMM_RATE if seller_price <= oz_low_limit else oz_high_comm
        )
        comm = _mul_rub(seller_price, comm_rate)
        logistics = _round_rub(
            max(_safe_float(ozon_logistics_rub, DEFAULT_OZON_ACTUAL_LOGISTICS_RUB), 0.0)
        )
        acq = _mul_rub(seller_price, acq_rate)
        tax_usn = _mul_rub(seller_price, tax_rate)

    mp_expenses = _round_rub(_to_dec(comm) + _to_dec(logistics) + _to_dec(acq))
    net_profit = _round_rub(
        _to_dec(seller_price) - _to_dec(clean_cogs) - _to_dec(mp_expenses) - _to_dec(tax_usn)
    )
    ros = _round_pct(net_profit, seller_price) if seller_price > 0.0 else 0.0

    return {
        "seller_price": seller_price,
        "buyer_price": clean_bp,
        "commission": comm,
        "commission_rate": comm_rate,
        "acquiring": acq,
        "logistics": logistics,
        "mp_expenses": mp_expenses,
        "tax": tax_usn,
        "cogs": clean_cogs,
        "profit": net_profit,
        "ros": ros,
    }


async def _call_gemini_competitor_verdict(prompt: str) -> str:
    """
    Запрашивает аналитический вердикт Gemini строго через локальный прокси (settings.LOCAL_PROXY_URL)
    с явными таймаутами и гарантированным закрытием aiohttp-сессии и коннектора.
    """
    api_key = str(getattr(settings, "GEMINI_API_KEY", "") or "").strip()
    if not api_key:
        return "⚠️ <i>GEMINI_API_KEY не задан — показаны только проверенные данные витрины.</i>"

    proxy_url = str(getattr(settings, "LOCAL_PROXY_URL", "") or "").strip()
    if not proxy_url:
        return "⚠️ <i>LOCAL_PROXY_URL не задан — прямой вызов Gemini отключён правилами безопасности.</i>"

    last_error = "нет ответа от моделей Gemini"
    connector = aiohttp.TCPConnector(limit=4, ssl=True, enable_cleanup_closed=True)
    session: Optional[aiohttp.ClientSession] = None
    try:
        async with aiohttp.ClientSession(
            connector=connector,
            connector_owner=True,
            trust_env=False,
            timeout=GEMINI_HTTP_TIMEOUT,
        ) as opened_session:
            session = opened_session
            models = await _discover_models_via_proxy(session, api_key, proxy_url)
            payload = {
                "contents": [{"parts": [{"text": prompt}]}],
                "generationConfig": {"temperature": 0.3, "maxOutputTokens": 4096},
            }
            for model_name in models:
                url = (
                    f"https://generativelanguage.googleapis.com/v1beta/models/"
                    f"{model_name}:generateContent?key={api_key}"
                )
                try:
                    async with session.post(url, json=payload, proxy=proxy_url) as resp:
                        raw_text = await resp.text(encoding="utf-8", errors="replace")
                        if resp.status in (401, 403):
                            last_error = f"{model_name}: HTTP {resp.status}"
                            break
                        if resp.status != 200:
                            last_error = f"{model_name}: HTTP {resp.status}"
                            continue
                        data = json.loads(raw_text)
                        candidates = data.get("candidates") or []
                        if not candidates:
                            continue
                        parts = (candidates[0].get("content") or {}).get("parts") or []
                        text_out = "".join(
                            str(p.get("text") or "")
                            for p in parts
                            if isinstance(p, dict) and not p.get("thought", False)
                        ).strip()
                        if text_out:
                            return sanitize_telegram_html(text_out)
                except asyncio.TimeoutError:
                    last_error = f"{model_name}: таймаут запроса"
                    continue
                except (aiohttp.ClientError, json.JSONDecodeError, OSError) as exc:
                    last_error = f"{model_name}: {type(exc).__name__}"
                    continue
    except asyncio.TimeoutError:
        last_error = "таймаут подключения к локальному прокси"
    except (aiohttp.ClientError, OSError) as exc:
        last_error = f"ошибка прокси ({type(exc).__name__})"
    finally:
        if session is not None and not session.closed:
            await session.close()
        if not connector.closed:
            await connector.close()

    return f"⚠️ <i>ИИ-разбор временно недоступен ({html.escape(last_error)}).</i>"


async def run_full_competitor_deep_audit(category_query: str) -> Tuple[str, Dict[str, float]]:
    clean_query = str(category_query or "").strip()
    safe_query = html.escape(clean_query or "не задан")

    wb_res, oz_res = await asyncio.gather(
        _parse_wb_competitors_with_status(clean_query, count=15),
        _parse_ozon_competitors_with_status(clean_query, count=15),
        return_exceptions=True,
    )

    if isinstance(wb_res, Exception):
        wb_comps: List[Dict[str, Any]] = []
        wb_err: Optional[str] = f"{type(wb_res).__name__}: {wb_res}"
    else:
        wb_comps, wb_err = wb_res

    if isinstance(oz_res, Exception):
        oz_comps: List[Dict[str, Any]] = []
        oz_err: Optional[str] = f"{type(oz_res).__name__}: {oz_res}"
    else:
        oz_comps, oz_err = oz_res

    wb_prices = [float(c["buyer_price"]) for c in wb_comps if _safe_float(c.get("buyer_price")) > 0.0]
    oz_prices = [float(c["buyer_price"]) for c in oz_comps if _safe_float(c.get("buyer_price")) > 0.0]
    all_prices = wb_prices + oz_prices

    avg_wb = round(sum(wb_prices) / len(wb_prices), 2) if wb_prices else 0.0
    avg_oz = round(sum(oz_prices) / len(oz_prices), 2) if oz_prices else 0.0
    avg_total = round(sum(all_prices) / len(all_prices), 2) if all_prices else 0.0
    median_total = round(float(statistics.median(all_prices)), 2) if all_prices else 0.0

    prices_dict: Dict[str, float] = {
        "wb": avg_wb,
        "ozon": avg_oz,
        "total": avg_total,
        "median": median_total,
    }

    if not wb_comps and not oz_comps:
        diag_lines = [
            f"📊 <b>Анализ конкурентов: «{safe_query}»</b>\n",
            "⚠️ <b>Не удалось получить реальные карточки с витрин (фейковые данные отключены):</b>",
            f"• Wildberries: <code>{html.escape(wb_err or 'карточки не найдены')}</code>",
            f"• Ozon: <code>{html.escape(oz_err or 'карточки не найдены')}</code>",
        ]
        return "\n".join(diag_lines), prices_dict

    def _format_comp_lines(comps: List[Dict[str, Any]]) -> Tuple[List[str], List[str]]:
        html_rows: List[str] = []
        plain_rows: List[str] = []
        for comp in comps:
            econ = calculate_competitor_unit_economy(float(comp["buyer_price"]), str(comp["mp"]))
            mp_lbl = html.escape(str(comp["mp"]))
            sku_lbl = html.escape(str(comp["sku"]))
            brand_lbl = html.escape(str(comp["brand"]))
            raw_name = str(comp["name"])
            short_name = (raw_name[:28] + "...") if len(raw_name) > 28 else raw_name
            name_lbl = html.escape(short_name)

            html_rows.append(
                f"• [{mp_lbl}] Арт. <code>{sku_lbl}</code> | <b>{brand_lbl}</b> — {name_lbl}\n"
                f"   Витрина: <b>{econ['buyer_price']:.0f} ₽</b> (оценка ЛК ~{econ['seller_price']:.0f} ₽) | "
                f"Расходы МП (с экв. 1.5%): -{econ['mp_expenses']:.0f} ₽ | УСН 6%: -{econ['tax']:.0f} ₽ | "
                f"Прибыль: <code>{econ['profit']:+.0f} ₽</code> (ROS {econ['ros']:.1f}%)"
            )
            plain_rows.append(
                f"[{comp['mp']}] SKU {comp['sku']} | {comp['brand']} — {short_name} | "
                f"витрина {econ['buyer_price']:.0f}₽, ЛК ~{econ['seller_price']:.0f}₽, "
                f"комиссия {econ['commission_rate']*100:.0f}% (-{econ['commission']:.0f}₽), "
                f"логистика -{econ['logistics']:.0f}₽, эквайринг -{econ['acquiring']:.1f}₽, "
                f"УСН -{econ['tax']:.1f}₽, прибыль {econ['profit']:+.0f}₽ (ROS {econ['ros']:.1f}%), "
                f"рейтинг {comp['rating']}, отзывов {comp['feedbacks']}"
            )
        return html_rows, plain_rows

    wb_html, wb_plain = _format_comp_lines(wb_comps)
    oz_html, oz_plain = _format_comp_lines(oz_comps)

    wb_price_str = f"<b>{avg_wb:.0f} ₽</b> ({len(wb_comps)} шт.)" if wb_comps else "<i>н/д (нет данных витрины)</i>"
    oz_price_str = f"<b>{avg_oz:.0f} ₽</b> ({len(oz_comps)} шт.)" if oz_comps else "<i>н/д (нет данных витрины)</i>"

    wb_section = (
        "\n".join(wb_html[:7])
        if wb_html
        else f"<i>Данные WB недоступны: <code>{html.escape(wb_err or 'пустая выдача')}</code></i>"
    )
    oz_section = (
        "\n".join(oz_html[:7])
        if oz_html
        else f"<i>Данные Ozon недоступны: <code>{html.escape(oz_err or 'пустая выдача')}</code> (фейковые карточки не подставляются)</i>"
    )

    sample_for_prompt = "\n".join([*wb_plain[:7], *oz_plain[:7]])
    wb_prompt_stat = f"{avg_wb:.0f} ₽ (карточек: {len(wb_comps)})" if wb_comps else f"недоступно ({wb_err})"
    oz_prompt_stat = f"{avg_oz:.0f} ₽ (карточек: {len(oz_comps)})" if oz_comps else f"недоступно ({oz_err})"

    prompt = (
        f"Ты — коммерческий директор бренда YaSoKo (гели для ногтей 15 г, производство г. Иваново, ИП УСН 6%).\n"
        f"Проведи анализ реально собранных карточек конкурентов по запросу «{clean_query}» "
        f"(всего получено: {len(wb_comps) + len(oz_comps)} шт.).\n\n"
        f"ФАКТИЧЕСКАЯ ЦЕНОВАЯ СТАТИСТИКА ВИТРИНЫ:\n"
        f"- Средняя цена Wildberries: {wb_prompt_stat}\n"
        f"- Средняя цена Ozon: {oz_prompt_stat}\n"
        f"- Средняя по выборке: {avg_total:.0f} ₽ | Истинная медиана: {median_total:.0f} ₽\n\n"
        f"РЕАЛЬНАЯ ВЫБОРКА КОНКУРЕНТОВ (оценочная себестоимость {DEFAULT_COMPETITOR_COGS_RUB:.0f} ₽, "
        f"логистика Ozon ~{DEFAULT_OZON_ACTUAL_LOGISTICS_RUB:.0f} ₽, эквайринг 1.5%, УСН 6%):\n"
        f"{sample_for_prompt}\n\n"
        "НАШИ ВВОДНЫЕ YASOKO:\n"
        "- Двойная жесткость: пластичный в тонком слое (без сколов), жесткий в толстом (держит углы квадрата без усадки).\n"
        "- Средне-вязкая консистенция (не затекает на валики), HEMA FREE, 11-FREE.\n"
        "- Тарифы Ozon: до 300 ₽ комиссия 22–23%, свыше 300 ₽ комиссия 52% (избегать ценовой ловушки 301–450 ₽).\n\n"
        "ТРЕБОВАНИЯ К ОТВЕТУ:\n"
        "- Без приветствий и воды. Не выдумывай данные по площадке, если она помечена как недоступная.\n"
        "- 1. Ценовое позиционирование Ozon (299 ₽ против выхода за порог 300 ₽ с комиссией 52%) и WB (с учетом СПП).\n"
        "- 2. Три незакрытые боли мастеров маникюра при выборе карточки в этой ценовой группе.\n"
        "- 3. Три конкретных шага по улучшению главного фото и второго слайда YaSoKo.\n"
        "- Используй только HTML-теги <b>, <i>, <code>, без Markdown (**, ###) и таблиц."
    )

    ai_verdict = await _call_gemini_competitor_verdict(prompt)

    total_found = len(wb_comps) + len(oz_comps)
    report_text = (
        f"📊 <b>Анализ конкурентов ({total_found} карточек): «{safe_query}»</b>\n"
        f"ℹ️ <i>Цены ЛК, СПП и себестоимость конкурентов ({DEFAULT_COMPETITOR_COGS_RUB:.0f} ₽) — расчётная оценка; "
        f"эквайринг 1.5%, УСН 6%, логистика Ozon ~{DEFAULT_OZON_ACTUAL_LOGISTICS_RUB:.0f} ₽.</i>\n\n"
        f"💵 <b>Цены на витринах:</b>\n"
        f"• Wildberries: {wb_price_str}\n"
        f"• Ozon: {oz_price_str}\n"
        f"• Средняя по выборке: <b>{avg_total:.0f} ₽</b> | Рыночная медиана: <b>{median_total:.0f} ₽</b>\n\n"
        f"<b>Выборка Wildberries:</b>\n{wb_section}\n\n"
        f"<b>Выборка Ozon:</b>\n{oz_section}\n\n"
        f"➖➖➖➖➖➖➖➖➖➖\n"
        f"🧠 <b>Аналитический вердикт и закрытие болей:</b>\n\n"
        f"{ai_verdict}"
    )

    return report_text, prices_dict
