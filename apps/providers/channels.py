"""渠道管理：DB CRUD、密钥加密、模型同步、动态模型规格。

渠道模型是"运行时事实"：每次同步从渠道的 /models 拉取并缓存，生成时按 channel_id 动态构造 provider。
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

import httpx
from sqlalchemy import select

from apps.core.crypto import decrypt_secret, encrypt_secret, mask_secret
from apps.core.db import Database
from apps.core.models import Channel

from .base import (
    Capabilities,
    ErrorCode,
    ModelSpec,
    ProviderError,
)
from .openai_compat import OpenAICompatProvider

VIDEO_HINTS = (
    "seedance",
    "veo",
    "sora",
    "kling",
    "runway",
    "hailuo",
    "pika",
    "luma",
    "i2v",
    "t2v",
    "video",
)
IMAGE_HINTS = (
    "image",
    "imagen",
    "dall-e",
    "dalle",
    "flux",
    "seedream",
    "nano-banana",
    "midjourney",
    "sdxl",
    "stable-diffusion",
    "sd-",
    "qwen-image",
)


def guess_kind(model_id: str) -> str:
    """渠道 /models 只给 id，按名称猜类型。猜不出时按 image 处理（当前主链路是生图）。"""
    m = (model_id or "").lower()
    if any(h in m for h in VIDEO_HINTS):
        return "video"
    if any(h in m for h in IMAGE_HINTS):
        return "image"
    return "image"


def normalize_base_url(url: str) -> str:
    url = (url or "").strip().rstrip("/")
    if not url.startswith(("http://", "https://")):
        raise ProviderError(ErrorCode.INVALID_PARAM, "Base URL 需以 http:// 或 https:// 开头")
    return url


def dynamic_capabilities() -> Capabilities:
    """动态渠道拿不到能力声明，给一组保守上限；真实限制由上游校验兜底。"""
    return Capabilities(
        max_n=4,
        sizes=("auto", "1024x1024", "1536x1024", "1024x1536"),
        qualities=("auto", "low", "medium", "high"),
        output_formats=("png", "jpeg", "webp"),
        backgrounds=("auto", "opaque", "transparent"),
        image_input=True,
        mask=True,
        max_ref_images=8,
    )


def dynamic_spec(channel: Channel, model_id: str) -> ModelSpec:
    return ModelSpec(
        key=model_id,
        model=model_id,
        provider_id=str(channel.id),
        kind=guess_kind(model_id),
        display_name=model_id,
        capabilities=dynamic_capabilities(),
        pricing={},
    )


class ChannelService:
    def __init__(self, db: Database, secret: str, *, timeout_s: float = 30.0) -> None:
        self.db = db
        self.secret = secret
        self.timeout_s = timeout_s

    # ---------- 查询 ----------

    async def list(self) -> list[Channel]:
        async with self.db.session() as session:
            rows = (
                await session.execute(select(Channel).order_by(Channel.created_at))
            ).scalars().all()
            return list(rows)

    async def get(self, channel_id: str) -> Channel:
        try:
            cid = uuid.UUID(str(channel_id))
        except ValueError as exc:
            raise ProviderError(ErrorCode.INVALID_PARAM, f"channel_id 不是合法 UUID: {channel_id}") from exc
        async with self.db.session() as session:
            row = await session.get(Channel, cid)
            if row is None:
                raise ProviderError(ErrorCode.INVALID_PARAM, "渠道不存在")
            return row

    # ---------- 写 ----------

    async def create(
        self,
        *,
        name: str,
        base_url: str,
        api_key: str,
        type: str = "openai_compat",
        enabled: bool = True,
    ) -> Channel:
        name = (name or "").strip()
        if not name:
            raise ProviderError(ErrorCode.INVALID_PARAM, "渠道名称不能为空")
        if not api_key.strip():
            raise ProviderError(ErrorCode.INVALID_PARAM, "API Key 不能为空")
        base = normalize_base_url(base_url)
        channel = Channel(
            tenant_id="default",
            name=name[:64],
            type=(type or "openai_compat")[:32],
            base_url=base,
            api_key_enc=encrypt_secret(self.secret, api_key.strip()),
            enabled=enabled,
            models=[],
        )
        async with self.db.session() as session:
            session.add(channel)
            await session.commit()
            await session.refresh(channel)
        return channel

    async def update(self, channel_id: str, **fields: Any) -> Channel:
        channel = await self.get(channel_id)
        if "name" in fields and fields["name"]:
            channel.name = str(fields["name"]).strip()[:64]
        if "base_url" in fields and fields["base_url"]:
            channel.base_url = normalize_base_url(fields["base_url"])
        if "api_key" in fields and fields["api_key"]:
            channel.api_key_enc = encrypt_secret(self.secret, str(fields["api_key"]).strip())
        if "enabled" in fields and fields["enabled"] is not None:
            channel.enabled = bool(fields["enabled"])
        if "type" in fields and fields["type"]:
            channel.type = str(fields["type"])[:32]
        async with self.db.session() as session:
            session.add(channel)
            await session.commit()
            await session.refresh(channel)
        return channel

    async def delete(self, channel_id: str) -> None:
        channel = await self.get(channel_id)
        async with self.db.session() as session:
            await session.delete(channel)
            await session.commit()

    # ---------- 同步模型 ----------

    async def sync(self, channel_id: str) -> Channel:
        channel = await self.get(channel_id)
        key = self.decrypt(channel)
        url = f"{channel.base_url}/models"
        try:
            async with httpx.AsyncClient(timeout=self.timeout_s) as client:
                resp = await client.get(url, headers={"Authorization": f"Bearer {key}"})
        except httpx.TimeoutException as exc:
            raise ProviderError(ErrorCode.TIMEOUT, f"拉取模型超时: {url}", retryable=True) from exc
        except httpx.HTTPError as exc:
            raise ProviderError(ErrorCode.PROVIDER_ERROR, f"拉取模型失败: {exc}", retryable=True) from exc

        if resp.status_code in (401, 403):
            raise ProviderError(ErrorCode.AUTH_FAILED, f"渠道鉴权失败（HTTP {resp.status_code}）", status=resp.status_code)
        if resp.status_code >= 400:
            raise ProviderError(
                ErrorCode.PROVIDER_ERROR,
                f"拉取模型失败 HTTP {resp.status_code}: {resp.text[:200]}",
                status=resp.status_code,
            )

        try:
            body = resp.json()
        except Exception as exc:
            raise ProviderError(ErrorCode.PROVIDER_ERROR, "渠道 /models 返回的不是 JSON") from exc

        items = body.get("data") if isinstance(body, dict) else None
        if not isinstance(items, list):
            raise ProviderError(ErrorCode.PROVIDER_ERROR, "渠道 /models 返回结构异常")

        models: list[dict[str, str]] = []
        seen: set[str] = set()
        for item in items:
            mid = item.get("id") if isinstance(item, dict) else str(item)
            if not mid or mid in seen:
                continue
            seen.add(mid)
            models.append({"id": mid, "kind": guess_kind(mid)})

        channel.models = models
        channel.last_synced_at = datetime.now(timezone.utc)
        channel.sync_error = None
        async with self.db.session() as session:
            session.add(channel)
            await session.commit()
            await session.refresh(channel)
        return channel

    # ---------- 辅助 ----------

    def decrypt(self, channel: Channel) -> str:
        return decrypt_secret(self.secret, channel.api_key_enc)

    def to_dict(self, channel: Channel) -> dict[str, Any]:
        try:
            masked = mask_secret(self.decrypt(channel))
        except Exception:
            masked = "••••••••"
        return {
            "id": str(channel.id),
            "name": channel.name,
            "type": channel.type,
            "base_url": channel.base_url,
            "api_key_masked": masked,
            "enabled": channel.enabled,
            "models": channel.models or [],
            "last_synced_at": channel.last_synced_at,
            "sync_error": channel.sync_error,
            "created_at": channel.created_at,
        }

    def provider(self, channel: Channel) -> OpenAICompatProvider:
        return OpenAICompatProvider(
            provider_id=str(channel.id),
            base_url=channel.base_url,
            api_key_env="",
            timeout_s=180,
            api_key=self.decrypt(channel),
        )
