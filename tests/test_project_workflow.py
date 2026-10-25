"""Offline tests for the project workflow's execution boundaries."""
import asyncio
import re
import uuid
from types import SimpleNamespace
import httpx
import pytest

from apps.api.projects import router
from apps.api.main import app
from apps.api.projects import ProjectCreate, SKUInput, FactInput, run_out, supports_channel_image, workflow_model
from fastapi import HTTPException
from apps.core.models import WorkflowRun
from apps.api.schemas import GenerateImageRequest
from apps.providers.openai_compat import OpenAICompatProvider
from apps.providers.base import ImageRequest, ModelSpec
from apps.core.planner import PlannerError, validate_items
from apps.core.workflow import WorkflowValidationError, default_workflow, validate_workflow


def test_project_routes_registered():
    paths = {route.path for route in router.routes if hasattr(route, "path")}
    assert "/api/projects" in paths
    assert "/api/projects/{project_id}/propose" in paths
    assert "/api/projects/{project_id}/runs/{run_id}/advance" in paths
    async def check():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/api/projects")
        assert response.status_code == 503  # lifespan/database not enabled in this offline test
    asyncio.run(check())


def test_project_ui_entry_and_assets_are_served_by_api():
    async def check():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            page = await client.get("/project-ui/")
            assert page.status_code == 200
            assert page.headers["cache-control"] == "no-store"
            assets = re.findall(r'(?:src|href)="(/project-ui/assets/[^"]+)"', page.text)
            assert len(assets) >= 2
            for path in assets:
                response = await client.get(path)
                assert response.status_code == 200
                assert response.content
                expected = "text/javascript" if path.endswith(".js") else "text/css"
                assert response.headers["content-type"].startswith(expected)
                assert response.headers["cache-control"] == "no-store"
    asyncio.run(check())


def test_default_workflow_has_required_gates():
    graph = default_workflow()
    assert validate_workflow(**graph) == [n["id"] for n in graph["nodes"]]


def test_run_next_node_uses_edge_order_not_node_array_order():
    graph = default_workflow()
    graph["nodes"].reverse()
    run = WorkflowRun(id=uuid.uuid4(), project_id=uuid.uuid4(), plan_id=uuid.uuid4(),
                      sku_ref="sku-a", item_id="item-a", graph=graph,
                      status="pending", cursor=1, context={})
    assert run_out(run)["next_node"]["type"] == "generate"
    run.cursor = len(graph["nodes"])
    assert run_out(run)["next_node"] is None


def test_only_enabled_synced_image_models_can_run():
    channel = SimpleNamespace(enabled=True, models=[{"id": "image-a", "kind": "image"},
                                                    {"id": "video-a", "kind": "video"}])
    assert supports_channel_image(channel, "image-a")
    assert not supports_channel_image(channel, "video-a")
    assert not supports_channel_image(channel, "unknown")
    channel.enabled = False
    assert not supports_channel_image(channel, "image-a")


def test_workflow_model_validates_channel_and_model():
    assert workflow_model({"data": {"model": "gpt-image-2"}}) == ("gpt-image-2", None)
    with pytest.raises(HTTPException):
        workflow_model({"data": {"model": 123}})
    with pytest.raises(HTTPException):
        workflow_model({"data": {"model": "gpt-image-2", "channel_id": "bad"}})
    with pytest.raises(HTTPException):
        workflow_model({"data": "not-an-object"})


def test_workflow_trace_is_not_forwarded_to_image_provider():
    payload = GenerateImageRequest(model="image-a", prompt="test")
    assert "workflow_trace" not in GenerateImageRequest.model_fields
    provider = OpenAICompatProvider(provider_id="test", base_url="https://example.invalid/v1",
                                    api_key_env="UNUSED_TEST_KEY", api_key="test")
    request = ImageRequest(prompt=payload.prompt, model_key=payload.model, extra=payload.extra)
    wire = provider._build_generations(request, ModelSpec(key="image-a", model="image-a", provider_id="test"))
    assert "workflow_trace" not in wire
    asyncio.run(provider.aclose())


def test_workflow_rejects_bypass_and_cycle():
    graph = default_workflow()
    graph["edges"] = [e for e in graph["edges"] if e["source"] != "quality_check"]
    graph["edges"].append({"source": "quality_check", "target": "deliver"})
    with pytest.raises(WorkflowValidationError):
        validate_workflow(**graph)
    graph = default_workflow()
    graph["edges"].append({"source": "quality_check", "target": "generate"})
    with pytest.raises(WorkflowValidationError):
        validate_workflow(**graph)


def test_plan_items_must_be_scoped_and_supported():
    item = {"sku_ref": "sku-a", "usage": "main", "title": "商品主图", "prompt": "使用商家原图", "reason": "展示商品"}
    assert validate_items([item], {"sku-a"})[0]["sku_ref"] == "sku-a"
    first = validate_items([item], {"sku-a"})[0]
    assert validate_items([first], {"sku-a"})[0]["id"] == first["id"]
    with pytest.raises(PlannerError):
        validate_items([item], {"other"})
    with pytest.raises(PlannerError):
        validate_items([{**item, "usage": "advertisement"}], {"sku-a"})


def test_project_payload_supports_multiple_skus():
    payload = ProjectCreate(product_ref="p1", name="商品", category="home",
        skus=[SKUInput(id="a", name="A"), SKUInput(id="b", name="B")],
        facts={"材质": FactInput(value="商家确认值", confirmed=True)})
    assert len(payload.skus) == 2
