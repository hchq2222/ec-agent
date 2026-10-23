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
import uuid
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from apps.core.config import get_settings
from apps.core.db import Database
from apps.core.imageinfo import image_size
from apps.core.models import Asset, Job, ProviderCall, utcnow
from apps.core.storage import build_object_key, build_storage
from apps.providers.base import ErrorCode, ImageRequest, ProviderError
from apps.providers.registry import Registry

from .schemas import (
    GenerateImageRequest,
    GenerateImageResponse,
    GeneratedImageOut,
    JobDetailResponse,
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


@asynccontextmanager
async def lifespan(app: FastAPI):
    global registry, storage, db
    settings = get_settings()
    logging.basicConfig(level=settings.log_level)
    registry = Registry.from_file(settings.providers_config)
    storage = build_storage(settings)

    if settings.database_url:
        db = Database(settings.database_url)
        log.info("database enabled: %s", settings.database_url.split("@")[-1])
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


@app.get("/api/models")
async def list_models(kind: str | None = None) -> dict[str, Any]:
    assert registry is not None
    return {"data": registry.catalog(kind)}


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
                    url=storage.url_for(a.object_key),  # type: ignore[union-attr]
                    mime=a.mime,
                    bytes=a.bytes,
                    width=a.width,
                    height=a.height,
                )
                for a in sorted(row.assets, key=lambda x: x.idx)
            ],
        )


@app.post("/api/images/generate", response_model=GenerateImageResponse)
async def generate_image(payload: GenerateImageRequest) -> GenerateImageResponse:
    assert registry is not None and storage is not None
    settings = get_settings()

    # 先解析模型（未知模型直接 404，不产生 job 记录）
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
                url=storage.url_for(stored.key),
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


def mount_static() -> None:
    settings = get_settings()
    settings.output_dir.mkdir(parents=True, exist_ok=True)
    app.mount("/files", StaticFiles(directory=str(settings.output_dir)), name="files")


mount_static()
