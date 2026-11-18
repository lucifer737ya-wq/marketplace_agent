import asyncio
import html
import json
import logging
import math
import re
import statistics
import urllib.parse
from typing import Any, Dict, List, Optional, Tuple

import aiohttp

from config import settings
from services.common.orders_report import _mul_rub, _round_pct, _round_rub, _to_dec
from services.common.ad_recommendations import sanitize_telegram_html
from services.common.gemini import generate

logger = logging.getLogger(__name__)

WB_HTTP_TIMEOUT = aiohttp.ClientTimeout(total=15, connect=6, sock_connect=6, sock_read=10)
OZON_HTTP_TIMEOUT = aiohttp.ClientTimeout(total=15, connect=6, sock_connect=6, sock_read=10)
MAX_RESPONSE_BYTES = 2 * 1024 * 1024  # 2 МБ — защита памяти хоста (4 ГБ RAM)

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
            async with session.get(url, allow_redirects=False) as resp:
                raw_bytes = bytearray()
                async for chunk in resp.content.iter_chunked(32768):
                    if len(raw_bytes) + len(chunk) > MAX_RESPONSE_BYTES:
                        return None, f"HTTP {resp.status}: превышен лимит ответа", False
                    raw_bytes.extend(chunk)
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
    # catalog.wb.ru/catalog/.../v2/search удалён вместе с версиями v2-v9.
    # Актуальная выдача — только search.wb.ru/exactmatch/ru/<шард>/v18/search.
    query_string = (
        f"appType=1&curr=rub&dest={{dest}}&inheritFilters=false&lang=ru"
        f"&page=1&query={encoded_query}&resultset=catalog&sort=popular&spp=30"
    )
    candidate_urls = [
        f"https://search.wb.ru/exactmatch/ru/{shard}/v18/search?"
        + query_string.format(dest=dest)
        for dest in ("-1257786", "-1221148", "-1255563")
        for shard in ("common", "ru")
    ]
    headers = {
        "User-Agent": _USER_AGENT,
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "ru-RU,ru;q=0.9",
        "Origin": "https://www.wildberries.ru",
        "Referer": "https://www.wildberries.ru/catalog/0/search.aspx",
        "Sec-Fetch-Site": "same-site",
        "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Dest": "empty",
    }

    raw_products: List[Dict[str, Any]] = []
    errors: List[str] = []
    data: Optional[Any] = None
    err_msg: Optional[str] = None
    for url in candidate_urls:
        data, err_msg, is_transport_err = await _fetch_json_direct(
            url, headers, WB_HTTP_TIMEOUT
        )
        if isinstance(data, dict) and not err_msg:
            products = (data.get("data") or data).get("products")
            if isinstance(products, list) and products:
                raw_products = [p for p in products if isinstance(p, dict)]
                err_msg = None
                break
            if isinstance(products, list):
                err_msg = "WB: пустая выдача"
            else:
                err_msg = "WB: неизвестный формат выдачи"
        errors.append(f"{url.split('?')[1][:40]}…: {err_msg}")
        logger.warning("[Competitors WB] %s", err_msg)
        if is_transport_err:
            await asyncio.sleep(0.4)
    if not raw_products:
        err_msg = (
            "витрина WB не отдала карточки (антибот-защита поиска): "
            + "; ".join(errors[-2:])
        )

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
    encoded_path = urllib.parse.quote(search_path, safe="")
    # composer-api отвечает 307-петлёй антибота, если нет прогретой сессии.
    # Пробуем несколько точек входа и честно сообщаем, что витрина закрыта.
    urls = [
        "https://www.ozon.ru/api/composer-api.bx/page/json/v2?url=" + encoded_path,
        "https://api.ozon.ru/composer-api.bx/page/json/v2?url=" + encoded_path,
        "https://www.ozon.ru/api/entrypoint-api.bx/page/json/v2?url=" + encoded_path,
    ]
    headers = {
        "User-Agent": _USER_AGENT,
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "ru-RU,ru;q=0.9",
        "Referer": "https://www.ozon.ru/search/?text="
        + urllib.parse.quote(clean_query),
        "x-o3-app-name": "dweb_client",
        "Sec-Fetch-Site": "same-origin",
        "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Dest": "empty",
    }

    errors: List[str] = []
    for url in urls:
        data, err_msg, _ = await _fetch_json_direct(url, headers, OZON_HTTP_TIMEOUT)
        if err_msg is not None:
            errors.append(err_msg)
            logger.warning("[Competitors Ozon] live search unavailable: %s", err_msg)
            if "307" in err_msg or "302" in err_msg:
                continue  # антибот-петля: меняем точку входа
            continue
        items = _extract_ozon_items_from_payload(data, limit=safe_count)
        if items:
            return items, None
        errors.append("в JSON-выдаче нет товарных карточек")

    if any("307" in e or "302" in e for e in errors):
        return [], (
            "витрина Ozon закрыта антибот-проверкой (HTTP 307): "
            "публичный поиск доступен только из прогретого браузера; "
            "используйте официальный Seller API"
        )
    return [], "; ".join(dict.fromkeys(errors)) or "витрина Ozon недоступна"


async def parse_ozon_competitors(query: str, count: int = 15) -> List[Dict[str, Any]]:
    """
    Парсит реальную поисковую выдачу Ozon через aiohttp с таймаутами и закрытием сессии.
    При блокировке антиботом или сетевой ошибке возвращает пустой список без фейковых SKU.
    """
    items, _ = await _parse_ozon_competitors_with_status(query=query, count=count)
    return items


def calculate_competitor_unit_economy(buyer_price: float, mp: str, *, seller_price: float,
                                      cogs: float, logistics_rub: float, commission_rate: float) -> dict:
    """Explicit what-if scenario, never infer a competitor's costs or seller price from the shelf."""
    mp = mp.upper()
    if (mp not in ('OZON', 'WB') or not all(math.isfinite(x) and x >= 0 for x in
            (buyer_price, seller_price, cogs, logistics_rub, commission_rate)) or
            min(buyer_price, seller_price) <= 0 or commission_rate >= 1):
        raise ValueError('Для сценария нужны цены, себестоимость, логистика и допустимые ставки')
    rate = settings.OZON_HIGH_COMMISSION_RATE if mp == 'OZON' and seller_price > settings.OZON_LOW_PRICE_LIMIT else commission_rate
    commission = _mul_rub(seller_price, rate)
    tax = _mul_rub(buyer_price if mp == 'WB' else seller_price, settings.TAX_RATE)
    acquiring = _mul_rub(seller_price, settings.ACQUIRING_RATE)
    expenses = _round_rub(_to_dec(commission) + _to_dec(logistics_rub) + _to_dec(acquiring))
    profit = _round_rub(_to_dec(seller_price) - _to_dec(cogs) - _to_dec(tax) - _to_dec(expenses))
    return dict(seller_price=seller_price, buyer_price=buyer_price, commission_rate=rate,
                commission=commission, logistics=logistics_rub, cogs=cogs, acquiring=acquiring,
                mp_expenses=expenses, tax=tax, profit=profit, ros=_round_pct(profit, seller_price))


async def _call_gemini_competitor_verdict(prompt: str) -> str:
    try:
        text, _ = await generate(prompt, max_tokens=4096, temperature=0.3)
        return sanitize_telegram_html(text)
    except RuntimeError as exc:
        return f"⚠️ <i>ИИ-разбор временно недоступен ({html.escape(str(exc))}).</i>"


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
            mp_lbl = html.escape(str(comp["mp"]))
            sku_lbl = html.escape(str(comp["sku"]))
            brand_lbl = html.escape(str(comp["brand"]))
            raw_name = str(comp["name"])
            short_name = (raw_name[:28] + "...") if len(raw_name) > 28 else raw_name
            name_lbl = html.escape(short_name)

            html_rows.append(
                f"• [{mp_lbl}] Арт. <code>{sku_lbl}</code> | <b>{brand_lbl}</b> — {name_lbl}\n"
                f"   Витрина: <b>{comp['buyer_price']:.0f} ₽</b>"
            )
            plain_rows.append(
                f"[{comp['mp']}] SKU {comp['sku']} | {comp['brand']} — {short_name} | "
                f"витрина {comp['buyer_price']:.0f}₽, "
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
        "Цены витрины не раскрывают цену в ЛК, СПП, тарифы и себестоимость конкурентов. "
        "Не вычисляй по ним чужую прибыль. Отделяй гипотезы от наблюдений.\n"
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
        "ℹ️ <i>Это цены витрин, не цены в ЛК. Себестоимость, логистика и прибыль конкурентов неизвестны.</i>\n\n"
        f"💵 <b>Цены на витринах:</b>\n"
        f"• Wildberries: {wb_price_str}\n"
        f"• Ozon: {oz_price_str}\n"
        f"• Средняя по выборке: <b>{avg_total:.0f} ₽</b> | Медиана выборки: <b>{median_total:.0f} ₽</b>\n\n"
        f"<b>Выборка Wildberries:</b>\n{wb_section}\n\n"
        f"<b>Выборка Ozon:</b>\n{oz_section}\n\n"
        f"➖➖➖➖➖➖➖➖➖➖\n"
        f"🧠 <b>Аналитический вердикт и закрытие болей:</b>\n\n"
        f"{ai_verdict}"
    )

    return report_text, prices_dict
