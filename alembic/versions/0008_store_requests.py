"""store requests from the public page and store contact data

Revision ID: 0008_store_requests
Revises: 0007_owner_phone
Create Date: 2026-10-31 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0008_store_requests"
down_revision: Union[str, Sequence[str], None] = "0007_owner_phone"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

STORE_COLUMNS = [
    ("document_id", sa.String(length=20)),
    ("email", sa.String(length=255)),
    ("city", sa.String(length=80)),
    ("neighborhood", sa.String(length=120)),
    ("address", sa.String(length=200)),
    ("receiving_hours", sa.String(length=80)),
    ("payment_method", sa.String(length=16)),
    ("latitude", sa.Numeric(9, 6)),
    ("longitude", sa.Numeric(9, 6)),
]


def upgrade() -> None:
    for name, type_ in STORE_COLUMNS:
        op.add_column("stores", sa.Column(name, type_, nullable=True))

    op.create_table(
        "store_requests",
        sa.Column("id", sa.Integer(), sa.Identity(), primary_key=True),
        sa.Column(
            "distributor_id", sa.Integer(), sa.ForeignKey("distributors.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="pending"),
        sa.Column("store_name", sa.String(length=160), nullable=False),
        sa.Column("owner_name", sa.String(length=160), nullable=False),
        sa.Column("document_id", sa.String(length=20), nullable=False),
        sa.Column("phone", sa.String(length=20), nullable=False),
        sa.Column("email", sa.String(length=255), nullable=False),
        sa.Column("city", sa.String(length=80), nullable=False),
        sa.Column("neighborhood", sa.String(length=120), nullable=False),
        sa.Column("address", sa.String(length=200), nullable=False),
        sa.Column("receiving_hours", sa.String(length=80), nullable=False),
        sa.Column("payment_method", sa.String(length=16), nullable=False),
        sa.Column("latitude", sa.Numeric(9, 6), nullable=True),
        sa.Column("longitude", sa.Numeric(9, 6), nullable=True),
        sa.Column("consent_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("telegram_chat_id", sa.BigInteger(), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("rejection_reason", sa.String(length=300), nullable=True),
        sa.Column("store_id", sa.Integer(), sa.ForeignKey("stores.id", ondelete="SET NULL"), nullable=True),
        sa.Column("reminder_email_sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("decision_notified_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("status IN ('pending', 'approved', 'rejected')", name="ck_store_requests_status"),
        sa.CheckConstraint("payment_method IN ('contado', 'credito')", name="ck_store_requests_payment"),
    )
    op.create_index("ix_store_requests_distributor_id", "store_requests", ["distributor_id"])
    op.create_index("ix_store_requests_phone", "store_requests", ["phone"])
    op.create_index("ix_store_requests_distributor_status", "store_requests", ["distributor_id", "status"])
    op.create_index(
        "uq_store_requests_pending_phone",
        "store_requests",
        ["phone"],
        unique=True,
        postgresql_where=sa.text("status = 'pending'"),
    )


def downgrade() -> None:
    op.drop_table("store_requests")
    for name, _type in reversed(STORE_COLUMNS):
        op.drop_column("stores", name)
