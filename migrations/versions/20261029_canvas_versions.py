"""Canvas projects, asset versions and template reference image.

Revision ID: 20261029_canvas_versions
Revises: 20261029_product_short
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "20261029_canvas_versions"
down_revision = "20261029_product_short"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "layout_template",
        sa.Column("ref_image", sa.Text(), nullable=False, server_default=""),
        schema="app",
    )

    op.create_table(
        "canvas_project",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("tenant_id", sa.String(64), nullable=False),
        sa.Column("product_ref", sa.String(128), nullable=False),
        sa.Column("name", sa.String(256), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("hidden", sa.Boolean(), nullable=False),
        sa.Column("canvas", postgresql.JSONB(), nullable=False),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        schema="app",
    )
    op.create_index("ix_canvas_project_status", "canvas_project", ["status"], schema="app")
    op.create_index("ix_canvas_project_product", "canvas_project", ["product_ref"], schema="app")

    op.create_table(
        "asset_version",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.String(64), nullable=False),
        sa.Column("product_ref", sa.String(128), nullable=False),
        sa.Column("project_id", sa.String(64), nullable=True),
        sa.Column("title", sa.String(256), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("images", postgresql.JSONB(), nullable=False),
        sa.Column("note", sa.Text(), nullable=False),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        schema="app",
    )
    op.create_index("ix_asset_version_product", "asset_version", ["product_ref"], schema="app")
    op.create_index("ix_asset_version_project", "asset_version", ["project_id"], schema="app")
    op.create_index("ix_asset_version_created", "asset_version", ["created_at"], schema="app")


def downgrade() -> None:
    op.drop_index("ix_asset_version_created", table_name="asset_version", schema="app")
    op.drop_index("ix_asset_version_project", table_name="asset_version", schema="app")
    op.drop_index("ix_asset_version_product", table_name="asset_version", schema="app")
    op.drop_table("asset_version", schema="app")
    op.drop_index("ix_canvas_project_product", table_name="canvas_project", schema="app")
    op.drop_index("ix_canvas_project_status", table_name="canvas_project", schema="app")
    op.drop_table("canvas_project", schema="app")
    op.drop_column("layout_template", "ref_image", schema="app")
