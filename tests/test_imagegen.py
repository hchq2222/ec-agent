"""注册表与能力校验的单元测试（不访问网络）。"""

from __future__ import annotations

import asyncio
import base64
import os

import httpx
import pytest

from apps.providers.base import ErrorCode, ImageRequest, ProviderError
from apps.providers.registry import Registry

PNG_B64 = base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"0" * 32).decode()


def _client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def _registry(handler=None) -> Registry:
    if handler is None:
        return Registry.from_file("config/providers.yaml", client=_client(lambda r: httpx.Response(200)))
    return Registry.from_file("config/providers.yaml", client=_client(handler))


def test_catalog_lists_models():
    reg = _registry()
    models = reg.list_models()
    assert len(models) >= 3
    keys = {m.key for m in models}
    assert {"gpt-image-2", "gpt-image-2.5-flare", "gpt-image-2.5-sunburst"} <= keys

    catalog = reg.catalog(kind="image")
    first = catalog[0]
    assert first["kind"] == "image"
    assert "capabilities" in first and "max_n" in first["capabilities"]


def test_unknown_model():
    reg = _registry()
    req = ImageRequest(prompt="x", model_key="nope")
    with pytest.raises(ProviderError) as ei:
        asyncio.run(reg.generate(req))
    assert ei.value.code == ErrorCode.MODEL_NOT_FOUND


def test_n_out_of_range():
    reg = _registry()
    req = ImageRequest(prompt="x", model_key="gpt-image-2", n=99)
    with pytest.raises(ProviderError) as ei:
        asyncio.run(reg.generate(req))
    assert ei.value.code == ErrorCode.INVALID_PARAM


def test_unsupported_size():
    reg = _registry()
    req = ImageRequest(prompt="x", model_key="gpt-image-2", size="4096x4096")
    with pytest.raises(ProviderError) as ei:
        asyncio.run(reg.generate(req))
    assert ei.value.code == ErrorCode.INVALID_PARAM


def test_empty_prompt():
    reg = _registry()
    req = ImageRequest(prompt="   ", model_key="gpt-image-2")
    with pytest.raises(ProviderError) as ei:
        asyncio.run(reg.generate(req))
    assert ei.value.code == ErrorCode.INVALID_PARAM


def test_generate_success(monkeypatch):
    monkeypatch.setenv("OMYAI_API_KEY", "sk-test")
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["body"] = request.read()
        return httpx.Response(200, json={"data": [{"b64_json": PNG_B64}], "usage": {"images": 1}})

    reg = _registry(handler)
    res = asyncio.run(
        reg.generate(ImageRequest(prompt="一只玻璃杯", model_key="gpt-image-2", size="1024x1024"))
    )

    assert seen["path"] == "/v1/images/generations"
    assert b"gpt-image-2" in seen["body"]
    assert len(res.images) == 1
    assert res.images[0].mime == "image/png"
    assert res.images[0].data.startswith(b"\x89PNG")
    assert res.usage == {"images": 1}


def test_auth_error(monkeypatch):
    monkeypatch.setenv("OMYAI_API_KEY", "sk-bad")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            401, json={"error": {"message": "Invalid token", "type": "new_api_error"}}
        )

    reg = _registry(handler)
    with pytest.raises(ProviderError) as ei:
        asyncio.run(reg.generate(ImageRequest(prompt="x", model_key="gpt-image-2")))
    assert ei.value.code == ErrorCode.AUTH_FAILED
    assert ei.value.status == 401


def test_rate_limited(monkeypatch):
    monkeypatch.setenv("OMYAI_API_KEY", "sk-test")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, json={"error": {"message": "Too many requests"}})

    reg = _registry(handler)
    with pytest.raises(ProviderError) as ei:
        asyncio.run(reg.generate(ImageRequest(prompt="x", model_key="gpt-image-2")))
    assert ei.value.code == ErrorCode.RATE_LIMITED
    assert ei.value.retryable is True


def test_content_blocked(monkeypatch):
    monkeypatch.setenv("OMYAI_API_KEY", "sk-test")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"error": {"message": "rejected by content policy"}})

    reg = _registry(handler)
    with pytest.raises(ProviderError) as ei:
        asyncio.run(reg.generate(ImageRequest(prompt="x", model_key="gpt-image-2")))
    assert ei.value.code == ErrorCode.CONTENT_BLOCKED


def test_url_response_is_downloaded(monkeypatch):
    monkeypatch.setenv("OMYAI_API_KEY", "sk-test")
    png = b"\x89PNG\r\n\x1a\n" + b"1" * 16

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(200, content=png, headers={"content-type": "image/png"})
        return httpx.Response(200, json={"data": [{"url": "https://cdn.example.com/a.png"}]})

    reg = _registry(handler)
    res = asyncio.run(reg.generate(ImageRequest(prompt="x", model_key="gpt-image-2")))
    assert res.images[0].data == png


def test_multi_image(monkeypatch):
    monkeypatch.setenv("OMYAI_API_KEY", "sk-test")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json={"data": [{"b64_json": PNG_B64}, {"b64_json": PNG_B64}]}
        )

    reg = _registry(handler)
    res = asyncio.run(reg.generate(ImageRequest(prompt="x", model_key="gpt-image-2", n=2)))
    assert len(res.images) == 2
