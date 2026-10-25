"""Validate the executable, restricted image workflow independently of React Flow."""

from __future__ import annotations

from collections import defaultdict, deque
from typing import Any

ALLOWED = {"source", "generate", "refine", "quality_check", "human_review", "deliver"}
PORTS = {
    "source": (None, "image"),
    "generate": ("image", "image"),
    "refine": ("image", "image"),
    "quality_check": ("image", "image"),
    "human_review": ("image", "image"),
    "deliver": ("image", None),
}
REQUIRED = {"source", "generate", "quality_check", "human_review", "deliver"}


class WorkflowValidationError(ValueError):
    pass


def validate_workflow(nodes: list[dict[str, Any]], edges: list[dict[str, Any]]) -> list[str]:
    """Return topological node ids; reject disconnected or unsafe delivery paths."""
    if not 5 <= len(nodes) <= 32 or len(edges) > 64:
        raise WorkflowValidationError("节点数量须为 5–32，连线不超过 64")
    if any(not isinstance(node, dict) for node in nodes) or any(not isinstance(edge, dict) for edge in edges):
        raise WorkflowValidationError("节点和连线必须是对象")
    by_id = {str(n.get("id")): n for n in nodes}
    if len(by_id) != len(nodes) or any(not n.get("id") or len(str(n["id"])) > 64 for n in nodes):
        raise WorkflowValidationError("节点 id 不能为空或重复")
    kinds = [n.get("type") for n in nodes]
    if any(not isinstance(kind, str) or kind not in ALLOWED for kind in kinds):
        raise WorkflowValidationError("包含未知节点")
    if not REQUIRED <= set(kinds):
        raise WorkflowValidationError("包含未知节点，或缺少输入、生成、质检、人工确认、交付节点")
    if any(kinds.count(kind) != 1 for kind in REQUIRED) or kinds.count("refine") > 1:
        raise WorkflowValidationError("每个必需节点须恰好一个，精修最多一个")
    graph: dict[str, list[str]] = defaultdict(list)
    incoming: dict[str, list[str]] = defaultdict(list)
    seen_edges: set[tuple[str, str]] = set()
    for edge in edges:
        src, dst = str(edge.get("source")), str(edge.get("target"))
        if src not in by_id or dst not in by_id or src == dst or (src, dst) in seen_edges:
            raise WorkflowValidationError("连线端点无效、重复或自环")
        if PORTS[by_id[src]["type"]][1] != PORTS[by_id[dst]["type"]][0]:
            raise WorkflowValidationError("节点输入输出类型不匹配")
        graph[src].append(dst)
        incoming[dst].append(src)
        seen_edges.add((src, dst))
    if any(len(incoming[nid]) != (0 if node["type"] == "source" else 1)
           for nid, node in by_id.items()):
        raise WorkflowValidationError("除输入外每个节点必须恰有一条入边")
    if any(len(graph[nid]) != (0 if node["type"] == "deliver" else 1)
           for nid, node in by_id.items()):
        raise WorkflowValidationError("当前版本每个节点必须在唯一的交付路径上")
    source = next(nid for nid, n in by_id.items() if n["type"] == "source")
    order: list[str] = []
    queue = deque([source])
    while queue:
        nid = queue.popleft()
        if nid in order:
            raise WorkflowValidationError("工作流不能有循环")
        order.append(nid)
        queue.extend(graph[nid])
    if len(order) != len(nodes) or by_id[order[-1]]["type"] != "deliver":
        raise WorkflowValidationError("工作流存在断开的节点或循环")
    ordered_kinds = [by_id[nid]["type"] for nid in order]
    if ordered_kinds.index("quality_check") < ordered_kinds.index("generate"):
        raise WorkflowValidationError("质检必须位于生成之后")
    if ordered_kinds.index("human_review") < ordered_kinds.index("quality_check"):
        raise WorkflowValidationError("人工确认必须位于质检之后")
    if any(kind in {"generate", "refine"} for kind in ordered_kinds[ordered_kinds.index("quality_check") + 1:]):
        raise WorkflowValidationError("最终生图或精修之后必须重新质检")
    if any(kind not in {"human_review", "deliver"} for kind in ordered_kinds[ordered_kinds.index("human_review") + 1:]):
        raise WorkflowValidationError("人工确认后只能交付，不得再改图")
    return order


def default_workflow() -> dict[str, list[dict[str, Any]]]:
    kinds = ["source", "generate", "quality_check", "human_review", "deliver"]
    nodes = [{"id": kind, "type": kind, "position": {"x": i * 220, "y": 100}, "data": {"label": kind}} for i, kind in enumerate(kinds)]
    edges = [{"id": f"e{i}", "source": kinds[i], "target": kinds[i + 1]} for i in range(len(kinds) - 1)]
    return {"nodes": nodes, "edges": edges}
