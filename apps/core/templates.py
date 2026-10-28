"""版式模板：内置定义与参数校验。

模板只描述版式（尺寸、主体占比、阴影、文字槽位、Logo 位），不包含品牌色/字体库。
项目节点创建时把模板快照进节点参数，之后改模板不影响已建项目。
"""
from __future__ import annotations

from typing import Any

TEMPLATE_CATEGORIES = {"all", "beauty", "home", "digital"}
TEMPLATE_SHADOWS = {"soft", "none", "hard"}
MAX_TEXT_SLOTS = 8

#: 内置模板。删除后可通过 POST /api/templates/restore-builtins 恢复。
BUILTIN_TEMPLATES: list[dict[str, Any]] = [
    {
        "builtin_key": "white-main-800",
        "name": "白底主图 800",
        "category": "all",
        "size_w": 800,
        "size_h": 800,
        "subject_ratio": 0.85,
        "shadow": "soft",
        "background": "white",
        "text_slots": [],
        "logo_slot": {},
    },
    {
        "builtin_key": "text-left-800",
        "name": "左文右图 800",
        "category": "all",
        "size_w": 800,
        "size_h": 800,
        "subject_ratio": 0.62,
        "shadow": "soft",
        "background": "white",
        "text_slots": [
            {"id": "title", "text": "", "x": 0.06, "y": 0.10, "w": 0.42, "h": 0.16,
             "font_size": 46, "color": "#1f2430", "align": "left"},
            {"id": "subtitle", "text": "", "x": 0.06, "y": 0.28, "w": 0.42, "h": 0.10,
             "font_size": 24, "color": "#6b7280", "align": "left"},
        ],
        "logo_slot": {"x": 0.06, "y": 0.84, "w": 0.12, "h": 0.10},
    },
    {
        "builtin_key": "scene-square-1024",
        "name": "场景方图 1024",
        "category": "beauty",
        "size_w": 1024,
        "size_h": 1024,
        "subject_ratio": 0.80,
        "shadow": "soft",
        "background": "scene",
        "text_slots": [],
        "logo_slot": {},
    },
    {
        "builtin_key": "detail-3x4-900",
        "name": "细节图 3:4",
        "category": "all",
        "size_w": 900,
        "size_h": 1200,
        "subject_ratio": 0.78,
        "shadow": "none",
        "background": "white",
        "text_slots": [
            {"id": "caption", "text": "", "x": 0.08, "y": 0.86, "w": 0.84, "h": 0.10,
             "font_size": 28, "color": "#1f2430", "align": "center"},
        ],
        "logo_slot": {},
    },
]


class TemplateError(ValueError):
    """模板参数不合法。"""


def _bounded(value: Any, low: float, high: float, name: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise TemplateError(f"{name} 必须是数字") from exc
    if not low <= number <= high:
        raise TemplateError(f"{name} 需在 {low}–{high} 之间")
    return number


def _slot(raw: Any, index: int) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise TemplateError(f"文字槽位 {index + 1} 必须是对象")
    text = str(raw.get("text") or "")[:200]
    align = str(raw.get("align") or "left")
    if align not in {"left", "center", "right"}:
        align = "left"
    return {
        "id": str(raw.get("id") or f"slot{index + 1}")[:32],
        "text": text,
        "x": _bounded(raw.get("x", 0.05), 0, 1, "文字槽位 x"),
        "y": _bounded(raw.get("y", 0.05), 0, 1, "文字槽位 y"),
        "w": _bounded(raw.get("w", 0.4), 0.01, 1, "文字槽位宽度"),
        "h": _bounded(raw.get("h", 0.1), 0.01, 1, "文字槽位高度"),
        "font_size": int(_bounded(raw.get("font_size", 28), 8, 256, "字号")),
        "color": str(raw.get("color") or "#1f2430")[:16],
        "align": align,
    }


def _logo(raw: Any) -> dict[str, Any]:
    if raw in (None, "") or raw == {}:
        return {}
    if not isinstance(raw, dict):
        raise TemplateError("Logo 位必须是对象")
    return {
        "x": _bounded(raw.get("x", 0.05), 0, 1, "Logo x"),
        "y": _bounded(raw.get("y", 0.85), 0, 1, "Logo y"),
        "w": _bounded(raw.get("w", 0.12), 0.01, 1, "Logo 宽度"),
        "h": _bounded(raw.get("h", 0.1), 0.01, 1, "Logo 高度"),
    }


def normalize_template(raw: Any, existing: dict[str, Any] | None = None) -> dict[str, Any]:
    """校验并归一化模板字段；existing 用于 PATCH 合并。"""
    if not isinstance(raw, dict):
        raise TemplateError("模板参数必须是对象")
    base = dict(existing or {})

    def pick(key: str, default: Any) -> Any:
        return raw[key] if key in raw else base.get(key, default)

    name = str(pick("name", "")).strip()
    if not name:
        raise TemplateError("模板名称不能为空")
    if len(name) > 128:
        raise TemplateError("模板名称不能超过 128 字")

    category = str(pick("category", "all"))
    if category not in TEMPLATE_CATEGORIES:
        raise TemplateError("模板品类只能是 all、beauty、home、digital")

    shadow = str(pick("shadow", "soft"))
    if shadow not in TEMPLATE_SHADOWS:
        raise TemplateError("阴影只能是 soft、none、hard")

    slots = pick("text_slots", [])
    if not isinstance(slots, list):
        raise TemplateError("文字槽位必须是数组")
    if len(slots) > MAX_TEXT_SLOTS:
        raise TemplateError(f"文字槽位最多 {MAX_TEXT_SLOTS} 个")

    return {
        "name": name,
        "category": category,
        "size_w": int(_bounded(pick("size_w", 800), 256, 4096, "宽度")),
        "size_h": int(_bounded(pick("size_h", 800), 256, 4096, "高度")),
        "subject_ratio": round(_bounded(pick("subject_ratio", 0.85), 0.1, 1.0, "主体占比"), 4),
        "shadow": shadow,
        "background": str(pick("background", "white"))[:32] or "white",
        "text_slots": [_slot(item, i) for i, item in enumerate(slots)],
        "logo_slot": _logo(pick("logo_slot", {})),
    }
