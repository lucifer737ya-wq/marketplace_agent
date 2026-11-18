from datetime import datetime
from sqlalchemy import String, Integer, Float, Boolean, DateTime, Text, event
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from config import settings


class Base(DeclarativeBase):
    pass


class Product(Base):
    __tablename__ = "products"

    sku: Mapped[str] = mapped_column(String(64), primary_key=True)
    marketplace: Mapped[str] = mapped_column(String(16), index=True)  # "ozon" | "wb"
    external_id: Mapped[str] = mapped_column(String(64), index=True)  # product_id / nmId
    title: Mapped[str] = mapped_column(String(255), default="")

    cost_price: Mapped[float] = mapped_column(Float, default=0.0)
    packaging_cost: Mapped[float] = mapped_column(Float, default=0.0)
    first_mile_unit_cost: Mapped[float] = mapped_column(Float, default=0.0)
    fbo_logistics_cost: Mapped[float] = mapped_column(Float, default=65.0)
    commission_rate: Mapped[float] = mapped_column(Float, default=0.18)
    target_margin_rub: Mapped[float] = mapped_column(Float, default=100.0)

    current_price: Mapped[float] = mapped_column(Float, default=0.0)
    stock_fbo: Mapped[int] = mapped_column(Integer, default=0)
    avg_daily_sales: Mapped[float] = mapped_column(Float, default=1.0)

    in_promo: Mapped[bool] = mapped_column(Boolean, default=False)
    promo_max_price: Mapped[float] = mapped_column(Float, default=0.0)
    promo_id: Mapped[str] = mapped_column(String(64), default="")

    active_campaign_id: Mapped[str] = mapped_column(String(64), default="")
    current_ad_bid: Mapped[float] = mapped_column(Float, default=0.0)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


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
    marketplace: Mapped[str] = mapped_column(String(16))
    sku: Mapped[str] = mapped_column(String(64), index=True)
    quantity: Mapped[int] = mapped_column(Integer)
    total_transport_cost: Mapped[float] = mapped_column(Float)
    unit_cost: Mapped[float] = mapped_column(Float)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class SaleRecord(Base):
    __tablename__ = "sales_records"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    order_id: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    marketplace: Mapped[str] = mapped_column(String(16), index=True)
    sku: Mapped[str] = mapped_column(String(64), index=True)
    quantity: Mapped[int] = mapped_column(Integer, default=1)
    payout_amount: Mapped[float] = mapped_column(Float)  # Дебет (к перечислению)
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
    marketplace: Mapped[str] = mapped_column(String(16))
    sku: Mapped[str] = mapped_column(String(64))
    action_type: Mapped[str] = mapped_column(String(64))
    payload_json: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(32), default="PENDING")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


engine = create_async_engine(settings.DB_URL, echo=False, connect_args={"timeout": 30})


@event.listens_for(engine.sync_engine, "connect")
def set_sqlite_pragma(dbapi_connection, connection_record):
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA journal_mode=WAL;")
    cursor.execute("PRAGMA synchronous=NORMAL;")
    cursor.execute("PRAGMA cache_size=-16000;")  # Ограничение кэша до 16MB под 4GB RAM
    cursor.execute("PRAGMA foreign_keys=ON;")
    cursor.close()


AsyncSessionLocal = async_sessionmaker(
    bind=engine, class_=AsyncSession, expire_on_commit=False
)


async def init_db() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)