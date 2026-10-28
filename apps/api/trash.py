"""回收站：列出已软删的商品、项目、模板。

恢复接口在各自模块（/api/products/{id}/restore 等）；这里只读。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from sqlalchemy import select

from apps.core.models import LayoutTemplate, Product, Project

router = APIRouter(prefix="/api/trash", tags=["trash"])
TYPES = ("product", "project", "template")


def api() -> Any:
    from . import main
    if main.db is None:
        raise HTTPException(503, "回收站需要 DATABASE_URL")
    return main


def _item(kind: str, row: Any) -> dict[str, Any]:
    return {
        "type": kind,
        "id": str(row.id),
        "name": getattr(row, "name", ""),
        "deleted_at": row.deleted_at.isoformat() if row.deleted_at else None,
    }


@router.get("")
async def list_trash(type: str | None = None) -> dict[str, Any]:
    if type and type not in TYPES:
        raise HTTPException(400, "type 只能是 product、project、template")
    a = api()
    async with a.db.session() as session:
        buckets: dict[str, list[dict[str, Any]]] = {"product": [], "project": [], "template": []}
        if type in (None, "product"):
            rows = (await session.execute(
                select(Product).where(Product.deleted_at.is_not(None)).order_by(Product.deleted_at.desc())
            )).scalars().all()
            buckets["product"] = [_item("product", r) for r in rows]
        if type in (None, "project"):
            rows = (await session.execute(
                select(Project).where(Project.deleted_at.is_not(None)).order_by(Project.deleted_at.desc())
            )).scalars().all()
            buckets["project"] = [_item("project", r) for r in rows]
        if type in (None, "template"):
            rows = (await session.execute(
                select(LayoutTemplate).where(LayoutTemplate.deleted_at.is_not(None))
                .order_by(LayoutTemplate.deleted_at.desc())
            )).scalars().all()
            buckets["template"] = [_item("template", r) for r in rows]
        data = [item for items in buckets.values() for item in items]
        data.sort(key=lambda x: x["deleted_at"] or "", reverse=True)
        return {"data": data, "groups": buckets,
                "counts": {k: len(v) for k, v in buckets.items()}}
