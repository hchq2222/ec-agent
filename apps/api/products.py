"""商品档案 API：对话识别、草稿编辑、图片证据与确认入库。"""
from __future__ import annotations

import mimetypes
import uuid
from typing import Any

from fastapi import APIRouter, File, Form, HTTPException, Response, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import delete, select

from apps.core.models import Product, ProductAttr, ProductImage, ProductMessage, ProductSKU, utcnow
from apps.core.product_intake import ProductIntakeError, extract_product
from apps.core.storage import build_object_key

router = APIRouter(prefix="/api/products", tags=["products"])
MAX_FILE = 10 * 1024 * 1024
MAX_TOTAL = 40 * 1024 * 1024


def api() -> Any:
    from . import main
    if main.db is None:
        raise HTTPException(503, "商品模块需要 DATABASE_URL")
    return main


def uid(value: str) -> uuid.UUID:
    try:
        return uuid.UUID(value)
    except (ValueError, TypeError, AttributeError) as exc:
        raise HTTPException(404, "商品不存在") from exc


async def product_out(session: Any, p: Product, *, include_messages: bool = False) -> dict[str, Any]:
    attrs = (await session.execute(
        select(ProductAttr).where(ProductAttr.product_id == p.id).order_by(ProductAttr.created_at)
    )).scalars().all()
    skus = (await session.execute(
        select(ProductSKU).where(ProductSKU.product_id == p.id).order_by(ProductSKU.created_at)
    )).scalars().all()
    images = (await session.execute(
        select(ProductImage).where(ProductImage.product_id == p.id).order_by(ProductImage.created_at)
    )).scalars().all()
    data: dict[str, Any] = {
        "id": str(p.id),
        "name": p.name,
        "category": p.category,
        "brand": p.brand,
        "code": p.code,
        "pitch": p.pitch,
        "status": p.status,
        "source": p.source,
        "created_at": p.created_at.isoformat() if p.created_at else None,
        "updated_at": p.updated_at.isoformat() if p.updated_at else None,
        "attrs": [{
            "key": a.key, "value": a.value, "unit": a.unit, "source": a.source,
            "confirmed": a.confirmed,
            "confidence": float(a.confidence) if a.confidence is not None else None,
        } for a in attrs],
        "skus": [{
            "id": str(s.id), "color": s.color, "spec": s.spec,
            "price": round(s.price_cents / 100, 2) if s.price_cents is not None else None,
        } for s in skus],
        "images": [{
            "id": str(i.id), "url": f"/api/products/{p.id}/images/{i.id}/content",
            "mime": i.mime, "kind": i.kind,
        } for i in images],
    }
    if include_messages:
        messages = (await session.execute(
            select(ProductMessage).where(ProductMessage.product_id == p.id)
            .order_by(ProductMessage.created_at)
        )).scalars().all()
        data["messages"] = [{
            "id": str(m.id), "role": m.role, "content": m.content,
            "attachments": m.attachments,
        } for m in messages]
    return data


async def load_product(session: Any, product_id: uuid.UUID) -> Product:
    p = await session.get(Product, product_id)
    if not p:
        raise HTTPException(404, "商品不存在")
    return p


class AttrInput(BaseModel):
    key: str = Field(min_length=1, max_length=80)
    value: str = Field(max_length=1000)
    unit: str = Field(default="", max_length=32)
    source: str = Field(default="human", max_length=16)
    confirmed: bool = True
    confidence: float | None = None


class SKUInput(BaseModel):
    id: str | None = None
    color: str = Field(default="默认", max_length=80)
    spec: str = Field(default="默认", max_length=80)
    price: float | None = Field(default=None, ge=0, le=10000000)


class ProductUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=256)
    category: str | None = None
    brand: str | None = Field(default=None, max_length=128)
    code: str | None = Field(default=None, max_length=64)
    pitch: str | None = Field(default=None, max_length=2000)
    status: str | None = None
    attrs: list[AttrInput] | None = None
    skus: list[SKUInput] | None = None


@router.post("/intake")
async def intake(
    message: str = Form(default=""),
    product_id: str | None = Form(default=None),
    files: list[UploadFile] = File(default=[]),
) -> dict[str, Any]:
    a = api()
    if a.storage is None:
        raise HTTPException(503, "未配置文件存储")
    if len(message) > 20000:
        raise HTTPException(400, "消息过长，请分批发送")
    files = files or []
    raw_files: list[tuple[str, str, bytes]] = []
    total = 0
    for f in files:
        data = await f.read()
        total += len(data)
        if len(data) > MAX_FILE:
            raise HTTPException(400, f"文件过大：{f.filename}")
        if total > MAX_TOTAL:
            raise HTTPException(400, "附件总大小超过 40MB")
        mime = (f.content_type or "").split(";")[0] or mimetypes.guess_type(f.filename or "")[0] or ""
        if (f.filename or "").lower().endswith(".pdf") and not mime.startswith("image/"):
            mime = "application/pdf"
        if not (mime.startswith("image/") or mime == "application/pdf"):
            raise HTTPException(400, f"只支持图片和 PDF：{f.filename}")
        raw_files.append((f.filename or "file", mime, data))

    async with a.db.session() as session:
        existing = None
        p = None
        if product_id:
            p = await load_product(session, uid(product_id))
            existing = await product_out(session, p)
        try:
            draft = await extract_product(
                message=message, files=raw_files, existing=existing, database=a.db,
            )
        except ProductIntakeError as exc:
            raise HTTPException(422, str(exc)) from exc

        if p is None:
            p = Product(
                name=draft["name"] or "未命名商品",
                category=draft["category"],
                brand=draft["brand"],
                pitch=draft["pitch"],
                status="draft",
                source="ai_intake",
            )
            session.add(p)
            await session.commit()
            await session.refresh(p)
        else:
            if draft["name"]:
                p.name = draft["name"]
            if draft["brand"]:
                p.brand = draft["brand"]
            if draft["pitch"]:
                p.pitch = draft["pitch"]
            if draft["category"]:
                p.category = draft["category"]
            p.status = "draft"
            await session.commit()

        old_attrs = (await session.execute(
            select(ProductAttr).where(ProductAttr.product_id == p.id)
        )).scalars().all()
        by_key = {x.key: x for x in old_attrs}
        for item in draft["attrs"]:
            row = by_key.get(item["key"])
            if row:
                row.value = item["value"]
                row.unit = item["unit"]
                row.source = "ai"
                row.confirmed = False
                row.confidence = item["confidence"]
            else:
                session.add(ProductAttr(
                    product_id=p.id, key=item["key"], value=item["value"], unit=item["unit"],
                    source="ai", confirmed=False, confidence=item["confidence"],
                ))
        if draft["skus"]:
            old_skus = (await session.execute(
                select(ProductSKU).where(ProductSKU.product_id == p.id)
            )).scalars().all()
            by_sku = {(s.color, s.spec): s for s in old_skus}
            for item in draft["skus"]:
                row = by_sku.get((item["color"], item["spec"]))
                cents = int(round(item["price"] * 100)) if item["price"] is not None else None
                if row:
                    if cents is not None:
                        row.price_cents = cents
                else:
                    session.add(ProductSKU(
                        product_id=p.id, color=item["color"], spec=item["spec"], price_cents=cents,
                    ))

        for filename, mime, data in raw_files:
            image_id = uuid.uuid4()
            ext = (filename.rsplit(".", 1)[-1] if "." in filename else
                   ("pdf" if mime == "application/pdf" else "png")).lower()[:8]
            key = build_object_key(asset_id=str(image_id), ext=ext, variant="product-source")
            try:
                a.storage.put(key, data, mime)
            except Exception as exc:
                raise HTTPException(502, f"资料上传失败：{exc}") from exc
            session.add(ProductImage(
                id=image_id, product_id=p.id, object_key=key, mime=mime,
                bytes=len(data), kind="document" if mime == "application/pdf" else "source",
            ))

        session.add(ProductMessage(
            product_id=p.id, role="user", content=message.strip(),
            attachments=[f[0] for f in raw_files],
        ))
        reply = draft["reply"]
        session.add(ProductMessage(
            product_id=p.id, role="assistant", content=reply, attachments=[],
        ))
        await session.commit()
        data = await product_out(session, p, include_messages=True)
        return {"data": data, "reply": reply, "missing": draft["missing"], "usage": draft["usage"]}


@router.get("")
async def list_products() -> dict[str, Any]:
    a = api()
    async with a.db.session() as session:
        rows = (await session.execute(
            select(Product).order_by(Product.updated_at.desc()).limit(100)
        )).scalars().all()
        return {"data": [await product_out(session, p) for p in rows]}


@router.get("/{product_id}")
async def get_product(product_id: str) -> dict[str, Any]:
    a = api()
    async with a.db.session() as session:
        p = await load_product(session, uid(product_id))
        return {"data": await product_out(session, p, include_messages=True)}


@router.patch("/{product_id}")
async def update_product(product_id: str, payload: ProductUpdate) -> dict[str, Any]:
    a = api()
    async with a.db.session() as session:
        p = await load_product(session, uid(product_id))
        if payload.name is not None:
            p.name = payload.name.strip()
        if payload.category is not None:
            if payload.category not in {"beauty", "home", "digital"}:
                raise HTTPException(400, "品类只能是 beauty、home、digital")
            p.category = payload.category
        if payload.brand is not None:
            p.brand = payload.brand.strip()
        if payload.code is not None:
            p.code = payload.code.strip()
        if payload.pitch is not None:
            p.pitch = payload.pitch.strip()
        if payload.status is not None:
            if payload.status not in {"draft", "confirmed"}:
                raise HTTPException(400, "status 只能是 draft 或 confirmed")
            p.status = payload.status
        if payload.attrs is not None:
            await session.execute(delete(ProductAttr).where(ProductAttr.product_id == p.id))
            for item in payload.attrs:
                if not item.key.strip():
                    continue
                session.add(ProductAttr(
                    product_id=p.id, key=item.key.strip(), value=item.value,
                    unit=item.unit, source=item.source if item.source in {"ai", "human"} else "human",
                    confirmed=item.confirmed, confidence=item.confidence,
                ))
        if payload.skus is not None:
            await session.execute(delete(ProductSKU).where(ProductSKU.product_id == p.id))
            for item in payload.skus:
                sid = uuid.uuid4()
                if item.id:
                    try:
                        sid = uuid.UUID(item.id)
                    except ValueError:
                        pass
                session.add(ProductSKU(
                    id=sid, product_id=p.id, color=item.color.strip() or "默认",
                    spec=item.spec.strip() or "默认",
                    price_cents=int(round(item.price * 100)) if item.price is not None else None,
                ))
        p.updated_at = utcnow()
        await session.commit()
        return {"data": await product_out(session, p, include_messages=True)}


@router.post("/{product_id}/confirm")
async def confirm_product(product_id: str) -> dict[str, Any]:
    a = api()
    async with a.db.session() as session:
        p = await load_product(session, uid(product_id))
        p.status = "confirmed"
        p.updated_at = utcnow()
        await session.commit()
        return {"data": await product_out(session, p, include_messages=True)}


@router.get("/{product_id}/images/{image_id}/content")
async def image_content(product_id: str, image_id: str) -> Response:
    a = api()
    if a.storage is None:
        raise HTTPException(503, "未配置文件存储")
    async with a.db.session() as session:
        img = await session.get(ProductImage, uid(image_id))
        if not img or str(img.product_id) != str(uid(product_id)):
            raise HTTPException(404, "图片不存在")
    try:
        data = a.storage.read(img.object_key)
    except Exception as exc:
        raise HTTPException(404, f"图片读取失败：{exc}") from exc
    return Response(content=data, media_type=img.mime or "application/octet-stream")
