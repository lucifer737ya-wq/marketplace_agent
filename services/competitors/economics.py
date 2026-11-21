"""Экономика конкурентов: примерная цена в ЛК продавца и наша чистая прибыль при такой цене.

* WB: на витрине цена с учётом СПП (скидка постоянного покупателя за счёт WB). Цена в ЛК продавца
  ≈ витрина / (1 − СПП). СПП берётся из ваших карточек (buyer_price / current_price), иначе из
  ``COMPETITOR_WB_SPP_RATE``.
* Ozon: цена ЛК ≈ витрина / (1 − скидка Ozon за его счёт, ``COMPETITOR_OZON_DISCOUNT_RATE``).

Чистая прибыль — «если бы мы продавали по такой цене» на нашей закупке: себестоимость + упаковка +
первая миля (средняя по каталогу), вознаграждение площадки, эквайринг, логистика, УСН 6%.
Ozon: вознаграждение 52% и дорогая логистика выше порога 300 ₽ (логистика — формула по объёму
0,18 л). Реклама и хранение не учитываются: оценка ориентировочная.
"""

from __future__ import annotations

import asyncio
import math
import statistics
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional

from config import settings
from services.common.ozon_tariffs import ozon_logistics_cost

# Типичные «прочие постоянные» расходы Ozon на заказ (возвраты, drop-off, упаковка, ПВЗ), ₽.
OZON_OTHER_FIXED_TYPICAL = 15.0
WB_DEFAULT_COMMISSION = 0.42
WB_DEFAULT_LOGISTICS = 50.0
OZON_DEFAULT_COMMISSION = 0.23


def _f(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if math.isfinite(number) else default


@dataclass(frozen=True)
class OurEconomy:
    cogs_unit: Optional[float]      # средняя себестоимость банки (закупка + упаковка + первая миля), ₽
    wb_spp: float                   # СПП WB, доля
    wb_commission: float
    wb_logistics: float
    ozon_commission_low: float
    ozon_discount: float
    note: str = ""


def spp_from_items(items: Iterable[Dict[str, Any]], fallback: float) -> float:
    ratios = []
    for it in items or []:
        if not isinstance(it, dict):
            continue
        cur, buyer = _f(it.get("current_price")), _f(it.get("buyer_price"))
        if cur > 0 and 0 < buyer <= cur:
            ratios.append(buyer / cur)
    if not ratios:
        return fallback
    spp = 1.0 - statistics.median(ratios)
    return spp if 0.0 <= spp <= 0.6 else fallback


def build_context(products: Iterable[Any], oz_items: Iterable[Dict[str, Any]],
                  wb_items: Iterable[Dict[str, Any]]) -> OurEconomy:
    costs = []
    for p in products or []:
        total = (_f(getattr(p, "cost_price", 0.0)) + _f(getattr(p, "packaging_cost", 0.0))
                 + _f(getattr(p, "first_mile_unit_cost", 0.0)))
        if _f(getattr(p, "cost_price", 0.0)) > 0:
            costs.append(total)
    cogs = round(statistics.mean(costs), 2) if costs else None
    wb_list = [i for i in (wb_items or []) if isinstance(i, dict)]
    oz_list = [i for i in (oz_items or []) if isinstance(i, dict)]
    wb_comm = [c for c in (_f(i.get("commission_rate")) for i in wb_list) if 0.01 <= c < 0.60]
    wb_log = [c for c in (max(_f(i.get("fbo_logistics_cost")), _f(i.get("fbs_logistics_cost"))) for i in wb_list) if c > 0]
    oz_comm = [c for c in (_f(i.get("commission_rate")) for i in oz_list) if 0.05 <= c <= 0.35]
    return OurEconomy(
        cogs_unit=cogs,
        wb_spp=spp_from_items(wb_list, float(settings.COMPETITOR_WB_SPP_RATE)),
        wb_commission=round(statistics.mean(wb_comm), 4) if wb_comm else WB_DEFAULT_COMMISSION,
        wb_logistics=round(statistics.mean(wb_log), 2) if wb_log else WB_DEFAULT_LOGISTICS,
        ozon_commission_low=round(statistics.mean(oz_comm), 4) if oz_comm else OZON_DEFAULT_COMMISSION,
        ozon_discount=min(max(float(settings.COMPETITOR_OZON_DISCOUNT_RATE), 0.0), 0.6),
    )


def lk_price(mp: str, showcase: float, ctx: OurEconomy) -> float:
    """Примерная цена в ЛК продавца: витрина + возврат скидки площадки (СПП / скидка Ozon)."""
    disc = ctx.wb_spp if str(mp).upper() == "WB" else ctx.ozon_discount
    disc = min(max(disc, 0.0), 0.6)
    return round(showcase / (1.0 - disc), 2) if showcase > 0 else 0.0


def net_profit(mp: str, lk: float, showcase: float, ctx: OurEconomy) -> Optional[float]:
    """Наша чистая прибыль с банки при цене ЛК ``lk``; ``None`` — нет себестоимости."""
    if ctx.cogs_unit is None or lk <= 0:
        return None
    acq = settings.ACQUIRING_RATE
    tax_rate = settings.TAX_RATE
    if str(mp).upper() == "WB":
        # База УСН на WB — цена покупателя с СПП.
        return round(lk * (1 - ctx.wb_commission - acq) - ctx.cogs_unit - ctx.wb_logistics
                     - showcase * tax_rate, 2)
    limit = float(settings.OZON_LOW_PRICE_LIMIT)
    comm = settings.OZON_HIGH_COMMISSION_RATE if lk > limit else ctx.ozon_commission_low
    logistics = ozon_logistics_cost(settings.OZON_ITEM_VOLUME_L, lk, limit)
    fixed = OZON_OTHER_FIXED_TYPICAL + float(settings.OZON_EXTRA_FIXED_PER_ORDER)
    return round(lk * (1 - comm - acq - tax_rate) - ctx.cogs_unit - logistics - fixed, 2)


def annotate(comps: Iterable[Dict[str, Any]], ctx: OurEconomy) -> List[Dict[str, Any]]:
    out = []
    for comp in comps or []:
        if not isinstance(comp, dict):
            continue
        mp = str(comp.get("mp") or "").upper()
        mp = "OZON" if "OZ" in mp else "WB"
        shop = _f(comp.get("buyer_price"))
        lk = lk_price(mp, shop, ctx)
        out.append(dict(comp, est_lk_price=lk, est_profit=net_profit(mp, lk, shop, ctx)))
    return out


def format_economy_line(comp: Dict[str, Any], ctx: OurEconomy) -> str:
    """Строка под карточкой конкурента (HTML)."""
    lk = _f(comp.get("est_lk_price"))
    if lk <= 0:
        return ""
    mp = str(comp.get("mp") or "").upper()
    if "OZ" in mp:
        disc = ctx.ozon_discount
        hint = f"скидка Ozon {disc * 100:.0f}%" if disc > 0 else "без скидки Ozon"
        warn = " ⚠️ выше 300 ₽: комиссия 52%" if lk > settings.OZON_LOW_PRICE_LIMIT else ""
    else:
        hint = f"СПП ~{ctx.wb_spp * 100:.0f}%"
        warn = ""
    profit = comp.get("est_profit")
    prof = "прибыль н/д (нет себестоимости)" if profit is None else f"у нас <b>{profit:+.0f} ₽/шт</b>"
    return f"   ≈ в ЛК продавца: <b>{lk:.0f} ₽</b> ({hint}){warn} | чистая прибыль {prof}"


def summarize(comps: List[Dict[str, Any]]) -> Optional[str]:
    lks = [c["est_lk_price"] for c in comps if _f(c.get("est_lk_price")) > 0]
    profits = [c["est_profit"] for c in comps if c.get("est_profit") is not None]
    if not lks:
        return None
    text = f"медиана цены в ЛК ≈ <b>{statistics.median(lks):.0f} ₽</b>"
    if profits:
        text += f", медианная чистая прибыль у нас <b>{statistics.median(profits):+.0f} ₽/шт</b>"
    return text


async def load_context(ozon_api: Any = None, wb_api: Any = None, timeout: float = 20.0) -> OurEconomy:
    """Контекст из каталога БД и (если доступны) кэшированных цен Ozon/WB; любой сбой — типовые значения."""
    async def safe(coro):
        try:
            return await asyncio.wait_for(coro, timeout=timeout)
        except Exception:  # noqa: BLE001 — отчёт важнее точности контекста
            return []

    try:
        from database.crud import get_all_products

        products = await get_all_products()
    except Exception:  # noqa: BLE001
        products = []
    oz = await safe(ozon_api.fetch_stocks_and_prices()) if ozon_api is not None else []
    wb = await safe(wb_api.fetch_stocks_and_prices()) if wb_api is not None else []
    return build_context(products, oz if isinstance(oz, list) else [], wb if isinstance(wb, list) else [])


def ozon_index_block(oz_items: Iterable[Dict[str, Any]], ctx: OurEconomy) -> List[str]:
    """Ozon без витрины: индексы цен из Seller API (/v5/product/info/prices → price_indexes)."""
    items = [i for i in (oz_items or []) if isinstance(i, dict)]
    on_ozon = [_f(i.get("competitor_min_ozon")) for i in items if _f(i.get("competitor_min_ozon")) > 0]
    ext = [_f(i.get("competitor_min_external")) for i in items if _f(i.get("competitor_min_external")) > 0]
    if not on_ozon and not ext:
        return []
    lines = ["🔵 <b>Ozon — индекс цен из Seller API</b> (витрина закрыта антиботом; это официальные данные Ozon "
             "по вашим SKU: минимальная цена схожих товаров):"]
    for label, values, mp in (("на Ozon", on_ozon, "OZON"), ("на других площадках", ext, "OZON")):
        if not values:
            continue
        med = statistics.median(values)
        lk = lk_price(mp, med, ctx)
        profit = net_profit(mp, lk, med, ctx)
        prof = "н/д" if profit is None else f"{profit:+.0f} ₽/шт"
        lines.append(
            f"• мин. цена {label}: медиана <b>{med:.0f} ₽</b> (от {min(values):.0f} до {max(values):.0f}, "
            f"SKU: {len(values)}) → цена ЛК ≈ {lk:.0f} ₽, чистая прибыль у нас {prof}"
        )
    return lines
