from __future__ import annotations

import asyncio
import inspect
from typing import Any

from langgraph.types import Command

from app.agents.graph import build_task_graph, create_graph_config, route_after_human_review
from app.agents.nodes import (
    IntentProfilerClient,
    PlannerClient,
    build_planner_prompt,
    task_tree_validator_node,
)
from app.services.checkpoint_service import TenantAwareMemorySaver


class CapturingPlanner(PlannerClient):
    def __init__(self, plans: list[dict[str, Any]]) -> None:
        self.plans = plans
        self.prompts: list[str] = []

    async def create_plan(self, prompt: str) -> dict[str, Any]:
        self.prompts.append(prompt)
        return self.plans.pop(0)


class CapturingIntentProfiler(IntentProfilerClient):
    def __init__(self, profile: dict[str, Any]) -> None:
        self.profile = profile
        self.inputs: list[str] = []

    async def profile_intent(self, intent_text: str) -> dict[str, Any]:
        self.inputs.append(intent_text)
        return self.profile


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


def plan_with_slow_first_action() -> dict[str, Any]:
    plan = valid_plan("Write paper draft")
    plan["root"]["children"][0]["estimated_minutes"] = 8
    return plan


def short_term_plan_with_low_value_icebreaker() -> dict[str, Any]:
    plan = valid_plan("打开 Word 准备开始")
    plan["root"]["children"][0]["verb"] = "打开"
    plan["root"]["children"][0]["estimated_minutes"] = 2
    return plan


def oversized_top_level_plan(count: int = 13) -> dict[str, Any]:
    plan = valid_plan("Draft core module")
    plan["root"]["children"] = [
        {
            "client_node_id": f"task-{index}",
            "title": f"Draft section {index}",
            "description": None,
            "verb": "Draft",
            "estimated_minutes": 15,
            "node_type": "action",
            "depends_on": [],
            "children": [],
        }
        for index in range(count)
    ]
    return plan


def long_term_plan_that_covers_full_cycle() -> dict[str, Any]:
    plan = valid_plan("Search N3 textbook")
    plan["root"]["title"] = "完成全年日语 N3 备考计划"
    plan["root"]["description"] = "覆盖未来一年每周复习、每月模考和完整考试周期。"
    plan["root"]["children"][0]["title"] = "搜索 N3 备考教材"
    plan["root"]["children"][0]["description"] = "在 72 小时启动阶段内完成低门槛破冰。"
    return plan


def long_term_plan_with_weekly_schedule_task() -> dict[str, Any]:
    plan = valid_plan("Search N3 textbook")
    plan["root"]["children"].append(
        {
            "client_node_id": "week-1",
            "title": "第1周完成 N3 词汇语法基础",
            "description": "每天坚持背单词并完成周计划。",
            "verb": "完成",
            "estimated_minutes": 120,
            "node_type": "action",
            "depends_on": [],
            "children": [],
        }
    )
    return plan


def exploration_plan_that_assumes_execution_decision() -> dict[str, Any]:
    plan = valid_plan("制定 6 个月转行产品经理学习计划")
    plan["root"]["title"] = "执行转行产品经理计划"
    plan["root"]["children"][0]["title"] = "报名产品经理系统课程"
    plan["root"]["children"][0]["description"] = "直接开始长期学习计划。"
    plan["root"]["children"][0]["estimated_minutes"] = 60
    return plan


def top_level_with_too_many_children() -> dict[str, Any]:
    plan = valid_plan("Draft core module")
    plan["root"]["children"] = [
        {
            "client_node_id": "group-1",
            "title": "Write proposal",
            "description": None,
            "verb": "Write",
            "estimated_minutes": 60,
            "node_type": "group",
            "depends_on": [],
            "children": [
                {
                    "client_node_id": f"child-{index}",
                    "title": f"Draft subsection {index}",
                    "description": None,
                    "verb": "Draft",
                    "estimated_minutes": 15,
                    "node_type": "action",
                    "depends_on": [],
                    "children": [],
                }
                for index in range(4)
            ],
        }
    ]
    return plan


def context_checklist_without_groups() -> dict[str, Any]:
    plan = valid_plan("Buy tomatoes")
    plan["root"]["children"].append(
        {
            "client_node_id": "pay-bill",
            "title": "Pay water bill",
            "description": None,
            "verb": "Pay",
            "estimated_minutes": 5,
            "node_type": "action",
            "depends_on": [],
            "children": [],
        }
    )
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


def test_planner_prompt_injects_size_limits_and_intent_strategy_without_global_two_minute_rule():
    prompt = build_planner_prompt(
        "Write a paper",
        feedback="Start with summary",
        intent_profile={"intent_type": "short_term_delivery"},
    )

    assert "整个任务树最多只能包含 12 个顶层节点" in prompt
    assert "每个顶层节点最多只能包含 3 个子节点" in prompt
    assert "最近 72 小时" in build_planner_prompt(
        "明年考过日语 N3",
        intent_profile={"intent_type": "long_term_growth"},
    )
    long_term_prompt = build_planner_prompt(
        "明年考过日语 N3",
        intent_profile={"intent_type": "long_term_growth"},
    )
    exploration_prompt = build_planner_prompt(
        "不知道要不要转行产品经理",
        intent_profile={"intent_type": "exploration_decision"},
    )
    assert "roadmap 只能是阶段标题和目的" in long_term_prompt
    assert "第1周/第2周/第3个月" in long_term_prompt
    assert "错误：为 N3 制定 3 个月每日学习计划" in long_term_prompt
    assert "不要假设用户已经决定" in exploration_prompt
    assert "错误：直接制定 6 个月转行产品经理学习计划" in exploration_prompt
    assert "时间盒法则" in prompt
    assert "打开电脑 / 新建文档 / 打开 Word / 准备开始" in prompt
    assert "必须遵守两分钟法则" not in prompt
    assert "每个叶子 action 的 estimated_minutes 必须 < 5" not in prompt
    assert "Start with summary" in prompt


def test_planner_prompt_uses_general_strategy_when_intent_profile_is_missing():
    prompt = build_planner_prompt("Write a paper")

    assert "用户意图不够明确" in prompt
    assert "short_term_delivery" not in prompt


def test_validator_does_not_reject_large_action_estimate_globally():
    result = asyncio.run(
        task_tree_validator_node(
            {
                "task_tree": plan_with_slow_first_action(),
                "intent_profile": {"intent_type": "short_term_delivery"},
                "replan_attempts": 0,
            }
        )
    )

    assert result["validation_status"] == "valid"


def test_validator_rejects_short_term_low_value_first_action():
    result = asyncio.run(
        task_tree_validator_node(
            {
                "task_tree": short_term_plan_with_low_value_icebreaker(),
                "intent_profile": {"intent_type": "short_term_delivery"},
                "replan_attempts": 0,
            }
        )
    )

    assert result["validation_status"] == "needs_replan"
    assert "low-value icebreaker" in result["validation_errors"][0]


def test_validator_requires_long_term_first_action_to_be_low_barrier():
    plan = valid_plan("Open paper document")
    plan["root"]["estimated_minutes"] = 120
    five_minute_plan = valid_plan("Search N3 textbook")
    five_minute_plan["root"]["children"][0]["estimated_minutes"] = 5

    valid_result = asyncio.run(
        task_tree_validator_node(
            {
                "task_tree": plan,
                "intent_profile": {"intent_type": "long_term_growth"},
                "replan_attempts": 0,
            }
        )
    )
    five_minute_result = asyncio.run(
        task_tree_validator_node(
            {
                "task_tree": five_minute_plan,
                "intent_profile": {"intent_type": "long_term_growth"},
                "replan_attempts": 0,
            }
        )
    )
    action_result = asyncio.run(
        task_tree_validator_node(
            {
                "task_tree": plan_with_slow_first_action(),
                "intent_profile": {"intent_type": "long_term_growth"},
                "replan_attempts": 0,
            }
        )
    )

    assert valid_result["validation_status"] == "valid"
    assert five_minute_result["validation_status"] == "valid"
    assert action_result["validation_status"] == "needs_replan"
    assert "low-barrier icebreaker" in action_result["validation_errors"][0]


def test_validator_rejects_long_term_plan_that_covers_full_cycle_instead_of_72h():
    result = asyncio.run(
        task_tree_validator_node(
            {
                "task_tree": long_term_plan_that_covers_full_cycle(),
                "intent_profile": {"intent_type": "long_term_growth"},
                "replan_attempts": 0,
            }
        )
    )

    assert result["validation_status"] == "needs_replan"
    assert any("72-hour Phase 1" in error for error in result["validation_errors"])


def test_validator_rejects_long_term_weekly_schedule_tasks():
    result = asyncio.run(
        task_tree_validator_node(
            {
                "task_tree": long_term_plan_with_weekly_schedule_task(),
                "intent_profile": {"intent_type": "long_term_growth"},
                "replan_attempts": 0,
            }
        )
    )

    assert result["validation_status"] == "needs_replan"
    assert any("第1周" in error or "24-72" in error for error in result["validation_errors"])


def test_validator_rejects_exploration_plan_that_assumes_execution_decision():
    result = asyncio.run(
        task_tree_validator_node(
            {
                "task_tree": exploration_plan_that_assumes_execution_decision(),
                "intent_profile": {"intent_type": "exploration_decision"},
                "replan_attempts": 0,
            }
        )
    )

    assert result["validation_status"] == "needs_replan"
    assert any("exploration_decision" in error for error in result["validation_errors"])


def test_validator_enforces_global_size_limits():
    too_many_top = asyncio.run(
        task_tree_validator_node({"task_tree": oversized_top_level_plan(), "replan_attempts": 0})
    )
    too_many_children = asyncio.run(
        task_tree_validator_node({"task_tree": top_level_with_too_many_children(), "replan_attempts": 0})
    )

    assert too_many_top["validation_status"] == "needs_replan"
    assert "top-level node count" in too_many_top["validation_errors"][0]
    assert too_many_children["validation_status"] == "needs_replan"
    assert "children count" in too_many_children["validation_errors"][0]


def test_validator_rejects_context_checklist_without_grouping():
    result = asyncio.run(
        task_tree_validator_node(
            {
                "task_tree": context_checklist_without_groups(),
                "intent_profile": {"intent_type": "context_checklist"},
                "replan_attempts": 0,
            }
        )
    )

    assert result["validation_status"] == "needs_replan"
    assert "grouped" in result["validation_errors"][0]


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


def test_graph_starts_with_intent_profiler_before_planner_without_prompt_injection():
    planner = CapturingPlanner([valid_plan("Draft core module")])
    profiler = CapturingIntentProfiler(
        {
            "intent_type": "short_term_delivery",
            "time_horizon": "hours",
            "confidence_score": 0.91,
        }
    )
    graph = build_task_graph(
        planner=planner,
        intent_profiler=profiler,
        checkpointer=TenantAwareMemorySaver(),
    )
    config = create_graph_config(user_id="user_1", thread_id="thread_profile")

    chunks = asyncio.run(
        _collect_astream(
            graph.astream(
                {
                    "user_id": "user_1",
                    "thread_id": "thread_profile",
                    "intent_text": "Finish the business plan by 4pm",
                },
                config,
            )
        )
    )

    assert "intent_profiler" in chunks[0]
    assert chunks[0]["intent_profiler"]["intent_profile"]["intent_type"] == "short_term_delivery"
    assert profiler.inputs == ["Finish the business plan by 4pm"]
    assert len(planner.prompts) == 1
    assert "时间盒法则" in planner.prompts[0]


def test_graph_auto_replans_when_validator_finds_large_leaf_task():
    planner = CapturingPlanner([plan_with_slow_first_action(), valid_plan("List paper title")])
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
