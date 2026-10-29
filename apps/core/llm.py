"""DeepSeek 文本模型调用（OpenAI 兼容）。

用于提示词生成与商品资料识别。密钥只从环境变量读取。
"""
from __future__ import annotations

import httpx

from .config import get_settings


class LLMError(RuntimeError):
    """文本模型不可用或返回为空。"""


async def chat(
    messages: list[dict],
    *,
    temperature: float = 0.3,
    max_tokens: int = 6000,
    timeout_s: float = 240.0,
) -> tuple[str, dict]:
    settings = get_settings()
    if not settings.deepseek_api_key:
        raise LLMError("未配置 DEEPSEEK_API_KEY")
    url = settings.deepseek_base_url.rstrip("/") + "/chat/completions"
    async with httpx.AsyncClient(timeout=timeout_s) as client:
        response = await client.post(
            url,
            headers={"Authorization": f"Bearer {settings.deepseek_api_key}"},
            json={
                "model": settings.deepseek_model,
                "temperature": temperature,
                "max_tokens": max_tokens,
                "messages": messages,
            },
        )
        response.raise_for_status()
        body = response.json()
    content = (body.get("choices") or [{}])[0].get("message", {}).get("content") or ""
    if not content.strip():
        raise LLMError("DeepSeek 返回内容为空")
    usage = body.get("usage") if isinstance(body.get("usage"), dict) else {}
    return content.strip(), usage
