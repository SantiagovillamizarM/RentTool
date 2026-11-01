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
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class Distributor(Base):
    """Proveedor (distribuidor). Lo crea el administrador de TenderoBot, o lo aprueba desde una solicitud."""

    __tablename__ = "distributors"
    __table_args__ = (CheckConstraint("status IN ('active', 'suspended')", name="ck_distributors_status"),)

    id: Mapped[int] = mapped_column(Identity(), primary_key=True)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    # Suspendido: no entra al panel y sus tenderos no pueden usar el bot hasta que se reactive.
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active", server_default="active")
    suspended_reason: Mapped[str | None] = mapped_column(String(300), nullable=True)
    # Datos de la empresa (los del proveedor de prueba del seed quedan vacíos).
    legal_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    nit: Mapped[str | None] = mapped_column(String(20), nullable=True)
    contact_name: Mapped[str | None] = mapped_column(String(160), nullable=True)
    contact_phone: Mapped[str | None] = mapped_column(String(20), nullable=True)
    contact_email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    city: Mapped[str | None] = mapped_column(String(80), nullable=True)
    address: Mapped[str | None] = mapped_column(String(200), nullable=True)
    coverage: Mapped[str | None] = mapped_column(String(500), nullable=True)
    categories: Mapped[str | None] = mapped_column(String(300), nullable=True)

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
    # None mientras no haya creado su contraseña con el enlace del correo.
    activated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    distributor: Mapped[Distributor] = relationship(back_populates="users")


class AdminUser(Base):
    """Administrador de TenderoBot: maneja proveedores. Solo se crea por comando (scripts/admin_user.py)."""

    __tablename__ = "admin_users"

    id: Mapped[int] = mapped_column(Identity(), primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class PasswordToken(Base):
    """Enlace de un solo uso para crear o cambiar la contraseña del panel. Solo se guarda el hash."""

    __tablename__ = "password_tokens"
    __table_args__ = (CheckConstraint("purpose IN ('activate', 'reset')", name="ck_password_tokens_purpose"),)

    id: Mapped[int] = mapped_column(Identity(), primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("distributor_users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    purpose: Mapped[str] = mapped_column(String(16), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class DistributorRequest(Base):
    """Solicitud de una empresa para ser proveedor en TenderoBot. La aprueba o rechaza el administrador."""

    __tablename__ = "distributor_requests"
    __table_args__ = (
        CheckConstraint("status IN ('pending', 'approved', 'rejected')", name="ck_distributor_requests_status"),
        Index(
            "uq_distributor_requests_pending_email",
            "contact_email",
            unique=True,
            postgresql_where=text("status = 'pending'"),
        ),
    )

    id: Mapped[int] = mapped_column(Identity(), primary_key=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    legal_name: Mapped[str] = mapped_column(String(200), nullable=False)
    trade_name: Mapped[str] = mapped_column(String(160), nullable=False)
    nit: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    chamber_registration: Mapped[str] = mapped_column(String(40), nullable=False)
    legal_rep_name: Mapped[str] = mapped_column(String(160), nullable=False)
    legal_rep_document: Mapped[str] = mapped_column(String(20), nullable=False)
    contact_name: Mapped[str] = mapped_column(String(160), nullable=False)
    contact_role: Mapped[str] = mapped_column(String(80), nullable=False)
    contact_phone: Mapped[str] = mapped_column(String(20), nullable=False)
    contact_email: Mapped[str] = mapped_column(String(255), nullable=False)
    city: Mapped[str] = mapped_column(String(80), nullable=False)
    address: Mapped[str] = mapped_column(String(200), nullable=False)
    coverage: Mapped[str] = mapped_column(String(500), nullable=False)
    categories: Mapped[str] = mapped_column(String(300), nullable=False)
    vehicles: Mapped[int] = mapped_column(Integer, nullable=False)
    stores_estimate: Mapped[int] = mapped_column(Integer, nullable=False)
    website: Mapped[str | None] = mapped_column(String(255), nullable=True)
    consent_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    rejection_reason: Mapped[str | None] = mapped_column(String(300), nullable=True)
    distributor_id: Mapped[int | None] = mapped_column(
        ForeignKey("distributors.id", ondelete="SET NULL"), nullable=True
    )


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
    # Datos para despachar y facturar (llegan de la solicitud o los escribe el distribuidor).
    document_id: Mapped[str | None] = mapped_column(String(20), nullable=True)
    email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    city: Mapped[str | None] = mapped_column(String(80), nullable=True)
    neighborhood: Mapped[str | None] = mapped_column(String(120), nullable=True)
    address: Mapped[str | None] = mapped_column(String(200), nullable=True)
    receiving_hours: Mapped[str | None] = mapped_column(String(80), nullable=True)
    payment_method: Mapped[str | None] = mapped_column(String(16), nullable=True)
    latitude: Mapped[Decimal | None] = mapped_column(Numeric(9, 6), nullable=True)
    longitude: Mapped[Decimal | None] = mapped_column(Numeric(9, 6), nullable=True)

    distributor: Mapped[Distributor] = relationship(back_populates="stores")
    sales: Mapped[list["Sale"]] = relationship(back_populates="store")
    stock_counts: Mapped[list["StockCount"]] = relationship(back_populates="store")
    receipts: Mapped[list["Receipt"]] = relationship(back_populates="store")
    suggested_orders: Mapped[list["SuggestedOrder"]] = relationship(back_populates="store")
    pending_actions: Mapped[list["PendingAction"]] = relationship(back_populates="store")


class StoreRequest(Base):
    """Solicitud de un tendero desde la página pública. El distribuidor la aprueba o la rechaza.

    Si el tendero abre el bot y comparte su número, se guarda su chat para avisarle por Telegram;
    si no, se le avisa por correo.
    """

    __tablename__ = "store_requests"
    __table_args__ = (
        CheckConstraint("status IN ('pending', 'approved', 'rejected')", name="ck_store_requests_status"),
        CheckConstraint("payment_method IN ('contado', 'credito')", name="ck_store_requests_payment"),
        Index("ix_store_requests_distributor_status", "distributor_id", "status"),
        # Una sola solicitud en revisión por celular.
        Index(
            "uq_store_requests_pending_phone",
            "phone",
            unique=True,
            postgresql_where=text("status = 'pending'"),
        ),
    )

    id: Mapped[int] = mapped_column(Identity(), primary_key=True)
    distributor_id: Mapped[int] = mapped_column(
        ForeignKey("distributors.id", ondelete="CASCADE"), nullable=False, index=True
    )
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    store_name: Mapped[str] = mapped_column(String(160), nullable=False)
    owner_name: Mapped[str] = mapped_column(String(160), nullable=False)
    document_id: Mapped[str] = mapped_column(String(20), nullable=False)
    phone: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    email: Mapped[str] = mapped_column(String(255), nullable=False)
    city: Mapped[str] = mapped_column(String(80), nullable=False)
    neighborhood: Mapped[str] = mapped_column(String(120), nullable=False)
    address: Mapped[str] = mapped_column(String(200), nullable=False)
    receiving_hours: Mapped[str] = mapped_column(String(80), nullable=False)
    payment_method: Mapped[str] = mapped_column(String(16), nullable=False)
    latitude: Mapped[Decimal | None] = mapped_column(Numeric(9, 6), nullable=True)
    longitude: Mapped[Decimal | None] = mapped_column(Numeric(9, 6), nullable=True)
    consent_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    telegram_chat_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    rejection_reason: Mapped[str | None] = mapped_column(String(300), nullable=True)
    store_id: Mapped[int | None] = mapped_column(ForeignKey("stores.id", ondelete="SET NULL"), nullable=True)
    reminder_email_sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    decision_notified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    distributor: Mapped[Distributor] = relationship()


class EmailVerification(Base):
    """Código de 6 dígitos que se manda al correo antes de dejar llenar la solicitud (frena a los robots).

    Solo se guarda el hash del código. Vale para el último código sin usar de ese correo.
    """

    __tablename__ = "email_verifications"
    __table_args__ = (Index("ix_email_verifications_email_created", "email", "created_at"),)

    id: Mapped[int] = mapped_column(Identity(), primary_key=True)
    email: Mapped[str] = mapped_column(String(255), nullable=False)
    code_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


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
    # Bloqueado por el administrador (no es apropiado): queda inactivo y el proveedor no lo puede reactivar.
    blocked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    blocked_reason: Mapped[str | None] = mapped_column(String(300), nullable=True)

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
