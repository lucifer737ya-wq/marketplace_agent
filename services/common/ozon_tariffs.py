"""Тарифы Ozon, которые зависят от объёма товара и цены в ЛК.

Логистика рассчитывается формулой продавца (Excel):

    =IF(F>300,
        IF(D<=0.2,97,IF(D<=0.4,106,IF(D<=1,112,IF(D<=1.25,113,IF(D<=3,122,
        IF(D<=4,129,IF(D<=7,162,IF(D<=10,168,IF(D<=13,175,IF(D<=15,200,0))))))))))),
        IF(D<=0.2,25,IF(D<=0.4,27,IF(D<=1,32,IF(D<=1.25,34,IF(D<=3,45,
        IF(D<=4,52,IF(D<=7,94,IF(D<=10,118,IF(D<=13,152,IF(D<=15,172,0)))))))))))

где D — объём в литрах, F — цена в ЛК. Объём одной банки — 0,18 л.
Выше порога 300 ₽ Ozon выходит из льготного тарифа: логистика дороже, а вознаграждение
показывается 52% (``OZON_HIGH_COMMISSION_RATE``).
"""

from __future__ import annotations

import math
from typing import Optional

# (верхняя граница объёма, л; стоимость, ₽)
LOGISTICS_LOW = (
    (0.2, 25.0), (0.4, 27.0), (1.0, 32.0), (1.25, 34.0), (3.0, 45.0),
    (4.0, 52.0), (7.0, 94.0), (10.0, 118.0), (13.0, 152.0), (15.0, 172.0),
)
LOGISTICS_HIGH = (
    (0.2, 97.0), (0.4, 106.0), (1.0, 112.0), (1.25, 113.0), (3.0, 122.0),
    (4.0, 129.0), (7.0, 162.0), (10.0, 168.0), (13.0, 175.0), (15.0, 200.0),
)
PRICE_LIMIT = 300.0


def ozon_logistics_cost(volume_l: float, lk_price: float, limit: float = PRICE_LIMIT) -> float:
    """Стоимость логистики Ozon за одно отправление по формуле продавца (₽).

    Объём больше 15 л формула не покрывает и возвращает 0 — как в исходной таблице.
    """
    try:
        volume = float(volume_l)
        price = float(lk_price)
    except (TypeError, ValueError):
        return 0.0
    if not (math.isfinite(volume) and math.isfinite(price)) or volume <= 0:
        return 0.0
    table = LOGISTICS_HIGH if price > limit else LOGISTICS_LOW
    for bound, cost in table:
        if volume <= bound:
            return cost
    return 0.0


def set_volume_l(units: int, unit_volume_l: float) -> float:
    """Объём набора из ``units`` банок."""
    return round(max(int(units), 1) * float(unit_volume_l), 4)


def ozon_commission_rate(lk_price: float, low_rate: float, high_rate: float,
                         limit: float = PRICE_LIMIT) -> float:
    """Вознаграждение Ozon: льготное до порога включительно, 52% выше порога."""
    return high_rate if float(lk_price) > limit else low_rate


def max_lk_price_in_low_tier(limit: float = PRICE_LIMIT) -> int:
    """Максимальная цена ЛК, при которой действует льготный тариф (целая, ≤ порога)."""
    return int(math.floor(limit))


def describe_tier(lk_price: float, limit: float = PRICE_LIMIT) -> Optional[str]:
    return "выше порога 300 ₽: комиссия 52%, дорогая логистика" if float(lk_price) > limit else None
