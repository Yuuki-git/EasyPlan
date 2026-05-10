from __future__ import annotations

import asyncio
import inspect
from typing import Any

from langgraph.types import Command

from app.agents.graph import build_task_graph, create_graph_config, route_after_human_review
from app.agents.nodes import PlannerClient, build_planner_prompt, task_tree_validator_node
from app.services.checkpoint_service import TenantAwareMemorySaver


class CapturingPlanner(PlannerClient):
    def __init__(self, plans: list[dict[str, Any]]) -> None:
        self.plans = plans
        self.prompts: list[str] = []

    async def create_plan(self, prompt: str) -> dict[str, Any]:
        self.prompts.append(prompt)
        return self.plans.pop(0)


def valid_plan(title: str = "Open paper document") -> dict[str, Any]:
    return {
        "root": {
            "client_node_id": "root",
            "title": "Paper draft",
            "description": None,
            "verb": "Plan",
            "estimated_minutes": 1,
            "node_type": "group",
            "depends_on": [],
            "children": [
                {
                    "client_node_id": "task-1",
                    "title": title,
                    "description": None,
                    "verb": title.split()[0],
                    "estimated_minutes": 2,
                    "node_type": "action",
                    "depends_on": [],
                    "children": [],
                }
            ],
        },
        "summary": "Paper draft starter plan",
        "assumptions": [],
    }


def invalid_plan() -> dict[str, Any]:
    plan = valid_plan("Write paper draft")
    plan["root"]["children"][0]["estimated_minutes"] = 8
    return plan


def plan_with_unknown_dependency() -> dict[str, Any]:
    plan = valid_plan("Open paper document")
    plan["root"]["children"][0]["depends_on"] = ["missing-node"]
    return plan


def plan_with_dependency_cycle() -> dict[str, Any]:
    plan = valid_plan("Open paper document")
    first = plan["root"]["children"][0]
    first["client_node_id"] = "task-a"
    first["depends_on"] = ["task-b"]
    plan["root"]["children"].append(
        {
            "client_node_id": "task-b",
            "title": "List summary points",
            "description": None,
            "verb": "List",
            "estimated_minutes": 2,
            "node_type": "action",
            "depends_on": ["task-a"],
            "children": [],
        }
    )
    return plan


def test_planner_prompt_contains_two_minute_and_verb_rules():
    prompt = build_planner_prompt("Write a paper", feedback="Start with summary")

    assert "两分钟法则" in prompt
    assert "动词开头" in prompt
    assert "< 5 分钟" in prompt
    assert "Start with summary" in prompt


def test_validator_routes_oversized_leaf_to_replan():
    result = asyncio.run(task_tree_validator_node({"task_tree": invalid_plan(), "replan_attempts": 0}))

    assert result["validation_status"] == "needs_replan"
    assert "task-1" in result["validation_errors"][0]


def test_validator_allows_large_group_estimate_but_rejects_large_action_estimate():
    plan = valid_plan("Open paper document")
    plan["root"]["estimated_minutes"] = 120

    group_result = asyncio.run(task_tree_validator_node({"task_tree": plan, "replan_attempts": 0}))
    action_plan = valid_plan("Write paper draft")
    action_plan["root"]["children"][0]["estimated_minutes"] = 8
    action_result = asyncio.run(task_tree_validator_node({"task_tree": action_plan, "replan_attempts": 0}))

    assert group_result["validation_status"] == "valid"
    assert action_result["validation_status"] == "needs_replan"
    assert "estimated_minutes must be < 5" in action_result["validation_errors"][0]


def test_validator_rejects_unknown_dependency_reference():
    result = asyncio.run(task_tree_validator_node({"task_tree": plan_with_unknown_dependency(), "replan_attempts": 0}))

    assert result["validation_status"] == "needs_replan"
    assert "missing-node" in result["validation_errors"][0]


def test_validator_rejects_dependency_cycles():
    result = asyncio.run(task_tree_validator_node({"task_tree": plan_with_dependency_cycle(), "replan_attempts": 0}))

    assert result["validation_status"] == "needs_replan"
    assert "cycle" in result["validation_errors"][0]


def test_graph_interrupts_for_human_review_and_supports_refine_resume():
    planner = CapturingPlanner([valid_plan("Open paper document"), valid_plan("List summary points")])
    graph = build_task_graph(planner=planner, checkpointer=TenantAwareMemorySaver())
    config = create_graph_config(user_id="user_1", thread_id="thread_1")

    first_chunks = asyncio.run(
        _collect_astream(graph.astream({"user_id": "user_1", "thread_id": "thread_1", "intent_text": "Write paper"}, config))
    )

    interrupt_chunk = first_chunks[-1]["__interrupt__"][0]
    assert interrupt_chunk.value["allowed_actions"] == ["approve", "edit", "refine", "reject"]

    refined_chunks = asyncio.run(
        _collect_astream(
            graph.astream(
                Command(resume={"action": "refine", "feedback": "Start with summary"}),
                config,
            )
        )
    )

    assert planner.prompts[-1].find("Start with summary") != -1
    assert refined_chunks[-1]["__interrupt__"][0].value["task_tree"]["root"]["children"][0]["title"] == "List summary points"


def test_graph_auto_replans_when_validator_finds_large_leaf_task():
    planner = CapturingPlanner([invalid_plan(), valid_plan("List paper title")])
    graph = build_task_graph(planner=planner, checkpointer=TenantAwareMemorySaver())
    config = create_graph_config(user_id="user_1", thread_id="thread_2")

    chunks = asyncio.run(
        _collect_astream(graph.astream({"user_id": "user_1", "thread_id": "thread_2", "intent_text": "Write paper"}, config))
    )

    assert len(planner.prompts) == 2
    assert "继续拆解" in planner.prompts[-1]
    assert chunks[-1]["__interrupt__"][0].value["task_tree"]["root"]["children"][0]["title"] == "List paper title"


def test_planner_client_contract_is_async():
    assert inspect.iscoroutinefunction(PlannerClient.create_plan)


def test_route_after_human_review_approve_persists_internal_tasks():
    state = {"human_decision": {"action": "approve"}}

    assert route_after_human_review(state) == "persist_tasks"


async def _collect_astream(stream):
    return [chunk async for chunk in stream]
