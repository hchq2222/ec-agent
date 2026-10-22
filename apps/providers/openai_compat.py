"""OpenAI 兼容协议适配器。

覆盖 omyai / one-api / new-api 这类中转站，以及 OpenAI 官方同形态接口：
    POST {base_url}/images/generations   文生图
    POST {base_url}/images/edits         图生图（multipart）
"""

from __future__ import annotations

import base64
import binascii
import os
import time
from typing import Any

import httpx

from .base import (
    ErrorCode,
    GeneratedImage,
    ImageRequest,
    ImageResult,
    ModelSpec,
    ProviderError,
)

_EXT_BY_MIME = {
    "image/png": "png",
    "image/jpeg": "jpg",
    "image/webp": "webp",
    "image/gif": "gif",
}


def _sniff_mime(data: bytes, fallback: str = "image/png") -> str:
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    return fallback


class OpenAICompatProvider:
    """一个 provider 实例对应 config/providers.yaml 里的一个渠道。"""

    type = "openai_compat"

    def __init__(
        self,
        *,
        provider_id: str,
        base_url: str,
        api_key_env: str,
        timeout_s: float = 180.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.id = provider_id
        self.base_url = base_url.rstrip("/")
        self.api_key_env = api_key_env
        self.timeout_s = timeout_s
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(timeout=httpx.Timeout(timeout_s))

    # ---------- 对外 ----------

    async def generate(self, req: ImageRequest, spec: ModelSpec) -> ImageResult:
        started = time.perf_counter()
        if req.images:
            payload_data, files = await self._build_edits(req, spec)
            resp = await self._post("/images/edits", data=payload_data, files=files)
        else:
            payload = self._build_generations(req, spec)
            resp = await self._post("/images/generations", json=payload)

        body = self._parse_json(resp)
        images = await self._extract_images(body, req)
        if not images:
            raise ProviderError(
                ErrorCode.PROVIDER_ERROR,
                "渠道返回成功但响应中没有图片",
                status=resp.status_code,
                raw=body,
            )

        return ImageResult(
            provider_id=self.id,
            model_key=spec.key,
            images=images,
            latency_ms=int((time.perf_counter() - started) * 1000),
            usage=body.get("usage") or {},
            raw={k: v for k, v in body.items() if k not in {"data"}},
        )

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    # ---------- 请求构造 ----------

    def _build_generations(self, req: ImageRequest, spec: ModelSpec) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": spec.model,
            "prompt": req.prompt,
            "n": req.n,
        }
        caps = spec.capabilities
        if req.size:
            payload["size"] = req.size
        if req.quality and req.quality in caps.qualities:
            payload["quality"] = req.quality
        if req.background and req.background in caps.backgrounds:
            payload["background"] = req.background
        if req.output_format and req.output_format in caps.output_formats:
            payload["output_format"] = req.output_format
        if req.user:
            payload["user"] = req.user
        for k, v in (req.extra or {}).items():
            payload.setdefault(k, v)
        return payload

    async def _build_edits(
        self, req: ImageRequest, spec: ModelSpec
    ) -> tuple[dict[str, str], list[tuple[str, tuple[str, bytes, str]]]]:
        caps = spec.capabilities
        if not caps.image_input:
            raise ProviderError(
                ErrorCode.CAPABILITY_UNSUPPORTED,
                f"模型 {spec.key} 不支持参考图输入",
            )
        if caps.max_ref_images and len(req.images) > caps.max_ref_images:
            raise ProviderError(
                ErrorCode.CAPABILITY_UNSUPPORTED,
                f"参考图最多 {caps.max_ref_images} 张，实际 {len(req.images)} 张",
            )

        data: dict[str, str] = {"model": spec.model, "prompt": req.prompt, "n": str(req.n)}
        if req.size:
            data["size"] = req.size
        if req.quality and req.quality in caps.qualities:
            data["quality"] = req.quality
        if req.output_format and req.output_format in caps.output_formats:
            data["output_format"] = req.output_format

        files: list[tuple[str, tuple[str, bytes, str]]] = []
        for idx, ref in enumerate(req.images):
            raw, mime = await self._load_image(ref)
            ext = _EXT_BY_MIME.get(mime, "png")
            files.append(("image[]", (f"ref{idx}.{ext}", raw, mime)))

        if req.mask:
            if not caps.mask:
                raise ProviderError(ErrorCode.CAPABILITY_UNSUPPORTED, f"模型 {spec.key} 不支持 mask")
            raw, mime = await self._load_image(req.mask)
            files.append(("mask", (f"mask.{_EXT_BY_MIME.get(mime, 'png')}", raw, mime)))

        return data, files

    # ---------- 响应处理 ----------

    async def _post(self, path: str, **kwargs: Any) -> httpx.Response:
        headers = self._auth_headers()
        try:
            resp = await self._client.post(
                f"{self.base_url}{path}", headers=headers, timeout=self.timeout_s, **kwargs
            )
        except httpx.TimeoutException as exc:
            raise ProviderError(
                ErrorCode.TIMEOUT,
                f"渠道请求超时（>{self.timeout_s}s）",
                retryable=True,
            ) from exc
        except httpx.HTTPError as exc:
            raise ProviderError(ErrorCode.PROVIDER_ERROR, f"渠道连接失败: {exc}", retryable=True) from exc

        if resp.status_code >= 400:
            raise self._map_error(resp)
        return resp

    def _auth_headers(self) -> dict[str, str]:
        key = os.environ.get(self.api_key_env, "").strip()
        if not key:
            raise ProviderError(
                ErrorCode.AUTH_FAILED,
                f"未配置环境变量 {self.api_key_env}",
            )
        return {"Authorization": f"Bearer {key}"}

    def _map_error(self, resp: httpx.Response) -> ProviderError:
        message = ""
        try:
            body = resp.json()
            err = body.get("error") if isinstance(body, dict) else None
            if isinstance(err, dict):
                message = str(err.get("message") or "")
            elif err:
                message = str(err)
            message = message or str(body)[:300]
        except Exception:
            message = (resp.text or "")[:300]

        lowered = message.lower()
        status = resp.status_code
        if any(k in lowered for k in ("content policy", "safety", "blocked", "违规", "敏感")):
            code, retryable = ErrorCode.CONTENT_BLOCKED, False
        elif status in (401, 403):
            code, retryable = ErrorCode.AUTH_FAILED, False
        elif status == 429:
            code, retryable = ErrorCode.RATE_LIMITED, True
        elif status in (400, 404, 422):
            code, retryable = ErrorCode.INVALID_PARAM, False
        elif status >= 500:
            code, retryable = ErrorCode.PROVIDER_ERROR, True
        else:
            code, retryable = ErrorCode.PROVIDER_ERROR, False
        return ProviderError(code, message or f"HTTP {status}", status=status, retryable=retryable)

    @staticmethod
    def _parse_json(resp: httpx.Response) -> dict[str, Any]:
        try:
            body = resp.json()
        except Exception as exc:
            raise ProviderError(
                ErrorCode.PROVIDER_ERROR,
                "渠道返回的不是合法 JSON",
                status=resp.status_code,
            ) from exc
        if not isinstance(body, dict):
            raise ProviderError(ErrorCode.PROVIDER_ERROR, "渠道返回结构异常")
        return body

    async def _extract_images(self, body: dict[str, Any], req: ImageRequest) -> list[GeneratedImage]:
        items = body.get("data") or []
        if not isinstance(items, list):
            return []
        out: list[GeneratedImage] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            revised = item.get("revised_prompt")
            if item.get("b64_json"):
                raw = self._decode_b64(item["b64_json"])
                mime = _sniff_mime(raw)
                out.append(GeneratedImage(data=raw, mime=mime, revised_prompt=revised))
            elif item.get("url"):
                raw, mime = await self._download(item["url"])
                out.append(GeneratedImage(data=raw, mime=mime, revised_prompt=revised))
        return out

    @staticmethod
    def _decode_b64(value: str) -> bytes:
        if value.startswith("data:"):
            _, _, value = value.partition(",")
        try:
            return base64.b64decode(value)
        except (binascii.Error, ValueError) as exc:
            raise ProviderError(ErrorCode.PROVIDER_ERROR, "渠道返回的 base64 图片无法解码") from exc

    async def _download(self, url: str) -> tuple[bytes, str]:
        try:
            resp = await self._client.get(url, timeout=self.timeout_s)
        except httpx.HTTPError as exc:
            raise ProviderError(ErrorCode.ARTIFACT_LOST, f"产物下载失败: {exc}", retryable=True) from exc
        if resp.status_code >= 400 or not resp.content:
            raise ProviderError(
                ErrorCode.ARTIFACT_LOST,
                f"产物下载失败 HTTP {resp.status_code}",
                status=resp.status_code,
                retryable=True,
            )
        return resp.content, _sniff_mime(resp.content, fallback=resp.headers.get("content-type", "image/png"))

    async def _load_image(self, ref: str) -> tuple[bytes, str]:
        """参考图：data URL / http(s) / 裸 base64 三种形态。"""
        ref = ref.strip()
        if ref.startswith("data:"):
            header, _, payload = ref.partition(",")
            mime = header[5:].split(";")[0] or "image/png"
            return self._decode_b64(payload), mime
        if ref.startswith(("http://", "https://")):
            return await self._download(ref)
        return self._decode_b64(ref), "image/png"
