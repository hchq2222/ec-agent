"""商品对话录入：把文字、商品图和资料页交给多模态模型，整理成结构化草稿。

原则：只提取资料里出现的事实，不补全；所有 AI 提取字段都标记为待确认。
"""
from __future__ import annotations

import base64
import json
import re
import uuid
from typing import Any

import httpx

from apps.core.config import get_settings
from apps.core.models import Job, ProviderCall, utcnow

CATEGORY_ALIASES = {
    "beauty": "beauty", "美妆": "beauty", "美妆个护": "beauty", "个护": "beauty",
    "home": "home", "家居": "home", "家居日用": "home",
    "digital": "digital", "3c": "digital", "3C": "digital", "3C 配件": "digital", "3C配件": "digital", "数码": "digital",
}

SYSTEM_PROMPT = """你是电商商品资料录入员。你的唯一任务是从用户提供的文字、商品图片、包装图、手册页中提取商品事实，整理成商品档案草稿。

硬性规则：
- 只能使用资料中明确出现的信息；没有出现的信息不要猜测、不要补全、不要写“通常/一般/可能”。
- 价格、容量、成分、材质、型号、接口、认证、适用说明等事实必须来自资料。
- 图片中看不清的文字不要编造；无法确认的字段放进 missing。
- 输出必须是单个 JSON 对象，不要 Markdown、不要解释性前后缀。

JSON 结构：
{
  "name": "商品名称，没有就留空",
  "category": "beauty | home | digital",
  "brand": "品牌，没有就留空",
  "pitch": "1-2 句卖点，只基于资料",
  "attrs": [
    {"key": "容量", "value": "150", "unit": "ml", "confidence": 0.95}
  ],
  "skus": [
    {"color": "青绿色", "spec": "150ml", "price": 129}
  ],
  "missing": ["资料中缺失、但建档需要的字段"],
  "reply": "用中文简短告诉用户整理结果和需要补充什么"
}

要求：
- category 只能是 beauty、home、digital 三者之一；无法判断时用 beauty。
- attrs 最多 40 项，key 用中文；value 用字符串；unit 可空；confidence 是 0-1。
- skus 最多 20 项；price 是人民币数字，未知填 null；没有 SKU 信息就返回空数组。
- missing 最多 20 项。
- 不要输出任何资料中没有的字段值。
"""


class ProductIntakeError(ValueError):
    pass


def _text(value: Any, limit: int) -> str:
    if value is None:
        return ""
    return str(value).strip()[:limit]


def _category(value: Any) -> str:
    return CATEGORY_ALIASES.get(str(value or "").strip(), "beauty")


def normalize(raw: Any, existing: dict[str, Any] | None = None) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ProductIntakeError("模型没有返回 JSON 对象")
    existing = existing or {}
    attrs: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in raw.get("attrs") or []:
        if not isinstance(item, dict):
            continue
        key = _text(item.get("key"), 80)
        value = _text(item.get("value"), 1000)
        if not key or not value or key in seen:
            continue
        seen.add(key)
        try:
            confidence = max(0.0, min(1.0, float(item.get("confidence"))))
        except (TypeError, ValueError):
            confidence = None
        attrs.append({"key": key, "value": value, "unit": _text(item.get("unit"), 32),
                      "confidence": confidence})
    skus: list[dict[str, Any]] = []
    seen_sku: set[tuple[str, str]] = set()
    for item in raw.get("skus") or []:
        if not isinstance(item, dict):
            continue
        color = _text(item.get("color"), 80) or "默认"
        spec = _text(item.get("spec"), 80) or "默认"
        if (color, spec) in seen_sku:
            continue
        seen_sku.add((color, spec))
        price = item.get("price")
        try:
            price = round(float(price), 2) if price not in (None, "") else None
        except (TypeError, ValueError):
            price = None
        skus.append({"color": color, "spec": spec, "price": price})
    return {
        "name": _text(raw.get("name"), 200) or existing.get("name", ""),
        "category": _category(raw.get("category") or existing.get("category")),
        "brand": _text(raw.get("brand"), 100) or existing.get("brand", ""),
        "pitch": _text(raw.get("pitch"), 1000) or existing.get("pitch", ""),
        "attrs": attrs[:40],
        "skus": skus[:20],
        "missing": [_text(x, 80) for x in (raw.get("missing") or []) if _text(x, 80)][:20],
        "reply": _text(raw.get("reply"), 1000) or "已整理出商品草稿，请核对字段后再入库。",
    }


def _json_content(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(str(part.get("text", "")) for part in content if isinstance(part, dict))
    return ""


def _extract_json(text: str) -> dict[str, Any]:
    clean = text.strip()
    if clean.startswith("```"):
        clean = re.sub(r"^```[a-zA-Z0-9_-]*\s*", "", clean)
        clean = re.sub(r"\s*```$", "", clean).strip()
    start, end = clean.find("{"), clean.rfind("}")
    if start < 0 or end <= start:
        raise ProductIntakeError("模型没有返回可解析的 JSON")
    try:
        data = json.loads(clean[start:end + 1])
    except json.JSONDecodeError as exc:
        raise ProductIntakeError("模型返回的 JSON 格式不正确") from exc
    if not isinstance(data, dict):
        raise ProductIntakeError("模型返回的不是 JSON 对象")
    return data


def _image_parts(files: list[tuple[str, str, bytes]]) -> list[dict[str, Any]]:
    parts: list[dict[str, Any]] = []
    for filename, mime, data in files[:6]:
        if mime.startswith("image/"):
            b64 = base64.b64encode(data).decode("ascii")
            parts.append({"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}})
            continue
        if mime == "application/pdf" or filename.lower().endswith(".pdf"):
            try:
                import fitz  # PyMuPDF
            except ImportError as exc:
                raise ProductIntakeError("PDF 识别需要服务端安装 PyMuPDF") from exc
            try:
                doc = fitz.open(stream=data, filetype="pdf")
            except Exception as exc:
                raise ProductIntakeError(f"PDF 打开失败：{exc}") from exc
            for page in doc[:4]:
                if len(parts) >= 6:
                    break
                pix = page.get_pixmap(matrix=fitz.Matrix(2, 2), alpha=False)
                png = pix.tobytes("png")
                b64 = base64.b64encode(png).decode("ascii")
                parts.append({"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}})
            if len(parts) >= 6:
                break
            continue
        raise ProductIntakeError(f"暂不支持的文件类型：{filename}")
    return parts


async def extract_product(
    *,
    message: str,
    files: list[tuple[str, str, bytes]],
    existing: dict[str, Any] | None,
    database: Any,
) -> dict[str, Any]:
    settings = get_settings()
    if not all((settings.planner_base_url, settings.planner_api_key, settings.planner_model)):
        raise ProductIntakeError("未配置 PLANNER_BASE_URL、PLANNER_API_KEY 或 PLANNER_MODEL")
    if not message.strip() and not files:
        raise ProductIntakeError("请发一段文字、商品图片或资料页")
    if len(files) > 6:
        raise ProductIntakeError("一次最多上传 6 个文件")

    existing_note = ""
    if existing:
        existing_note = (
            "\n\n已有商品草稿（用户可能已修改，优先保留用户已填写的内容；"
            "本次只更新用户新消息中明确提到的字段，未提到的字段保持不变）：\n"
            + json.dumps(existing, ensure_ascii=False)[:12000]
        )
    user_text = (
        "请从下面的资料中提取商品档案。资料可能包含文字、商品图、包装图或手册页。"
        "没有出现的信息不要猜测。\n\n用户消息：\n"
        + (message.strip() or "（只发了文件，请识别文件内容）")
        + existing_note
    )
    content: list[dict[str, Any]] = [{"type": "text", "text": user_text}]
    content.extend(_image_parts(files))

    job_id = uuid.uuid4()
    async with database.session() as session:
        session.add(Job(
            id=job_id, kind="product_intake", model_key=settings.planner_model,
            provider_id="planner", status="running", n=1,
            params={"product_id": (existing or {}).get("id"), "files": [f[0] for f in files]},
        ))
        await session.commit()

    body: dict[str, Any] = {}
    usage: dict[str, Any] = {}
    error: Exception | None = None
    raw: dict[str, Any] | None = None
    try:
        async with httpx.AsyncClient(timeout=120) as client:
            response = await client.post(
                settings.planner_base_url.rstrip("/") + "/chat/completions",
                headers={"Authorization": f"Bearer {settings.planner_api_key}"},
                json={
                    "model": settings.planner_model,
                    "temperature": 0.1,
                    "max_tokens": 3000,
                    "messages": [
                        {"role": "system", "content": SYSTEM_PROMPT},
                        {"role": "user", "content": content},
                    ],
                },
            )
            response.raise_for_status()
            body = response.json()
        usage = body.get("usage") if isinstance(body.get("usage"), dict) else {}
        answer = _json_content(body["choices"][0]["message"].get("content"))
        raw = _extract_json(answer)
    except Exception as exc:
        error = exc

    status = "succeeded" if raw is not None else "failed"
    async with database.session() as session:
        job = await session.get(Job, job_id)
        if job:
            job.status = status
            job.finished_at = utcnow()
            if error:
                job.error_code = "PRODUCT_INTAKE_FAILED"
                job.error_message = str(error)[:2000]
        try:
            units = max(0, int(usage.get("total_tokens") or 0))
        except (TypeError, ValueError):
            units = 0
        session.add(ProviderCall(
            job_id=job_id, provider_id="planner", model_key=settings.planner_model,
            upstream_model=settings.planner_model, unit="token", units=units,
            unit_price=None, amount=None, currency="USD", status=status, usage=usage,
        ))
        await session.commit()

    if error or raw is None:
        raise ProductIntakeError(f"AI 识别失败：{error}")
    return {**normalize(raw, existing), "usage": usage}
