"""项目画布持久化：交付隐藏、回收站恢复、状态流转。"""
from __future__ import annotations

import copy
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm.attributes import flag_modified

from apps.core.models import CanvasProject, utcnow

router = APIRouter(prefix="/api/canvas-projects", tags=["canvas"])


def api() -> Any:
    from . import main

    if main.db is None:
        raise HTTPException(503, "项目画布需要 DATABASE_URL")
    return main


class CanvasIn(BaseModel):
    id: str = Field(min_length=1, max_length=64)
    product_ref: str = Field(default="", max_length=128)
    name: str = Field(default="", max_length=256)
    status: str = "active"
    hidden: bool = False
    canvas: dict[str, Any] = Field(default_factory=dict)


class CanvasPatch(BaseModel):
    name: str | None = None
    status: str | None = None
    hidden: bool | None = None
    canvas: dict[str, Any] | None = None
    base_updated_at: str | None = None


def out(p: CanvasProject) -> dict[str, Any]:
    return {
        "id": p.id,
        "product_ref": p.product_ref,
        "name": p.name,
        "status": p.status,
        "hidden": p.hidden,
        "canvas": p.canvas or {},
        "delivered_at": p.delivered_at.isoformat() if p.delivered_at else None,
        "deleted_at": p.deleted_at.isoformat() if p.deleted_at else None,
        "created_at": p.created_at.isoformat() if p.created_at else None,
        "updated_at": p.updated_at.isoformat() if p.updated_at else None,
    }


def reset_review_states(canvas: dict[str, Any]) -> dict[str, Any]:
    canvas = copy.deepcopy(canvas or {})
    runs = canvas.get("runs") or {}
    targets = [n.get("id") for n in (canvas.get("nodes") or [])
               if n.get("type") in {"human_review", "deliver"}]
    for run in runs.values():
        if not isinstance(run, dict):
            continue
        states = run.get("states") or {}
        for nid in targets:
            if nid:
                states[nid] = "pending"
        run["states"] = states
    canvas["runs"] = runs
    return canvas


@router.get("")
async def list_projects(status: str = "active") -> dict[str, Any]:
    a = api()
    async with a.db.session() as session:
        stmt = select(CanvasProject)
        if status == "deleted":
            stmt = stmt.where(CanvasProject.deleted_at.is_not(None))
        else:
            stmt = stmt.where(CanvasProject.deleted_at.is_(None))
            if status == "delivered":
                stmt = stmt.where(CanvasProject.status == "delivered")
            elif status == "active":
                stmt = stmt.where(CanvasProject.hidden.is_(False), CanvasProject.status == "active")
        rows = (await session.execute(stmt.order_by(CanvasProject.updated_at.desc()))).scalars().all()
        return {"data": [out(p) for p in rows]}


@router.post("", status_code=201)
async def upsert_project(payload: CanvasIn) -> dict[str, Any]:
    """用于新建项目与 localStorage 首次迁移（按 id 幂等）。"""
    a = api()
    async with a.db.session() as session:
        p = await session.get(CanvasProject, payload.id)
        if p is None:
            p = CanvasProject(id=payload.id)
            session.add(p)
        p.product_ref = payload.product_ref
        p.name = payload.name
        p.status = payload.status
        p.hidden = payload.hidden
        p.canvas = payload.canvas
        flag_modified(p, "canvas")
        p.updated_at = utcnow()
        await session.commit()
        return {"data": out(p)}


@router.get("/{project_id}")
async def get_project(project_id: str) -> dict[str, Any]:
    a = api()
    async with a.db.session() as session:
        p = await session.get(CanvasProject, project_id)
        if not p or p.deleted_at is not None:
            raise HTTPException(404, "项目不存在")
        return {"data": out(p)}


@router.put("/{project_id}")
async def update_project(project_id: str, payload: CanvasPatch) -> dict[str, Any]:
    a = api()
    async with a.db.session() as session:
        p = await session.get(CanvasProject, project_id)
        if not p or p.deleted_at is not None:
            raise HTTPException(404, "项目不存在")
        if not payload.base_updated_at:
            raise HTTPException(409, "缺少 base_updated_at，请刷新项目后再保存")
        if payload.base_updated_at != p.updated_at.isoformat():
            raise HTTPException(409, "项目已在其它页面更新，请刷新后再试")
        if payload.name is not None:
            p.name = payload.name
        if payload.status is not None:
            if payload.status not in {"active", "delivered"}:
                raise HTTPException(400, "status 只能是 active 或 delivered")
            p.status = payload.status
            if payload.status == "delivered":
                p.hidden = True
                p.delivered_at = utcnow()
        if payload.hidden is not None:
            p.hidden = payload.hidden
        if payload.canvas is not None:
            p.canvas = payload.canvas
            flag_modified(p, "canvas")
        p.updated_at = utcnow()
        await session.commit()
        return {"data": out(p)}


@router.delete("/{project_id}")
async def delete_project(project_id: str) -> dict[str, Any]:
    a = api()
    async with a.db.session() as session:
        p = await session.get(CanvasProject, project_id)
        if not p or p.deleted_at is not None:
            raise HTTPException(404, "项目不存在")
        p.deleted_at = utcnow()
        p.status = "deleted"
        p.hidden = True
        await session.commit()
        return {"data": out(p)}


@router.post("/{project_id}/restore")
async def restore_project(project_id: str) -> dict[str, Any]:
    """回收站恢复：回到进行中，确认/交付节点重置为待运行；版本不删除。"""
    a = api()
    async with a.db.session() as session:
        p = await session.get(CanvasProject, project_id)
        if not p:
            raise HTTPException(404, "项目不存在")
        p.deleted_at = None
        p.status = "active"
        p.hidden = False
        p.delivered_at = None
        p.canvas = reset_review_states(p.canvas or {})
        flag_modified(p, "canvas")
        p.updated_at = utcnow()
        await session.commit()
        return {"data": out(p)}
