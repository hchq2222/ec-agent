"""init: job / asset / provider_call

Revision ID: 0001_init
Revises:
Create Date: 2026-10-23
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0001_init"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE SCHEMA IF NOT EXISTS app")

    op.create_table(
        "job",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.String(64), nullable=False, server_default=sa.text("'default'")),
        sa.Column("kind", sa.String(16), nullable=False, server_default=sa.text("'image'")),
        sa.Column("model_key", sa.String(64), nullable=False),
        sa.Column("provider_id", sa.String(64), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default=sa.text("'running'")),
        sa.Column("n", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("params", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("error_code", sa.String(32), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        schema="app",
    )
    op.create_index("ix_job_created_at", "job", ["created_at"], schema="app")
    op.create_index("ix_job_model_status", "job", ["model_key", "status"], schema="app")

    op.create_table(
        "asset",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.String(64), nullable=False, server_default=sa.text("'default'")),
        sa.Column(
            "job_id",
            sa.Uuid(),
            sa.ForeignKey("app.job.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("idx", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("type", sa.String(16), nullable=False, server_default=sa.text("'image'")),
        sa.Column("object_key", sa.String(512), nullable=False),
        sa.Column("mime", sa.String(64), nullable=False),
        sa.Column("bytes", sa.Integer(), nullable=False),
        sa.Column("width", sa.Integer(), nullable=True),
        sa.Column("height", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        schema="app",
    )
    op.create_index("ix_asset_job_id", "asset", ["job_id"], schema="app")
    op.create_index("ix_asset_created_at", "asset", ["created_at"], schema="app")

    op.create_table(
        "provider_call",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "job_id",
            sa.Uuid(),
            sa.ForeignKey("app.job.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("provider_id", sa.String(64), nullable=False),
        sa.Column("model_key", sa.String(64), nullable=False),
        sa.Column("upstream_model", sa.String(128), nullable=False),
        sa.Column("unit", sa.String(16), nullable=False, server_default=sa.text("'image'")),
        sa.Column("units", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("unit_price", sa.Numeric(12, 6), nullable=True),
        sa.Column("amount", sa.Numeric(12, 6), nullable=True),
        sa.Column("currency", sa.String(8), nullable=False, server_default=sa.text("'USD'")),
        sa.Column("status", sa.String(16), nullable=False, server_default=sa.text("'succeeded'")),
        sa.Column("usage", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        schema="app",
    )
    op.create_index("ix_provider_call_job_id", "provider_call", ["job_id"], schema="app")
    op.create_index("ix_provider_call_created_at", "provider_call", ["created_at"], schema="app")


def downgrade() -> None:
    op.drop_index("ix_provider_call_created_at", table_name="provider_call", schema="app")
    op.drop_index("ix_provider_call_job_id", table_name="provider_call", schema="app")
    op.drop_table("provider_call", schema="app")

    op.drop_index("ix_asset_created_at", table_name="asset", schema="app")
    op.drop_index("ix_asset_job_id", table_name="asset", schema="app")
    op.drop_table("asset", schema="app")

    op.drop_index("ix_job_model_status", table_name="job", schema="app")
    op.drop_index("ix_job_created_at", table_name="job", schema="app")
    op.drop_table("job", schema="app")
