"""Политика рекомендаций цены: чистая прибыль к себестоимости 30–100% + оглядка на конкурентов.

Правила (по требованию продавца):
* ROI = чистая прибыль (после вычета себестоимости) / ЗАКУПОЧНАЯ цена * 100% (38/61*100 = 62,3%); рекомендация лежит в коридоре ``PRICE_MIN_ROI``…``PRICE_MAX_ROI``
  (по умолчанию 30%…100%).
* Если известна цена конкурентов (в пересчёте на цену ЛК) — цена не выше неё (минус ``COMPETITOR_PRICE_UNDERCUT``):
  нельзя ставить 800 ₽, когда у конкурентов 650 ₽.
* Ниже точки «в плюс» не опускаемся никогда: если конкуренты дешевле нашей безубыточной цены, это прямо сказано.
"""

from __future__ import annotations

import json
import math
import time
from dataclasses import dataclass
from typing import Any, Callable, Optional

from config import LOGS_DIR, settings

COMPETITOR_REF_FILE = LOGS_DIR / "competitor_prices.json"
COMPETITOR_REF_TTL_SEC = 7 * 24 * 3600


@dataclass(frozen=True)
class PriceRec:
    price: int
    profit: float
    roi: float            # прибыль / закупочная цена (база ROI)
    margin: float         # прибыль / цена ЛК
    p_break_even: int     # минимальная цена с прибылью > 0
    p_min_roi: int        # минимальная цена для ROI >= min
    p_max_roi: int        # цена для ROI = max (потолок)
    competitor_lk: Optional[float]
    status: str           # ok | no_competitors | below_target | loss_risk
    verdict: str


def smallest_price(pf: Callable[[float], float], target: float, lo: int, hi: int) -> Optional[int]:
    """Минимальная целая цена в [lo, hi], где прибыль >= target (прибыль не убывает с ценой)."""
    if pf(float(hi)) < target:
        return None
    if pf(float(lo)) >= target:
        return lo
    a, b = lo, hi
    while b - a > 1:
        mid = (a + b) // 2
        if pf(float(mid)) >= target:
            b = mid
        else:
            a = mid
    return b


def recommend_price(pf: Callable[[float], float], cogs: float, *, lo: int = 1, hi: int = 6000,
                    competitor_lk: Optional[float] = None,
                    min_roi: Optional[float] = None, max_roi: Optional[float] = None,
                    roi_base: Optional[float] = None) -> Optional[PriceRec]:
    """Цена по ROI-коридору и конкурентам; ``None`` — прибыль на этой ветке тарифа недостижима."""
    if not math.isfinite(cogs) or cogs <= 0:
        return None
    base = roi_base if roi_base and math.isfinite(roi_base) and roi_base > 0 else cogs
    min_roi = settings.PRICE_MIN_ROI if min_roi is None else min_roi
    max_roi = settings.PRICE_MAX_ROI if max_roi is None else max_roi
    max_roi = max(max_roi, min_roi)
    p_be = smallest_price(pf, 0.5, lo, hi)
    p_min = smallest_price(pf, min_roi * base, lo, hi)
    if p_be is None or p_min is None:
        return None
    p_max = smallest_price(pf, max_roi * base, lo, hi) or hi
    p_max = max(p_max, p_min)

    ref = None
    if competitor_lk and competitor_lk > 0:
        ref = math.floor(competitor_lk * (1.0 - max(float(settings.COMPETITOR_PRICE_UNDERCUT), 0.0)))

    if ref is None:
        price, status = p_min, "no_competitors"
        verdict = f"цены конкурентов неизвестны — взята минимальная цена для ROI {min_roi * 100:.0f}% (запустите «🔎 Пробив конкурентов»)"
    elif ref >= p_min:
        price, status = min(ref, p_max), "ok"
        verdict = (f"в рынке: конкуренты ≈ {competitor_lk:.0f} ₽ (в пересчёте на цену ЛК), "
                   f"цена не выше их и в коридоре ROI {min_roi * 100:.0f}–{max_roi * 100:.0f}%")
    elif ref >= p_be:
        price, status = max(ref, lo), "below_target"
        verdict = (f"конкуренты дешевле (≈ {competitor_lk:.0f} ₽): держим цену на их уровне, "
                   f"ROI ниже цели {min_roi * 100:.0f}%, но в плюсе")
    else:
        price, status = p_be, "loss_risk"
        verdict = (f"⚠️ конкуренты ≈ {competitor_lk:.0f} ₽ — ниже нашей безубыточной цены {p_be} ₽. "
                   "Опускаться ниже нельзя (убыток): лучше не демпинговать, а менять набор/расходы")
    price = int(min(max(price, lo), hi))
    profit = round(pf(float(price)), 2)
    return PriceRec(price, profit, round(profit / base, 4), round(profit / price, 4) if price else 0.0,
                    p_be, p_min, p_max, competitor_lk, status, verdict)


# --- Кэш цен конкурентов: пишет «Пробив конкурентов», читает аудит цен --------------------------

def save_competitor_reference(wb_buyer_prices: list[float], ozon_buyer_prices: list[float]) -> None:
    from services.common.storage import write_json
    import statistics

    def med(values: list[float]) -> Optional[float]:
        values = [float(v) for v in values if v and float(v) > 0]
        return round(float(statistics.median(values)), 2) if values else None

    try:
        write_json(COMPETITOR_REF_FILE, {
            "ts": time.time(), "wb_median_buyer": med(wb_buyer_prices), "ozon_median_buyer": med(ozon_buyer_prices),
            "wb_n": len(wb_buyer_prices), "ozon_n": len(ozon_buyer_prices),
        })
    except OSError:
        pass


def load_competitor_reference() -> dict[str, Any]:
    try:
        data = json.loads(COMPETITOR_REF_FILE.read_text(encoding="utf-8"))
        if time.time() - float(data.get("ts") or 0) > COMPETITOR_REF_TTL_SEC:
            return {}
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}
