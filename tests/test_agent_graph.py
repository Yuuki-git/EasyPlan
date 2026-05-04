from __future__ import annotations

import inspect
from typing import Any

from langgraph.types import Command

from app.agents.graph import build_task_graph, create_graph_config
from app.agents.nodes import PlannerClient, build_planner_prompt, task_tree_validator_node
from app.services.checkpoint_service import TenantAwareMemorySaver


class CapturingPlanner(PlannerClient):
    def __init__(self, plans: list[dict[str, Any]]) -> None:
        self.plans = plans
        self.prompts: list[str] = []

    async def create_plan(self, prompt: str) -> dict[str, Any]:
        self.prompts.append(prompt)
        return self.plans.pop(0)


def valid_plan(title: str = "打开论文文档") -> dict[str, Any]:
    return {
        "root": {
            "client_node_id": "root",
            "title": "论文初稿",
            "description": None,
            "verb": "规划",
            "estimated_minutes": 1,
            "node_type": "group",
            "depends_on": [],
            "children": [
                {
                    "client_node_id": "task-1",
                    "title": title,
                    "description": None,
                    "verb": title.split()[0] if " " in title else title[:2],
                    "estimated_minutes": 2,
                    "node_type": "action",
                    "depends_on": [],
                    "children": [],
                }
            ],
        },
        "summary": "论文初稿启动计划",
        "assumptions": [],
    }


def invalid_plan() -> dict[str, Any]:
    plan = valid_plan("写完论文初稿")
    plan["root"]["children"][0]["estimated_minutes"] = 8
    return plan


def plan_with_unknown_dependency() -> dict[str, Any]:
    plan = valid_plan("打开论文文档")
    plan["root"]["children"][0]["depends_on"] = ["missing-node"]
    return plan


def plan_with_dependency_cycle() -> dict[str, Any]:
    plan = valid_plan("打开论文文档")
    first = plan["root"]["children"][0]
    first["client_node_id"] = "task-a"
    first["depends_on"] = ["task-b"]
    plan["root"]["children"].append(
        {
            "client_node_id": "task-b",
            "title": "列出摘要要点",
            "description": None,
            "verb": "列出",
            "estimated_minutes": 2,
            "node_type": "action",
            "depends_on": ["task-a"],
            "children": [],
        }
    )
    return plan


def test_planner_prompt_contains_two_minute_and_verb_rules():
    prompt = build_planner_prompt("写论文", feedback="先做摘要")

    assert "两分钟法则" in prompt
    assert "动词开头" in prompt
    assert "< 5 分钟" in prompt
    assert "先做摘要" in prompt


def test_validator_routes_oversized_leaf_to_replan():
    result = task_tree_validator_node({"task_tree": invalid_plan(), "replan_attempts": 0})

    assert result["validation_status"] == "needs_replan"
    assert "task-1" in result["validation_errors"][0]


def test_validator_rejects_unknown_dependency_reference():
    result = task_tree_validator_node({"task_tree": plan_with_unknown_dependency(), "replan_attempts": 0})

    assert result["validation_status"] == "needs_replan"
    assert "missing-node" in result["validation_errors"][0]


def test_validator_rejects_dependency_cycles():
    result = task_tree_validator_node({"task_tree": plan_with_dependency_cycle(), "replan_attempts": 0})

    assert result["validation_status"] == "needs_replan"
    assert "cycle" in result["validation_errors"][0]


def test_graph_interrupts_for_human_review_and_supports_refine_resume():
    planner = CapturingPlanner([valid_plan("打开论文文档"), valid_plan("列出摘要要点")])
    graph = build_task_graph(planner=planner, checkpointer=TenantAwareMemorySaver())
    config = create_graph_config(user_id="user_1", thread_id="thread_1")

    first_chunks = list(graph.stream({"user_id": "user_1", "thread_id": "thread_1", "intent_text": "写论文"}, config))

    interrupt_chunk = first_chunks[-1]["__interrupt__"][0]
    assert interrupt_chunk.value["allowed_actions"] == ["approve", "edit", "refine", "reject"]

    refined_chunks = list(
        graph.stream(
            Command(resume={"action": "refine", "feedback": "先聚焦摘要"}),
            config,
        )
    )

    assert planner.prompts[-1].find("先聚焦摘要") != -1
    assert refined_chunks[-1]["__interrupt__"][0].value["task_tree"]["root"]["children"][0]["title"] == "列出摘要要点"


def test_graph_auto_replans_when_validator_finds_large_leaf_task():
    planner = CapturingPlanner([invalid_plan(), valid_plan("列出论文标题")])
    graph = build_task_graph(planner=planner, checkpointer=TenantAwareMemorySaver())
    config = create_graph_config(user_id="user_1", thread_id="thread_2")

    chunks = list(graph.stream({"user_id": "user_1", "thread_id": "thread_2", "intent_text": "写论文"}, config))

    assert len(planner.prompts) == 2
    assert "继续拆解" in planner.prompts[-1]
    assert chunks[-1]["__interrupt__"][0].value["task_tree"]["root"]["children"][0]["title"] == "列出论文标题"


def test_planner_client_contract_is_async():
    assert inspect.iscoroutinefunction(PlannerClient.create_plan)
