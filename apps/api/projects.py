"""Project plans and resumable image workflow endpoints."""
from __future__ import annotations

import base64
import asyncio
import uuid
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm.attributes import flag_modified

from apps.core.imageinfo import image_size
from apps.core.models import Asset, Job, Project, ProjectPlan, WorkflowRun, WorkflowStep, utcnow
from apps.core.planner import PlannerError, propose, validate_items
from apps.core.storage import build_object_key
from apps.core.workflow import WorkflowValidationError, default_workflow, validate_workflow

router = APIRouter(prefix="/api/projects", tags=["projects"])


def api() -> Any:
    from . import main
    if main.db is None:
        raise HTTPException(503, "项目模块需要 DATABASE_URL")
    return main


def uid(value: str) -> uuid.UUID:
    try:
        return uuid.UUID(value)
    except ValueError as exc:
        raise HTTPException(400, "id 不是合法 UUID") from exc


def supports_channel_image(channel: Any, model: str) -> bool:
    return bool(channel.enabled and any(
        entry.get("id") == model and entry.get("kind", "image") == "image"
        for entry in channel.models or []
    ))


def workflow_model(node: dict[str, Any]) -> tuple[str, str | None]:
    data = node.get("data") or {}
    if not isinstance(data, dict):
        raise HTTPException(400, "节点模型配置必须是对象")
    model = data.get("model")
    channel_id = data.get("channel_id")
    if not isinstance(model, str) or not model.strip() or len(model) > 128:
        raise HTTPException(400, "生图或精修节点必须选择合法模型")
    if channel_id is not None and (not isinstance(channel_id, str) or len(channel_id) > 64):
        raise HTTPException(400, "渠道 ID 不合法")
    if channel_id:
        uid(channel_id)
    return model.strip(), channel_id or None


class SKUInput(BaseModel):
    id: str = Field(min_length=1, max_length=128)
    name: str = Field(min_length=1, max_length=256)


class FactInput(BaseModel):
    value: str = Field(max_length=1000)
    confirmed: bool


class ProjectCreate(BaseModel):
    product_ref: str = Field(min_length=1, max_length=128)
    name: str = Field(min_length=1, max_length=256)
    category: str = Field(min_length=1, max_length=64)
    skus: list[SKUInput] = Field(min_length=1, max_length=20)
    facts: dict[str, FactInput]


class PlanEdit(BaseModel):
    items: list[dict[str, Any]]


class WorkflowCreate(BaseModel):
    plan_id: str
    item_id: str
    graph: dict[str, list[dict[str, Any]]] = Field(default_factory=default_workflow)


class GateResult(BaseModel):
    expected_cursor: int = Field(ge=0)
    approved: bool
    note: str = Field(default="", max_length=500)
    checks: dict[str, bool] = Field(default_factory=dict)


def project_out(p: Project) -> dict[str, Any]:
    return {"id": str(p.id), "product_ref": p.product_ref, "name": p.name,
            "status": p.status, "product_snapshot": p.product_snapshot,
            "created_at": p.created_at}


def plan_out(p: ProjectPlan) -> dict[str, Any]:
    return {"id": str(p.id), "project_id": str(p.project_id), "version": p.version,
            "status": p.status, "items": p.items, "usage": p.usage}


def run_out(r: WorkflowRun, current_url: str | None = None) -> dict[str, Any]:
    order = validate_workflow(r.graph["nodes"], r.graph["edges"])
    by_id = {node["id"]: node for node in r.graph["nodes"]}
    next_node = by_id[order[r.cursor]] if r.cursor < len(order) else None
    return {"id": str(r.id), "project_id": str(r.project_id), "plan_id": str(r.plan_id),
            "sku_ref": r.sku_ref, "item_id": r.item_id, "status": r.status,
            "cursor": r.cursor, "graph": r.graph,
            "context": {**(r.context or {}), **({"current_url": current_url} if current_url else {})},
            "next_node": next_node}


async def run_out_with_asset(session: Any, r: WorkflowRun, a: Any) -> dict[str, Any]:
    asset_id = (r.context or {}).get("current_asset_id")
    if not asset_id:
        return run_out(r)
    asset = await session.get(Asset, uid(asset_id))
    if not asset or a.storage is None:
        return run_out(r)
    return run_out(r, a._asset_url(asset.id, asset.object_key))


async def require_current_asset(session: Any, context: dict[str, Any]) -> Asset:
    asset_id = context.get("current_asset_id")
    if not asset_id:
        raise HTTPException(409, "须先完成商品原图节点")
    asset = await session.get(Asset, uid(asset_id))
    if not asset:
        raise HTTPException(409, "当前图片素材不存在")
    return asset


@router.post("", status_code=201)
async def create_project(payload: ProjectCreate) -> dict[str, Any]:
    a = api()
    if not payload.product_ref.strip() or not payload.name.strip():
        raise HTTPException(400, "商品 ID 和名称不能为空")
    if any(not s.id.strip() or not s.name.strip() for s in payload.skus):
        raise HTTPException(400, "SKU ID 和名称不能为空")
    if len({s.id for s in payload.skus}) != len(payload.skus):
        raise HTTPException(400, "SKU id 重复")
    if any("|" in s.id or "\n" in s.id for s in payload.skus):
        raise HTTPException(400, "SKU ID 不能包含表单分隔符")
    if len(payload.facts) > 40 or any(not k.strip() or len(k) > 80 for k in payload.facts):
        raise HTTPException(400, "商品事实字段数量或名称不合法")
    if payload.category not in {"beauty", "home", "digital"}:
        raise HTTPException(400, "MVP 只支持美妆、家居、3C")
    if not payload.facts:
        raise HTTPException(400, "至少填写一条商家已确认的商品事实")
    if any(not fact.confirmed or not fact.value.strip() for fact in payload.facts.values()):
        raise HTTPException(400, "项目只接受已确认且非空的商品事实")
    snapshot = {"category": payload.category, "skus": [s.model_dump() for s in payload.skus],
                "facts": {k: v.model_dump() for k, v in payload.facts.items()}, "sources": {}}
    async with a.db.session() as session:
        p = Project(product_ref=payload.product_ref, name=payload.name, product_snapshot=snapshot)
        session.add(p)
        await session.commit()
        await session.refresh(p)
        return {"data": project_out(p)}


@router.get("")
async def list_projects() -> dict[str, Any]:
    a = api()
    async with a.db.session() as session:
        rows = (await session.execute(
            select(Project).where(Project.deleted_at.is_(None))
            .order_by(Project.created_at.desc()).limit(100)
        )).scalars().all()
        return {"data": [project_out(p) for p in rows]}


@router.get("/{project_id}")
async def get_project(project_id: str) -> dict[str, Any]:
    a = api()
    async with a.db.session() as session:
        p = await session.get(Project, uid(project_id))
        if not p or p.deleted_at is not None:
            raise HTTPException(404, "项目不存在")
        plans = (await session.execute(select(ProjectPlan).where(ProjectPlan.project_id == p.id)
                 .order_by(ProjectPlan.version.desc()))).scalars().all()
        runs = (await session.execute(select(WorkflowRun).where(WorkflowRun.project_id == p.id)
                .order_by(WorkflowRun.created_at.desc()))).scalars().all()
        return {"data": {**project_out(p), "plans": [plan_out(x) for x in plans],
                         "runs": [await run_out_with_asset(session, x, a) for x in runs]}}


@router.post("/{project_id}/sources/{sku_ref}")
async def upload_source(project_id: str, sku_ref: str, request: Request) -> dict[str, Any]:
    a = api()
    if a.storage is None:
        raise HTTPException(503, "未配置素材存储")
    pid = uid(project_id)
    async with a.db.session() as session:
        p = await session.get(Project, pid)
        if not p or p.deleted_at is not None:
            raise HTTPException(404, "项目不存在")
        if sku_ref not in {s["id"] for s in p.product_snapshot["skus"]}:
            raise HTTPException(400, "SKU 不属于项目")
        if sku_ref in p.product_snapshot.get("sources", {}):
            raise HTTPException(409, "此 SKU 的原图已上传；请新建项目保存另一版本")
        if (await session.execute(select(ProjectPlan.id).where(
            ProjectPlan.project_id == pid, ProjectPlan.status == "confirmed").limit(1))).first():
            raise HTTPException(409, "清单已确认，原图不可替换；请新建项目版本")
        raw = await request.body()
        if not raw or len(raw) > 10 * 1024 * 1024:
            raise HTTPException(400, "原图须为 1 字节至 10 MB")
        if raw.startswith(b"\x89PNG\r\n\x1a\n"):
            ext, mime = "png", "image/png"
        elif raw.startswith(b"\xff\xd8\xff"):
            ext, mime = "jpg", "image/jpeg"
        elif raw[:4] == b"RIFF" and raw[8:12] == b"WEBP":
            ext, mime = "webp", "image/webp"
        else:
            raise HTTPException(400, "只接受 PNG、JPEG 或 WebP 原图")
        width, height = image_size(raw)
        if not width or not height or width > 10000 or height > 10000:
            raise HTTPException(400, "图片尺寸无法识别或超过 10000 像素")
        aid, jid = uuid.uuid4(), uuid.uuid4()
        key = build_object_key(asset_id=str(aid), ext=ext, variant="original")
        stored = await asyncio.to_thread(a.storage.put, key, raw, mime)
        session.add(Job(id=jid, kind="source", model_key="upload", provider_id="merchant",
                        status="succeeded", n=1, params={"project_id": project_id, "sku_ref": sku_ref},
                        finished_at=utcnow()))
        session.add(Asset(id=aid, job_id=jid, type="original", object_key=stored.key, mime=mime,
                          bytes=stored.bytes, width=width, height=height))
        snapshot = dict(p.product_snapshot)
        snapshot["sources"] = {**snapshot.get("sources", {}), sku_ref: str(aid)}
        p.product_snapshot = snapshot
        flag_modified(p, "product_snapshot")
        await session.commit()
        return {"data": {"asset_id": str(aid), "url": a._asset_url(aid, stored.key)}}


@router.post("/{project_id}/propose")
async def propose_plan(project_id: str) -> dict[str, Any]:
    a = api()
    async with a.db.session() as session:
        p = await session.get(Project, uid(project_id))
        if not p or p.deleted_at is not None:
            raise HTTPException(404, "项目不存在")
        if any(not fact.get("confirmed") for fact in p.product_snapshot["facts"].values()):
            raise HTTPException(409, "商品事实尚未全部确认")
        if not all(s["id"] in p.product_snapshot.get("sources", {}) for s in p.product_snapshot["skus"]):
            raise HTTPException(409, "请先为每个 SKU 上传真实商品原图，再调用 AI 规划")
    try:
        plan = await propose(p, a.db)
    except PlannerError as exc:
        raise HTTPException(422, str(exc)) from exc
    return {"data": plan_out(plan)}


@router.put("/{project_id}/plans/{plan_id}")
async def edit_plan(project_id: str, plan_id: str, payload: PlanEdit) -> dict[str, Any]:
    a = api()
    async with a.db.session() as session:
        p = await session.get(Project, uid(project_id))
        plan = (await session.execute(
            select(ProjectPlan).where(ProjectPlan.id == uid(plan_id)).with_for_update()
        )).scalar_one_or_none()
        if not p or p.deleted_at is not None or not plan or plan.project_id != p.id:
            raise HTTPException(404, "项目或规划不存在")
        if plan.status != "draft":
            raise HTTPException(409, "已确认规划不能编辑")
        if (await session.execute(select(WorkflowRun.id).where(
            WorkflowRun.plan_id == plan.id).limit(1))).first():
            raise HTTPException(409, "已有执行记录，不能编辑此清单")
        try:
            items = validate_items(payload.items, {s["id"] for s in p.product_snapshot["skus"]})
        except PlannerError as exc:
            raise HTTPException(400, str(exc)) from exc
        plan.items = items
        await session.commit()
        return {"data": plan_out(plan)}


@router.post("/{project_id}/plans/{plan_id}/confirm")
async def confirm_plan(project_id: str, plan_id: str) -> dict[str, Any]:
    a = api()
    async with a.db.session() as session:
        p = await session.get(Project, uid(project_id))
        plan = (await session.execute(
            select(ProjectPlan).where(ProjectPlan.id == uid(plan_id)).with_for_update()
        )).scalar_one_or_none()
        if not p or p.deleted_at is not None or not plan or plan.project_id != p.id:
            raise HTTPException(404, "项目或规划不存在")
        if plan.status != "draft":
            raise HTTPException(409, "规划已经确认")
        if not p.product_snapshot.get("sources") or any(
            item["sku_ref"] not in p.product_snapshot["sources"] for item in plan.items
        ):
            raise HTTPException(400, "每个选中 SKU 需要先上传真实商品原图")
        if any(not fact.get("confirmed") for fact in p.product_snapshot["facts"].values()):
            raise HTTPException(400, "商品事实须先由商家确认")
        try:
            verified_items = validate_items(plan.items, {s["id"] for s in p.product_snapshot["skus"]})
        except PlannerError as exc:
            raise HTTPException(400, str(exc)) from exc
        if len(verified_items) != len(plan.items):
            raise HTTPException(400, "交付清单存在无效项")
        plan.status = "confirmed"
        plan.confirmed_at = utcnow()
        p.status = "confirmed"
        await session.commit()
        return {"data": plan_out(plan)}


@router.post("/{project_id}/runs", status_code=201)
async def create_run(project_id: str, payload: WorkflowCreate) -> dict[str, Any]:
    a = api()
    if len(str(payload.graph)) > 30000:
        raise HTTPException(400, "工作流配置过大")
    try:
        validate_workflow(payload.graph.get("nodes", []), payload.graph.get("edges", []))
    except WorkflowValidationError as exc:
        raise HTTPException(400, str(exc)) from exc
    for node in payload.graph["nodes"]:
        if node["type"] in {"generate", "refine"}:
            workflow_model(node)
    async with a.db.session() as session:
        p = await session.get(Project, uid(project_id))
        plan = (await session.execute(
            select(ProjectPlan).where(ProjectPlan.id == uid(payload.plan_id)).with_for_update()
        )).scalar_one_or_none()
        if not p or p.deleted_at is not None or not plan or plan.project_id != p.id or plan.status != "confirmed":
            raise HTTPException(409, "必须先确认本项目的任务清单")
        for node in payload.graph["nodes"]:
            if node["type"] not in {"generate", "refine"}:
                continue
            key, channel_id = workflow_model(node)
            if channel_id:
                if a.channel_service is None:
                    raise HTTPException(503, "渠道服务不可用")
                from apps.providers.channels import dynamic_spec
                channel = await a.channel_service.get(channel_id)
                spec = dynamic_spec(channel, key)
            else:
                spec = a.registry.get_model(key)
            if channel_id and not supports_channel_image(channel, key):
                raise HTTPException(400, "渠道停用或未同步该生图模型")
            if spec.kind != "image" or not spec.capabilities.image_input:
                raise HTTPException(400, "生图节点模型必须支持商品参考图")
        item = next((i for i in plan.items if i["id"] == payload.item_id), None)
        if not item:
            raise HTTPException(404, "交付项不存在")
        if (await session.execute(select(WorkflowRun.id).where(WorkflowRun.plan_id == plan.id,
            WorkflowRun.item_id == payload.item_id).limit(1))).first():
            raise HTTPException(409, "该交付项已有工作流；请继续原运行")
        if item["sku_ref"] not in p.product_snapshot.get("sources", {}):
            raise HTTPException(400, "该 SKU 缺少真实商品原图")
        run = WorkflowRun(project_id=p.id, plan_id=plan.id, sku_ref=item["sku_ref"],
                          item_id=item["id"], graph=payload.graph, context={})
        session.add(run)
        await session.commit()
        await session.refresh(run)
        return {"data": await run_out_with_asset(session, run, a)}


@router.get("/{project_id}/runs/{run_id}")
async def get_run(project_id: str, run_id: str) -> dict[str, Any]:
    a = api()
    async with a.db.session() as session:
        run = await session.get(WorkflowRun, uid(run_id))
        if not run or run.project_id != uid(project_id):
            raise HTTPException(404, "工作流不存在")
        steps = (await session.execute(select(WorkflowStep).where(WorkflowStep.run_id == run.id)
                 .order_by(WorkflowStep.created_at))).scalars().all()
        return {"data": {**(await run_out_with_asset(session, run, a)), "steps": [{"node_id": s.node_id, "status": s.status,
            "attempt": s.attempt, "result": s.result} for s in steps]}}


@router.post("/{project_id}/runs/{run_id}/advance")
async def advance_run(project_id: str, run_id: str, gate: GateResult | None = None) -> dict[str, Any]:
    """Advance one step; failures pause for billing reconciliation."""
    a = api()
    from .schemas import GenerateImageRequest
    from .main import _generate_image
    rid, pid = uid(run_id), uid(project_id)
    async with a.db.session() as session:
        run = (await session.execute(select(WorkflowRun).where(WorkflowRun.id == rid).with_for_update())).scalar_one_or_none()
        if not run or run.project_id != pid:
            raise HTTPException(404, "工作流不存在")
        if gate is None or gate.expected_cursor != run.cursor:
            raise HTTPException(409, "工作流游标已变化，请刷新状态后重试")
        if run.status == "running":
            raise HTTPException(409, "节点正在执行；需要人工核对任务流水，不能自动重试")
        if run.status == "succeeded":
            return {"data": await run_out_with_asset(session, run, a)}
        if run.status == "failed":
            raise HTTPException(409, "失败节点需先核对计费流水，当前不允许自动重试")
        order = validate_workflow(run.graph["nodes"], run.graph["edges"])
        if run.cursor < 0 or run.cursor >= len(order):
            raise HTTPException(409, "工作流游标异常，需人工核查")
        node = next(n for n in run.graph["nodes"] if n["id"] == order[run.cursor])
        kind = node["type"]
        project = await session.get(Project, pid)
        if not project or project.deleted_at is not None:
            raise HTTPException(404, "项目不存在")
        plan = await session.get(ProjectPlan, run.plan_id)
        item = next(i for i in plan.items if i["id"] == run.item_id)
        context = dict(run.context or {})
        if kind in {"quality_check", "human_review"}:
            if gate is None or not gate.approved or not gate.note.strip():
                raise HTTPException(409, f"{kind} 节点需要人工确认及备注")
            if kind == "quality_check" and not all(gate.checks.get(k) is True for k in
                ("product_identity", "text_accuracy", "compliance")):
                raise HTTPException(409, "商品一致性、文字与合规三项必须通过")
            await require_current_asset(session, context)
        if kind == "source":
            source_id = project.product_snapshot["sources"].get(run.sku_ref)
            source = await session.get(Asset, uid(source_id)) if source_id else None
            if not source:
                raise HTTPException(409, "原图不存在")
            context["current_asset_id"] = str(source.id)
        elif kind in {"generate", "refine"}:
            model, channel_id = workflow_model(node)
            if channel_id:
                if a.channel_service is None:
                    raise HTTPException(503, "渠道服务不可用")
                channel = await a.channel_service.get(channel_id)
                from apps.providers.channels import dynamic_spec
                spec = dynamic_spec(channel, model)
            else:
                spec = a.registry.get_model(model)
            if channel_id and not supports_channel_image(channel, model):
                raise HTTPException(400, "渠道已停用或不再提供该生图模型")
            if spec.kind != "image" or not spec.capabilities.image_input:
                raise HTTPException(400, "该模型未声明参考图能力，不能执行商品一致性生图")
            source_asset = await require_current_asset(session, context)
            raw = await asyncio.to_thread(a.storage.read, source_asset.object_key)
            image_ref = "data:" + source_asset.mime + ";base64," + base64.b64encode(raw).decode()
            instruction = str(node.get("data", {}).get("instruction") or "")[:400]
            prompt = item["prompt"] if kind == "generate" else "仅调整背景/光线/画面瑕疵，保持商品原貌。" + instruction
            request = GenerateImageRequest(model=model, channel_id=channel_id, prompt=prompt,
                n=1, size=item.get("size") or "1024x1024", images=[image_ref])
            trace = {"project_id": project_id, "plan_id": str(run.plan_id),
                     "run_id": run_id, "item_id": run.item_id, "sku_ref": run.sku_ref,
                     "node_id": node["id"]}
            run.status = "running"
            run.cursor += 1
            await session.commit()
            try:
                generated = await _generate_image(request, workflow_trace=trace)
                if isinstance(generated, JSONResponse):
                    raise HTTPException(502, "生图渠道返回错误，已停止工作流")
                if not generated.images or not generated.images[0].asset_id or not generated.persisted:
                    raise HTTPException(502, "生成结果或计费流水没有可靠落库，已停止工作流")
            except Exception as exc:
                run.cursor -= 1
                session.add(WorkflowStep(run_id=rid, node_id=node["id"], status="failed",
                    result={"error": str(exc)[:400]}, finished_at=utcnow()))
                run.status = "failed"
                await session.commit()
                raise
            context["current_asset_id"] = generated.images[0].asset_id
            context["last_job_id"] = generated.id
        elif kind == "deliver":
            context["delivered_asset_id"] = str((await require_current_asset(session, context)).id)
        result = {"kind": kind, "asset_id": context.get("current_asset_id")}
        if kind in {"quality_check", "human_review"}:
            result.update({"note": gate.note, "checks": gate.checks})
        attempt = (await session.execute(select(func.count()).select_from(WorkflowStep).where(
            WorkflowStep.run_id == rid, WorkflowStep.node_id == node["id"]))).scalar_one() + 1
        session.add(WorkflowStep(run_id=rid, node_id=node["id"], attempt=attempt, status="succeeded",
                                 result=result, finished_at=utcnow()))
        run.context = context
        if kind not in {"generate", "refine"}:
            run.cursor += 1
        run.status = "succeeded" if run.cursor == len(order) else "pending"
        await session.commit()
        return {"data": await run_out_with_asset(session, run, a)}


@router.delete("/{project_id}")
async def delete_project(project_id: str) -> dict[str, Any]:
    """软删项目；不动物料与计费流水。"""
    a = api()
    async with a.db.session() as session:
        p = await session.get(Project, uid(project_id))
        if not p or p.deleted_at is not None:
            raise HTTPException(404, "项目不存在")
        p.deleted_at = utcnow()
        await session.commit()
        return {"data": {"id": str(p.id), "deleted_at": p.deleted_at.isoformat()}}


@router.post("/{project_id}/restore")
async def restore_project(project_id: str) -> dict[str, Any]:
    a = api()
    async with a.db.session() as session:
        p = await session.get(Project, uid(project_id))
        if not p:
            raise HTTPException(404, "项目不存在")
        p.deleted_at = None
        p.updated_at = utcnow()
        await session.commit()
        return {"data": project_out(p)}
