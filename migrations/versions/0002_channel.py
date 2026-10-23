"""channel: 渠道与模型缓存

Revision ID: 0002_channel
Revises: 0001_init
Create Date: 2026-10-23
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0002_channel"
down_revision = "0001_init"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "channel",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.String(64), nullable=False, server_default=sa.text("'default'")),
        sa.Column("name", sa.String(64), nullable=False),
        sa.Column("type", sa.String(32), nullable=False, server_default=sa.text("'openai_compat'")),
        sa.Column("base_url", sa.String(256), nullable=False),
        sa.Column("api_key_enc", sa.Text(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("models", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("last_synced_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("sync_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        schema="app",
    )
    op.create_index("ix_channel_enabled", "channel", ["enabled"], schema="app")
    op.create_index("ix_channel_created_at", "channel", ["created_at"], schema="app")


def downgrade() -> None:
    op.drop_index("ix_channel_created_at", table_name="channel", schema="app")
    op.drop_index("ix_channel_enabled", table_name="channel", schema="app")
    op.drop_table("channel", schema="app")
