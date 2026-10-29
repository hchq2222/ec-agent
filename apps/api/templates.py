"""版式模板 API：增删改查、软删除恢复、内置模板重置。

删除一律软删；没有任何物理删除入口。
"""
from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Body, HTTPException
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from apps.core.models import LayoutTemplate, utcnow
from apps.core.templates import BUILTIN_TEMPLATES, TemplateError, normalize_template

router = APIRouter(prefix="/api/templates", tags=["templates"])


def api() -> Any:
    from . import main
    if main.db is None:
        raise HTTPException(503, "模板模块需要 DATABASE_URL")
    return main


def uid(value: str) -> uuid.UUID:
    try:
        return uuid.UUID(value)
    except (ValueError, TypeError, AttributeError) as exc:
        raise HTTPException(404, "模板不存在") from exc


def template_out(t: LayoutTemplate) -> dict[str, Any]:
    return {
        "id": str(t.id),
        "name": t.name,
        "category": t.category,
        "size_w": t.size_w,
        "size_h": t.size_h,
        "subject_ratio": t.subject_ratio,
        "shadow": t.shadow,
        "background": t.background,
        "text_slots": t.text_slots or [],
        "logo_slot": t.logo_slot or {},
        "ref_image": t.ref_image or "",
        "builtin": t.builtin,
        "builtin_key": t.builtin_key,
        "version": t.version,
        "created_at": t.created_at.isoformat() if t.created_at else None,
        "updated_at": t.updated_at.isoformat() if t.updated_at else None,
        "deleted_at": t.deleted_at.isoformat() if t.deleted_at else None,
    }


async def seed_builtins_if_empty(session: Any) -> None:
    """表从未初始化过时补内置模板；用户删空后不会自动回填。"""
    total = await session.scalar(select(func.count()).select_from(LayoutTemplate))
    if total:
        return
    for item in BUILTIN_TEMPLATES:
        session.add(LayoutTemplate(**item, builtin=True))
    try:
        await session.commit()
    except IntegrityError:
        # 并发首次初始化时由唯一索引兜底
        await session.rollback()


@router.get("")
async def list_templates(category: str | None = None) -> dict[str, Any]:
    a = api()
    async with a.db.session() as session:
        await seed_builtins_if_empty(session)
        stmt = select(LayoutTemplate).where(LayoutTemplate.deleted_at.is_(None))
        if category:
            stmt = stmt.where(LayoutTemplate.category.in_(["all", category]))
        stmt = stmt.order_by(LayoutTemplate.builtin.desc(), LayoutTemplate.created_at)
        rows = (await session.execute(stmt)).scalars().all()
        return {"data": [template_out(t) for t in rows]}


@router.post("", status_code=201)
async def create_template(payload: dict = Body(...)) -> dict[str, Any]:
    a = api()
    try:
        fields = normalize_template(payload)
    except TemplateError as exc:
        raise HTTPException(400, str(exc)) from exc
    async with a.db.session() as session:
        t = LayoutTemplate(**fields, builtin=False, builtin_key=None)
        session.add(t)
        await session.commit()
        await session.refresh(t)
        return {"data": template_out(t)}


@router.post("/restore-builtins")
async def restore_builtins() -> dict[str, Any]:
    """按内置定义恢复：缺失则新增，已删或改过的内置模板重置为默认值。"""
    a = api()
    async with a.db.session() as session:
        restored = 0
        for item in BUILTIN_TEMPLATES:
            t = (await session.execute(
                select(LayoutTemplate).where(LayoutTemplate.builtin_key == item["builtin_key"])
            )).scalars().first()
            if t is None:
                session.add(LayoutTemplate(**item, builtin=True))
            else:
                for key, value in item.items():
                    setattr(t, key, value)
                t.builtin = True
                t.ref_image = ""
                t.deleted_at = None
                t.updated_at = utcnow()
                t.version += 1
            restored += 1
        await session.commit()
        return {"data": {"restored": restored}}


@router.patch("/{template_id}")
async def update_template(template_id: str, payload: dict = Body(...)) -> dict[str, Any]:
    a = api()
    async with a.db.session() as session:
        t = await session.get(LayoutTemplate, uid(template_id))
        if not t or t.deleted_at is not None:
            raise HTTPException(404, "模板不存在")
        try:
            fields = normalize_template(payload, existing=template_out(t))
        except TemplateError as exc:
            raise HTTPException(400, str(exc)) from exc
        for key, value in fields.items():
            setattr(t, key, value)
        t.version += 1
        t.updated_at = utcnow()
        await session.commit()
        return {"data": template_out(t)}


@router.delete("/{template_id}")
async def delete_template(template_id: str) -> dict[str, Any]:
    a = api()
    async with a.db.session() as session:
        t = await session.get(LayoutTemplate, uid(template_id))
        if not t or t.deleted_at is not None:
            raise HTTPException(404, "模板不存在")
        t.deleted_at = utcnow()
        await session.commit()
        return {"data": template_out(t)}


@router.post("/{template_id}/restore")
async def restore_template(template_id: str) -> dict[str, Any]:
    a = api()
    async with a.db.session() as session:
        t = await session.get(LayoutTemplate, uid(template_id))
        if not t:
            raise HTTPException(404, "模板不存在")
        t.deleted_at = None
        t.updated_at = utcnow()
        await session.commit()
        return {"data": template_out(t)}
