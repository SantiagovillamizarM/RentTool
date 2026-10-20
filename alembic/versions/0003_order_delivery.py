"""order delivery date and reminders

Revision ID: 0003_order_delivery
Revises: 0002_delivery_days
Create Date: 2026-10-17 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0003_order_delivery"
down_revision: Union[str, Sequence[str], None] = "0002_delivery_days"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("stores", sa.Column("last_reminder_for", sa.Date(), nullable=True))
    op.add_column("suggested_orders", sa.Column("delivery_date", sa.Date(), nullable=True))


def downgrade() -> None:
    op.drop_column("suggested_orders", "delivery_date")
    op.drop_column("stores", "last_reminder_for")
