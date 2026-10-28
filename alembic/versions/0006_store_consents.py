"""data-processing consent per store and Telegram account

Revision ID: 0006_store_consents
Revises: 0005_store_activity
Create Date: 2026-10-23 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0006_store_consents"
down_revision: Union[str, Sequence[str], None] = "0005_store_activity"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "store_consents",
        sa.Column("id", sa.Integer(), sa.Identity(), primary_key=True),
        sa.Column("store_id", sa.Integer(), sa.ForeignKey("stores.id", ondelete="CASCADE"), nullable=False),
        sa.Column("telegram_chat_id", sa.BigInteger(), nullable=False),
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("store_id", "telegram_chat_id", name="uq_store_consents_store_chat"),
    )
    op.create_index("ix_store_consents_store_id", "store_consents", ["store_id"])
    # Las tiendas vinculadas que ya aceptaron conservan su autorización.
    op.execute(
        "INSERT INTO store_consents (store_id, telegram_chat_id, accepted_at) "
        "SELECT id, telegram_chat_id, consent_at FROM stores "
        "WHERE telegram_chat_id IS NOT NULL AND consent_at IS NOT NULL"
    )


def downgrade() -> None:
    op.drop_index("ix_store_consents_store_id", table_name="store_consents")
    op.drop_table("store_consents")
