"""模型注册表：把 config/providers.yaml 变成可调用的 provider + model 目录。

新增模型 = YAML 加一条；新增渠道 = 加一个 provider 块 + 一个 adapter 类。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx
import yaml

from .base import (
    Capabilities,
    ErrorCode,
    ImageProvider,
    ImageRequest,
    ImageResult,
    ModelSpec,
    ProviderError,
)
from .openai_compat import OpenAICompatProvider

ADAPTERS: dict[str, type] = {
    "openai_compat": OpenAICompatProvider,
}


def _build_capabilities(raw: dict[str, Any] | None) -> Capabilities:
    raw = raw or {}
    return Capabilities(
        max_n=int(raw.get("max_n", 1)),
        sizes=tuple(raw.get("sizes") or ()),
        qualities=tuple(raw.get("qualities") or ()),
        output_formats=tuple(raw.get("output_formats") or ("png",)),
        backgrounds=tuple(raw.get("backgrounds") or ()),
        image_input=bool(raw.get("image_input", False)),
        mask=bool(raw.get("mask", False)),
        max_ref_images=int(raw.get("max_ref_images", 0)),
    )


class Registry:
    def __init__(self, raw: dict[str, Any], *, client: httpx.AsyncClient | None = None) -> None:
        self._models: dict[str, ModelSpec] = {}
        self._providers: dict[str, ImageProvider] = {}

        providers = raw.get("providers") or []
        if not providers:
            raise ValueError("providers.yaml 中没有任何 provider")

        for p in providers:
            pid = p.get("id")
            ptype = p.get("type", "openai_compat")
            if not pid:
                raise ValueError("provider 缺少 id")
            adapter_cls = ADAPTERS.get(ptype)
            if adapter_cls is None:
                raise ValueError(f"未知 provider type: {ptype}（已支持: {sorted(ADAPTERS)}）")

            self._providers[pid] = adapter_cls(
                provider_id=pid,
                base_url=p["base_url"],
                api_key_env=p.get("api_key_env", ""),
                timeout_s=float(p.get("timeout_s", 180)),
                client=client,
            )

            for m in p.get("models") or []:
                key = m.get("key") or m.get("model")
                if not key:
                    raise ValueError(f"provider {pid} 下有模型缺少 key")
                if key in self._models:
                    raise ValueError(f"模型 key 重复: {key}")
                self._models[key] = ModelSpec(
                    key=key,
                    model=m.get("model", key),
                    provider_id=pid,
                    kind=m.get("kind", "image"),
                    display_name=m.get("display_name", key),
                    capabilities=_build_capabilities(m.get("capabilities")),
                    pricing=m.get("pricing") or {},
                )

    @classmethod
    def from_file(cls, path: str | Path, *, client: httpx.AsyncClient | None = None) -> "Registry":
        with open(path, "r", encoding="utf-8") as fh:
            raw = yaml.safe_load(fh) or {}
        return cls(raw, client=client)

    # ---------- 查询 ----------

    def list_models(self, kind: str | None = None) -> list[ModelSpec]:
        specs = list(self._models.values())
        if kind:
            specs = [s for s in specs if s.kind == kind]
        return specs

    def get_model(self, key: str) -> ModelSpec:
        spec = self._models.get(key)
        if spec is None:
            raise ProviderError(
                ErrorCode.MODEL_NOT_FOUND,
                f"未知模型 key: {key}（可用: {', '.join(sorted(self._models))}）",
            )
        return spec

    def catalog(self, kind: str | None = None) -> list[dict[str, Any]]:
        out = []
        for s in self.list_models(kind):
            c = s.capabilities
            out.append(
                {
                    "key": s.key,
                    "provider": s.provider_id,
                    "kind": s.kind,
                    "display_name": s.display_name,
                    "capabilities": {
                        "max_n": c.max_n,
                        "sizes": list(c.sizes),
                        "qualities": list(c.qualities),
                        "output_formats": list(c.output_formats),
                        "backgrounds": list(c.backgrounds),
                        "image_input": c.image_input,
                        "mask": c.mask,
                        "max_ref_images": c.max_ref_images,
                    },
                    "pricing": s.pricing,
                }
            )
        return out

    # ---------- 调用 ----------

    async def generate(self, req: ImageRequest) -> ImageResult:
        spec = self.get_model(req.model_key)
        if spec.kind != "image":
            raise ProviderError(ErrorCode.MODEL_NOT_FOUND, f"{spec.key} 不是生图模型")
        self._validate(req, spec)
        provider = self._providers[spec.provider_id]
        return await provider.generate(req, spec)

    @staticmethod
    def _validate(req: ImageRequest, spec: ModelSpec) -> None:
        c = spec.capabilities
        if req.n < 1 or req.n > c.max_n:
            raise ProviderError(
                ErrorCode.INVALID_PARAM,
                f"{spec.key} 的 n 取值范围 1–{c.max_n}，实际 {req.n}",
            )
        if not req.prompt.strip():
            raise ProviderError(ErrorCode.INVALID_PARAM, "prompt 不能为空")
        if req.size and c.sizes and req.size not in c.sizes:
            raise ProviderError(
                ErrorCode.INVALID_PARAM,
                f"{spec.key} 不支持 size={req.size}（可选: {', '.join(c.sizes)}）",
            )
        if req.quality and c.qualities and req.quality not in c.qualities:
            raise ProviderError(ErrorCode.INVALID_PARAM, f"{spec.key} 不支持 quality={req.quality}")
        if req.output_format and req.output_format not in c.output_formats:
            raise ProviderError(
                ErrorCode.INVALID_PARAM, f"{spec.key} 不支持 output_format={req.output_format}"
            )
        if req.background and c.backgrounds and req.background not in c.backgrounds:
            raise ProviderError(ErrorCode.INVALID_PARAM, f"{spec.key} 不支持 background={req.background}")
        if req.images:
            if not c.image_input:
                raise ProviderError(
                    ErrorCode.CAPABILITY_UNSUPPORTED, f"{spec.key} 不支持参考图输入"
                )
            if c.max_ref_images and len(req.images) > c.max_ref_images:
                raise ProviderError(
                    ErrorCode.CAPABILITY_UNSUPPORTED,
                    f"{spec.key} 最多接受 {c.max_ref_images} 张参考图",
                )

    async def aclose(self) -> None:
        for p in self._providers.values():
            await p.aclose()
