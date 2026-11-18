import json
from datetime import datetime, timedelta
from typing import Sequence, Optional
from sqlalchemy import select, func
from database.models import (
    AsyncSessionLocal,
    Product,
    FirstMileSupply,
    SaleRecord,
    AdSpendRecord,
    PendingAction,
)


async def upsert_product_costs(
    sku: str,
    marketplace: str,
    cost_price: float,
    packaging_cost: float,
    external_id: str = "",
) -> Product:
    async with AsyncSessionLocal() as session:
        stmt = select(Product).where(Product.sku == sku)
        res = await session.execute(stmt)
        product = res.scalar_one_or_none()
        if not product:
            product = Product(
                sku=sku,
                marketplace=marketplace.lower(),
                external_id=external_id or sku,
                cost_price=cost_price,
                packaging_cost=packaging_cost,
            )
            session.add(product)
        else:
            product.cost_price = cost_price
            product.packaging_cost = packaging_cost
            product.updated_at = datetime.utcnow()
        await session.commit()
        await session.refresh(product)
        return product


async def record_first_mile_supply(
    supply_number: str,
    marketplace: str,
    sku: str,
    quantity: int,
    total_transport_cost: float,
) -> FirstMileSupply:
    unit_cost = round(total_transport_cost / max(quantity, 1), 2)
    async with AsyncSessionLocal() as session:
        supply = FirstMileSupply(
            supply_number=supply_number,
            marketplace=marketplace.lower(),
            sku=sku,
            quantity=quantity,
            total_transport_cost=total_transport_cost,
            unit_cost=unit_cost,
        )
        session.add(supply)

        stmt = select(Product).where(Product.sku == sku)
        res = await session.execute(stmt)
        product = res.scalar_one_or_none()
        if product:
            product.first_mile_unit_cost = unit_cost
        else:
            product = Product(
                sku=sku,
                marketplace=marketplace.lower(),
                external_id=sku,
                first_mile_unit_cost=unit_cost,
            )
            session.add(product)

        await session.commit()
        await session.refresh(supply)
        return supply


async def update_product_market_state(
    sku: str,
    marketplace: str,
    external_id: str,
    current_price: float,
    stock_fbo: int,
    in_promo: bool = False,
    promo_max_price: float = 0.0,
    promo_id: str = "",
    avg_daily_sales: Optional[float] = None,
) -> Product:
    async with AsyncSessionLocal() as session:
        stmt = select(Product).where(Product.sku == sku)
        res = await session.execute(stmt)
        product = res.scalar_one_or_none()
        if not product:
            product = Product(
                sku=sku,
                marketplace=marketplace.lower(),
                external_id=str(external_id),
                current_price=current_price,
                stock_fbo=stock_fbo,
                in_promo=in_promo,
                promo_max_price=promo_max_price,
                promo_id=str(promo_id),
            )
            session.add(product)
        else:
            product.external_id = str(external_id)
            product.current_price = current_price
            product.stock_fbo = stock_fbo
            product.in_promo = in_promo
            product.promo_max_price = promo_max_price
            product.promo_id = str(promo_id)
            if avg_daily_sales is not None:
                product.avg_daily_sales = avg_daily_sales
            product.updated_at = datetime.utcnow()

        await session.commit()
        await session.refresh(product)
        return product


async def get_all_products(marketplace: Optional[str] = None) -> Sequence[Product]:
    async with AsyncSessionLocal() as session:
        stmt = select(Product)
        if marketplace:
            stmt = stmt.where(Product.marketplace == marketplace.lower())
        res = await session.execute(stmt)
        return res.scalars().all()


async def get_lagged_drr(sku: str, shift_days_start: int = 7, shift_days_end: int = 3) -> float:
    """Расчет ДРР со сдвигом на 3-7 дней назад для учета окна атрибуции."""
    now = datetime.utcnow()
    date_from = now - timedelta(days=shift_days_start)
    date_to = now - timedelta(days=shift_days_end)

    async with AsyncSessionLocal() as session:
        sales_stmt = select(func.sum(SaleRecord.payout_amount)).where(
            SaleRecord.sku == sku,
            SaleRecord.is_return.is_(False),
            SaleRecord.sale_date >= date_from,
            SaleRecord.sale_date <= date_to,
        )
        ad_stmt = select(func.sum(AdSpendRecord.spend_amount)).where(
            AdSpendRecord.sku == sku,
            AdSpendRecord.spend_date >= date_from,
            AdSpendRecord.spend_date <= date_to,
        )
        total_sales = (await session.execute(sales_stmt)).scalar() or 0.0
        total_ads = (await session.execute(ad_stmt)).scalar() or 0.0

        if total_sales <= 0:
            return 0.05  # Базовый консервативный ДРР 5%, если не было продаж
        return min(round(total_ads / total_sales, 4), 0.50)


async def create_pending_action(
    marketplace: str, sku: str, action_type: str, payload: dict
) -> PendingAction:
    async with AsyncSessionLocal() as session:
        action = PendingAction(
            marketplace=marketplace.lower(),
            sku=sku,
            action_type=action_type,
            payload_json=json.dumps(payload, ensure_ascii=False),
            status="PENDING",
        )
        session.add(action)
        await session.commit()
        await session.refresh(action)
        return action


async def get_pending_action(action_id: int) -> Optional[PendingAction]:
    """Получает отложенное действие по ID."""
    async with AsyncSessionLocal() as session:
        stmt = select(PendingAction).where(PendingAction.id == action_id)
        res = await session.execute(stmt)
        return res.scalar_one_or_none()

async def mark_action_executed(action_id: int) -> bool:
    """Переводит действие в статус executed."""
    async with AsyncSessionLocal() as session:
        stmt = select(PendingAction).where(PendingAction.id == action_id)
        res = await session.execute(stmt)
        action = res.scalar_one_or_none()
        if action:
            action.status = "executed"
            action.executed_at = datetime.utcnow()
            await session.commit()
            return True
        return False

async def mark_action_rejected(action_id: int) -> bool:
    """Переводит действие в статус rejected."""
    async with AsyncSessionLocal() as session:
        stmt = select(PendingAction).where(PendingAction.id == action_id)
        res = await session.execute(stmt)
        action = res.scalar_one_or_none()
        if action:
            action.status = "rejected"
            await session.commit()
            return True
        return False


async def add_sale_record(
    order_id: str,
    marketplace: str,
    sku: str,
    quantity: int,
    payout_amount: float,
    commission_amount: float,
    logistics_amount: float,
    is_return: bool,
    sale_date: datetime,
) -> None:
    async with AsyncSessionLocal() as session:
        exists = await session.execute(
            select(SaleRecord).where(SaleRecord.order_id == order_id)
        )
        if exists.scalar_one_or_none():
            return
        rec = SaleRecord(
            order_id=order_id,
            marketplace=marketplace.lower(),
            sku=sku,
            quantity=quantity,
            payout_amount=payout_amount,
            commission_amount=commission_amount,
            logistics_amount=logistics_amount,
            is_return=is_return,
            sale_date=sale_date,
        )
        session.add(rec)
        await session.commit()


async def bulk_upsert_costs(records: list[dict]) -> int:
    """
    records: list of dicts:
    [{"marketplace": "ozon", "sku": "GEL-01", "cost_price": 120.0, "packaging_cost": 15.0}, ...]
    """
    count = 0
    async with AsyncSessionLocal() as session:
        for item in records:
            sku = item["sku"].strip()
            mp = item["marketplace"].strip().lower()
            stmt = select(Product).where(Product.sku == sku)
            res = await session.execute(stmt)
            product = res.scalar_one_or_none()
            
            if not product:
                product = Product(
                    sku=sku,
                    marketplace=mp,
                    external_id=sku,
                    cost_price=item["cost_price"],
                    packaging_cost=item["packaging_cost"]
                )
                session.add(product)
            else:
                product.cost_price = item["cost_price"]
                product.packaging_cost = item["packaging_cost"]
                product.marketplace = mp
                product.updated_at = datetime.utcnow()
            count += 1
            
        await session.commit()
    return count