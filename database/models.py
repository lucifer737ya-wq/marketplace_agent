import json
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any, AsyncIterator, Dict, Optional

from sqlalchemy import Boolean, DateTime, Float, Integer, String, Text, event, text
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.pool import NullPool

from config import settings


def utcnow_naive() -> datetime:
    """Возвращает текущее время UTC без tzinfo (совместимо с SQLite и Python 3.11+)."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


class Base(DeclarativeBase):
    """Declarative metadata shared by the SQLite tables."""


class Product(Base):
    __tablename__ = "products"

    # Составной первичный ключ (sku, marketplace), т.к. артикулы YaSoKo едины на Ozon и WB
    sku: Mapped[str] = mapped_column(String(64), primary_key=True)
    marketplace: Mapped[str] = mapped_column(String(16), primary_key=True, index=True)  # "ozon" | "wb"
    external_id: Mapped[str] = mapped_column(String(64), index=True, default="")  # product_id / числовой nmID
    title: Mapped[str] = mapped_column(String(255), default="")

    cost_price: Mapped[float] = mapped_column(Float, default=0.0)
    packaging_cost: Mapped[float] = mapped_column(Float, default=0.0)
    first_mile_unit_cost: Mapped[float] = mapped_column(Float, default=0.0)
    fbo_logistics_cost: Mapped[float] = mapped_column(Float, default=0.0)
    commission_rate: Mapped[float] = mapped_column(Float, default=0.0)
    target_margin_rub: Mapped[float] = mapped_column(Float, default=100.0)

    current_price: Mapped[float] = mapped_column(Float, default=0.0)
    stock_fbo: Mapped[int] = mapped_column(Integer, default=0)
    stock_fbs: Mapped[int] = mapped_column(Integer, default=0)
    avg_daily_sales: Mapped[float] = mapped_column(Float, default=0.0)

    in_promo: Mapped[bool] = mapped_column(Boolean, default=False)
    promo_max_price: Mapped[float] = mapped_column(Float, default=0.0)
    promo_id: Mapped[str] = mapped_column(String(64), default="")

    active_campaign_id: Mapped[str] = mapped_column(String(64), default="")
    current_ad_bid: Mapped[float] = mapped_column(Float, default=0.0)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive, onupdate=utcnow_naive)


class OrderRecord(Base):
    __tablename__ = "orders"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    order_id: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    marketplace: Mapped[str] = mapped_column(String(16), index=True)  # "ozon" | "wb"
    sku: Mapped[str] = mapped_column(String(64), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    price: Mapped[float] = mapped_column(Float, default=0.0)
    commission: Mapped[float] = mapped_column(Float, default=0.0)
    logistics: Mapped[float] = mapped_column(Float, default=0.0)
    status: Mapped[str] = mapped_column(String(64), default="delivered")


class FirstMileSupply(Base):
    __tablename__ = "first_mile_supplies"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    supply_number: Mapped[str] = mapped_column(String(64), index=True)
    marketplace: Mapped[str] = mapped_column(String(16), index=True)
    sku: Mapped[str] = mapped_column(String(64), index=True)
    quantity: Mapped[int] = mapped_column(Integer)
    total_transport_cost: Mapped[float] = mapped_column(Float)
    unit_cost: Mapped[float] = mapped_column(Float)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive)


class SaleRecord(Base):
    __tablename__ = "sales_records"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    order_id: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    marketplace: Mapped[str] = mapped_column(String(16), index=True)
    sku: Mapped[str] = mapped_column(String(64), index=True)
    quantity: Mapped[int] = mapped_column(Integer, default=1)
    payout_amount: Mapped[float] = mapped_column(Float)
    commission_amount: Mapped[float] = mapped_column(Float)
    logistics_amount: Mapped[float] = mapped_column(Float)
    is_return: Mapped[bool] = mapped_column(Boolean, default=False)
    sale_date: Mapped[datetime] = mapped_column(DateTime, index=True)


class AdSpendRecord(Base):
    __tablename__ = "ad_spend_records"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    marketplace: Mapped[str] = mapped_column(String(16), index=True)
    sku: Mapped[str] = mapped_column(String(64), index=True)
    spend_amount: Mapped[float] = mapped_column(Float)
    spend_date: Mapped[datetime] = mapped_column(DateTime, index=True)


class PendingAction(Base):
    __tablename__ = "pending_actions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    marketplace: Mapped[str] = mapped_column(String(16), index=True)
    sku: Mapped[str] = mapped_column(String(64), index=True)
    action_type: Mapped[str] = mapped_column(String(64))
    payload_json: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(32), default="pending", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive)
    executed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True, default=None)

    @property
    def payload(self) -> Dict[str, Any]:
        """Десериализованный JSON-словарь параметров отложенного действия."""
        if not self.payload_json:
            return {}
        try:
            parsed = json.loads(self.payload_json)
            return parsed if isinstance(parsed, dict) else {}
        except (ValueError, TypeError):
            return {}

    def __int__(self) -> int:
        return int(self.id)

    def __str__(self) -> str:
        return str(self.id) if self.id is not None else ""


_engine_kwargs: Dict[str, Any] = {
    "echo": False,
    "connect_args": {"timeout": 30},
}
if ":memory:" not in settings.DB_URL:
    # NullPool исключает зависание фоновых потоков aiosqlite и утечки файловых дескрипторов SQLite
    _engine_kwargs["poolclass"] = NullPool

engine = create_async_engine(settings.DB_URL, **_engine_kwargs)


@event.listens_for(engine.sync_engine, "connect")
def set_sqlite_pragma(dbapi_connection, connection_record) -> None:
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA journal_mode=WAL;")
    cursor.execute("PRAGMA busy_timeout=30000;")
    cursor.execute("PRAGMA synchronous=NORMAL;")
    cursor.execute("PRAGMA cache_size=-16000;")  # Ограничение кэша до 16MB под 4GB RAM
    cursor.execute("PRAGMA foreign_keys=ON;")
    cursor.close()


AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
)


@asynccontextmanager
async def get_db_session() -> AsyncIterator[AsyncSession]:
    """Безопасный менеджер сессий БД с гарантированным rollback при ошибках и закрытием соединения."""
    session = AsyncSessionLocal()
    try:
        yield session
    except Exception:
        await session.rollback()
        raise
    finally:
        await session.close()


async def _migrate_legacy_sqlite_schema(conn) -> None:
    """Безопасная идемпотентная миграция существующей БД store.db без потери данных."""
    tables_res = await conn.execute(
        text("SELECT name FROM sqlite_master WHERE type='table' AND name IN ('products', 'pending_actions')")
    )
    existing_tables = {row[0] for row in tables_res.fetchall()}

    if "products" in existing_tables:
        info_res = await conn.execute(text("PRAGMA table_info(products)"))
        cols = info_res.fetchall()
        col_names = {row[1] for row in cols}
        pk_cols = [row[1] for row in sorted(cols, key=lambda r: r[5]) if row[5] > 0]

        if "stock_fbs" not in col_names:
            await conn.execute(text("ALTER TABLE products ADD COLUMN stock_fbs INTEGER NOT NULL DEFAULT 0"))

        # Миграция со старого одиночного PK (sku) на составной PK (sku, marketplace)
        if pk_cols == ["sku"]:
            await conn.execute(
                text(
                    """
                    CREATE TABLE products_v2 (
                        sku VARCHAR(64) NOT NULL,
                        marketplace VARCHAR(16) NOT NULL,
                        external_id VARCHAR(64) NOT NULL DEFAULT '',
                        title VARCHAR(255) NOT NULL DEFAULT '',
                        cost_price FLOAT NOT NULL DEFAULT 0.0,
                        packaging_cost FLOAT NOT NULL DEFAULT 0.0,
                        first_mile_unit_cost FLOAT NOT NULL DEFAULT 0.0,
                        fbo_logistics_cost FLOAT NOT NULL DEFAULT 65.0,
                        commission_rate FLOAT NOT NULL DEFAULT 0.23,
                        target_margin_rub FLOAT NOT NULL DEFAULT 100.0,
                        current_price FLOAT NOT NULL DEFAULT 0.0,
                        stock_fbo INTEGER NOT NULL DEFAULT 0,
                        stock_fbs INTEGER NOT NULL DEFAULT 0,
                        avg_daily_sales FLOAT NOT NULL DEFAULT 1.0,
                        in_promo BOOLEAN NOT NULL DEFAULT 0,
                        promo_max_price FLOAT NOT NULL DEFAULT 0.0,
                        promo_id VARCHAR(64) NOT NULL DEFAULT '',
                        active_campaign_id VARCHAR(64) NOT NULL DEFAULT '',
                        current_ad_bid FLOAT NOT NULL DEFAULT 0.0,
                        updated_at DATETIME,
                        PRIMARY KEY (sku, marketplace)
                    )
                    """
                )
            )
            await conn.execute(
                text(
                    """
                    INSERT OR REPLACE INTO products_v2 (
                        sku, marketplace, external_id, title,
                        cost_price, packaging_cost, first_mile_unit_cost,
                        fbo_logistics_cost, commission_rate, target_margin_rub,
                        current_price, stock_fbo, stock_fbs, avg_daily_sales,
                        in_promo, promo_max_price, promo_id,
                        active_campaign_id, current_ad_bid, updated_at
                    )
                    SELECT
                        sku,
                        LOWER(COALESCE(NULLIF(TRIM(marketplace), ''), 'ozon')),
                        COALESCE(external_id, ''),
                        COALESCE(title, ''),
                        COALESCE(cost_price, 0.0),
                        COALESCE(packaging_cost, 0.0),
                        COALESCE(first_mile_unit_cost, 0.0),
                        COALESCE(fbo_logistics_cost, 65.0),
                        COALESCE(commission_rate, 0.23),
                        COALESCE(target_margin_rub, 100.0),
                        COALESCE(current_price, 0.0),
                        COALESCE(stock_fbo, 0),
                        COALESCE(stock_fbs, 0),
                        COALESCE(avg_daily_sales, 1.0),
                        COALESCE(in_promo, 0),
                        COALESCE(promo_max_price, 0.0),
                        COALESCE(promo_id, ''),
                        COALESCE(active_campaign_id, ''),
                        COALESCE(current_ad_bid, 0.0),
                        updated_at
                    FROM products
                    """
                )
            )
            await conn.execute(text("DROP TABLE products"))
            await conn.execute(text("ALTER TABLE products_v2 RENAME TO products"))

    if "pending_actions" in existing_tables:
        pa_info = await conn.execute(text("PRAGMA table_info(pending_actions)"))
        pa_cols = {row[1] for row in pa_info.fetchall()}
        if "executed_at" not in pa_cols:
            await conn.execute(text("ALTER TABLE pending_actions ADD COLUMN executed_at DATETIME"))
        await conn.execute(
            text(
                "UPDATE pending_actions SET status = LOWER(status) "
                "WHERE status IN ('PENDING', 'EXECUTED', 'REJECTED')"
            )
        )


async def init_db() -> None:
    async with engine.begin() as conn:
        # sqlite3 legacy mode не начинает транзакцию для DDL автоматически.
        await conn.execute(text("BEGIN IMMEDIATE"))
        await _migrate_legacy_sqlite_schema(conn)
        await conn.run_sync(Base.metadata.create_all)
        await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_products_marketplace ON products (marketplace)"))
        await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_products_external_id ON products (external_id)"))


async def close_db() -> None:
    """Корректно закрывает движок БД и освобождает пул соединений при завершении процесса."""
    await engine.dispose()
