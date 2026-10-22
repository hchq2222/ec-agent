"""生图能力的统一抽象。

分层：
    Provider  = 协议/渠道（OpenAI 兼容、方舟原生、Replicate、Fal...）
    ModelSpec = 对外暴露的模型 key + 能力声明 + 价格口径

业务层只认 model key，不认底层模型 ID 与渠道；换渠道只改 config/providers.yaml。
后续接生视频时，新增 VideoProvider 协议与 VideoRequest/VideoResult，复用同一 Registry。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Protocol, runtime_checkable


class ErrorCode(str, Enum):
    AUTH_FAILED = "AUTH_FAILED"
    RATE_LIMITED = "RATE_LIMITED"
    INVALID_PARAM = "INVALID_PARAM"
    CONTENT_BLOCKED = "CONTENT_BLOCKED"
    TIMEOUT = "TIMEOUT"
    PROVIDER_ERROR = "PROVIDER_ERROR"
    MODEL_NOT_FOUND = "MODEL_NOT_FOUND"
    CAPABILITY_UNSUPPORTED = "CAPABILITY_UNSUPPORTED"
    ARTIFACT_LOST = "ARTIFACT_LOST"


class ProviderError(Exception):
    """统一的渠道错误。上层只处理 code，不解析各家错误结构。"""

    def __init__(
        self,
        code: ErrorCode,
        message: str,
        *,
        status: int | None = None,
        retryable: bool = False,
        raw: Any = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status
        self.retryable = retryable
        self.raw = raw

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code.value,
            "message": self.message,
            "status": self.status,
            "retryable": self.retryable,
        }


@dataclass(frozen=True)
class Capabilities:
    max_n: int = 1
    sizes: tuple[str, ...] = ()
    qualities: tuple[str, ...] = ()
    output_formats: tuple[str, ...] = ("png",)
    backgrounds: tuple[str, ...] = ()
    image_input: bool = False
    mask: bool = False
    max_ref_images: int = 0


@dataclass(frozen=True)
class ModelSpec:
    key: str                 # 对外 key，业务代码使用
    model: str               # 上游模型 ID
    provider_id: str
    kind: str = "image"      # image | video（为后续扩展预留）
    display_name: str = ""
    capabilities: Capabilities = field(default_factory=Capabilities)
    pricing: dict[str, Any] = field(default_factory=dict)


@dataclass
class ImageRequest:
    prompt: str
    model_key: str
    n: int = 1
    size: str | None = None
    quality: str | None = None
    background: str | None = None
    output_format: str | None = None
    # 参考图：URL、data URL 或裸 base64 均可；用于图生图 / 局部重绘
    images: list[str] = field(default_factory=list)
    mask: str | None = None
    user: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class GeneratedImage:
    data: bytes
    mime: str
    revised_prompt: str | None = None


@dataclass
class ImageResult:
    provider_id: str
    model_key: str
    images: list[GeneratedImage]
    latency_ms: int
    usage: dict[str, Any] = field(default_factory=dict)
    raw: dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class ImageProvider(Protocol):
    id: str

    async def generate(self, req: ImageRequest, spec: ModelSpec) -> ImageResult: ...

    async def aclose(self) -> None: ...
