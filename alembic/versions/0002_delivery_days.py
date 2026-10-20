"""delivery days chosen by the store

Revision ID: 0002_delivery_days
Revises: 0001_initial
Create Date: 2026-10-17 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0002_delivery_days"
down_revision: Union[str, Sequence[str], None] = "0001_initial"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "stores",
        sa.Column("route_weekdays", sa.String(length=32), server_default="0,2,4", nullable=False),
    )
    op.add_column("stores", sa.Column("delivery_days_set_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("stores", "delivery_days_set_at")
    op.drop_column("stores", "route_weekdays")
