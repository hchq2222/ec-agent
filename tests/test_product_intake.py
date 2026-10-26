"""Offline tests for product intake normalization and route registration."""
from apps.api.products import router
from apps.core.product_intake import ProductIntakeError, normalize


def test_product_intake_routes_registered():
    paths = {route.path for route in router.routes if hasattr(route, "path")}
    assert "/api/products/intake" in paths
    assert "/api/products/{product_id}" in paths
    assert "/api/products/{product_id}/confirm" in paths


def test_normalize_dedupes_and_keeps_units():
    data = normalize({
        "name": " 测试洁面乳 ",
        "category": "美妆个护",
        "brand": "森序",
        "pitch": "温和清洁",
        "attrs": [
            {"key": "容量", "value": "150", "unit": "ml", "confidence": 0.9},
            {"key": "容量", "value": "200", "unit": "ml"},
            {"key": "成分", "value": "氨基酸表活"},
            {"key": "", "value": "忽略"},
        ],
        "skus": [
            {"color": "青绿色", "spec": "150ml", "price": "129"},
            {"color": "青绿色", "spec": "150ml", "price": 139},
            {"color": "米白色", "spec": "150ml"},
        ],
        "missing": ["品牌", "品牌"],
        "reply": "已整理",
    })
    assert data["name"] == "测试洁面乳"
    assert data["category"] == "beauty"
    assert [a["key"] for a in data["attrs"]] == ["容量", "成分"]
    assert data["attrs"][0]["unit"] == "ml"
    assert len(data["skus"]) == 2
    assert data["skus"][0]["price"] == 129
    assert data["skus"][1]["price"] is None


def test_normalize_falls_back_to_existing():
    data = normalize({}, {"name": "已有商品", "brand": "已有品牌", "category": "home", "pitch": "已有卖点"})
    assert data["name"] == "已有商品"
    assert data["brand"] == "已有品牌"
    assert data["category"] == "home"
    assert data["pitch"] == "已有卖点"


def test_normalize_rejects_non_object():
    try:
        normalize([])
    except ProductIntakeError:
        pass
    else:
        raise AssertionError("expected ProductIntakeError")
