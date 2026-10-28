from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Identity,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class Distributor(Base):
    __tablename__ = "distributors"

    id: Mapped[int] = mapped_column(Identity(), primary_key=True)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    users: Mapped[list["DistributorUser"]] = relationship(back_populates="distributor")
    stores: Mapped[list["Store"]] = relationship(back_populates="distributor")
    products: Mapped[list["Product"]] = relationship(back_populates="distributor")


class DistributorUser(Base):
    __tablename__ = "distributor_users"

    id: Mapped[int] = mapped_column(Identity(), primary_key=True)
    distributor_id: Mapped[int] = mapped_column(
        ForeignKey("distributors.id", ondelete="CASCADE"), nullable=False, index=True
    )
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)

    distributor: Mapped[Distributor] = relationship(back_populates="users")


class Store(Base):
    __tablename__ = "stores"

    id: Mapped[int] = mapped_column(Identity(), primary_key=True)
    distributor_id: Mapped[int] = mapped_column(
        ForeignKey("distributors.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    owner_name: Mapped[str] = mapped_column(String(160), nullable=False)
    # Celular del dueño (solo dígitos, con indicativo). Entra al bot compartiendo este número.
    owner_phone: Mapped[str | None] = mapped_column(String(20), unique=True, nullable=True)
    telegram_chat_id: Mapped[int | None] = mapped_column(BigInteger, unique=True, nullable=True)
    # Días en que el proveedor pasa por la zona de la tienda (los define el distribuidor).
    route_weekdays: Mapped[str] = mapped_column(String(32), nullable=False, default="0,2,4")
    # Días en que el tendero recibe la mercancía (los escoge de route_weekdays en el bot).
    visit_weekdays: Mapped[str] = mapped_column(String(32), nullable=False, default="0,3")
    lead_time_days: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    consent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    delivery_days_set_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Entrega para la que ya se mandó el recordatorio de pedido (evita repetirlo).
    last_reminder_for: Mapped[date | None] = mapped_column(Date, nullable=True)
    # Último mensaje o botón del chat vinculado, y cuándo se le avisó que la sesión sigue abierta.
    last_activity_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    idle_notice_sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    distributor: Mapped[Distributor] = relationship(back_populates="stores")
    sales: Mapped[list["Sale"]] = relationship(back_populates="store")
    stock_counts: Mapped[list["StockCount"]] = relationship(back_populates="store")
    receipts: Mapped[list["Receipt"]] = relationship(back_populates="store")
    suggested_orders: Mapped[list["SuggestedOrder"]] = relationship(back_populates="store")
    pending_actions: Mapped[list["PendingAction"]] = relationship(back_populates="store")


class StoreConsent(Base):
    """Autorización de datos (Ley 1581) que dio una cuenta de Telegram para una tienda.

    Se conserva al cerrar sesión: si la misma cuenta vuelve a entrar no se le pregunta otra vez.
    """

    __tablename__ = "store_consents"
    __table_args__ = (UniqueConstraint("store_id", "telegram_chat_id", name="uq_store_consents_store_chat"),)

    id: Mapped[int] = mapped_column(Identity(), primary_key=True)
    store_id: Mapped[int] = mapped_column(
        ForeignKey("stores.id", ondelete="CASCADE"), nullable=False, index=True
    )
    telegram_chat_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    accepted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class Product(Base):
    __tablename__ = "products"
    __table_args__ = (
        UniqueConstraint("distributor_id", "sku", name="uq_products_distributor_sku"),
        CheckConstraint(
            "available_units IS NULL OR available_units >= 0", name="ck_products_available_units_non_negative"
        ),
    )

    id: Mapped[int] = mapped_column(Identity(), primary_key=True)
    distributor_id: Mapped[int] = mapped_column(
        ForeignKey("distributors.id", ondelete="CASCADE"), nullable=False, index=True
    )
    sku: Mapped[str] = mapped_column(String(32), nullable=False)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    aliases: Mapped[str] = mapped_column(Text, nullable=False, default="")
    presentation: Mapped[str] = mapped_column(String(80), nullable=False)
    units_per_case: Mapped[int] = mapped_column(Integer, nullable=False)
    unit_price: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    # Unidades que el distribuidor tiene para vender. None = no lleva la cuenta (sin límite).
    available_units: Mapped[int | None] = mapped_column(Integer, nullable=True)

    distributor: Mapped[Distributor] = relationship(back_populates="products")


class Sale(Base):
    __tablename__ = "sales"
    __table_args__ = (
        CheckConstraint("quantity > 0", name="ck_sales_quantity_positive"),
        CheckConstraint("source IN ('voice', 'text', 'manual')", name="ck_sales_source"),
        Index("ix_sales_store_sold_at", "store_id", "sold_at"),
    )

    id: Mapped[int] = mapped_column(Identity(), primary_key=True)
    store_id: Mapped[int] = mapped_column(
        ForeignKey("stores.id", ondelete="CASCADE"), nullable=False, index=True
    )
    product_id: Mapped[int] = mapped_column(
        ForeignKey("products.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    unit_price: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    sold_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    source: Mapped[str] = mapped_column(String(16), nullable=False)

    store: Mapped[Store] = relationship(back_populates="sales")
    product: Mapped[Product] = relationship()


class StockCount(Base):
    __tablename__ = "stock_counts"
    __table_args__ = (
        CheckConstraint("quantity >= 0", name="ck_stock_counts_quantity_non_negative"),
        CheckConstraint("source IN ('photo', 'manual')", name="ck_stock_counts_source"),
        Index("ix_stock_counts_store_counted_at", "store_id", "counted_at"),
    )

    id: Mapped[int] = mapped_column(Identity(), primary_key=True)
    store_id: Mapped[int] = mapped_column(
        ForeignKey("stores.id", ondelete="CASCADE"), nullable=False, index=True
    )
    product_id: Mapped[int] = mapped_column(
        ForeignKey("products.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    counted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    source: Mapped[str] = mapped_column(String(16), nullable=False)

    store: Mapped[Store] = relationship(back_populates="stock_counts")
    product: Mapped[Product] = relationship()


class Receipt(Base):
    __tablename__ = "receipts"
    __table_args__ = (
        CheckConstraint("quantity > 0", name="ck_receipts_quantity_positive"),
        Index("ix_receipts_store_received_at", "store_id", "received_at"),
    )

    id: Mapped[int] = mapped_column(Identity(), primary_key=True)
    store_id: Mapped[int] = mapped_column(
        ForeignKey("stores.id", ondelete="CASCADE"), nullable=False, index=True
    )
    product_id: Mapped[int] = mapped_column(
        ForeignKey("products.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    store: Mapped[Store] = relationship(back_populates="receipts")
    product: Mapped[Product] = relationship()


class SuggestedOrder(Base):
    __tablename__ = "suggested_orders"
    __table_args__ = (
        CheckConstraint("status IN ('draft', 'sent', 'received')", name="ck_suggested_orders_status"),
        Index("ix_suggested_orders_store_created_at", "store_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(Identity(), primary_key=True)
    store_id: Mapped[int] = mapped_column(
        ForeignKey("stores.id", ondelete="CASCADE"), nullable=False, index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="draft")
    delivery_date: Mapped[date | None] = mapped_column(Date, nullable=True)

    store: Mapped[Store] = relationship(back_populates="suggested_orders")
    items: Mapped[list["SuggestedOrderItem"]] = relationship(
        back_populates="order", cascade="all, delete-orphan"
    )


class SuggestedOrderItem(Base):
    __tablename__ = "suggested_order_items"

    id: Mapped[int] = mapped_column(Identity(), primary_key=True)
    order_id: Mapped[int] = mapped_column(
        ForeignKey("suggested_orders.id", ondelete="CASCADE"), nullable=False, index=True
    )
    product_id: Mapped[int] = mapped_column(
        ForeignKey("products.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    cases: Mapped[int] = mapped_column(Integer, nullable=False)
    units: Mapped[int] = mapped_column(Integer, nullable=False)
    reason: Mapped[str] = mapped_column(String(240), nullable=False)

    order: Mapped[SuggestedOrder] = relationship(back_populates="items")
    product: Mapped[Product] = relationship()


class PendingAction(Base):
    __tablename__ = "pending_actions"
    __table_args__ = (Index("ix_pending_actions_store_expires", "store_id", "expires_at"),)

    id: Mapped[int] = mapped_column(Identity(), primary_key=True)
    store_id: Mapped[int] = mapped_column(
        ForeignKey("stores.id", ondelete="CASCADE"), nullable=False, index=True
    )
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    store: Mapped[Store] = relationship(back_populates="pending_actions")
