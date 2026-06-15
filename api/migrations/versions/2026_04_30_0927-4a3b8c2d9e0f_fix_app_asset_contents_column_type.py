"""Fix app_asset_contents content column type to LONGTEXT for MySQL.

Revision ID: 4a3b8c2d9e0f
Revises: 5ee0aa981887
Create Date: 2026-04-30 09:27:00.000000

"""
from alembic import op

import models as models

revision = "4a3b8c2d9e0f"
down_revision = "5ee0aa981887"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column(
        "app_asset_contents",
        "content",
        type_=models.types.LongText(),
        existing_nullable=False,
    )


def downgrade() -> None:
    pass
