"""数据模型（MVP 最小集）。

只落三张表，够回答"这次生成了什么、花了多少、文件在哪"：
    job            一次生成请求（成功/失败都记录）
    asset          生成的图片（一张一行）
    provider_call  渠道调用流水（计费与排查用）

与 TD-001 §3.3 的长期模型对齐：后续补 product/sku/job_item/asset_version 等，
本表结构已预留 tenant_id，不需要重建。
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import DateTime, ForeignKey, Index, Integer, JSON, Numeric, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import Uuid

from .db import Base

JSONType = JSONB().with_variant(JSON, "sqlite")


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Job(Base):
    """一次生图请求。失败也落库，方便统计失败率与错误分布。"""

    __tablename__ = "job"
    __table_args__ = (
        Index("ix_job_created_at", "created_at"),
        Index("ix_job_model_status", "model_key", "status"),
        {"schema": "app"},
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[str] = mapped_column(String(64), default="default", nullable=False)
    kind: Mapped[str] = mapped_column(String(16), default="image", nullable=False)
    model_key: Mapped[str] = mapped_column(String(64), nullable=False)
    provider_id: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="running", nullable=False)
    n: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    params: Mapped[dict] = mapped_column(JSONType, default=dict, nullable=False)
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(32), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    assets: Mapped[list["Asset"]] = relationship(back_populates="job", cascade="all, delete-orphan")


class Asset(Base):
    """生成出来的图片。object_key 是存储里的相对路径，不存会过期的 URL。"""

    __tablename__ = "asset"
    __table_args__ = (
        Index("ix_asset_job_id", "job_id"),
        Index("ix_asset_created_at", "created_at"),
        {"schema": "app"},
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[str] = mapped_column(String(64), default="default", nullable=False)
    job_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("app.job.id", ondelete="CASCADE"), nullable=False
    )
    idx: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    type: Mapped[str] = mapped_column(String(16), default="image", nullable=False)
    object_key: Mapped[str] = mapped_column(String(512), nullable=False)
    mime: Mapped[str] = mapped_column(String(64), nullable=False)
    bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    width: Mapped[int | None] = mapped_column(Integer, nullable=True)
    height: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)

    job: Mapped[Job] = relationship(back_populates="assets")


class ProviderCall(Base):
    """每次渠道调用一行。金额与单价快照分开存，价格变动后历史成本仍可复现。"""

    __tablename__ = "provider_call"
    __table_args__ = (
        Index("ix_provider_call_job_id", "job_id"),
        Index("ix_provider_call_created_at", "created_at"),
        {"schema": "app"},
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    job_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("app.job.id", ondelete="CASCADE"), nullable=False
    )
    provider_id: Mapped[str] = mapped_column(String(64), nullable=False)
    model_key: Mapped[str] = mapped_column(String(64), nullable=False)
    upstream_model: Mapped[str] = mapped_column(String(128), nullable=False)
    unit: Mapped[str] = mapped_column(String(16), default="image", nullable=False)
    units: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    unit_price: Mapped[float | None] = mapped_column(Numeric(12, 6), nullable=True)
    amount: Mapped[float | None] = mapped_column(Numeric(12, 6), nullable=True)
    currency: Mapped[str] = mapped_column(String(8), default="USD", nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="succeeded", nullable=False)
    usage: Mapped[dict] = mapped_column(JSONType, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
