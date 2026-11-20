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
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional

from config import settings


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


@dataclass(frozen=True)
class PriceAdvice:
    price: int
    tier: str                 # "low" — льготная комиссия, "high" — выше порога
    commission: float
    profit: float             # чистая прибыль с заказа на этой цене, ₽
    margin: float             # прибыль / цена


def ozon_rates_from_finance(fin: Optional[Dict[str, Any]]) -> Rates:
    """Ставки Ozon из ``fetch_real_finance_breakdown``; при пустом отчёте — из .env."""
    default_low = 0.23
    fallback = Rates(
        default_low,
        settings.OZON_HIGH_COMMISSION_RATE,
        settings.ACQUIRING_RATE + 0.10,
        45.0,
        "нет отчёта: типовые значения (комиссия 23%, реклама 10%, логистика 45 ₽)",
    )
    if not isinstance(fin, dict):
        return fallback
    sales = abs(_f(fin.get("sales_total")))
    orders = int(_f(fin.get("orders_count")))
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
    fixed = sum(
        abs(_f(fin.get(key)))
        for key in ("deliv_total", "partner_dropoff", "partner_pack",
                    "partner_returns", "partner_last_mile")
    ) / orders
    return Rates(
        comm, settings.OZON_HIGH_COMMISSION_RATE, pct, round(fixed, 2),
        f"факт Ozon: {orders} заказов, комиссия {comm * 100:.1f}%, "
        f"прочие удержания {pct * 100:.1f}%, логистика+ПВЗ {fixed:.0f} ₽/заказ",
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
    return price * (1 - comm - rates.pct_other - tax) - cogs - rates.fixed_per_order


def solve_price(cogs: float, rates: Rates, margin: float, *, tiered: bool,
                tax_base_ratio: float = 1.0) -> Optional[PriceAdvice]:
    """Минимальная целая цена ЛК для заданной маржи; ``None`` — цена недостижима."""
    margin = max(margin, 0.0)
    tax = settings.TAX_RATE * tax_base_ratio
    limit = float(settings.OZON_LOW_PRICE_LIMIT)

    def solve(comm: float) -> Optional[int]:
        denom = 1 - comm - rates.pct_other - tax - margin
        if denom <= 0.01:
            return None
        return max(1, math.ceil((cogs + rates.fixed_per_order) / denom))

    low = solve(rates.commission_low)
    if not tiered:
        if low is None:
            return None
        return _advice(low, False, cogs, rates, tax_base_ratio, limit=None)
    if low is not None and low <= limit:
        return _advice(low, False, cogs, rates, tax_base_ratio, limit)
    high = solve(rates.commission_high)
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
    return (
        _f(getattr(row, "cost_price", 0.0))
        + _f(getattr(row, "packaging_cost", 0.0))
        + _f(getattr(row, "first_mile_unit_cost", 0.0))
    )


def _pct_ru(value: float) -> str:
    """18.2 → «18,2%» (десятичная запятая, знак для убытка)."""
    return f"{value:+.1f}%".replace(".", ",") if value < 0 else f"{value:.1f}%".replace(".", ",")


def _line(label: str, art: str, current: float, cogs: float, rates: Rates, *,
          tiered: bool, ratio: float) -> str:
    """Строка «Цены в ЛК»: фактическая чистая прибыль и её процент от цены, цена «в ноль»."""
    cur_profit = profit_at(current, cogs, rates, limit=float(settings.OZON_LOW_PRICE_LIMIT) if tiered else None,
                           tax_base_ratio=ratio) if current > 0 else None
    be = solve_price(cogs, rates, 0.0, tiered=tiered, tax_base_ratio=ratio)
    be_text = "недостижима" if be is None else f"<b>{be.price} ₽</b>" + (
        " (тариф выше порога)" if be.tier == "high" else ""
    )
    if cur_profit is None:
        now = "чистая прибыль н/д"
    else:
        pct = cur_profit / current * 100.0
        now = f"чистая прибыль {cur_profit:+.0f} ₽/шт (<b>{_pct_ru(pct)}</b>)"
    return (
        f"• [{label}] <code>{html.escape(art)}</code>: в ЛК {current:.0f} ₽ → {now} → "
        f"в ноль: {be_text}"
    )


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
    out.append("🔵 <b>OZON</b> — " + html.escape(oz_rates.source))
    for item in sorted((i for i in oz_items if isinstance(i, dict)), key=lambda i: str(i.get("sku"))):
        sku = str(item.get("sku") or "").strip()
        cogs = costs.get(("ozon", sku), 0.0)
        if cogs <= 0:
            missing.append(f"ozon:{sku}")
            continue
        out.append(_line("OZON", sku, _f(item.get("current_price")), cogs, oz_rates, tiered=True, ratio=1.0))
    out.append("")
    out.append("🟣 <b>WILDBERRIES</b> — комиссия по предмету из тарифов WB")
    for item in sorted((i for i in wb_items if isinstance(i, dict)), key=lambda i: str(i.get("sku"))):
        sku = str(item.get("sku") or "").strip()
        cogs = costs.get(("wb", sku), 0.0)
        if cogs <= 0:
            missing.append(f"wb:{sku}")
            continue
        cur = _f(item.get("current_price"))
        buyer = _f(item.get("buyer_price"), cur)
        ratio = min(max(buyer / cur, 0.3), 1.5) if cur > 0 else 1.0
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
    )
    tail = (
        "\nℹ️ «чистая прибыль» — по цене в ЛК после комиссии, логистики, рекламы, налога и себестоимости; "
        "«в ноль» — цена, при которой она равна 0. Это оценка по цене ЛК: если в P&L фактическая прибыль ниже, "
        "значит реальная цена реализации ниже цены ЛК (акции, скидки за счёт продавца, возвраты) — сверьте "
        "средний чек P&L с ценой в ЛК и калькулятор ЛК перед изменением цены."
    )
    return head + "\n".join(notes + lines) + tail
