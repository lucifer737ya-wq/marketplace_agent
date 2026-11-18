import asyncio
import json
from datetime import datetime, timedelta
from typing import Any, Dict, Optional, Sequence, Tuple

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from database.models import (
    AdSpendRecord,
    AsyncSessionLocal,
    FirstMileSupply,
    PendingAction,
    Product,
    SaleRecord,
    get_db_session,
    utcnow_naive,
)
from products_catalog import get_catalog_product, resolve_catalog_external_id

# Сериализация конкурентных транзакций записи в SQLite внутри одного процесса воркера
_db_write_lock = asyncio.Lock()


def _resolve_ext_id_and_title(
    sku: str,
    marketplace: str,
    external_id: str = "",
    title: str = "",
) -> Tuple[str, str]:
    """Использует переданный product_id Ozon или известный nmID WB, не путая Ozon SKU с product_id."""
    clean_sku = str(sku or "").strip()
    mp = str(marketplace or "").strip().lower()
    clean_ext = str(external_id or "").strip()
    clean_title = str(title or "").strip()

    cat_meta = get_catalog_product(clean_sku) or (get_catalog_product(clean_ext) if clean_ext else None)
    cat_ext = resolve_catalog_external_id(clean_sku, mp)

    if mp == "wb":
        # Для Wildberries external_id должен быть числовым nmID, а не строковым артикулом продавца
        if (not clean_ext or clean_ext == clean_sku or not clean_ext.isdigit()) and cat_ext:
            clean_ext = cat_ext
    else:
        if (not clean_ext or clean_ext == clean_sku) and cat_ext:
            clean_ext = cat_ext

    if not clean_title and cat_meta:
        clean_title = str(cat_meta.get("title") or cat_meta.get("name") or "")

    return clean_ext, clean_title


async def upsert_product_costs(
    sku: str,
    marketplace: str,
    cost_price: float,
    packaging_cost: float,
    external_id: str = "",
) -> Product:
    clean_sku = str(sku).strip()
    mp = str(marketplace).strip().lower()
    resolved_ext, resolved_title = _resolve_ext_id_and_title(clean_sku, mp, external_id=external_id)

    async with _db_write_lock:
        async with get_db_session() as session:
            stmt = select(Product).where(Product.sku == clean_sku, Product.marketplace == mp)
            res = await session.execute(stmt)
            product = res.scalar_one_or_none()

            if not product:
                product = Product(
                    sku=clean_sku,
                    marketplace=mp,
                    external_id=resolved_ext,
                    title=resolved_title,
                    cost_price=float(cost_price),
                    packaging_cost=float(packaging_cost),
                )
                session.add(product)
            else:
                product.cost_price = float(cost_price)
                product.packaging_cost = float(packaging_cost)
                if external_id or not product.external_id or product.external_id == clean_sku:
                    product.external_id = resolved_ext
                if not product.title and resolved_title:
                    product.title = resolved_title
                product.updated_at = utcnow_naive()

            await session.commit()
            return product


async def record_first_mile_supply(
    supply_number: str,
    marketplace: str,
    sku: str,
    quantity: int,
    total_transport_cost: float,
) -> FirstMileSupply:
    clean_sku = str(sku).strip()
    mp = str(marketplace).strip().lower()
    safe_qty = max(int(quantity), 1)
    unit_cost = round(float(total_transport_cost) / safe_qty, 2)
    resolved_ext, resolved_title = _resolve_ext_id_and_title(clean_sku, mp)

    async with _db_write_lock:
        async with get_db_session() as session:
            supply = FirstMileSupply(
                supply_number=str(supply_number).strip(),
                marketplace=mp,
                sku=clean_sku,
                quantity=safe_qty,
                total_transport_cost=float(total_transport_cost),
                unit_cost=unit_cost,
            )
            session.add(supply)

            stmt = select(Product).where(Product.sku == clean_sku, Product.marketplace == mp)
            res = await session.execute(stmt)
            product = res.scalar_one_or_none()
            if product:
                product.first_mile_unit_cost = unit_cost
                if not product.external_id or product.external_id == clean_sku:
                    product.external_id = resolved_ext
                if not product.title and resolved_title:
                    product.title = resolved_title
                product.updated_at = utcnow_naive()
            else:
                product = Product(
                    sku=clean_sku,
                    marketplace=mp,
                    external_id=resolved_ext,
                    title=resolved_title,
                    first_mile_unit_cost=unit_cost,
                )
                session.add(product)

            await session.commit()
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
    stock_fbs: int = 0,
    commission_rate: Optional[float] = None,
    fbo_logistics_cost: Optional[float] = None,
    title: str = "",
) -> Product:
    clean_sku = str(sku).strip()
    mp = str(marketplace).strip().lower()
    resolved_ext, resolved_title = _resolve_ext_id_and_title(
        clean_sku, mp, external_id=str(external_id or ""), title=title
    )

    async with _db_write_lock:
        async with get_db_session() as session:
            stmt = select(Product).where(Product.sku == clean_sku, Product.marketplace == mp)
            res = await session.execute(stmt)
            product = res.scalar_one_or_none()

            if not product:
                product = Product(
                    sku=clean_sku,
                    marketplace=mp,
                    external_id=resolved_ext,
                    title=resolved_title,
                    current_price=float(current_price or 0.0),
                    stock_fbo=int(stock_fbo or 0),
                    stock_fbs=int(stock_fbs or 0),
                    in_promo=bool(in_promo),
                    promo_max_price=float(promo_max_price or 0.0),
                    promo_id=str(promo_id or ""),
                )
                if avg_daily_sales is not None:
                    product.avg_daily_sales = float(avg_daily_sales)
                if commission_rate is not None and commission_rate > 0:
                    product.commission_rate = float(commission_rate)
                if fbo_logistics_cost is not None and fbo_logistics_cost > 0:
                    product.fbo_logistics_cost = float(fbo_logistics_cost)
                session.add(product)
            else:
                product.external_id = resolved_ext
                if resolved_title and not product.title:
                    product.title = resolved_title
                product.current_price = float(current_price or 0.0)
                product.stock_fbo = int(stock_fbo or 0)
                product.stock_fbs = int(stock_fbs or 0)
                product.in_promo = bool(in_promo)
                product.promo_max_price = float(promo_max_price or 0.0)
                product.promo_id = str(promo_id or "")
                if avg_daily_sales is not None:
                    product.avg_daily_sales = float(avg_daily_sales)
                if commission_rate is not None and commission_rate > 0:
                    product.commission_rate = float(commission_rate)
                if fbo_logistics_cost is not None and fbo_logistics_cost > 0:
                    product.fbo_logistics_cost = float(fbo_logistics_cost)
                product.updated_at = utcnow_naive()

            await session.commit()
            return product


async def bulk_update_product_market_state(
    marketplace: str,
    items: Sequence[Dict[str, Any]],
) -> int:
    """Пакетное обновление состояния карточек маркетплейса за одну транзакцию без утечек сессий."""
    if not items:
        return 0

    mp = str(marketplace).strip().lower()
    updated_count = 0
    now = utcnow_naive()

    async with _db_write_lock:
        async with get_db_session() as session:
            existing_res = await session.execute(select(Product).where(Product.marketplace == mp))
            existing_by_sku: Dict[str, Product] = {p.sku: p for p in existing_res.scalars().all()}

            for item in items:
                clean_sku = str(item.get("sku") or "").strip()
                if not clean_sku:
                    continue

                raw_ext = str(
                    item.get("nm_id") if mp == "wb" and item.get("nm_id") else (item.get("external_id") or "")
                )
                resolved_ext, resolved_title = _resolve_ext_id_and_title(
                    clean_sku,
                    mp,
                    external_id=raw_ext,
                    title=str(item.get("title") or ""),
                )

                cur_price = float(item.get("current_price") or 0.0)
                st_fbo = int(item.get("stock_fbo") or 0)
                st_fbs = int(item.get("stock_fbs") or 0)
                in_promo = bool(item.get("in_promo", False))
                promo_max = float(item.get("promo_max_price") or 0.0)
                promo_id = str(item.get("promo_id") or item.get("promo_names") or "")[:64]
                daily_sales = item.get("daily_sales")
                comm_rate = item.get("commission_rate")
                fbo_log = item.get("fbo_logistics_cost")

                product = existing_by_sku.get(clean_sku)
                if not product:
                    product = Product(
                        sku=clean_sku,
                        marketplace=mp,
                        external_id=resolved_ext,
                        title=resolved_title,
                        current_price=cur_price,
                        stock_fbo=st_fbo,
                        stock_fbs=st_fbs,
                        in_promo=in_promo,
                        promo_max_price=promo_max,
                        promo_id=promo_id,
                        updated_at=now,
                    )
                    if daily_sales is not None:
                        product.avg_daily_sales = float(daily_sales)
                    if comm_rate is not None and float(comm_rate) > 0:
                        product.commission_rate = float(comm_rate)
                    if fbo_log is not None and float(fbo_log) > 0:
                        product.fbo_logistics_cost = float(fbo_log)
                    session.add(product)
                    existing_by_sku[clean_sku] = product
                else:
                    product.external_id = resolved_ext
                    if resolved_title and not product.title:
                        product.title = resolved_title
                    product.current_price = cur_price
                    product.stock_fbo = st_fbo
                    product.stock_fbs = st_fbs
                    product.in_promo = in_promo
                    product.promo_max_price = promo_max
                    product.promo_id = promo_id
                    if daily_sales is not None:
                        product.avg_daily_sales = float(daily_sales)
                    if comm_rate is not None and float(comm_rate) > 0:
                        product.commission_rate = float(comm_rate)
                    if fbo_log is not None and float(fbo_log) > 0:
                        product.fbo_logistics_cost = float(fbo_log)
                    product.updated_at = now

                updated_count += 1

            await session.commit()
    return updated_count


async def get_all_products(marketplace: Optional[str] = None) -> Sequence[Product]:
    async with get_db_session() as session:
        stmt = select(Product)
        if marketplace:
            stmt = stmt.where(Product.marketplace == marketplace.strip().lower())
        res = await session.execute(stmt)
        return res.scalars().all()


async def get_lagged_drr(
    sku: str,
    shift_days_start: int = 7,
    shift_days_end: int = 3,
    marketplace: Optional[str] = None,
) -> float:
    """Расчет ДРР со сдвигом на 3-7 дней назад для учета окна атрибуции."""
    now = utcnow_naive()
    date_from = now - timedelta(days=shift_days_start)
    date_to = now - timedelta(days=shift_days_end)
    clean_sku = str(sku).strip()

    async with get_db_session() as session:
        sales_stmt = select(func.sum(SaleRecord.payout_amount)).where(
            SaleRecord.sku == clean_sku,
            SaleRecord.is_return.is_(False),
            SaleRecord.sale_date >= date_from,
            SaleRecord.sale_date <= date_to,
        )
        ad_stmt = select(func.sum(AdSpendRecord.spend_amount)).where(
            AdSpendRecord.sku == clean_sku,
            AdSpendRecord.spend_date >= date_from,
            AdSpendRecord.spend_date <= date_to,
        )
        if marketplace:
            mp = marketplace.strip().lower()
            sales_stmt = sales_stmt.where(SaleRecord.marketplace == mp)
            ad_stmt = ad_stmt.where(AdSpendRecord.marketplace == mp)

        total_sales = float((await session.execute(sales_stmt)).scalar() or 0.0)
        total_ads = float((await session.execute(ad_stmt)).scalar() or 0.0)

        if total_sales <= 0:
            return 0.05  # Базовый консервативный ДРР 5%, если не было продаж
        return min(round(total_ads / total_sales, 4), 0.50)


async def create_pending_action(
    marketplace: str,
    sku: str,
    action_type: str,
    payload: dict,
) -> PendingAction:
    async with _db_write_lock:
        async with get_db_session() as session:
            action = PendingAction(
                marketplace=str(marketplace).strip().lower(),
                sku=str(sku).strip(),
                action_type=str(action_type).strip(),
                payload_json=json.dumps(payload, ensure_ascii=False),
                status="pending",
            )
            session.add(action)
            await session.commit()
            return action


async def get_pending_action(action_id: int) -> Optional[PendingAction]:
    """Получает отложенное действие по ID."""
    async with get_db_session() as session:
        stmt = select(PendingAction).where(PendingAction.id == int(action_id))
        res = await session.execute(stmt)
        return res.scalar_one_or_none()


async def mark_action_executed(action_id: int) -> bool:
    """Переводит действие в статус executed."""
    async with _db_write_lock:
        async with get_db_session() as session:
            stmt = select(PendingAction).where(PendingAction.id == int(action_id))
            res = await session.execute(stmt)
            action = res.scalar_one_or_none()
            if action:
                action.status = "executed"
                action.executed_at = utcnow_naive()
                await session.commit()
                return True
            return False


async def mark_action_rejected(action_id: int) -> bool:
    """Переводит действие в статус rejected."""
    async with _db_write_lock:
        async with get_db_session() as session:
            stmt = select(PendingAction).where(PendingAction.id == int(action_id))
            res = await session.execute(stmt)
            action = res.scalar_one_or_none()
            if action:
                action.status = "rejected"
                action.executed_at = utcnow_naive()
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
    clean_order_id = str(order_id).strip()
    if not clean_order_id:
        return

    async with _db_write_lock:
        async with get_db_session() as session:
            exists = await session.execute(
                select(SaleRecord.id).where(SaleRecord.order_id == clean_order_id)
            )
            if exists.scalar_one_or_none() is not None:
                return
            rec = SaleRecord(
                order_id=clean_order_id,
                marketplace=str(marketplace).strip().lower(),
                sku=str(sku).strip(),
                quantity=int(quantity),
                payout_amount=float(payout_amount),
                commission_amount=float(commission_amount),
                logistics_amount=float(logistics_amount),
                is_return=bool(is_return),
                sale_date=sale_date,
            )
            session.add(rec)
            try:
                await session.commit()
            except IntegrityError:
                await session.rollback()


async def bulk_upsert_costs(records: list[dict]) -> int:
    """
    Пакетная загрузка себестоимости и упаковки с разделением по (sku, marketplace).
    Формат элементов: [{"marketplace": "ozon", "sku": "1001", "cost_price": 52.0, "packaging_cost": 15.0}, ...]
    """
    if not records:
        return 0

    count = 0
    now = utcnow_naive()

    async with _db_write_lock:
        async with get_db_session() as session:
            existing = {}
            for item in records:
                clean_sku = str(item["sku"]).strip()
                mp = str(item["marketplace"]).strip().lower()
                resolved_ext, resolved_title = _resolve_ext_id_and_title(clean_sku, mp)

                key = (clean_sku, mp)
                if key not in existing:
                    stmt = select(Product).where(Product.sku == clean_sku, Product.marketplace == mp)
                    res = await session.execute(stmt)
                    existing[key] = res.scalar_one_or_none()
                product = existing[key]

                if not product:
                    product = Product(
                        sku=clean_sku,
                        marketplace=mp,
                        external_id=resolved_ext,
                        title=resolved_title,
                        cost_price=float(item["cost_price"]),
                        packaging_cost=float(item["packaging_cost"]),
                        updated_at=now,
                    )
                    session.add(product)
                    existing[key] = product
                else:
                    product.cost_price = float(item["cost_price"])
                    product.packaging_cost = float(item["packaging_cost"])
                    if not product.external_id or product.external_id == clean_sku:
                        product.external_id = resolved_ext
                    if not product.title and resolved_title:
                        product.title = resolved_title
                    product.updated_at = now
                count += 1

            await session.commit()
    return count
