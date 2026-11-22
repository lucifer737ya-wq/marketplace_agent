"""Ориентировочные цены в ЛК продавца с учётом ВАШИХ фактических комиссий.

Ставки берутся не из головы, а из отчёта площадки за последние дни:
вознаграждение, эквайринг, реклама, хранение, логистика и услуги партнёров
в пересчёте на заказ или на выручку. Налог УСН и целевая маржа — из ``.env``.

Формула: ``P · (1 − комиссия − %расходов − налог − маржа) = себестоимость + ₽/заказ``.
Для Ozon учитывается скачок комиссии на пороге ``OZON_LOW_PRICE_LIMIT``.
Расчёт ориентировочный: логистика зависит от объёма/локализации, а на WB — ещё
и от СПП и коэффициентов склада; перед правкой цены сверяйте с калькулятором ЛК.
"""

from __future__ import annotations

import html
import math
import re
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional

from config import settings
from services.common.cogs import ozon_tax_base_ratio, unit_cogs_of
from services.common.ozon_tariffs import ozon_logistics_cost, set_volume_l
from services.competitors.economics import spp_from_items


def _f(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if math.isfinite(number) else default


@dataclass(frozen=True)
class Rates:
    """Фактические ставки площадки: доли от выручки и ₽ на заказ."""

    commission_low: float
    commission_high: float
    pct_other: float          # эквайринг + реклама + хранение + прочее, доля от цены
    fixed_per_order: float    # логистика + ПВЗ + упаковка партнёров, ₽ на заказ
    source: str
    # Ozon: логистика считается по формуле объём×цена ЛК; fixed_per_order тогда —
    # только «прочие постоянные» расходы (возвраты, drop-off, упаковка, ПВЗ).
    volume_l: Optional[float] = None
    # Реклама Ozon в составе ``pct_other``: доля от выручки и ₽ на заказ. Для наборов реклама —
    # это стоимость ЗАКАЗА, а не % от цены (иначе на 1100 ₽ закладывалось ~140 ₽ рекламы вместо ~45 ₽).
    ads_pct: float = 0.0
    ads_per_order: float = 0.0

    def fixed_for(self, high_tier: bool) -> float:
        """₽ на заказ на соответствующем тарифе (выше порога логистика Ozon заметно дороже)."""
        if self.volume_l is None:
            return self.fixed_per_order
        limit = float(settings.OZON_LOW_PRICE_LIMIT)
        probe = limit + 1.0 if high_tier else limit
        return ozon_logistics_cost(self.volume_l, probe, limit) + self.fixed_per_order


@dataclass(frozen=True)
class PriceAdvice:
    price: int
    tier: str                 # "low" — льготная комиссия, "high" — выше порога
    commission: float
    profit: float             # чистая прибыль с заказа на этой цене, ₽
    margin: float             # прибыль / цена


def _sold_units(fin: Dict[str, Any]) -> int:
    """Проданные единицы (без строк возвратов и строк с нулевой ценой)."""
    orders = fin.get("orders")
    if isinstance(orders, list) and orders:
        units = 0
        for o in orders:
            if not isinstance(o, dict):
                continue
            if str(o.get("status") or "").lower() in {"returned", "return", "cancelled", "canceled"}:
                continue
            if _f(o.get("price")) <= 0:
                continue
            units += max(int(_f(o.get("quantity"), 1)), 1)
        if units > 0:
            return units
    return int(_f(fin.get("orders_count")))


def ozon_rates_from_finance(fin: Optional[Dict[str, Any]]) -> Rates:
    """Ставки Ozon из ``fetch_real_finance_breakdown``; при пустом отчёте — из .env.

    Прямая логистика заменяется формулой по объёму (``OZON_LOGISTICS_BY_FORMULA``);
    остальные постоянные расходы маркетплейса (обратная логистика/невыкупы, доставка до
    ПВЗ, drop-off, упаковка, возвраты партнёров) усредняются на ПРОДАННУЮ единицу.
    """
    default_low = 0.23
    by_formula = bool(settings.OZON_LOGISTICS_BY_FORMULA)
    volume = float(settings.OZON_ITEM_VOLUME_L) if by_formula else None
    extra_env = max(float(settings.OZON_EXTRA_FIXED_PER_ORDER), 0.0)
    fallback = Rates(
        default_low,
        settings.OZON_HIGH_COMMISSION_RATE,
        settings.ACQUIRING_RATE + 0.10,
        (15.0 if by_formula else 45.0) + extra_env,
        "нет отчёта: типовые значения (комиссия 23%, реклама 10%, прочие постоянные 15 ₽, логистика по формуле)"
        if by_formula else "нет отчёта: типовые значения (комиссия 23%, реклама 10%, логистика 45 ₽)",
        volume,
    )
    if not isinstance(fin, dict):
        return fallback
    sales = abs(_f(fin.get("sales_total")))
    orders = _sold_units(fin)
    if sales <= 0 or orders <= 0:
        return fallback
    comm = abs(_f(fin.get("commission"))) / sales
    if not 0.05 <= comm <= 0.30:
        comm = default_low  # в отчёте уже смешаны тарифы выше и ниже порога
    pct = sum(
        abs(_f(fin.get(key)))
        for key in ("acquiring", "ads_total", "fbo_storage", "star_products",
                    "other_fines", "decompensations", "mutual_settlements")
    ) / sales
    other_keys = ["deliv_return", "deliv_ozon_handover", "partner_dropoff", "partner_pack",
                  "partner_returns", "partner_last_mile"]
    if not by_formula:
        other_keys.insert(0, "deliv_direct")
    fixed = sum(abs(_f(fin.get(key))) for key in other_keys) / orders + extra_env
    avg_check = sales / orders
    ads_abs = abs(_f(fin.get("ads_total")))
    return Rates(
        comm, settings.OZON_HIGH_COMMISSION_RATE, pct, round(fixed, 2),
        f"факт Ozon: {orders} проданных шт., средний чек {avg_check:.0f} ₽, комиссия {comm * 100:.1f}%, "
        f"прочие удержания {pct * 100:.1f}%, постоянные {fixed:.0f} ₽/шт"
        + (f" + логистика по формуле (объём {volume:g} л)" if by_formula else ""),
        volume,
        ads_pct=ads_abs / sales,
        ads_per_order=round(ads_abs / orders, 2),
    )


def wb_rates_from_item(item: Dict[str, Any], fin: Optional[Dict[str, Any]]) -> Rates:
    """Ставки WB: комиссия предмета из тарифов, логистика — базовый тариф склада."""
    comm = _f(item.get("commission_rate"))
    if not 0.01 <= comm < 0.60:
        comm = 0.25  # нет тарифа предмета: типичный диапазон WB, пометка в источнике
        comm_note = "комиссия н/д → 25%"
    else:
        comm_note = f"комиссия предмета {comm * 100:.1f}%"
    logistics = max(_f(item.get("fbo_logistics_cost")), _f(item.get("fbs_logistics_cost")), 50.0)
    ads_pct = 0.08
    storage_pct = 0.02
    if isinstance(fin, dict) and fin.get("orders_count"):
        sales = abs(_f(fin.get("revenue_seller")))
        if sales > 0:
            ads_pct = min(abs(_f(fin.get("ads"))) / sales, 0.40)
            storage_pct = min(
                (abs(_f(fin.get("storage"))) + abs(_f(fin.get("acceptance")))) / sales, 0.20
            )
    pct = settings.ACQUIRING_RATE + ads_pct + storage_pct
    return Rates(
        comm, comm, pct, round(logistics, 2),
        f"WB: {comm_note}, логистика ~{logistics:.0f} ₽, реклама+хранение {100 * (ads_pct + storage_pct):.1f}%",
    )


def profit_at(price: float, cogs: float, rates: Rates, *, high_tier: Optional[bool] = None,
              tax_base_ratio: float = 1.0, limit: Optional[float] = None) -> float:
    """Чистая прибыль с одного заказа при цене ЛК ``price`` (₽)."""
    if high_tier is None:
        high_tier = limit is not None and price > limit
    comm = rates.commission_high if high_tier else rates.commission_low
    tax = settings.TAX_RATE * tax_base_ratio
    return price * (1 - comm - rates.pct_other - tax) - cogs - rates.fixed_for(bool(high_tier))


def solve_price(cogs: float, rates: Rates, margin: float, *, tiered: bool,
                tax_base_ratio: float = 1.0, profit_abs: float = 0.0) -> Optional[PriceAdvice]:
    """Минимальная целая цена ЛК для заданной маржи; ``None`` — цена недостижима."""
    margin = max(margin, 0.0)
    tax = settings.TAX_RATE * tax_base_ratio
    limit = float(settings.OZON_LOW_PRICE_LIMIT)

    def solve(comm: float, high: bool) -> Optional[int]:
        denom = 1 - comm - rates.pct_other - tax - margin
        if denom <= 0.01:
            return None
        return max(1, math.ceil((cogs + rates.fixed_for(high) + profit_abs) / denom))

    low = solve(rates.commission_low, False)
    if not tiered:
        if low is None:
            return None
        return _advice(low, False, cogs, rates, tax_base_ratio, limit=None)
    if low is not None and low <= limit:
        return _advice(low, False, cogs, rates, tax_base_ratio, limit)
    high = solve(rates.commission_high, True)
    if high is None:
        return None
    return _advice(max(high, int(limit) + 1), True, cogs, rates, tax_base_ratio, limit)


def _advice(price: int, high: bool, cogs: float, rates: Rates, ratio: float,
            limit: Optional[float]) -> PriceAdvice:
    profit = profit_at(price, cogs, rates, high_tier=high, tax_base_ratio=ratio)
    return PriceAdvice(
        price, "high" if high else "low",
        rates.commission_high if high else rates.commission_low,
        round(profit, 2), round(profit / price, 4) if price else 0.0,
    )


def _cogs(row: Any) -> float:
    return unit_cogs_of(row)


def _pct_ru(value: float) -> str:
    """18.2 → «18,2%» (десятичная запятая, знак для убытка)."""
    return f"{value:+.1f}%".replace(".", ",") if value < 0 else f"{value:.1f}%".replace(".", ",")


def _line(label: str, art: str, current: float, cogs: float, rates: Rates, *,
          tiered: bool, ratio: float, effective: Optional[float] = None) -> str:
    """Строка «Цены в ЛК»: фактическая чистая прибыль и её процент от цены, цена «в ноль».

    ``effective`` — цена реализации после акций Ozon (marketing_seller_price), если она
    ниже цены в ЛК: именно с неё Ozon считает вознаграждение, налог и выплату.
    """
    limit = float(settings.OZON_LOW_PRICE_LIMIT) if tiered else None
    real = effective if effective and 0 < effective < current else current
    cur_profit = profit_at(real, cogs, rates, limit=limit, tax_base_ratio=ratio) if real > 0 else None
    be = solve_price(cogs, rates, 0.0, tiered=tiered, tax_base_ratio=ratio)
    be_text = "недостижима" if be is None else f"<b>{be.price} ₽</b>" + (
        " (тариф выше порога)" if be.tier == "high" else ""
    )
    if cur_profit is None:
        now = "чистая прибыль н/д"
    else:
        pct = cur_profit / real * 100.0
        now = f"чистая прибыль {cur_profit:+.0f} ₽/шт (<b>{_pct_ru(pct)}</b>)"
    price_txt = f"{current:.0f} ₽" if real == current else f"{current:.0f} ₽ (по акции {real:.0f} ₽)"
    warn = ""
    if tiered and limit is not None and current > limit:
        warn = f" ⚠️ выше {limit:.0f} ₽: комиссия {rates.commission_high * 100:.0f}%"
    return (
        f"• [{label}] <code>{html.escape(art)}</code>: в ЛК {price_txt} → {now} → "
        f"в ноль: {be_text}{warn}"
    )


def build_bundle_lines(cogs_unit: float, rates: Rates, *, max_units: int = 4) -> List[str]:
    """Наборы из N банок на Ozon: выгодна ли цена ≤300 ₽ или выше порога (с логистикой по формуле).

    Отличия от прежней версии (набор 2 шт. «в ноль» 1125 ₽ при реальных ~41%):
    * налог 6% — со стоимости для покупателя (``ozon_tax_base_ratio``);
    * реклама — ₽ за заказ (CPO), а не % от цены набора;
    * цели — по ROI к себестоимости: 30% и 100% (коридор рекомендаций).
    """
    if cogs_unit <= 0 or rates.volume_l is None:
        return []
    limit = float(settings.OZON_LOW_PRICE_LIMIT)
    unit_vol = float(settings.OZON_ITEM_VOLUME_L)
    ratio = ozon_tax_base_ratio()
    roi_min, roi_max = float(settings.PRICE_MIN_ROI), float(settings.PRICE_MAX_ROI)
    out = ["", "📦 <b>Наборы Ozon</b> (себестоимость банки "
           f"{cogs_unit:.0f} ₽ = закупка + упаковка {settings.PACKAGING_COST_PER_UNIT:.0f} + зарплата "
           f"{settings.LABOR_COST_PER_UNIT:.0f}; объём {unit_vol:g} л; налог с цены покупателя ≈{ratio * 100:.0f}% цены ЛК; "
           "реклама — как стоимость заказа):"]
    for n in range(2, max_units + 1):
        vol = set_volume_l(n, unit_vol)
        sub = Rates(rates.commission_low, rates.commission_high, max(rates.pct_other - rates.ads_pct, 0.0),
                    rates.fixed_per_order + rates.ads_per_order, rates.source, vol)
        cogs = cogs_unit * n
        low_p = profit_at(limit, cogs, sub, high_tier=False, tax_base_ratio=ratio)
        be = solve_price(cogs, sub, 0.0, tiered=True, tax_base_ratio=ratio)
        lo_t = solve_price(cogs, sub, 0.0, tiered=True, tax_base_ratio=ratio, profit_abs=roi_min * cogs)
        hi_t = solve_price(cogs, sub, 0.0, tiered=True, tax_base_ratio=ratio, profit_abs=roi_max * cogs)
        verdict = "✅" if low_p > 0 else "❌"

        def fmt(adv: Optional[PriceAdvice]) -> str:
            if adv is None:
                return "недостижима"
            return f"{adv.price} ₽" + (" (тариф 52%)" if adv.tier == "high" else "")

        out.append(
            f"• {n} шт ({vol:g} л): на {limit:.0f} ₽ {verdict} {low_p:+.0f} ₽/набор; "
            f"в ноль: <b>{fmt(be)}</b>; ROI {roi_min * 100:.0f}%: <b>{fmt(lo_t)}</b>; ROI {roi_max * 100:.0f}%: <b>{fmt(hi_t)}</b>"
        )
    return out


def build_price_lines(
    oz_items: Iterable[Dict[str, Any]],
    wb_items: Iterable[Dict[str, Any]],
    products: Iterable[Any],
    oz_fin: Optional[Dict[str, Any]],
    wb_fin: Optional[Dict[str, Any]],
) -> List[str]:
    costs = {(str(p.marketplace).lower(), str(p.sku).strip()): _cogs(p) for p in products}
    out: List[str] = []
    missing: List[str] = []
    oz_rates = ozon_rates_from_finance(oz_fin)
    oz_cogs_seen: List[float] = []
    out.append("🔵 <b>OZON</b> — " + html.escape(oz_rates.source))
    for item in sorted((i for i in oz_items if isinstance(i, dict)), key=lambda i: str(i.get("sku"))):
        sku = str(item.get("sku") or "").strip()
        cogs = costs.get(("ozon", sku), 0.0)
        if cogs <= 0:
            missing.append(f"ozon:{sku}")
            continue
        out.append(_line("OZON", sku, _f(item.get("current_price")), cogs, oz_rates, tiered=True, ratio=ozon_tax_base_ratio(),
                         effective=_f(item.get("marketing_seller_price")) or None))
        oz_cogs_seen.append(cogs)
    if oz_cogs_seen:
        out.extend(build_bundle_lines(sum(oz_cogs_seen) / len(oz_cogs_seen), oz_rates))
    out.append("")
    out.append("🟣 <b>WILDBERRIES</b> — комиссия по предмету из тарифов WB; налог 6% — с цены покупателя (после СПП)")
    wb_spp = spp_from_items(list(wb_items), float(settings.COMPETITOR_WB_SPP_RATE))
    for item in sorted((i for i in wb_items if isinstance(i, dict)), key=lambda i: str(i.get("sku"))):
        sku = str(item.get("sku") or "").strip()
        cogs = costs.get(("wb", sku), 0.0)
        if cogs <= 0:
            missing.append(f"wb:{sku}")
            continue
        cur = _f(item.get("current_price"))
        buyer = _f(item.get("buyer_price"), cur)
        ratio = min(max(buyer / cur, 0.3), 1.5) if cur > 0 and buyer < cur * 0.995 else 1.0 - wb_spp
        out.append(_line("WB", sku, cur, cogs, wb_rates_from_item(item, wb_fin), tiered=False, ratio=ratio))
    if missing:
        out.append("")
        out.append(
            f"⚠️ Нет себестоимости у {len(missing)} арт.: " + html.escape(", ".join(missing[:15]))
            + (" …" if len(missing) > 15 else "") + " — задайте через «📦 Себестоимость»."
        )
    return out


async def build_price_report(ozon_api: Any, wb_api: Any) -> str:
    """Отчёт «Цены в ЛК» для Telegram (HTML). Любой источник может быть недоступен."""
    import asyncio

    from database.crud import get_all_products

    days = int(settings.PRICE_CALC_PERIOD_DAYS)
    oz_items, wb_items, oz_fin, wb_fin, products = await asyncio.gather(
        ozon_api.fetch_stocks_and_prices(),
        wb_api.fetch_stocks_and_prices(),
        ozon_api.fetch_real_finance_breakdown(days=days),
        wb_api.fetch_real_finance_breakdown(days=days),
        get_all_products(),
        return_exceptions=True,
    )
    notes: List[str] = []
    for label, value in (("Ozon: цены", oz_items), ("WB: цены", wb_items),
                         ("Ozon: финансы", oz_fin), ("WB: финансы", wb_fin)):
        if isinstance(value, BaseException):
            notes.append(f"⚠️ {label}: {html.escape(type(value).__name__)}: {html.escape(str(value))[:160]}")
    lines = build_price_lines(
        oz_items if isinstance(oz_items, list) else [],
        wb_items if isinstance(wb_items, list) else [],
        products if isinstance(products, (list, tuple)) else [],
        oz_fin if isinstance(oz_fin, dict) else None,
        wb_fin if isinstance(wb_fin, dict) else None,
    )
    head = (
        f"💲 <b>Ориентировочные цены в ЛК</b> (ставки — по вашим отчётам за {days} дн.)\n"
        f"УСН {settings.TAX_RATE * 100:.0f}%, эквайринг {settings.ACQUIRING_RATE * 100:.1f}%, "
        f"порог Ozon {settings.OZON_LOW_PRICE_LIMIT:.0f} ₽ (выше — комиссия {settings.OZON_HIGH_COMMISSION_RATE * 100:.0f}%).\n"
        f"Логистика Ozon по формуле: банка {settings.OZON_ITEM_VOLUME_L:g} л → "
        f"{ozon_logistics_cost(settings.OZON_ITEM_VOLUME_L, settings.OZON_LOW_PRICE_LIMIT):.0f} ₽ при цене ≤ "
        f"{settings.OZON_LOW_PRICE_LIMIT:.0f} ₽ и "
        f"{ozon_logistics_cost(settings.OZON_ITEM_VOLUME_L, settings.OZON_LOW_PRICE_LIMIT + 1):.0f} ₽ выше.\n"
        f"Ставка цены для Ozon не должна превышать {int(settings.OZON_LOW_PRICE_LIMIT) - 1} ₽, пока расчёт выше порога не даёт прибыли.\n"
    )
    tail = (
        "\nℹ️ «чистая прибыль» — по цене в ЛК после комиссии, логистики, рекламы, налога и себестоимости; "
        "«в ноль» — цена, при которой она равна 0. Это оценка по цене ЛК: если в P&L фактическая прибыль ниже, "
        "значит реальная цена реализации ниже цены ЛК (акции, скидки за счёт продавца, возвраты) — сверьте "
        "средний чек P&L с ценой в ЛК и калькулятор ЛК перед изменением цены."
    )
    return head + "\n".join(notes + lines) + tail


# ---------------------------------------------------------------------------
# Защита рекомендаций ИИ: «ловушка 300 ₽» Ozon
# ---------------------------------------------------------------------------

def _avg_ozon_cogs(products: Iterable[Any]) -> float:
    values = [_cogs(p) for p in products if str(getattr(p, "marketplace", "")).lower() == "ozon"
              and _f(getattr(p, "cost_price", 0.0)) > 0]
    return sum(values) / len(values) if values else 0.0


def build_ozon_guard(products: Iterable[Any], oz_fin: Optional[Dict[str, Any]]) -> str:
    """Жёсткие правила и расчёт для промпта ИИ-Совета: цены Ozon, порог 300 ₽, наборы."""
    limit = float(settings.OZON_LOW_PRICE_LIMIT)
    cap = int(limit) - 1
    rates = ozon_rates_from_finance(oz_fin)
    cogs = _avg_ozon_cogs(products)
    log_low = ozon_logistics_cost(settings.OZON_ITEM_VOLUME_L, limit, limit)
    log_high = ozon_logistics_cost(settings.OZON_ITEM_VOLUME_L, limit + 1, limit)
    lines = [
        "ЖЁСТКИЕ ПРАВИЛА OZON (расчёт кода — их нельзя нарушать и пересчитывать «на глаз»):",
        f"• Порог льготного тарифа — {limit:.0f} ₽ по цене в ЛК. Выше порога вознаграждение Ozon "
        f"{settings.OZON_HIGH_COMMISSION_RATE * 100:.0f}%, а логистика банки {settings.OZON_ITEM_VOLUME_L:g} л "
        f"дорожает с {log_low:.0f} до {log_high:.0f} ₽ (формула по объёму и цене ЛК).",
        f"• Максимальная цена, которую можно рекомендовать для Ozon: {cap} ₽. Цену выше {limit:.0f} ₽ "
        "предлагать ЗАПРЕЩЕНО, если расчёт ниже не показывает положительную прибыль на этой цене.",
    ]
    if cogs > 0:
        lines.append(f"• Средняя себестоимость банки (закупка+упаковка+первая миля): {cogs:.0f} ₽. "
                     f"Постоянные расходы Ozon ≈ {rates.fixed_per_order:.0f} ₽/шт, прочие удержания {rates.pct_other * 100:.1f}% цены.")
        for price in (cap, int(limit) + 1, 349, 360, 399):
            p = profit_at(price, cogs, rates, limit=limit, tax_base_ratio=ozon_tax_base_ratio())
            lines.append(f"  – цена ЛК {price} ₽: чистая прибыль {p:+.0f} ₽/шт ({p / price * 100:+.1f}%)")
        be = solve_price(cogs, rates, 0.0, tiered=True, tax_base_ratio=ozon_tax_base_ratio())
        tgt = solve_price(cogs, rates, float(settings.PRICE_TARGET_MARGIN), tiered=True, tax_base_ratio=ozon_tax_base_ratio())
        if be:
            lines.append(f"  – цена «в ноль»: {be.price} ₽" + (" (выше порога, тариф 52%)" if be.tier == "high" else ""))
        lines.append("  – цена для целевой маржи " + (
            f"{settings.PRICE_TARGET_MARGIN * 100:.0f}%: {tgt.price} ₽" + (" (выше порога, тариф 52%)" if tgt.tier == "high" else "")
            if tgt else f"{settings.PRICE_TARGET_MARGIN * 100:.0f}%: недостижима"))
        for line in build_bundle_lines(cogs, rates):
            lines.append(re.sub(r"</?[a-z]+>", "", line))
    lines.append("• Если SKU убыточен на ≤299 ₽, подними цену НЕЛЬЗЯ — предлагай снижение расходов (реклама, "
                 "FBS↔FBO, акции), набор по расчёту выше или вывод SKU. Для наборов цена и прибыль — только из таблицы выше.")
    return "\n".join(lines)


_PRICE_MENTION_RE = re.compile(r"(?<![\d,.\u00a0])(\d{3,4})\s*₽(?!\s*/)")


def check_ai_ozon_prices(text: str, products: Iterable[Any], oz_fin: Optional[Dict[str, Any]]) -> str:
    """Проверка ответа ИИ: цена Ozon выше порога без прибыли — добавить предупреждение с расчётом."""
    limit = float(settings.OZON_LOW_PRICE_LIMIT)
    rates = ozon_rates_from_finance(oz_fin)
    cogs = _avg_ozon_cogs(products)
    flagged: Dict[int, str] = {}
    for raw in (text or "").splitlines():
        low = raw.lower()
        if not ("ozon" in low or "озон" in low) or "цен" not in low:
            continue
        if re.search(r"\b(?:wb|wildberries|вб)\b", low) and "ozon" not in low and "озон" not in low:
            continue
        for m in _PRICE_MENTION_RE.finditer(raw):
            price = int(m.group(1))
            if limit < price <= 3000 and price not in flagged:
                if cogs > 0:
                    pr = profit_at(price, cogs, rates, limit=limit, tax_base_ratio=ozon_tax_base_ratio())
                    flagged[price] = (f"{price} ₽: тариф {settings.OZON_HIGH_COMMISSION_RATE * 100:.0f}%, "
                                      f"логистика {ozon_logistics_cost(settings.OZON_ITEM_VOLUME_L, price, limit):.0f} ₽ → "
                                      f"чистая прибыль {pr:+.0f} ₽/шт")
                else:
                    flagged[price] = f"{price} ₽: выше порога {limit:.0f} ₽ (комиссия {settings.OZON_HIGH_COMMISSION_RATE * 100:.0f}%)"
    if not flagged:
        return text
    cap = int(limit) - 1
    warn = ("\n\n⚠️ ПРОВЕРКА КОДОМ: в разборе есть цены Ozon выше порога " f"{limit:.0f} ₽ — льготный тариф теряется.\n"
            + "\n".join(f"• {v}" for v in flagged.values())
            + f"\nНе применяйте такие цены без проверки в калькуляторе ЛК; безопасный потолок — {cap} ₽.")
    return text + warn
