"""ИИ-разбор карточек: чем конкуренты выигрывают у покупателя и чего нет у нас.

Цель — не «у них 18 фото, у вас 10», а ответ на вопрос «как покупатель выбирает банку геля»:
цена за грамм, общая цена и порог 300 ₽, доверие (рейтинг × число отзывов), бренд против «без бренда»,
выбор оттенков, набор против одиночной банки, состав/безопасность, быстрый результат в работе.
Числа считает код (ИИ их не выдумывает), ИИ расставляет их по критериям и ставит измеримые цели.
"""

from __future__ import annotations

import html
import logging
import re
import statistics
from typing import Any, Dict, List, Optional

from config import settings
from products_catalog import GEL_COMMON_DESCRIPTION, PRODUCTS_CATALOG as CATALOG

logger = logging.getLogger(__name__)

_GRAMS_RE = re.compile(r"(\d{1,3}(?:[.,]\d)?)\s*(?:г|гр|g|мл|ml)\b", re.I)
_PACK_RE = re.compile(r"(?:набор|комплект|\bиз\s*\d+\s*шт|\b\d+\s*шт\b|\b\d+\s*цвет)", re.I)


def _f(v: Any) -> float:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return 0.0
    return x if x == x and x not in (float("inf"), float("-inf")) else 0.0


def parse_grams(name: str) -> Optional[float]:
    m = _GRAMS_RE.search(name or "")
    if not m:
        return None
    value = float(m.group(1).replace(",", "."))
    return value if 1 <= value <= 500 else None


def competitor_metrics(comps: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Сводка по конкурентам, которую код считает сам (без участия ИИ)."""
    prices, per_g, ratings, feedbacks = [], [], [], []
    no_brand = packs = with_rating = 0
    rows = []
    for c in comps:
        price = _f(c.get("buyer_price"))
        if price <= 0:
            continue
        name = str(c.get("name") or "")
        grams = parse_grams(name)
        is_pack = bool(_PACK_RE.search(name))
        prices.append(price)
        if grams:
            per_g.append(price / grams)
        rating, fb = _f(c.get("rating")), int(_f(c.get("feedbacks")))
        if rating > 0 or fb > 0:
            with_rating += 1
            ratings.append(rating)
            feedbacks.append(fb)
        no_brand += str(c.get("brand") or "").lower() in {"", "без бренда", "нет бренда"}
        packs += is_pack
        rows.append(f"{c.get('mp')} | {name[:70]} | {price:.0f} ₽ | "
                    f"{(str(round(price / grams, 1)) + ' ₽/г') if grams else '₽/г н/д'} | "
                    f"{'набор' if is_pack else 'одна банка'} | "
                    f"{('⭐' + format(rating, '.1f') + ' (' + str(fb) + ')') if rating > 0 or fb > 0 else 'рейтинг н/д'}")
    n = len(prices)
    return {
        "n": n,
        "median_price": round(statistics.median(prices), 0) if prices else None,
        "min_price": min(prices) if prices else None,
        "median_per_gram": round(statistics.median(per_g), 1) if per_g else None,
        "min_per_gram": round(min(per_g), 1) if per_g else None,
        "no_brand_share": round(no_brand / n * 100) if n else None,
        "pack_share": round(packs / n * 100) if n else None,
        "rating_known": with_rating,
        "median_rating": round(statistics.median(ratings), 2) if ratings else None,
        "median_feedbacks": int(statistics.median(feedbacks)) if feedbacks else None,
        "rows": rows[:24],
    }


def our_card_facts(wb_spp: float, oz_disc: float, oz_items: List[Dict[str, Any]], wb_items: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Факты о нашей карточке: цена на витрине, цена за грамм, ассортимент оттенков, формула."""
    oz_lk = [_f(i.get("current_price")) for i in oz_items if _f(i.get("current_price")) > 0]
    wb_lk = [_f(i.get("current_price")) for i in wb_items if _f(i.get("current_price")) > 0]
    shades = sorted({str(v.get("shade") or "") for v in CATALOG.values() if isinstance(v, dict) and v.get("shade")})
    oz_show = statistics.median(oz_lk) * (1 - oz_disc) if oz_lk else None
    wb_show = statistics.median(wb_lk) * (1 - wb_spp) if wb_lk else None
    return {
        "ozon_lk": round(statistics.median(oz_lk)) if oz_lk else None,
        "ozon_showcase": round(oz_show) if oz_show else None,
        "ozon_per_gram": round(oz_show / 15, 1) if oz_show else None,
        "wb_lk": round(statistics.median(wb_lk)) if wb_lk else None,
        "wb_showcase": round(wb_show) if wb_show else None,
        "wb_per_gram": round(wb_show / 15, 1) if wb_show else None,
        "shades": len(shades),
        "volume": "15 г",
        "formula": "HEMA FREE, 11-FREE, средне-вязкий, полимеризация 60–90 сек UV/LED, стойкость до 4 недель",
        "description_excerpt": GEL_COMMON_DESCRIPTION[:700],
    }


def build_prompt(query: str, wb: Dict[str, Any], oz: Dict[str, Any], ours: Dict[str, Any]) -> str:
    rows = "\n".join(oz["rows"] + wb["rows"]) or "нет карточек"
    return f"""Ты — аналитик маркетплейсов. Разбери, КАК ПОКУПАТЕЛЬ ВЫБИРАЕТ товар по запросу «{query}»,
и чем карточки конкурентов выигрывают у наших (бренд YaSoKo, гель 15 г).

ЗАПРЕЩЕНО: общие советы вида «добавь фото», «у них 18 фото, у вас 10», «улучшите описание», «сделайте красивее».
Каждый пункт обязан отвечать на вопрос «почему покупатель кликнет/купит именно у них» и содержать ЧИСЛО-ЦЕЛЬ.
Используй ТОЛЬКО данные ниже. Если данных нет — пиши «н/д» и не выдумывай. Не называй базы/топы YaSoKo — их нет в продаже.

ДАННЫЕ КОНКУРЕНТОВ (код посчитал сам):
Ozon: карточек {oz['n']}, медиана витрины {oz['median_price']} ₽, мин {oz['min_price']} ₽, медиана за грамм {oz['median_per_gram']} ₽/г, без бренда {oz['no_brand_share']}%, наборов {oz['pack_share']}%.
WB: карточек {wb['n']}, медиана витрины {wb['median_price']} ₽, мин {wb['min_price']} ₽, медиана за грамм {wb['median_per_gram']} ₽/г, без бренда {wb['no_brand_share']}%, наборов {wb['pack_share']}%.
Рейтинг/отзывы известны у {oz['rating_known'] + wb['rating_known']} карточек (медианный рейтинг Ozon {oz['median_rating']}, WB {wb['median_rating']}; медиана отзывов Ozon {oz['median_feedbacks']}, WB {wb['median_feedbacks']}).
Карточки (площадка | название | витрина | ₽/г | формат | рейтинг):
{rows}

НАШИ ДАННЫЕ:
Ozon: цена в ЛК {ours['ozon_lk']} ₽ → витрина ≈ {ours['ozon_showcase']} ₽ ({ours['ozon_per_gram']} ₽/г).
WB: цена в ЛК {ours['wb_lk']} ₽ → витрина ≈ {ours['wb_showcase']} ₽ ({ours['wb_per_gram']} ₽/г).
Оттенков в линейке: {ours['shades']}; объём {ours['volume']}; формула: {ours['formula']}.
Фрагмент нашего описания: {ours['description_excerpt']}
Ограничение Ozon: цена в ЛК до 300 ₽ — льготная комиссия, выше — комиссия 52%.

ФОРМАТ ОТВЕТА (HTML для Telegram: только <b>, <i>, <code>; без таблиц, без markdown):
<b>Как покупатель выбирает (по убыванию влияния)</b> — 5–7 критериев. Для каждого одним блоком:
• <b>Критерий</b> — что на этом критерии у конкурентов (с числами из данных) → что у нас → ЦЕЛЬ (измеримая: «витрина ≤ N ₽», «≥ N отзывов при рейтинге ≥ 4.8», «набор из N банок за N ₽» и т.п.) → как проверить результат.
<b>Чего нет у нас, но есть у конкурентов</b> — только то, что влияет на выбор (по пунктам, с числом).
<b>Чего нет у конкурентов, но есть у нас</b> — наши сильные стороны, которые стоит вынести в первый экран карточки.
<b>3 приоритетные цели на 14 дней</b> — каждая с числом и ожидаемым эффектом на заказы.
Не более 2800 символов."""


async def run_ai_card_analysis(query: str, wb_comps: List[Dict[str, Any]], oz_comps: List[Dict[str, Any]],
                               ozon_api: Any = None, wb_api: Any = None, wb_spp: float = 0.27) -> str:
    """HTML-блок с ИИ-разбором; любой сбой → понятная строка, а не падение отчёта."""
    import asyncio

    from services.common.ad_recommendations import sanitize_telegram_html
    from services.common.gemini import generate

    if not wb_comps and not oz_comps:
        return ("🧠 <b>ИИ-разбор карточек</b>: нет карточек конкурентов с витрин — разбирать нечего. "
                "Запустите «🔎 Пробив конкурентов» после настройки браузера (login_wb.bat).")

    async def safe(coro):
        try:
            return await asyncio.wait_for(coro, timeout=20)
        except Exception:  # noqa: BLE001
            return []

    oz_items = await safe(ozon_api.fetch_stocks_and_prices()) if ozon_api is not None else []
    wb_items = await safe(wb_api.fetch_stocks_and_prices()) if wb_api is not None else []
    oz_items = oz_items if isinstance(oz_items, list) else []
    wb_items = wb_items if isinstance(wb_items, list) else []
    ours = our_card_facts(wb_spp, float(settings.COMPETITOR_OZON_DISCOUNT_RATE),
                          [i for i in oz_items if isinstance(i, dict)], [i for i in wb_items if isinstance(i, dict)])
    prompt = build_prompt(query, competitor_metrics(wb_comps), competitor_metrics(oz_comps), ours)
    try:
        text, _ = await generate(prompt, max_tokens=3500, temperature=0.25)
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001
        logger.warning("ИИ-разбор карточек недоступен: %s", exc)
        return f"🧠 <b>ИИ-разбор карточек</b>: временно недоступен ({html.escape(str(exc)[:160])})."
    return "🧠 <b>ИИ-разбор: как покупатель выбирает</b>\n" + sanitize_telegram_html(text)
