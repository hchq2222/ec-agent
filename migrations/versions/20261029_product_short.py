"""Product short name.

Revision ID: 20261029_product_short
Revises: 20261028_templates_softdelete
"""
import sqlalchemy as sa
from alembic import op

revision = "20261029_product_short"
down_revision = "20261028_templates_softdelete"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "product",
        sa.Column("short_name", sa.String(128), nullable=False, server_default=""),
        schema="app",
    )


def downgrade() -> None:
    op.drop_column("product", "short_name", schema="app")
