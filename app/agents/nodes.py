import asyncio
from typing import Any, Protocol

from pydantic import ValidationError

from app.agents.state import AgentState, DISALLOWED_CHECKPOINT_KEYS, prune_state
from app.api.schemas import TaskTree


MAX_REPLAN_ATTEMPTS = 3


class PlannerClient(Protocol):
    async def create_plan(self, prompt: str) -> dict[str, Any]:
        """Create a TaskTree-shaped plan using an async LLM client."""


class RuleBasedPlannerClient:
    async def create_plan(self, prompt: str) -> dict[str, Any]:
        return {
            "root": {
                "client_node_id": "root",
                "title": "启动计划",
                "description": None,
                "verb": "规划",
                "estimated_minutes": 1,
                "node_type": "group",
                "depends_on": [],
                "children": [
                    {
                        "client_node_id": "task-1",
                        "title": "写下第一步",
                        "description": "根据当前意图写下一个可立即启动的动作。",
                        "verb": "写下",
                        "estimated_minutes": 2,
                        "node_type": "action",
                        "depends_on": [],
                        "children": [],
                    }
                ],
            },
            "summary": "默认启动计划",
            "assumptions": [],
        }


def build_planner_prompt(
    intent_text: str,
    *,
    feedback: str | None = None,
    current_task_tree_summary: str | None = None,
    validation_errors: list[str] | None = None,
) -> str:
    parts = [
        "你是 EasyPlan 的任务拆解 Agent。",
        "必须遵守两分钟法则：优先把任务拆成约 2 分钟可启动的微行动。",
        "硬性规则：每个叶子 action 的 estimated_minutes 必须 < 5 分钟。",
        "硬性规则：每个叶子 action 的标题必须动词开头，且 verb 字段必须是具体动词。",
        "输出必须是符合 TaskTree JSON Schema 的 JSON，不要输出 Markdown。",
        f"用户意图：{intent_text}",
    ]
    if current_task_tree_summary:
        parts.append(f"当前计划摘要：{current_task_tree_summary}")
    if feedback:
        parts.append(f"用户自然语言反馈：{feedback}")
    if validation_errors:
        parts.append("上次校验失败，请继续拆解以下问题：" + "; ".join(validation_errors))
    return "\n".join(parts)


def planner_node_factory(planner: PlannerClient):
    def planner_node(state: AgentState) -> AgentState:
        prompt = build_planner_prompt(
            state.get("intent_text", ""),
            feedback=state.get("refinement_feedback"),
            current_task_tree_summary=_task_tree_summary(state.get("task_tree")),
            validation_errors=state.get("validation_errors"),
        )
        task_tree = _run_async(planner.create_plan(prompt))
        next_state: AgentState = {
            **state,
            "task_tree": task_tree,
            "reasoning_events": [
                *state.get("reasoning_events", []),
                {
                    "node": "planner_node",
                    "code": "PLAN_CREATED",
                    "message": "已生成结构化任务树，正在进行规则校验",
                },
            ],
        }
        pruned = prune_state(next_state)
        for key in DISALLOWED_CHECKPOINT_KEYS:
            if key in state:
                pruned[key] = None
        return pruned

    return planner_node


def task_tree_validator_node(state: AgentState) -> AgentState:
    errors = _validate_task_tree(state.get("task_tree"))
    if not errors:
        return {
            "task_tree": TaskTree.model_validate(state["task_tree"]).model_dump(mode="json"),
            "validation_status": "valid",
            "validation_errors": [],
        }

    attempts = state.get("replan_attempts", 0)
    return {
        "validation_status": "needs_replan" if attempts < MAX_REPLAN_ATTEMPTS else "failed",
        "validation_errors": errors,
        "replan_attempts": attempts + 1,
    }


def route_after_validation(state: AgentState) -> str:
    if state.get("validation_status") == "valid":
        return "human_review"
    if state.get("validation_status") == "needs_replan":
        return "planner"
    return "failed"


def failed_validation_node(state: AgentState) -> AgentState:
    return {
        "error": {
            "code": "TASK_TREE_VALIDATION_FAILED",
            "message": "; ".join(state.get("validation_errors", [])),
        }
    }


def _validate_task_tree(task_tree: Any) -> list[str]:
    raw_rule_errors = _collect_raw_rule_errors(task_tree)
    if raw_rule_errors:
        return raw_rule_errors

    try:
        parsed = TaskTree.model_validate(task_tree)
    except ValidationError as exc:
        return [str(exc)]

    errors: list[str] = []
    seen: set[str] = set()
    nodes_by_id: dict[str, dict[str, Any]] = {}
    _collect_rule_errors(parsed.root.model_dump(mode="json"), seen, nodes_by_id, errors)
    _collect_dependency_errors(nodes_by_id, errors)
    return errors


def _collect_raw_rule_errors(task_tree: Any) -> list[str]:
    if not isinstance(task_tree, dict):
        return []
    root = task_tree.get("root")
    if not isinstance(root, dict):
        return []
    errors: list[str] = []
    _collect_raw_node_errors(root, errors)
    return errors


def _collect_raw_node_errors(node: dict[str, Any], errors: list[str]) -> None:
    client_node_id = str(node.get("client_node_id") or "<unknown>")
    if node.get("node_type") == "action" and node.get("estimated_minutes", 0) >= 5:
        errors.append(f"{client_node_id}: estimated_minutes must be < 5")
    for child in node.get("children", []):
        if isinstance(child, dict):
            _collect_raw_node_errors(child, errors)


def _collect_rule_errors(
    node: dict[str, Any],
    seen: set[str],
    nodes_by_id: dict[str, dict[str, Any]],
    errors: list[str],
) -> None:
    client_node_id = node["client_node_id"]
    if client_node_id in seen:
        errors.append(f"{client_node_id}: duplicate client_node_id")
    seen.add(client_node_id)
    nodes_by_id[client_node_id] = node

    if node["node_type"] == "action":
        if node["estimated_minutes"] >= 5:
            errors.append(f"{client_node_id}: estimated_minutes must be < 5")
        if not node.get("verb"):
            errors.append(f"{client_node_id}: verb is required")

    for child in node.get("children", []):
        _collect_rule_errors(child, seen, nodes_by_id, errors)


def _collect_dependency_errors(nodes_by_id: dict[str, dict[str, Any]], errors: list[str]) -> None:
    graph: dict[str, list[str]] = {}
    for node_id, node in nodes_by_id.items():
        dependencies = node.get("depends_on", [])
        graph[node_id] = list(dependencies)
        for dependency in dependencies:
            if dependency not in nodes_by_id:
                errors.append(f"{node_id}: depends_on references unknown node {dependency}")

    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(node_id: str, path: list[str]) -> None:
        if node_id in visiting:
            cycle_start = path.index(node_id) if node_id in path else 0
            cycle = " -> ".join([*path[cycle_start:], node_id])
            errors.append(f"dependency cycle detected: {cycle}")
            return
        if node_id in visited:
            return
        visiting.add(node_id)
        for dependency in graph.get(node_id, []):
            if dependency in nodes_by_id:
                visit(dependency, [*path, dependency])
        visiting.remove(node_id)
        visited.add(node_id)

    for node_id in nodes_by_id:
        visit(node_id, [node_id])


def _task_tree_summary(task_tree: dict[str, Any] | None) -> str | None:
    if not task_tree:
        return None
    summary = task_tree.get("summary")
    if isinstance(summary, str):
        return summary
    root = task_tree.get("root", {})
    title = root.get("title")
    return title if isinstance(title, str) else None


def _run_async(coro):
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    raise RuntimeError("planner_node requires sync LangGraph stream execution for interrupt support")
