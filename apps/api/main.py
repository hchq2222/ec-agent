"""生图服务 API。

启动：
    uvicorn apps.api.main:app --reload --port 8000

接口：
    GET  /healthz
    GET  /api/models?kind=image      列出可用模型与能力
    POST /api/images/generate        生成图片
    GET  /files/...                  产物静态访问
"""

from __future__ import annotations

import logging
import secrets
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from apps.core.config import get_settings
from apps.core.storage import LocalStorage
from apps.providers.base import ErrorCode, ImageRequest, ProviderError
from apps.providers.registry import Registry

from .schemas import GenerateImageRequest, GenerateImageResponse, GeneratedImageOut

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
storage: LocalStorage | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global registry, storage
    settings = get_settings()
    logging.basicConfig(level=settings.log_level)
    registry = Registry.from_file(settings.providers_config)
    storage = LocalStorage(settings.output_dir)
    settings.output_dir.mkdir(parents=True, exist_ok=True)
    log.info(
        "loaded %d models from %s", len(registry.list_models()), settings.providers_config
    )
    try:
        yield
    finally:
        if registry:
            await registry.aclose()


app = FastAPI(title="Dianshang ImageGen", version="0.1.0", lifespan=lifespan)

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
    return {"ok": True, "models": len(registry.list_models())}


@app.get("/api/models")
async def list_models(kind: str | None = None) -> dict[str, Any]:
    assert registry is not None
    return {"data": registry.catalog(kind)}


@app.post("/api/images/generate", response_model=GenerateImageResponse)
async def generate_image(payload: GenerateImageRequest) -> GenerateImageResponse:
    assert registry is not None and storage is not None
    settings = get_settings()

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
    result = await registry.generate(req)

    job_id = f"img_{secrets.token_hex(6)}"
    images: list[GeneratedImageOut] = []
    for idx, img in enumerate(result.images):
        ext = img.mime.split("/")[-1].replace("jpeg", "jpg")
        stored = storage.save(img.data, ext=ext, prefix=f"{job_id}_{idx}")
        images.append(
            GeneratedImageOut(
                url=storage.public_url(settings.public_base_url, stored.key),
                mime=img.mime,
                bytes=stored.bytes,
            )
        )

    return GenerateImageResponse(
        id=job_id,
        model=result.model_key,
        provider=result.provider_id,
        images=images,
        latency_ms=result.latency_ms,
        usage=result.usage,
    )


def mount_static() -> None:
    settings = get_settings()
    settings.output_dir.mkdir(parents=True, exist_ok=True)
    app.mount("/files", StaticFiles(directory=str(settings.output_dir)), name="files")


mount_static()
