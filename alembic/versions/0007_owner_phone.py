"""owner phone replaces the store link code

Revision ID: 0007_owner_phone
Revises: 0006_store_consents
Create Date: 2026-10-23 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0007_owner_phone"
down_revision: Union[str, Sequence[str], None] = "0006_store_consents"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # El tendero entra compartiendo su número verificado de Telegram: el código ya no da acceso.
    op.add_column("stores", sa.Column("owner_phone", sa.String(length=20), nullable=True))
    op.create_unique_constraint("stores_owner_phone_key", "stores", ["owner_phone"])
    op.drop_column("stores", "link_code")


def downgrade() -> None:
    op.add_column("stores", sa.Column("link_code", sa.String(length=32), nullable=True))
    op.execute("UPDATE stores SET link_code = 'TIENDA' || id")
    op.alter_column("stores", "link_code", nullable=False)
    op.create_unique_constraint("stores_link_code_key", "stores", ["link_code"])
    op.drop_constraint("stores_owner_phone_key", "stores", type_="unique")
    op.drop_column("stores", "owner_phone")
