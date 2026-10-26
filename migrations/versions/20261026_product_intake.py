"""Product intake: product, attrs, SKUs, evidence images, chat messages.

Revision ID: 20261026_product_intake
Revises: 20261025_project_workflow
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "20261026_product_intake"
down_revision = "20261025_project_workflow"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "product",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.String(64), nullable=False),
        sa.Column("name", sa.String(256), nullable=False),
        sa.Column("category", sa.String(32), nullable=False),
        sa.Column("brand", sa.String(128), nullable=False),
        sa.Column("code", sa.String(64), nullable=False),
        sa.Column("pitch", sa.Text(), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("source", sa.String(24), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        schema="app",
    )
    op.create_index("ix_product_status", "product", ["status"], schema="app")
    op.create_index("ix_product_created_at", "product", ["created_at"], schema="app")

    op.create_table(
        "product_attr",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("product_id", sa.Uuid(), sa.ForeignKey("app.product.id", ondelete="CASCADE"), nullable=False),
        sa.Column("key", sa.String(80), nullable=False),
        sa.Column("value", sa.Text(), nullable=False),
        sa.Column("unit", sa.String(32), nullable=False),
        sa.Column("source", sa.String(16), nullable=False),
        sa.Column("confidence", sa.Numeric(5, 4), nullable=True),
        sa.Column("confirmed", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        schema="app",
    )
    op.create_index("ix_product_attr_product", "product_attr", ["product_id"], schema="app")

    op.create_table(
        "sku",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("product_id", sa.Uuid(), sa.ForeignKey("app.product.id", ondelete="CASCADE"), nullable=False),
        sa.Column("color", sa.String(80), nullable=False),
        sa.Column("spec", sa.String(80), nullable=False),
        sa.Column("price_cents", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        schema="app",
    )
    op.create_index("ix_sku_product", "sku", ["product_id"], schema="app")

    op.create_table(
        "product_image",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("product_id", sa.Uuid(), sa.ForeignKey("app.product.id", ondelete="CASCADE"), nullable=False),
        sa.Column("object_key", sa.String(512), nullable=False),
        sa.Column("mime", sa.String(128), nullable=False),
        sa.Column("bytes", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(24), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        schema="app",
    )
    op.create_index("ix_product_image_product", "product_image", ["product_id"], schema="app")

    op.create_table(
        "product_intake_message",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("product_id", sa.Uuid(), sa.ForeignKey("app.product.id", ondelete="CASCADE"), nullable=False),
        sa.Column("role", sa.String(16), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("attachments", postgresql.JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        schema="app",
    )
    op.create_index("ix_product_message_product", "product_intake_message", ["product_id", "created_at"], schema="app")


def downgrade() -> None:
    op.drop_index("ix_product_message_product", table_name="product_intake_message", schema="app")
    op.drop_table("product_intake_message", schema="app")
    op.drop_index("ix_product_image_product", table_name="product_image", schema="app")
    op.drop_table("product_image", schema="app")
    op.drop_index("ix_sku_product", table_name="sku", schema="app")
    op.drop_table("sku", schema="app")
    op.drop_index("ix_product_attr_product", table_name="product_attr", schema="app")
    op.drop_table("product_attr", schema="app")
    op.drop_index("ix_product_created_at", table_name="product", schema="app")
    op.drop_index("ix_product_status", table_name="product", schema="app")
    op.drop_table("product", schema="app")
