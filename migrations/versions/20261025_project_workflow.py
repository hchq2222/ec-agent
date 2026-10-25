"""Project planning and workflow execution snapshots.

Revision ID: 20261025_project_workflow
Revises: 0002_channel
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "20261025_project_workflow"
down_revision = "0002_channel"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table("project",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.String(64), nullable=False),
        sa.Column("product_ref", sa.String(128), nullable=False),
        sa.Column("name", sa.String(256), nullable=False),
        sa.Column("product_snapshot", postgresql.JSONB(), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False), schema="app")
    op.create_index("ix_project_product", "project", ["product_ref"], schema="app")
    op.create_table("project_plan",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("project_id", sa.Uuid(), sa.ForeignKey("app.project.id"), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("items", postgresql.JSONB(), nullable=False),
        sa.Column("usage", postgresql.JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("confirmed_at", sa.DateTime(timezone=True)), schema="app")
    op.create_index("ix_project_plan_project", "project_plan", ["project_id", "version"], schema="app")
    op.create_table("workflow_run",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("project_id", sa.Uuid(), sa.ForeignKey("app.project.id"), nullable=False),
        sa.Column("plan_id", sa.Uuid(), sa.ForeignKey("app.project_plan.id"), nullable=False),
        sa.Column("sku_ref", sa.String(128), nullable=False),
        sa.Column("item_id", sa.String(64), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("graph", postgresql.JSONB(), nullable=False),
        sa.Column("cursor", sa.Integer(), nullable=False),
        sa.Column("context", postgresql.JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False), schema="app")
    op.create_index("ix_workflow_run_plan", "workflow_run", ["plan_id"], schema="app")
    op.create_table("workflow_step",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("run_id", sa.Uuid(), sa.ForeignKey("app.workflow_run.id"), nullable=False),
        sa.Column("node_id", sa.String(64), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("result", postgresql.JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True)), schema="app")
    op.create_index("ix_workflow_step_run", "workflow_step", ["run_id"], schema="app")


def downgrade() -> None:
    for table, index in [("workflow_step", "ix_workflow_step_run"), ("workflow_run", "ix_workflow_run_plan"), ("project_plan", "ix_project_plan_project"), ("project", "ix_project_product")]:
        op.drop_index(index, table_name=table, schema="app")
        op.drop_table(table, schema="app")
