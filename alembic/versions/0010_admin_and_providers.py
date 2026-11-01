"""admin panel: provider data and status, provider requests, admin users, password links, blocked products

Revision ID: 0010_admin_and_providers
Revises: 0009_email_verifications
Create Date: 2026-10-31 18:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0010_admin_and_providers"
down_revision: Union[str, Sequence[str], None] = "0009_email_verifications"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

DISTRIBUTOR_COLUMNS = [
    ("suspended_reason", sa.String(length=300)),
    ("legal_name", sa.String(length=200)),
    ("nit", sa.String(length=20)),
    ("contact_name", sa.String(length=160)),
    ("contact_phone", sa.String(length=20)),
    ("contact_email", sa.String(length=255)),
    ("city", sa.String(length=80)),
    ("address", sa.String(length=200)),
    ("coverage", sa.String(length=500)),
    ("categories", sa.String(length=300)),
]


def upgrade() -> None:
    op.add_column("distributors", sa.Column("status", sa.String(length=16), nullable=False, server_default="active"))
    op.create_check_constraint("ck_distributors_status", "distributors", "status IN ('active', 'suspended')")
    for name, type_ in DISTRIBUTOR_COLUMNS:
        op.add_column("distributors", sa.Column(name, type_, nullable=True))

    # Las cuentas que ya existen ya tienen contraseña: cuentan como activadas.
    op.add_column("distributor_users", sa.Column("activated_at", sa.DateTime(timezone=True), nullable=True))
    op.execute("UPDATE distributor_users SET activated_at = now()")

    op.add_column("products", sa.Column("blocked_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("products", sa.Column("blocked_reason", sa.String(length=300), nullable=True))

    op.create_table(
        "admin_users",
        sa.Column("id", sa.Integer(), sa.Identity(), primary_key=True),
        sa.Column("email", sa.String(length=255), nullable=False, unique=True),
        sa.Column("password_hash", sa.String(length=255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    op.create_table(
        "password_tokens",
        sa.Column("id", sa.Integer(), sa.Identity(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("distributor_users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False, unique=True),
        sa.Column("purpose", sa.String(length=16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("purpose IN ('activate', 'reset')", name="ck_password_tokens_purpose"),
    )
    op.create_index("ix_password_tokens_user_id", "password_tokens", ["user_id"])

    op.create_table(
        "distributor_requests",
        sa.Column("id", sa.Integer(), sa.Identity(), primary_key=True),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="pending"),
        sa.Column("legal_name", sa.String(length=200), nullable=False),
        sa.Column("trade_name", sa.String(length=160), nullable=False),
        sa.Column("nit", sa.String(length=20), nullable=False),
        sa.Column("chamber_registration", sa.String(length=40), nullable=False),
        sa.Column("legal_rep_name", sa.String(length=160), nullable=False),
        sa.Column("legal_rep_document", sa.String(length=20), nullable=False),
        sa.Column("contact_name", sa.String(length=160), nullable=False),
        sa.Column("contact_role", sa.String(length=80), nullable=False),
        sa.Column("contact_phone", sa.String(length=20), nullable=False),
        sa.Column("contact_email", sa.String(length=255), nullable=False),
        sa.Column("city", sa.String(length=80), nullable=False),
        sa.Column("address", sa.String(length=200), nullable=False),
        sa.Column("coverage", sa.String(length=500), nullable=False),
        sa.Column("categories", sa.String(length=300), nullable=False),
        sa.Column("vehicles", sa.Integer(), nullable=False),
        sa.Column("stores_estimate", sa.Integer(), nullable=False),
        sa.Column("website", sa.String(length=255), nullable=True),
        sa.Column("consent_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("rejection_reason", sa.String(length=300), nullable=True),
        sa.Column("distributor_id", sa.Integer(), sa.ForeignKey("distributors.id", ondelete="SET NULL"), nullable=True),
        sa.CheckConstraint("status IN ('pending', 'approved', 'rejected')", name="ck_distributor_requests_status"),
    )
    op.create_index("ix_distributor_requests_nit", "distributor_requests", ["nit"])
    op.create_index(
        "uq_distributor_requests_pending_email",
        "distributor_requests",
        ["contact_email"],
        unique=True,
        postgresql_where=sa.text("status = 'pending'"),
    )


def downgrade() -> None:
    op.drop_table("distributor_requests")
    op.drop_table("password_tokens")
    op.drop_table("admin_users")
    op.drop_column("products", "blocked_reason")
    op.drop_column("products", "blocked_at")
    op.drop_column("distributor_users", "activated_at")
    for name, _type in reversed(DISTRIBUTOR_COLUMNS):
        op.drop_column("distributors", name)
    op.drop_constraint("ck_distributors_status", "distributors", type_="check")
    op.drop_column("distributors", "status")
