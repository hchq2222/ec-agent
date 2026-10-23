"""API 请求/响应模型。"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class GenerateImageRequest(BaseModel):
    model: str = Field(..., description="模型 key，见 GET /api/models")
    channel_id: str | None = Field(None, description="渠道 id；不传则用 YAML 配置里的内置渠道")
    prompt: str
    n: int = 1
    size: str | None = None
    quality: str | None = None
    background: str | None = None
    output_format: str | None = None
    images: list[str] = Field(
        default_factory=list,
        description="参考图：URL / data URL / 裸 base64，用于图生图",
    )
    mask: str | None = None
    user: str | None = None
    extra: dict[str, Any] = Field(default_factory=dict)


class GeneratedImageOut(BaseModel):
    asset_id: str | None = None
    url: str
    mime: str
    bytes: int
    width: int | None = None
    height: int | None = None


class GenerateImageResponse(BaseModel):
    id: str
    model: str
    provider: str
    images: list[GeneratedImageOut]
    latency_ms: int
    usage: dict[str, Any] = Field(default_factory=dict)
    persisted: bool = Field(True, description="是否已写入数据库；false 表示只落盘")


class JobDetailResponse(BaseModel):
    id: str
    status: str
    model: str
    provider: str
    n: int
    latency_ms: int | None = None
    error_code: str | None = None
    error_message: str | None = None
    created_at: datetime
    finished_at: datetime | None = None
    images: list[GeneratedImageOut] = Field(default_factory=list)


class ChannelCreateRequest(BaseModel):
    name: str
    base_url: str
    api_key: str
    type: str = "openai_compat"
    enabled: bool = True
    auto_sync: bool = True


class ChannelUpdateRequest(BaseModel):
    name: str | None = None
    base_url: str | None = None
    api_key: str | None = None
    type: str | None = None
    enabled: bool | None = None


class ChannelOut(BaseModel):
    id: str
    name: str
    type: str
    base_url: str
    api_key_masked: str
    enabled: bool
    models: list[dict[str, Any]] = Field(default_factory=list)
    last_synced_at: datetime | None = None
    sync_error: str | None = None
    created_at: datetime

class ParseLinkRequest(BaseModel):
    url: str