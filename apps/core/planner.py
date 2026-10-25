"""Plan image deliverables from confirmed merchant facts only."""
from __future__ import annotations

import json
import uuid
from typing import Any

import httpx
from sqlalchemy import select

from apps.core.config import get_settings
from apps.core.models import Job, ProviderCall, Project, ProjectPlan, utcnow

ALLOWED_USAGES = {"scene", "main", "detail"}


class PlannerError(ValueError):
    pass


def validate_items(raw: Any, sku_refs: set[str]) -> list[dict[str, Any]]:
    if not isinstance(raw, list) or not 1 <= len(raw) <= 60:
        raise PlannerError("规划须包含 1–60 个交付项")
    items: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for item in raw:
        if not isinstance(item, dict):
            raise PlannerError("交付项格式错误")
        usage = item.get("usage")
        if usage not in ALLOWED_USAGES:
            raise PlannerError(f"不支持的生图用途: {usage}")
        sku = str(item.get("sku_ref", ""))
        if sku not in sku_refs:
            raise PlannerError("交付项必须绑定本项目的 SKU")
        title = str(item.get("title", "")).strip()[:100]
        prompt = str(item.get("prompt", "")).strip()[:1200]
        reason = str(item.get("reason", "")).strip()[:300]
        if not title or not prompt or not reason:
            raise PlannerError("交付项缺少标题、画面描述或理由")
        try:
            item_id = str(uuid.UUID(str(item["id"]))) if item.get("id") else str(uuid.uuid4())
        except (ValueError, TypeError, KeyError) as exc:
            raise PlannerError("交付项 id 不是合法 UUID") from exc
        if item_id in seen_ids:
            raise PlannerError("交付项 id 重复")
        seen_ids.add(item_id)
        size = str(item.get("size") or "1024x1024")
        if size not in {"1024x1024", "1536x1024", "1024x1536"}:
            raise PlannerError("暂不支持该图片尺寸")
        items.append({
            "id": item_id, "sku_ref": sku, "usage": usage,
            "title": title, "prompt": prompt, "reason": reason,
            "size": size,
        })
    return items


async def propose(project: Project, database: Any) -> ProjectPlan:
    settings = get_settings()
    if not all((settings.planner_base_url, settings.planner_api_key, settings.planner_model)):
        raise PlannerError("未配置 PLANNER_BASE_URL、PLANNER_API_KEY 和 PLANNER_MODEL")
    sku_refs = {str(s["id"]) for s in project.product_snapshot["skus"]}
    if any(not fact.get("confirmed") for fact in project.product_snapshot["facts"].values()):
        raise PlannerError("商品事实存在未确认字段，请先由商家确认")
    if not sku_refs <= set(project.product_snapshot.get("sources", {})):
        raise PlannerError("每个 SKU 必须先上传真实商品原图")
    job_id = uuid.uuid4()
    async with database.session() as session:
        session.add(Job(id=job_id, kind="plan", model_key=settings.planner_model, provider_id="planner",
                        status="running", n=1, params={"project_id": str(project.id)}))
        await session.commit()

    facts = {k: v["value"] for k, v in project.product_snapshot["facts"].items() if v.get("confirmed")}
    prompt = ("你是电商图片制作规划员。只使用以下已确认的商家事实，不补造材质、规格、认证、功效或兼容性。"
              "只建议 scene/main/detail 三种图片，不要视频、白底抠图、长图、信息图、广告图。"
              "返回 JSON 对象 {\"items\":[...]}，每项含 sku_ref、usage、title、prompt、reason、size；"
              "每 SKU 至少一项，全部最多 60 项。size 只可为 1024x1024、1536x1024、1024x1536。"
              "请优先真实商品像素与参考图一致性。\n"
              + json.dumps({"product": project.name, "category": project.product_snapshot.get("category"),
                            "facts": facts, "skus": project.product_snapshot["skus"]}, ensure_ascii=False))
    status = "failed"
    usage: dict[str, Any] = {}
    items: list[dict[str, Any]] | None = None
    error: Exception | None = None
    try:
        async with httpx.AsyncClient(timeout=60) as client:
            response = await client.post(settings.planner_base_url.rstrip("/") + "/chat/completions",
                headers={"Authorization": f"Bearer {settings.planner_api_key}"},
                json={"model": settings.planner_model, "temperature": 0.2,
                      "messages": [{"role": "user", "content": prompt}]})
            response.raise_for_status()
            body = response.json()
        if not isinstance(body, dict):
            raise PlannerError("规划模型响应格式错误")
        usage = body.get("usage") or {}
        if not isinstance(usage, dict):
            usage = {}
        content = body["choices"][0]["message"]["content"]
        if not isinstance(content, str):
            raise PlannerError("规划模型未返回文本 JSON")
        clean = content.strip()
        if clean.startswith("```"):
            clean = clean.split("\n", 1)[1].rsplit("```", 1)[0].strip()
        items = validate_items(json.loads(clean)["items"], sku_refs)
        for item in items:
            item["prompt"] = (item["prompt"] + "。仅使用真实商品原图中的商品像素；"
                "优先生成背景并合成，保持商品结构、颜色、标签、Logo 和包装文字不变。")[:1600]
        if not sku_refs <= {i["sku_ref"] for i in items}:
            raise PlannerError("规划未覆盖所有选中 SKU")
        status = "succeeded"
    except Exception as exc:  # preserve a failed billing record even for unexpected provider payloads
        error = exc
    finally:
        try:
            units = max(0, int(usage.get("total_tokens") or 0))
        except (TypeError, ValueError):
            units = 0
        async with database.session() as session:
            job = await session.get(Job, job_id)
            job.status = status
            job.finished_at = utcnow()
            if error:
                job.error_code = "PLAN_FAILED"
                job.error_message = str(error)[:2000]
            session.add(ProviderCall(job_id=job_id, provider_id="planner", model_key=settings.planner_model,
                upstream_model=settings.planner_model, unit="token",
                units=units, unit_price=None, amount=None,
                currency="USD", status=status, usage=usage))
            await session.commit()
    if error or items is None:
        raise PlannerError(f"AI 规划失败：{error}")
    async with database.session() as session:
        previous = (await session.execute(select(ProjectPlan.version).where(ProjectPlan.project_id == project.id)
                          .order_by(ProjectPlan.version.desc()).limit(1))).scalar_one_or_none()
        plan = ProjectPlan(project_id=project.id, version=(previous or 0) + 1, status="draft", items=items, usage=usage)
        session.add(plan)
        await session.commit()
        await session.refresh(plan)
        return plan
