"""last activity of the linked chat and idle-session notice

Revision ID: 0005_store_activity
Revises: 0004_product_availability
Create Date: 2026-10-23 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0005_store_activity"
down_revision: Union[str, Sequence[str], None] = "0004_product_availability"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("stores", sa.Column("last_activity_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("stores", sa.Column("idle_notice_sent_at", sa.DateTime(timezone=True), nullable=True))
    # Las tiendas ya vinculadas empiezan a contar la inactividad desde hoy.
    op.execute("UPDATE stores SET last_activity_at = now() WHERE telegram_chat_id IS NOT NULL")


def downgrade() -> None:
    op.drop_column("stores", "idle_notice_sent_at")
    op.drop_column("stores", "last_activity_at")
