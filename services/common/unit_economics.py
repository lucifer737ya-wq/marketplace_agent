import math
from dataclasses import dataclass, field
from typing import List, Dict, Any
from config import settings


@dataclass
class PricingRecommendation:
    sku: str
    marketplace: str
    current_price: float
    calculated_floor: float
    in_ozon_tariff_trap: bool
    locked_by_promo: bool
    promo_max_price: float
    stock_critical: bool
    days_of_stock: float | None
    alternatives: List[Dict[str, Any]] = field(default_factory=list)


def calculate_floor_price(
    cost_price: float,
    packaging_cost: float,
    fbo_logistics_cost: float,
    first_mile_unit_cost: float,
    target_profit: float,
    commission_rate: float,
    acquiring_rate: float,
    tax_rate: float,
    drr_rate: float,
) -> float:
    """
    Формула:
    Floor = (Себестоимость + Упаковка + Логистика FBO + Доставка первой мили + Прибыль) /
            (1 - (Комиссия + Эквайринг + Налог 6% + ДРР))
    """
    if any(not math.isfinite(x) or x < 0 for x in (cost_price, packaging_cost, fbo_logistics_cost,
            first_mile_unit_cost, target_profit, commission_rate, acquiring_rate, tax_rate, drr_rate)):
        raise ValueError("Нужны конечные неотрицательные суммы и ставки")
    numerator = (
        cost_price
        + packaging_cost
        + fbo_logistics_cost
        + first_mile_unit_cost
        + target_profit
    )
    total_rate = commission_rate + acquiring_rate + tax_rate + drr_rate
    denominator = 1.0 - total_rate
    if denominator <= 0:
        raise ValueError("Суммарные удержания >= 100%: прибыльная цена недостижима")
    return round(numerator / denominator, 2)


def evaluate_product_decision(
    sku: str,
    marketplace: str,
    external_id: str,
    current_price: float,
    cost_price: float,
    packaging_cost: float,
    fbo_logistics_cost: float,
    first_mile_unit_cost: float,
    target_profit: float,
    commission_rate: float,
    drr_rate: float,
    stock_fbo: int,
    avg_daily_sales: float,
    in_promo: bool,
    promo_max_price: float,
    promo_id: str,
    active_campaign_id: str,
) -> PricingRecommendation:
    floor_price = calculate_floor_price(
        cost_price=cost_price,
        packaging_cost=packaging_cost,
        fbo_logistics_cost=fbo_logistics_cost,
        first_mile_unit_cost=first_mile_unit_cost,
        target_profit=target_profit,
        commission_rate=commission_rate,
        acquiring_rate=settings.ACQUIRING_RATE,
        tax_rate=settings.TAX_RATE,
        drr_rate=drr_rate,
    )

    days_of_stock = round(stock_fbo / avg_daily_sales, 1) if avg_daily_sales > 0 else None
    stock_critical = days_of_stock is not None and days_of_stock < 2.0

    in_trap = marketplace.lower() == "ozon" and floor_price > 300.0
    if marketplace.lower() == "ozon" and floor_price > 300.0:
        floor_price = calculate_floor_price(cost_price, packaging_cost, fbo_logistics_cost,
            first_mile_unit_cost, target_profit, settings.OZON_HIGH_COMMISSION_RATE,
            settings.ACQUIRING_RATE, settings.TAX_RATE, drr_rate)
        floor_price = max(301.0, floor_price)
    locked_by_promo = in_promo and (promo_max_price > 0) and (floor_price > promo_max_price)

    alternatives: List[Dict[str, Any]] = []

    # 1. Защита от слива рекламного бюджета при остатках < 2 дней
    if stock_critical and active_campaign_id:
        alternatives.append(
            {
                "label": f"🛑 Остановить рекламу (остаток {days_of_stock} дн.)",
                "action_type": "STOP_AD_CAMPAIGN",
                "payload": {
                    "external_id": external_id,
                    "campaign_id": active_campaign_id,
                },
            }
        )

    # 2. Проверка блокировки акцией с верхним порогом
    if locked_by_promo:
        target_after_exit = round(floor_price)
        alternatives.append(
            {
                "label": f"🔓 Исключить из акции (порог {promo_max_price:.0f} ₽) и поставить {target_after_exit:.0f} ₽",
                "action_type": "EXIT_PROMO_AND_PRICE",
                "payload": {
                    "external_id": external_id,
                    "promo_id": promo_id,
                    "new_price": target_after_exit,
                },
            }
        )

    # 3. Защита от тарифной ловушки Ozon (301–450 ₽)
    if in_trap:
        alternatives.extend(
            [
                {
                    "label": "Поставить 299 ₽ (льготный тариф до 300 ₽)",
                    "action_type": "SET_PRICE",
                    "payload": {"external_id": external_id, "new_price": 299.0},
                },
                {
                    "label": f"Расчетная цена {floor_price:.0f} ₽ (комиссия 52%)",
                    "action_type": "SET_PRICE",
                    "payload": {"external_id": external_id, "new_price": round(floor_price)},
                },
                {
                    "label": "Снизить ставку рекламы (удержать цену <= 300 ₽)",
                    "action_type": "REDUCE_AD_BID",
                    "payload": {
                        "external_id": external_id,
                        "campaign_id": active_campaign_id,
                    },
                },
            ]
        )
    elif abs(current_price - floor_price) >= 15.0 and not locked_by_promo:
        alternatives.append(
            {
                "label": f"Установить расчетную цену {round(floor_price)} ₽",
                "action_type": "SET_PRICE",
                "payload": {"external_id": external_id, "new_price": round(floor_price)},
            }
        )

    return PricingRecommendation(
        sku=sku,
        marketplace=marketplace,
        current_price=current_price,
        calculated_floor=floor_price,
        in_ozon_tariff_trap=in_trap,
        locked_by_promo=locked_by_promo,
        promo_max_price=promo_max_price,
        stock_critical=stock_critical,
        days_of_stock=days_of_stock,
        alternatives=alternatives,
    )