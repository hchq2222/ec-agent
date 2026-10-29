"""素材版本与审核：交付生成版本，人工确认/驳回/删除/回滚。"""
from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select

from apps.core.models import AssetVersion, CanvasProject, utcnow

from .canvas import out as project_out, reset_review_states

router = APIRouter(prefix="/api/versions", tags=["versions"])
STATUSES = {"待确认", "已确认", "已驳回"}


def api() -> Any:
    from . import main

    if main.db is None:
        raise HTTPException(503, "素材版本需要 DATABASE_URL")
    return main


def uid(value: str) -> uuid.UUID:
    try:
        return uuid.UUID(value)
    except (ValueError, TypeError, AttributeError) as exc:
        raise HTTPException(404, "版本不存在") from exc


class VersionIn(BaseModel):
    product_ref: str = Field(default="", max_length=128)
    project_id: str | None = Field(default=None, max_length=64)
    title: str = Field(default="", max_length=256)
    images: list[str] = Field(default_factory=list, min_length=1)
    note: str = Field(default="", max_length=2000)


class VersionPatch(BaseModel):
    status: str | None = None


def out(v: AssetVersion) -> dict[str, Any]:
    return {
        "id": str(v.id),
        "product_ref": v.product_ref,
        "project_id": v.project_id,
        "title": v.title,
        "status": v.status,
        "images": v.images or [],
        "note": v.note,
        "confirmed_at": v.confirmed_at.isoformat() if v.confirmed_at else None,
        "deleted_at": v.deleted_at.isoformat() if v.deleted_at else None,
        "created_at": v.created_at.isoformat() if v.created_at else None,
    }


@router.get("")
async def list_versions(product_ref: str | None = None, include_deleted: int = 0) -> dict[str, Any]:
    a = api()
    async with a.db.session() as session:
        stmt = select(AssetVersion)
        if not include_deleted:
            stmt = stmt.where(AssetVersion.deleted_at.is_(None))
        if product_ref:
            stmt = stmt.where(AssetVersion.product_ref == product_ref)
        rows = (await session.execute(stmt.order_by(AssetVersion.created_at.desc()).limit(200))).scalars().all()
        return {"data": [out(v) for v in rows]}


@router.post("", status_code=201)
async def create_version(payload: VersionIn) -> dict[str, Any]:
    a = api()
    images = [u for u in payload.images if u]
    if not images:
        raise HTTPException(400, "至少需要一张图片")
    async with a.db.session() as session:
        v = AssetVersion(product_ref=payload.product_ref, project_id=payload.project_id,
                         title=payload.title, status="待确认", images=images, note=payload.note)
        session.add(v)
        await session.commit()
        await session.refresh(v)
        return {"data": out(v)}


@router.patch("/{version_id}")
async def update_version(version_id: str, payload: VersionPatch) -> dict[str, Any]:
    a = api()
    async with a.db.session() as session:
        v = await session.get(AssetVersion, uid(version_id))
        if not v or v.deleted_at is not None:
            raise HTTPException(404, "版本不存在")
        if payload.status is not None:
            if payload.status not in STATUSES:
                raise HTTPException(400, "status 只能是 待确认、已确认、已驳回")
            v.status = payload.status
            v.confirmed_at = utcnow() if payload.status == "已确认" else None
        await session.commit()
        return {"data": out(v)}


@router.delete("/{version_id}")
async def delete_version(version_id: str) -> dict[str, Any]:
    """人工删除版本（软删），不影响项目。"""
    a = api()
    async with a.db.session() as session:
        v = await session.get(AssetVersion, uid(version_id))
        if not v or v.deleted_at is not None:
            raise HTTPException(404, "版本不存在")
        v.deleted_at = utcnow()
        await session.commit()
        return {"data": out(v)}


@router.post("/{version_id}/rollback")
async def rollback_version(version_id: str) -> dict[str, Any]:
    """回滚：删除当前版本与审核，项目恢复到进行中，确认/交付节点重置。"""
    a = api()
    async with a.db.session() as session:
        v = await session.get(AssetVersion, uid(version_id))
        if not v or v.deleted_at is not None:
            raise HTTPException(404, "版本不存在")
        v.deleted_at = utcnow()
        project = None
        if v.project_id:
            project = await session.get(CanvasProject, v.project_id)
            if project and project.deleted_at is None:
                project.status = "active"
                project.hidden = False
                project.delivered_at = None
                project.canvas = reset_review_states(dict(project.canvas or {}))
                project.updated_at = utcnow()
        await session.commit()
        return {"data": {"version": out(v), "project": project_out(project) if project else None}}
