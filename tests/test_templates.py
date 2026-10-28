"""Offline tests for layout templates, soft delete routes and trash."""
import pytest

from apps.api.products import router as products_router
from apps.api.projects import router as projects_router
from apps.api.templates import router as templates_router
from apps.api.trash import router as trash_router
from apps.core.templates import BUILTIN_TEMPLATES, TemplateError, normalize_template


def paths(router):
    return {route.path for route in router.routes if hasattr(route, "path")}


def test_template_routes_registered():
    registered = paths(templates_router)
    assert "/api/templates" in registered
    assert "/api/templates/{template_id}" in registered
    assert "/api/templates/{template_id}/restore" in registered
    assert "/api/templates/restore-builtins" in registered


def test_soft_delete_and_trash_routes_registered():
    products = paths(products_router)
    projects = paths(projects_router)
    assert "/api/products/{product_id}" in products
    assert "/api/products/{product_id}/restore" in products
    assert "/api/projects/{project_id}" in projects
    assert "/api/projects/{project_id}/restore" in projects
    assert "/api/trash" in paths(trash_router)


def test_builtins_have_unique_keys_and_required_fields():
    assert 3 <= len(BUILTIN_TEMPLATES) <= 5
    keys = [item["builtin_key"] for item in BUILTIN_TEMPLATES]
    assert len(keys) == len(set(keys))
    for item in BUILTIN_TEMPLATES:
        assert item["name"]
        assert item["category"] in {"all", "beauty", "home", "digital"}
        assert 256 <= item["size_w"] <= 4096
        assert 256 <= item["size_h"] <= 4096
        assert 0.1 <= item["subject_ratio"] <= 1.0


def test_builtins_are_valid_templates():
    for item in BUILTIN_TEMPLATES:
        fields = normalize_template(item)
        assert fields["name"] == item["name"]
        assert fields["size_w"] == item["size_w"]


def test_normalize_fills_defaults():
    fields = normalize_template({"name": " 新模板 "})
    assert fields["name"] == "新模板"
    assert fields["category"] == "all"
    assert fields["size_w"] == 800
    assert fields["shadow"] == "soft"
    assert fields["text_slots"] == []
    assert fields["logo_slot"] == {}


def test_normalize_merges_existing_on_patch():
    existing = normalize_template({"name": "旧名", "size_w": 900, "size_h": 900})
    fields = normalize_template({"name": "新名"}, existing=existing)
    assert fields["name"] == "新名"
    assert fields["size_w"] == 900
    assert fields["size_h"] == 900


@pytest.mark.parametrize("payload,message", [
    ({"name": ""}, "模板名称不能为空"),
    ({"name": "x", "category": "food"}, "模板品类"),
    ({"name": "x", "shadow": "blur"}, "阴影"),
    ({"name": "x", "size_w": 100}, "宽度"),
    ({"name": "x", "subject_ratio": 1.5}, "主体占比"),
    ({"name": "x", "text_slots": [{}] * 9}, "文字槽位最多"),
])
def test_normalize_rejects_invalid(payload, message):
    with pytest.raises(TemplateError) as exc:
        normalize_template(payload)
    assert message in str(exc.value)


def test_normalize_clamps_text_slot_fields():
    fields = normalize_template({
        "name": "带文字",
        "text_slots": [{"text": "标题", "x": 0.1, "y": 0.2, "w": 0.3, "h": 0.2,
                        "font_size": 200, "align": "weird"}],
    })
    slot = fields["text_slots"][0]
    assert slot["text"] == "标题"
    assert slot["align"] == "left"
    assert slot["x"] == 0.1
