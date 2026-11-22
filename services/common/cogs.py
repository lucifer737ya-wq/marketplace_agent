"""Единый расчёт себестоимости одной банки.

Себестоимость = закупка (по SKU, реально 58–66 ₽) + упаковка (9 ₽) + зарплата (5 ₽) + первая миля.

Упаковка и зарплата задаются в .env (``PACKAGING_COST_PER_UNIT``, ``LABOR_COST_PER_UNIT``) и
применяются ко ВСЕМ товарам одинаково. Поле ``packaging_cost`` в БД используется только если
``PACKAGING_COST_PER_UNIT=0``. Раньше «себес» был одной цифрой ~70 ₽ без разбивки.
"""

from __future__ import annotations

import math
from typing import Any, Dict

from config import settings


def _nn(value: Any) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    return number if math.isfinite(number) and number > 0 else 0.0


def packaging_per_unit(prod: Any = None) -> float:
    fixed = _nn(settings.PACKAGING_COST_PER_UNIT)
    if fixed > 0:
        return fixed
    return _nn(getattr(prod, "packaging_cost", 0.0))


def cogs_parts(prod: Any) -> Dict[str, float]:
    """Разбивка себестоимости одной единицы."""
    return {
        "purchase": _nn(getattr(prod, "cost_price", 0.0)),
        "packaging": packaging_per_unit(prod),
        "labor": _nn(settings.LABOR_COST_PER_UNIT),
        "first_mile": _nn(getattr(prod, "first_mile_unit_cost", 0.0)),
    }


def unit_cogs_of(prod: Any) -> float:
    """Полная себестоимость единицы (₽). 0.0 — закупка по SKU не задана."""
    parts = cogs_parts(prod)
    if parts["purchase"] <= 0:
        return 0.0
    return round(sum(parts.values()), 2)


def purchase_out_of_range(prod: Any) -> bool:
    """Закупка вне реального диапазона 58–66 ₽ — вероятно, устаревшая цифра «70»."""
    value = _nn(getattr(prod, "cost_price", 0.0))
    return value > 0 and not (settings.PURCHASE_COST_MIN <= value <= settings.PURCHASE_COST_MAX)


def ozon_tax_base_ratio() -> float:
    """Доля цены ЛК Ozon, по которой покупатель реально платит (налог 6% — с неё)."""
    if not settings.OZON_TAX_ON_BUYER_PRICE:
        return 1.0
    return min(max(1.0 - float(settings.COMPETITOR_OZON_DISCOUNT_RATE), 0.3), 1.0)
