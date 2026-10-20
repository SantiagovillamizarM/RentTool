"""units the distributor has available per product

Revision ID: 0004_product_availability
Revises: 0003_order_delivery
Create Date: 2026-10-17 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0004_product_availability"
down_revision: Union[str, Sequence[str], None] = "0003_order_delivery"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("products", sa.Column("available_units", sa.Integer(), nullable=True))
    op.create_check_constraint(
        "ck_products_available_units_non_negative", "products", "available_units IS NULL OR available_units >= 0"
    )


def downgrade() -> None:
    op.drop_constraint("ck_products_available_units_non_negative", "products", type_="check")
    op.drop_column("products", "available_units")
