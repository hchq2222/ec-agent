"""Layout templates and soft delete for products/projects.

Revision ID: 20261028_templates_softdelete
Revises: 20261026_product_intake
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "20261028_templates_softdelete"
down_revision = "20261026_product_intake"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "layout_template",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.String(64), nullable=False),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("category", sa.String(32), nullable=False),
        sa.Column("size_w", sa.Integer(), nullable=False),
        sa.Column("size_h", sa.Integer(), nullable=False),
        sa.Column("subject_ratio", sa.Float(), nullable=False),
        sa.Column("shadow", sa.String(16), nullable=False),
        sa.Column("background", sa.String(32), nullable=False),
        sa.Column("text_slots", postgresql.JSONB(), nullable=False),
        sa.Column("logo_slot", postgresql.JSONB(), nullable=False),
        sa.Column("builtin", sa.Boolean(), nullable=False),
        sa.Column("builtin_key", sa.String(64), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        schema="app",
    )
    op.create_index("ix_layout_template_category", "layout_template", ["category"], schema="app")
    op.create_index("ux_layout_template_builtin_key", "layout_template", ["builtin_key"], unique=True, schema="app")
    op.create_index("ix_layout_template_deleted_at", "layout_template", ["deleted_at"], schema="app")

    op.add_column("product", sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True), schema="app")
    op.create_index("ix_product_deleted_at", "product", ["deleted_at"], schema="app")

    op.add_column("project", sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True), schema="app")
    op.create_index("ix_project_deleted_at", "project", ["deleted_at"], schema="app")


def downgrade() -> None:
    op.drop_index("ix_project_deleted_at", table_name="project", schema="app")
    op.drop_column("project", "deleted_at", schema="app")
    op.drop_index("ix_product_deleted_at", table_name="product", schema="app")
    op.drop_column("product", "deleted_at", schema="app")
    op.drop_index("ix_layout_template_deleted_at", table_name="layout_template", schema="app")
    op.drop_index("ux_layout_template_builtin_key", table_name="layout_template", schema="app")
    op.drop_index("ix_layout_template_category", table_name="layout_template", schema="app")
    op.drop_table("layout_template", schema="app")
