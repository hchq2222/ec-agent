"""生图服务 API。

启动：
    uvicorn apps.api.main:app --reload --port 8000

接口：
    GET  /healthz
    GET  /api/models?kind=image      列出可用模型与能力
    POST /api/images/generate        生成图片（成功/失败都落库）
    GET  /api/jobs/{job_id}          查询一次生成及其产物
    GET  /files/...                  产物静态访问
"""

from __future__ import annotations

import asyncio
import logging
import mimetypes
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from apps.core.config import get_settings
from apps.core.db import Database
from apps.core.imageinfo import image_size
from apps.core.models import Asset, Channel, Job, ProviderCall, utcnow
from apps.core.storage import build_object_key, build_storage
from apps.providers.base import ErrorCode, ImageRequest, ProviderError
from apps.providers.channels import ChannelService, dynamic_capabilities, dynamic_spec
from apps.providers.registry import Registry, validate_request

from .link_parser import parse_product_link
from .schemas import (
    ChannelCreateRequest,
    ChannelOut,
    ChannelUpdateRequest,
    GenerateImageRequest,
    GenerateImageResponse,
    GeneratedImageOut,
    JobDetailResponse,
    ParseLinkRequest,
)

log = logging.getLogger("imagegen")

_STATUS_BY_CODE = {
    ErrorCode.AUTH_FAILED: 502,
    ErrorCode.RATE_LIMITED: 429,
    ErrorCode.INVALID_PARAM: 400,
    ErrorCode.CONTENT_BLOCKED: 422,
    ErrorCode.TIMEOUT: 504,
    ErrorCode.PROVIDER_ERROR: 502,
    ErrorCode.MODEL_NOT_FOUND: 404,
    ErrorCode.CAPABILITY_UNSUPPORTED: 400,
    ErrorCode.ARTIFACT_LOST: 502,
}

registry: Registry | None = None
storage: Any | None = None
db: Database | None = None
channel_service: ChannelService | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global registry, storage, db, channel_service
    settings = get_settings()
    logging.basicConfig(level=settings.log_level)
    registry = Registry.from_file(settings.providers_config)
    storage = build_storage(settings)

    if settings.database_url:
        db = Database(settings.database_url)
        log.info("database enabled: %s", settings.database_url.split("@")[-1])
        if settings.channel_secret_key:
            channel_service = ChannelService(db, settings.channel_secret_key)
        else:
            log.warning("未配置 CHANNEL_SECRET_KEY：渠道管理接口不可用")
    else:
        log.warning("DATABASE_URL 未配置：本次运行不落库（仅落盘）")

    log.info("loaded %d models from %s", len(registry.list_models()), settings.providers_config)
    try:
        yield
    finally:
        if registry:
            await registry.aclose()
        if db:
            await db.dispose()


app = FastAPI(title="Dianshang ImageGen", version="0.2.0", lifespan=lifespan)

# 原型是 file:// 或本地端口打开的，开发期放开跨域
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(ProviderError)
async def provider_error_handler(_: Request, exc: ProviderError) -> JSONResponse:
    status = _STATUS_BY_CODE.get(exc.code, 500)
    return JSONResponse(status_code=status, content={"error": exc.to_dict()})


@app.get("/healthz")
async def healthz() -> dict[str, Any]:
    assert registry is not None
    return {
        "ok": True,
        "models": len(registry.list_models()),
        "database": db is not None,
        "storage": storage.backend if storage else None,
    }


def _caps_dict(c: Any) -> dict[str, Any]:
    return {
        "max_n": c.max_n,
        "sizes": list(c.sizes),
        "qualities": list(c.qualities),
        "output_formats": list(c.output_formats),
        "backgrounds": list(c.backgrounds),
        "image_input": c.image_input,
        "mask": c.mask,
        "max_ref_images": c.max_ref_images,
    }


@app.get("/api/models")
async def list_models(
    kind: str | None = None,
    channel_id: str | None = None,
    refresh: bool = False,
) -> dict[str, Any]:
    """内置（YAML）模型 + 数据库渠道模型的合并列表。

    传 channel_id 只看该渠道；该渠道尚未同步过模型时会自动拉一次。
    """
    assert registry is not None
    data: list[dict[str, Any]] = []

    if not channel_id:
        for item in registry.catalog(kind):
            entry = dict(item)
            entry["channel_id"] = item["provider"]
            entry["source"] = "config"
            data.append(entry)

    if channel_service is not None:
        channels = await channel_service.list()
        if channel_id:
            channels = [c for c in channels if str(c.id) == channel_id]
        for ch in channels:
            if not ch.enabled:
                continue
            if (refresh or not ch.models) and ch.enabled:
                try:
                    ch = await channel_service.sync(str(ch.id))
                except ProviderError:
                    pass
            for m in ch.models or []:
                if kind and m.get("kind") != kind:
                    continue
                caps = dynamic_capabilities()
                data.append(
                    {
                        "key": m["id"],
                        "model": m["id"],
                        "provider": str(ch.id),
                        "channel_id": str(ch.id),
                        "channel_name": ch.name,
                        "source": "channel",
                        "kind": m.get("kind", "image"),
                        "display_name": m["id"],
                        "capabilities": _caps_dict(caps),
                        "pricing": {},
                    }
                )

    return {"data": data}


@app.get("/api/channels")
async def list_channels() -> Any:
    if channel_service is None:
        return JSONResponse(
            status_code=503,
            content={"error": {"code": "CHANNEL_UNAVAILABLE", "message": "未配置 CHANNEL_SECRET_KEY 或数据库"}},
        )
    rows = await channel_service.list()
    return {"data": [channel_service.to_dict(c) for c in rows]}


@app.post("/api/channels", status_code=201)
async def create_channel(payload: ChannelCreateRequest) -> Any:
    if channel_service is None:
        return JSONResponse(
            status_code=503,
            content={"error": {"code": "CHANNEL_UNAVAILABLE", "message": "未配置 CHANNEL_SECRET_KEY 或数据库"}},
        )
    ch = await channel_service.create(
        name=payload.name,
        base_url=payload.base_url,
        api_key=payload.api_key,
        type=payload.type,
        enabled=payload.enabled,
    )
    if payload.auto_sync and db is not None:
        try:
            ch = await channel_service.sync(str(ch.id))
        except ProviderError as exc:
            # 创建成功但同步失败：把错误写回，前端能看到「同步失败」而不是"渠道不存在"
            async with db.session() as session:
                row = await session.get(Channel, ch.id)
                if row is not None:
                    row.sync_error = f"{exc.code.value}: {exc.message[:500]}"
                    await session.commit()
            ch = await channel_service.get(str(ch.id))
    return {"data": channel_service.to_dict(ch)}


@app.patch("/api/channels/{channel_id}")
async def update_channel(channel_id: str, payload: ChannelUpdateRequest) -> Any:
    if channel_service is None:
        return JSONResponse(
            status_code=503,
            content={"error": {"code": "CHANNEL_UNAVAILABLE", "message": "未配置 CHANNEL_SECRET_KEY 或数据库"}},
        )
    fields = payload.model_dump(exclude_unset=True, exclude_none=True)
    ch = await channel_service.update(channel_id, **fields)
    return {"data": channel_service.to_dict(ch)}


@app.delete("/api/channels/{channel_id}")
async def delete_channel(channel_id: str) -> Any:
    if channel_service is None:
        return JSONResponse(
            status_code=503,
            content={"error": {"code": "CHANNEL_UNAVAILABLE", "message": "未配置 CHANNEL_SECRET_KEY 或数据库"}},
        )
    await channel_service.delete(channel_id)
    return {"ok": True}


@app.post("/api/channels/{channel_id}/sync")
async def sync_channel(channel_id: str) -> Any:
    if channel_service is None:
        return JSONResponse(
            status_code=503,
            content={"error": {"code": "CHANNEL_UNAVAILABLE", "message": "未配置 CHANNEL_SECRET_KEY 或数据库"}},
        )
    ch = await channel_service.sync(channel_id)
    return {"data": channel_service.to_dict(ch)}


@app.get("/api/jobs/{job_id}", response_model=JobDetailResponse)
async def get_job(job_id: str) -> JobDetailResponse:
    if db is None:
        return JSONResponse(status_code=503, content={"error": "未配置数据库"})  # type: ignore[return-value]

    try:
        jid = uuid.UUID(job_id)
    except ValueError:
        return JSONResponse(status_code=400, content={"error": "job_id 不是合法 UUID"})  # type: ignore[return-value]

    from sqlalchemy import select
    from sqlalchemy.orm import selectinload

    async with db.session() as session:
        row = (
            await session.execute(
                select(Job).options(selectinload(Job.assets)).where(Job.id == jid)
            )
        ).scalar_one_or_none()
        if row is None:
            return JSONResponse(status_code=404, content={"error": "job 不存在"})  # type: ignore[return-value]

        settings = get_settings()
        return JobDetailResponse(
            id=str(row.id),
            status=row.status,
            model=row.model_key,
            provider=row.provider_id,
            n=row.n,
            latency_ms=row.latency_ms,
            error_code=row.error_code,
            error_message=row.error_message,
            created_at=row.created_at,
            finished_at=row.finished_at,
            images=[
                GeneratedImageOut(
                    asset_id=str(a.id),
                    url=_asset_url(a.id, a.object_key),  # type: ignore[union-attr]
                    mime=a.mime,
                    bytes=a.bytes,
                    width=a.width,
                    height=a.height,
                )
                for a in sorted(row.assets, key=lambda x: x.idx)
            ],
        )


def _asset_url(asset_id: Any, key: str) -> str:
    """presign：直连 MinIO（生产推荐）；proxy：经 API 中转（开发时免装私有 CA）。"""
    assert storage is not None
    settings = get_settings()
    if settings.storage_url_mode == "proxy":
        return f"{settings.public_base_url.rstrip('/')}/api/assets/{asset_id}/content"
    return storage.url_for(key)


@app.get("/api/assets/{asset_id}/content")
async def asset_content(asset_id: str) -> Any:
    if db is None or storage is None:
        return JSONResponse(
            status_code=503, content={"error": {"code": "DB_UNAVAILABLE", "message": "未配置数据库或存储"}}
        )
    try:
        aid = uuid.UUID(asset_id)
    except ValueError:
        return JSONResponse(
            status_code=400, content={"error": {"code": "INVALID_PARAM", "message": "asset_id 不是合法 UUID"}}
        )
    async with db.session() as session:
        row = await session.get(Asset, aid)
        if row is None:
            return JSONResponse(
                status_code=404, content={"error": {"code": "NOT_FOUND", "message": "素材不存在"}}
            )
        object_key, mime = row.object_key, row.mime
    try:
        data = await asyncio.to_thread(storage.read, object_key)
    except Exception:
        return JSONResponse(
            status_code=404,
            content={"error": {"code": "ARTIFACT_LOST", "message": "对象存储中找不到该文件"}},
        )
    return Response(
        content=data,
        media_type=mime,
        headers={"Cache-Control": "private, max-age=3600"},
    )


@app.get("/api/jobs")
async def list_jobs(limit: int = 20) -> Any:
    """最近生成记录（工作台左栏用）。"""
    if db is None:
        return JSONResponse(
            status_code=503,
            content={"error": {"code": "DB_UNAVAILABLE", "message": "未配置数据库"}},
        )
    assert storage is not None
    limit = max(1, min(int(limit), 100))

    from sqlalchemy import select
    from sqlalchemy.orm import selectinload

    async with db.session() as session:
        rows = (
            await session.execute(
                select(Job)
                .options(selectinload(Job.assets))
                .order_by(Job.created_at.desc())
                .limit(limit)
            )
        ).scalars().all()

    return {
        "data": [
            {
                "id": str(row.id),
                "status": row.status,
                "model": row.model_key,
                "provider": row.provider_id,
                "prompt": (row.params or {}).get("prompt", ""),
                "n": row.n,
                "latency_ms": row.latency_ms,
                "error_code": row.error_code,
                "error_message": row.error_message,
                "created_at": row.created_at,
                "images": [
                    {
                        "asset_id": str(a.id),
                        "url": _asset_url(a.id, a.object_key),
                        "mime": a.mime,
                        "bytes": a.bytes,
                        "width": a.width,
                        "height": a.height,
                    }
                    for a in sorted(row.assets, key=lambda x: x.idx)
                ],
            }
            for row in rows
        ]
    }


@app.delete("/api/jobs/{job_id}")
async def delete_job(job_id: str) -> Any:
    """删除一次生成记录：DB 行 + 对象存储里的产物。"""
    if db is None or storage is None:
        return JSONResponse(
            status_code=503,
            content={"error": {"code": "DB_UNAVAILABLE", "message": "未配置数据库或存储"}},
        )
    try:
        jid = uuid.UUID(job_id)
    except ValueError:
        return JSONResponse(
            status_code=400, content={"error": {"code": "INVALID_PARAM", "message": "job_id 不是合法 UUID"}}
        )

    from sqlalchemy import select
    from sqlalchemy.orm import selectinload

    async with db.session() as session:
        row = (
            await session.execute(
                select(Job).options(selectinload(Job.assets)).where(Job.id == jid)
            )
        ).scalar_one_or_none()
        if row is None:
            return JSONResponse(
                status_code=404, content={"error": {"code": "NOT_FOUND", "message": "任务不存在"}}
            )
        keys = [a.object_key for a in row.assets]
        await session.delete(row)
        await session.commit()

    for key in keys:
        try:
            await asyncio.to_thread(storage.delete, key)
        except Exception:
            log.warning("删除对象失败（DB 已删）: %s", key)
    return {"ok": True, "deleted_assets": len(keys)}


@app.post("/api/products/parse-link")
async def parse_link(payload: ParseLinkRequest) -> Any:
    """解析商品链接，返回可编辑的字段草稿（不直接入库）。"""
    data = await parse_product_link(payload.url)
    return {"data": data}


@app.post("/api/images/generate", response_model=GenerateImageResponse)
async def generate_image(payload: GenerateImageRequest) -> GenerateImageResponse:
    return await _generate_image(payload)


async def _generate_image(
    payload: GenerateImageRequest, workflow_trace: dict[str, str] | None = None
) -> GenerateImageResponse:
    assert registry is not None and storage is not None
    settings = get_settings()

    # 先解析模型（未知模型 / 渠道不存在直接报错，不产生 job 记录）
    channel = None
    if payload.channel_id:
        if channel_service is None:
            return JSONResponse(
                status_code=503,
                content={
                    "error": {
                        "code": "CHANNEL_UNAVAILABLE",
                        "message": "未配置 CHANNEL_SECRET_KEY 或数据库",
                    }
                },
            )
        channel = await channel_service.get(payload.channel_id)
        if not channel.enabled:
            raise ProviderError(ErrorCode.INVALID_PARAM, "渠道已停用")
        spec = dynamic_spec(channel, payload.model)
    else:
        spec = registry.get_model(payload.model)

    job_id = uuid.uuid4()
    params = {
        "prompt": payload.prompt,
        "n": payload.n,
        "size": payload.size,
        "quality": payload.quality,
        "background": payload.background,
        "output_format": payload.output_format,
        "images_count": len(payload.images),
        "user": payload.user,
    }
    if workflow_trace:
        params["workflow_trace"] = workflow_trace

    # 1) 先记 running，失败也能留下痕迹
    if db is not None:
        async with db.session() as session:
            session.add(
                Job(
                    id=job_id,
                    kind="image",
                    model_key=spec.key,
                    provider_id=spec.provider_id,
                    status="running",
                    n=payload.n,
                    params=params,
                )
            )
            await session.commit()

    req = ImageRequest(
        prompt=payload.prompt,
        model_key=payload.model,
        n=payload.n,
        size=payload.size,
        quality=payload.quality,
        background=payload.background,
        output_format=payload.output_format,
        images=payload.images,
        mask=payload.mask,
        user=payload.user,
        extra=payload.extra,
    )

    try:
        if channel is not None:
            provider = channel_service.provider(channel)  # type: ignore[union-attr]
            try:
                validate_request(req, spec)
                result = await provider.generate(req, spec)
            finally:
                await provider.aclose()
        else:
            result = await registry.generate(req)
    except ProviderError as exc:
        if db is not None:
            await _mark_failed(job_id, spec, exc)
        raise

    # 2) 写对象存储 + 记录产物
    images: list[GeneratedImageOut] = []
    stored_rows: list[Asset] = []
    for idx, img in enumerate(result.images):
        ext = img.mime.split("/")[-1].replace("jpeg", "jpg")
        asset_id = uuid.uuid4()
        key = build_object_key(asset_id=str(asset_id), ext=ext)
        stored = await asyncio.to_thread(storage.put, key, img.data, img.mime)
        width, height = image_size(img.data)
        stored_rows.append(
            Asset(
                id=asset_id,
                tenant_id="default",
                job_id=job_id,
                idx=idx,
                type="image",
                object_key=stored.key,
                mime=img.mime,
                bytes=stored.bytes,
                width=width,
                height=height,
            )
        )
        images.append(
            GeneratedImageOut(
                asset_id=str(asset_id),
                url=_asset_url(asset_id, stored.key),
                mime=img.mime,
                bytes=stored.bytes,
                width=width,
                height=height,
            )
        )

    persisted = False
    if db is not None:
        persisted = await _mark_succeeded(job_id, spec, result, stored_rows)

    return GenerateImageResponse(
        id=str(job_id),
        model=result.model_key,
        provider=result.provider_id,
        images=images,
        latency_ms=result.latency_ms,
        usage=result.usage,
        persisted=persisted,
    )


async def _mark_succeeded(
    job_id: uuid.UUID, spec: Any, result: Any, assets: list[Asset]
) -> bool:
    assert db is not None
    try:
        async with db.session() as session:
            job = await session.get(Job, job_id)
            if job is None:
                return False
            job.status = "succeeded"
            job.latency_ms = result.latency_ms
            job.finished_at = utcnow()
            session.add_all(assets)

            price = spec.pricing.get("price")
            units = len(result.images)
            session.add(
                ProviderCall(
                    job_id=job_id,
                    provider_id=result.provider_id,
                    model_key=result.model_key,
                    upstream_model=spec.model,
                    unit=spec.pricing.get("unit", "image"),
                    units=units,
                    unit_price=price,
                    amount=(price * units) if isinstance(price, (int, float)) else None,
                    currency=spec.pricing.get("currency", "USD"),
                    status="succeeded",
                    usage=result.usage or {},
                )
            )
            await session.commit()
        return True
    except Exception:
        log.exception("job %s 落库失败（产物已保存）", job_id)
        return False


async def _mark_failed(job_id: uuid.UUID, spec: Any, exc: ProviderError) -> None:
    assert db is not None
    try:
        async with db.session() as session:
            job = await session.get(Job, job_id)
            if job is not None:
                job.status = "failed"
                job.error_code = exc.code.value
                job.error_message = exc.message[:2000]
                job.finished_at = utcnow()
            session.add(
                ProviderCall(
                    job_id=job_id,
                    provider_id=spec.provider_id,
                    model_key=spec.key,
                    upstream_model=spec.model,
                    unit=spec.pricing.get("unit", "image"),
                    units=0,
                    unit_price=None,
                    amount=0,
                    currency=spec.pricing.get("currency", "USD"),
                    status="failed",
                    usage={},
                )
            )
            await session.commit()
    except Exception:
        log.exception("job %s 失败记录落库失败", job_id)


class ProjectStaticFiles(StaticFiles):
    async def get_response(self, path: str, scope: dict[str, Any]) -> Response:
        response = await super().get_response(path, scope)
        response.headers["Cache-Control"] = "no-store"
        return response


def mount_static() -> None:
    # Windows MIME registry can label .js as text/plain; browsers reject module scripts.
    mimetypes.add_type("text/javascript", ".js", strict=True)
    mimetypes.add_type("text/css", ".css", strict=True)
    settings = get_settings()
    settings.output_dir.mkdir(parents=True, exist_ok=True)
    app.mount("/files", StaticFiles(directory=str(settings.output_dir)), name="files")
    project_ui = Path(__file__).resolve().parent / "static" / "project-ui"
    app.mount("/project-ui", ProjectStaticFiles(directory=str(project_ui), html=True, check_dir=False), name="project-ui")


mount_static()

from .projects import router as projects_router  # noqa: E402
app.include_router(projects_router)
from .products import router as products_router  # noqa: E402
app.include_router(products_router)
from .templates import router as templates_router  # noqa: E402
app.include_router(templates_router)
from .trash import router as trash_router  # noqa: E402
app.include_router(trash_router)
