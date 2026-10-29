"""DeepSeek 文本能力：按商品事实生成电商图片提示词。"""
from __future__ import annotations

import json
import uuid
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlalchemy import select

from apps.core.llm import LLMError, chat
from apps.core.models import Job, Product, ProductAttr, ProductSKU, ProviderCall, utcnow

router = APIRouter(prefix="/api/ai", tags=["ai"])

SYSTEM_PROMPT = (
    "你是电商视觉团队的提示词工程师。只输出最终的中文图片生成提示词，"
    "不要解释、不要标题、不要 Markdown、不要引号。"
    "只能使用用户给出的商品事实，缺什么就不写，禁止编造卖点、认证、成分或配件。"
    "提示词必须包含：商品身份与 SKU、画面用途（白底图/场景图）、背景与光线、"
    "主体占比、阴影、输出尺寸，以及“保持商品形状/颜色/Logo/包装文字不变、只调整背景与构图”的一致性约束。"
)


class PromptRequest(BaseModel):
    product_id: str
    sku_id: str | None = None
    template: dict[str, Any] | None = None


def api() -> Any:
    from . import main

    if main.db is None:
        raise HTTPException(503, "该接口需要 DATABASE_URL")
    return main


def uid(value: str) -> uuid.UUID:
    try:
        return uuid.UUID(value)
    except (ValueError, TypeError, AttributeError) as exc:
        raise HTTPException(404, "商品不存在") from exc


@router.post("/prompt")
async def make_prompt(payload: PromptRequest) -> dict[str, Any]:
    a = api()
    product_id = uid(payload.product_id)
    async with a.db.session() as session:
        p = await session.get(Product, product_id)
        if not p or p.deleted_at is not None:
            raise HTTPException(404, "商品不存在")
        attrs = (await session.execute(
            select(ProductAttr).where(ProductAttr.product_id == p.id).order_by(ProductAttr.created_at)
        )).scalars().all()
        skus = (await session.execute(
            select(ProductSKU).where(ProductSKU.product_id == p.id).order_by(ProductSKU.created_at)
        )).scalars().all()

    sku = next((s for s in skus if payload.sku_id and str(s.id) == payload.sku_id), None)
    facts = {
        "商品名": p.name,
        "品牌": p.brand or None,
        "卖点": p.pitch or None,
        "已确认参数": [
            {"名称": x.key, "值": (x.value + (x.unit or ""))} for x in attrs if x.confirmed and x.value
        ],
        "SKU": (f"{sku.color} {sku.spec}".strip() if sku else None),
        "版式模板": payload.template,
    }
    user_text = (
        "商品事实（只能使用这些，不得补全或编造）：\n"
        + json.dumps(facts, ensure_ascii=False)
        + "\n\n请生成一段可直接用于生图模型的中文提示词，120–220 字，一段话。"
    )

    job_id = uuid.uuid4()
    from apps.core.config import get_settings

    model_key = get_settings().deepseek_model
    async with a.db.session() as session:
        session.add(Job(id=job_id, kind="text_prompt", model_key=model_key,
                        provider_id="deepseek", status="running", n=1,
                        params={"product_id": str(p.id), "sku_id": payload.sku_id}))
        await session.commit()

    usage: dict[str, Any] = {}
    prompt = ""
    error: Exception | None = None
    try:
        prompt, usage = await chat(
            [{"role": "system", "content": SYSTEM_PROMPT},
             {"role": "user", "content": user_text}],
            temperature=0.5,
            max_tokens=4000,
        )
    except Exception as exc:  # noqa: BLE001 - 统一转成 502 给前端回退
        error = exc

    status = "succeeded" if prompt else "failed"
    async with a.db.session() as session:
        job = await session.get(Job, job_id)
        if job:
            job.status = status
            job.finished_at = utcnow()
            if error:
                job.error_code = "TEXT_PROMPT_FAILED"
                job.error_message = str(error)[:2000]
        try:
            units = max(0, int(usage.get("total_tokens") or 0))
        except (TypeError, ValueError):
            units = 0
        session.add(ProviderCall(job_id=job_id, provider_id="deepseek", model_key=model_key,
                                 upstream_model=model_key, unit="token", units=units,
                                 unit_price=None, amount=None, currency="USD",
                                 status=status, usage=usage))
        await session.commit()

    if error or not prompt:
        raise HTTPException(502, f"提示词生成失败：{error or '模型返回为空'}")
    return {"data": {"prompt": prompt, "model": model_key, "usage": usage}}
