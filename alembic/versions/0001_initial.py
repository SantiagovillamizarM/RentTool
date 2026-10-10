"""initial schema

Revision ID: 0001_initial
Revises:
Create Date: 2026-10-08 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001_initial"
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "distributors",
        sa.Column("id", sa.Integer(), sa.Identity(), nullable=False),
        sa.Column("name", sa.String(length=160), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "distributor_users",
        sa.Column("id", sa.Integer(), sa.Identity(), nullable=False),
        sa.Column("distributor_id", sa.Integer(), nullable=False),
        sa.Column("email", sa.String(length=255), nullable=False),
        sa.Column("password_hash", sa.String(length=255), nullable=False),
        sa.ForeignKeyConstraint(["distributor_id"], ["distributors.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("email"),
    )
    op.create_index(
        "ix_distributor_users_distributor_id",
        "distributor_users",
        ["distributor_id"],
    )
    op.create_table(
        "stores",
        sa.Column("id", sa.Integer(), sa.Identity(), nullable=False),
        sa.Column("distributor_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=160), nullable=False),
        sa.Column("owner_name", sa.String(length=160), nullable=False),
        sa.Column("link_code", sa.String(length=32), nullable=False),
        sa.Column("telegram_chat_id", sa.BigInteger(), nullable=True),
        sa.Column("visit_weekdays", sa.String(length=32), nullable=False),
        sa.Column("lead_time_days", sa.Integer(), nullable=False),
        sa.Column("consent_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["distributor_id"], ["distributors.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("link_code"),
        sa.UniqueConstraint("telegram_chat_id"),
    )
    op.create_index("ix_stores_distributor_id", "stores", ["distributor_id"])
    op.create_table(
        "products",
        sa.Column("id", sa.Integer(), sa.Identity(), nullable=False),
        sa.Column("distributor_id", sa.Integer(), nullable=False),
        sa.Column("sku", sa.String(length=32), nullable=False),
        sa.Column("name", sa.String(length=160), nullable=False),
        sa.Column("aliases", sa.Text(), nullable=False),
        sa.Column("presentation", sa.String(length=80), nullable=False),
        sa.Column("units_per_case", sa.Integer(), nullable=False),
        sa.Column("unit_price", sa.Numeric(precision=12, scale=2), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.ForeignKeyConstraint(["distributor_id"], ["distributors.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("distributor_id", "sku", name="uq_products_distributor_sku"),
    )
    op.create_index("ix_products_distributor_id", "products", ["distributor_id"])
    op.create_table(
        "sales",
        sa.Column("id", sa.Integer(), sa.Identity(), nullable=False),
        sa.Column("store_id", sa.Integer(), nullable=False),
        sa.Column("product_id", sa.Integer(), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("unit_price", sa.Numeric(precision=12, scale=2), nullable=False),
        sa.Column("sold_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source", sa.String(length=16), nullable=False),
        sa.CheckConstraint("quantity > 0", name="ck_sales_quantity_positive"),
        sa.CheckConstraint("source IN ('voice', 'text', 'manual')", name="ck_sales_source"),
        sa.ForeignKeyConstraint(["product_id"], ["products.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["store_id"], ["stores.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_sales_store_id", "sales", ["store_id"])
    op.create_index("ix_sales_product_id", "sales", ["product_id"])
    op.create_index("ix_sales_store_sold_at", "sales", ["store_id", "sold_at"])
    op.create_table(
        "stock_counts",
        sa.Column("id", sa.Integer(), sa.Identity(), nullable=False),
        sa.Column("store_id", sa.Integer(), nullable=False),
        sa.Column("product_id", sa.Integer(), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("counted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source", sa.String(length=16), nullable=False),
        sa.CheckConstraint("quantity >= 0", name="ck_stock_counts_quantity_non_negative"),
        sa.CheckConstraint("source IN ('photo', 'manual')", name="ck_stock_counts_source"),
        sa.ForeignKeyConstraint(["product_id"], ["products.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["store_id"], ["stores.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_stock_counts_store_id", "stock_counts", ["store_id"])
    op.create_index("ix_stock_counts_product_id", "stock_counts", ["product_id"])
    op.create_index("ix_stock_counts_store_counted_at", "stock_counts", ["store_id", "counted_at"])
    op.create_table(
        "receipts",
        sa.Column("id", sa.Integer(), sa.Identity(), nullable=False),
        sa.Column("store_id", sa.Integer(), nullable=False),
        sa.Column("product_id", sa.Integer(), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("quantity > 0", name="ck_receipts_quantity_positive"),
        sa.ForeignKeyConstraint(["product_id"], ["products.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["store_id"], ["stores.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_receipts_store_id", "receipts", ["store_id"])
    op.create_index("ix_receipts_product_id", "receipts", ["product_id"])
    op.create_index("ix_receipts_store_received_at", "receipts", ["store_id", "received_at"])
    op.create_table(
        "suggested_orders",
        sa.Column("id", sa.Integer(), sa.Identity(), nullable=False),
        sa.Column("store_id", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.CheckConstraint(
            "status IN ('draft', 'sent', 'received')",
            name="ck_suggested_orders_status",
        ),
        sa.ForeignKeyConstraint(["store_id"], ["stores.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_suggested_orders_store_id", "suggested_orders", ["store_id"])
    op.create_index(
        "ix_suggested_orders_store_created_at",
        "suggested_orders",
        ["store_id", "created_at"],
    )
    op.create_table(
        "suggested_order_items",
        sa.Column("id", sa.Integer(), sa.Identity(), nullable=False),
        sa.Column("order_id", sa.Integer(), nullable=False),
        sa.Column("product_id", sa.Integer(), nullable=False),
        sa.Column("cases", sa.Integer(), nullable=False),
        sa.Column("units", sa.Integer(), nullable=False),
        sa.Column("reason", sa.String(length=240), nullable=False),
        sa.ForeignKeyConstraint(["order_id"], ["suggested_orders.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["product_id"], ["products.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_suggested_order_items_order_id", "suggested_order_items", ["order_id"])
    op.create_index(
        "ix_suggested_order_items_product_id",
        "suggested_order_items",
        ["product_id"],
    )
    op.create_table(
        "pending_actions",
        sa.Column("id", sa.Integer(), sa.Identity(), nullable=False),
        sa.Column("store_id", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["store_id"], ["stores.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_pending_actions_store_id", "pending_actions", ["store_id"])
    op.create_index(
        "ix_pending_actions_store_expires",
        "pending_actions",
        ["store_id", "expires_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_pending_actions_store_expires", table_name="pending_actions")
    op.drop_index("ix_pending_actions_store_id", table_name="pending_actions")
    op.drop_table("pending_actions")
    op.drop_index("ix_suggested_order_items_product_id", table_name="suggested_order_items")
    op.drop_index("ix_suggested_order_items_order_id", table_name="suggested_order_items")
    op.drop_table("suggested_order_items")
    op.drop_index("ix_suggested_orders_store_created_at", table_name="suggested_orders")
    op.drop_index("ix_suggested_orders_store_id", table_name="suggested_orders")
    op.drop_table("suggested_orders")
    op.drop_index("ix_receipts_store_received_at", table_name="receipts")
    op.drop_index("ix_receipts_product_id", table_name="receipts")
    op.drop_index("ix_receipts_store_id", table_name="receipts")
    op.drop_table("receipts")
    op.drop_index("ix_stock_counts_store_counted_at", table_name="stock_counts")
    op.drop_index("ix_stock_counts_product_id", table_name="stock_counts")
    op.drop_index("ix_stock_counts_store_id", table_name="stock_counts")
    op.drop_table("stock_counts")
    op.drop_index("ix_sales_store_sold_at", table_name="sales")
    op.drop_index("ix_sales_product_id", table_name="sales")
    op.drop_index("ix_sales_store_id", table_name="sales")
    op.drop_table("sales")
    op.drop_index("ix_products_distributor_id", table_name="products")
    op.drop_table("products")
    op.drop_index("ix_stores_distributor_id", table_name="stores")
    op.drop_table("stores")
    op.drop_index("ix_distributor_users_distributor_id", table_name="distributor_users")
    op.drop_table("distributor_users")
    op.drop_table("distributors")
