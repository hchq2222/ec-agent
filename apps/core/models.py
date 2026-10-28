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

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Index, Integer, JSON, Numeric, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import Uuid

from .db import Base

JSONType = JSONB().with_variant(JSON, "sqlite")


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Channel(Base):
    """生图/生视频渠道。API Key 加密存储，接口只回显后 4 位。"""

    __tablename__ = "channel"
    __table_args__ = (
        Index("ix_channel_enabled", "enabled"),
        Index("ix_channel_created_at", "created_at"),
        {"schema": "app"},
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[str] = mapped_column(String(64), default="default", nullable=False)
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    type: Mapped[str] = mapped_column(String(32), default="openai_compat", nullable=False)
    base_url: Mapped[str] = mapped_column(String(256), nullable=False)
    api_key_enc: Mapped[str] = mapped_column(Text, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    # [{"id": "gpt-image-2", "kind": "image"}]
    models: Mapped[list] = mapped_column(JSONType, default=list, nullable=False)
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    sync_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )


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


class Project(Base):
    """One product and its selected SKUs. Snapshot until product tables are live."""

    __tablename__ = "project"
    __table_args__ = (Index("ix_project_product", "product_ref"), {"schema": "app"})

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[str] = mapped_column(String(64), default="default", nullable=False)
    product_ref: Mapped[str] = mapped_column(String(128), nullable=False)
    name: Mapped[str] = mapped_column(String(256), nullable=False)
    product_snapshot: Mapped[dict] = mapped_column(JSONType, nullable=False)
    status: Mapped[str] = mapped_column(String(24), default="draft", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ProjectPlan(Base):
    __tablename__ = "project_plan"
    __table_args__ = (Index("ix_project_plan_project", "project_id", "version"), {"schema": "app"})

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("app.project.id"), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(24), default="draft", nullable=False)
    items: Mapped[list] = mapped_column(JSONType, nullable=False)
    usage: Mapped[dict] = mapped_column(JSONType, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class WorkflowRun(Base):
    __tablename__ = "workflow_run"
    __table_args__ = (Index("ix_workflow_run_plan", "plan_id"), {"schema": "app"})

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("app.project.id"), nullable=False)
    plan_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("app.project_plan.id"), nullable=False)
    sku_ref: Mapped[str] = mapped_column(String(128), nullable=False)
    item_id: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(24), default="pending", nullable=False)
    graph: Mapped[dict] = mapped_column(JSONType, nullable=False)
    cursor: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    context: Mapped[dict] = mapped_column(JSONType, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)


class WorkflowStep(Base):
    __tablename__ = "workflow_step"
    __table_args__ = (Index("ix_workflow_step_run", "run_id"), {"schema": "app"})

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    run_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("app.workflow_run.id"), nullable=False)
    node_id: Mapped[str] = mapped_column(String(64), nullable=False)
    attempt: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    result: Mapped[dict] = mapped_column(JSONType, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Product(Base):
    """AI 对话录入产生的商品主档。草稿可反复修改，确认后才进入制作。"""

    __tablename__ = "product"
    __table_args__ = (
        Index("ix_product_status", "status"),
        Index("ix_product_created_at", "created_at"),
        {"schema": "app"},
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[str] = mapped_column(String(64), default="default", nullable=False)
    name: Mapped[str] = mapped_column(String(256), nullable=False)
    category: Mapped[str] = mapped_column(String(32), default="beauty", nullable=False)
    brand: Mapped[str] = mapped_column(String(128), default="", nullable=False)
    code: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    pitch: Mapped[str] = mapped_column(Text, default="", nullable=False)
    status: Mapped[str] = mapped_column(String(24), default="draft", nullable=False)
    source: Mapped[str] = mapped_column(String(24), default="ai_intake", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ProductAttr(Base):
    """商品事实：source=ai 表示待确认，human 表示人工确认过。"""

    __tablename__ = "product_attr"
    __table_args__ = (Index("ix_product_attr_product", "product_id"), {"schema": "app"})

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    product_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("app.product.id", ondelete="CASCADE"), nullable=False
    )
    key: Mapped[str] = mapped_column(String(80), nullable=False)
    value: Mapped[str] = mapped_column(Text, default="", nullable=False)
    unit: Mapped[str] = mapped_column(String(32), default="", nullable=False)
    source: Mapped[str] = mapped_column(String(16), default="ai", nullable=False)
    confidence: Mapped[float | None] = mapped_column(Numeric(5, 4), nullable=True)
    confirmed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)


class ProductSKU(Base):
    __tablename__ = "sku"
    __table_args__ = (Index("ix_sku_product", "product_id"), {"schema": "app"})

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    product_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("app.product.id", ondelete="CASCADE"), nullable=False
    )
    color: Mapped[str] = mapped_column(String(80), default="默认", nullable=False)
    spec: Mapped[str] = mapped_column(String(80), default="默认", nullable=False)
    price_cents: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="active", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class ProductImage(Base):
    __tablename__ = "product_image"
    __table_args__ = (Index("ix_product_image_product", "product_id"), {"schema": "app"})

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    product_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("app.product.id", ondelete="CASCADE"), nullable=False
    )
    object_key: Mapped[str] = mapped_column(String(512), nullable=False)
    mime: Mapped[str] = mapped_column(String(128), default="application/octet-stream", nullable=False)
    bytes: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    kind: Mapped[str] = mapped_column(String(24), default="source", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class ProductMessage(Base):
    __tablename__ = "product_intake_message"
    __table_args__ = (Index("ix_product_message_product", "product_id", "created_at"), {"schema": "app"})

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    product_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("app.product.id", ondelete="CASCADE"), nullable=False
    )
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    content: Mapped[str] = mapped_column(Text, default="", nullable=False)
    attachments: Mapped[list] = mapped_column(JSONType, default=list, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class LayoutTemplate(Base):
    """版式模板：尺寸、主体占比、阴影、文字槽位、Logo 位。

    节点创建时把字段快照进节点参数，运行时不再读这张表；改模板只影响新项目。
    """

    __tablename__ = "layout_template"
    __table_args__ = (
        Index("ix_layout_template_category", "category"),
        Index("ux_layout_template_builtin_key", "builtin_key", unique=True),
        Index("ix_layout_template_deleted_at", "deleted_at"),
        {"schema": "app"},
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[str] = mapped_column(String(64), default="default", nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    category: Mapped[str] = mapped_column(String(32), default="all", nullable=False)
    size_w: Mapped[int] = mapped_column(Integer, default=800, nullable=False)
    size_h: Mapped[int] = mapped_column(Integer, default=800, nullable=False)
    subject_ratio: Mapped[float] = mapped_column(Float, default=0.85, nullable=False)
    shadow: Mapped[str] = mapped_column(String(16), default="soft", nullable=False)
    background: Mapped[str] = mapped_column(String(32), default="white", nullable=False)
    text_slots: Mapped[list] = mapped_column(JSONType, default=list, nullable=False)
    logo_slot: Mapped[dict] = mapped_column(JSONType, default=dict, nullable=False)
    builtin: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    builtin_key: Mapped[str | None] = mapped_column(String(64), nullable=True)
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)
