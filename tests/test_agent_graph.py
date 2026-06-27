from __future__ import annotations

import asyncio
import inspect
from typing import Any

from langgraph.types import Command

from app.agents.graph import (
    build_task_graph,
    create_graph_config,
    route_after_human_review,
    route_from_start,
)
from app.agents.nodes import (
    IntentProfilerClient,
    PlannerClient,
    _validate_task_tree,
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


def phase_plan(*, current_order: int = 1) -> dict[str, Any]:
    plan = valid_plan(f"Complete phase {current_order} action")
    plan["root"]["client_node_id"] = f"phase_{current_order:02d}_root"
    plan["root"]["children"][0]["client_node_id"] = f"phase_{current_order:02d}_action_01"
    plan["root"]["children"][0].update(
        {
            "done_criteria": f"Complete the phase {current_order} deliverable.",
            "start_hint": "Open the saved working document.",
            "fallback_action": "Complete only the first checklist item.",
        }
    )
    roadmap = []
    for order in range(1, 4):
        status = "completed" if order < current_order else "current" if order == current_order else "planned"
        roadmap.append(
            {
                "phase_id": f"phase_{order:02d}",
                "order": order,
                "title": f"Phase {order}",
                "objective": f"Objective {order}",
                "status": status,
            }
        )
    plan["planning_context"] = {
        "schema_version": 1,
        "intent_type": "long_term_growth",
        "time_horizon": "months",
        "roadmap": roadmap,
        "current_phase": {
            "phase_id": f"phase_{current_order:02d}",
            "title": f"Phase {current_order}",
            "objective": f"Objective {current_order}",
            "completion_rule": "all_ai_actions_completed",
        },
        "next_action_client_node_id": f"phase_{current_order:02d}_action_01",
    }
    return plan


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


def plan_with_action_quality_issue(
    *,
    title: str,
    verb: str,
    estimated_minutes: int = 25,
    done_criteria: str | None = None,
    start_hint: str | None = None,
    fallback_action: str | None = None,
) -> dict[str, Any]:
    plan = valid_plan(title)
    action = plan["root"]["children"][0]
    action["title"] = title
    action["verb"] = verb
    action["estimated_minutes"] = estimated_minutes
    action["done_criteria"] = done_criteria
    action["start_hint"] = start_hint
    action["fallback_action"] = fallback_action
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
    assert prompt.index("规则优先级") < prompt.index("硬性规则")
    assert "intent_type 对应策略高于普通任务拆解习惯" in prompt
    assert "Scope Horizon 高于计划完整性" in prompt
    assert "Strategy Compliance 高于任务数量" in prompt
    assert "JSON Schema 合法性高于表达丰富度" in prompt
    assert "当前阶段可执行性高于长期完整性" in prompt
    assert "Scope Horizon 规则" in prompt
    assert "不得生成完整周期计划、每日打卡表、周计划、月计划或备考全程表" in prompt
    assert "默认 assumptions 为 []" in prompt
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
    context_prompt = build_planner_prompt(
        "下班后取快递买菜交电费",
        intent_profile={"intent_type": "context_checklist"},
    )
    assert "roadmap 只能是阶段标题和目的" in long_term_prompt
    assert "第1周/第2周/第3个月" in long_term_prompt
    assert "错误：为 N3 制定 3 个月每日学习计划" in long_term_prompt
    assert "long_term_growth 禁止" in long_term_prompt
    assert "完整备考周期计划" in long_term_prompt
    assert "第一项就是高压力深度任务" in long_term_prompt
    assert "超出 Phase 1 的具体行动" in long_term_prompt
    assert "root.children 中第一个 action 的 estimated_minutes 必须 <= 5" in long_term_prompt
    assert "安装环境" in long_term_prompt
    assert "训练计划" in long_term_prompt
    assert "summary 写成“Phase 1 启动计划”" in long_term_prompt
    assert "assumptions 必须是 []" in long_term_prompt
    assert "Roadmap 只能写入 planning_context.roadmap" in long_term_prompt
    assert "short_term_delivery 禁止" in prompt
    assert "想一想" in prompt
    assert "搜集资料但没有明确产出" in prompt
    assert "context_checklist 禁止" in context_prompt
    assert "深度父子任务树" in context_prompt
    assert "每个琐事再拆成多个子任务" in context_prompt
    assert "2 个以上零散事项" in context_prompt
    assert "root.children 必须使用 group 节点" in context_prompt
    assert "同一地点、工具或时间场景的事项必须放入同一个 Group" in context_prompt
    assert "优先使用 Group" in context_prompt
    assert "不要直接输出多个散乱顶层 Action" in context_prompt
    assert "root.children 顶层必须全部是 group 节点" in context_prompt
    assert "即使只有一个场景，也建立一个 group" in context_prompt
    assert "不要假设用户已经决定" in exploration_prompt
    assert "澄清问题、信息收集、低成本验证、决策依据" in exploration_prompt
    assert "禁止直接生成长期执行计划或连续投入型任务" in exploration_prompt
    assert "错误：直接制定 6 个月转行产品经理学习计划" in exploration_prompt
    assert "exploration_decision 禁止" in exploration_prompt
    assert "假设用户已经做出最终决定" in exploration_prompt
    assert "生成打卡式任务" in exploration_prompt
    assert "跳过信息收集和低成本验证" in exploration_prompt
    assert "不要在 summary、assumptions、title、description 或 verb 中写" in exploration_prompt
    assert "把用户原词改写为“方向A/选项A/当前选择”" in exploration_prompt
    assert "root.children 最多 5 个顶层任务" in exploration_prompt
    assert "summary 先给 1-2 句当前判断，再给探索路线总览" in exploration_prompt
    assert "summary 可以写成“当前判断 + 探索澄清计划”" in exploration_prompt
    assert "assumptions 必须是 []" in exploration_prompt
    assert "时间盒法则" in prompt
    assert "打开电脑 / 新建文档 / 打开 Word / 准备开始" in prompt
    assert "必须遵守两分钟法则" not in prompt
    assert "每个叶子 action 的 estimated_minutes 必须 < 5" not in prompt
    assert "Start with summary" in prompt


def test_planner_prompt_includes_action_quality_field_guidance():
    prompt = build_planner_prompt(
        "写一份周报",
        intent_profile={"intent_type": "short_term_delivery"},
    )
    long_term_prompt = build_planner_prompt(
        "明年考过日语 N3",
        intent_profile={"intent_type": "long_term_growth"},
    )
    exploration_prompt = build_planner_prompt(
        "不知道要不要转行产品经理",
        intent_profile={"intent_type": "exploration_decision"},
    )

    assert "done_criteria" in prompt
    assert "start_hint" in prompt
    assert "fallback_action" in prompt
    assert "对所有 Action，尽量生成 done_criteria" in prompt
    assert "done_criteria 必须具体说明做到什么程度算完成" in prompt
    assert "start_hint 必须是用户可以立刻执行的第一步" in prompt
    assert "fallback_action 必须是更小、更低门槛的替代动作" in prompt
    assert "estimated_minutes >= 20" in prompt
    assert "字段值必须是一句短句" in prompt
    assert "不要包含英文双引号" in prompt

    assert "done_criteria: “完成任务”" in prompt
    assert "start_hint: “开始做”" in prompt
    assert "fallback_action: “少做一点”" in prompt
    assert "done_criteria: “学习完成”" in prompt
    assert "start_hint: “准备好材料”" in prompt
    assert "保存 1 个可打开的 N3 真题链接" in prompt
    assert "打开浏览器搜索“N3 真题 PDF”" in prompt
    assert "如果没有精力做 20 题，就先做前 5 题" in prompt

    assert "首个破冰 Action 必须生成 start_hint" in long_term_prompt
    assert "即使用户当前已经能做较长动作，也必须先安排 <=5 分钟破冰" in long_term_prompt
    assert "信息收集、小实验、决策节点任务建议生成 start_hint" in exploration_prompt


def test_planner_prompt_uses_general_strategy_when_intent_profile_is_missing():
    prompt = build_planner_prompt("Write a paper")

    assert "用户意图不够明确" in prompt
    assert "short_term_delivery" not in prompt


def test_exploration_prompt_requires_judgment_first_summary_before_route():
    prompt = build_planner_prompt(
        "我是否要考虑转行产品经理",
        intent_profile={"intent_type": "exploration_decision"},
    )

    assert "先给 1-2 句当前判断" in prompt
    assert "写入现有 task_tree.summary" in prompt
    assert "再给探索路线" in prompt


def test_initial_long_term_prompt_requires_roadmap_but_short_term_does_not():
    long_prompt = build_planner_prompt(
        "学习日语 N3",
        intent_profile={
            "intent_type": "long_term_growth",
            "time_horizon": "months",
            "confidence_score": 0.95,
        },
    )
    short_prompt = build_planner_prompt(
        "今天完成初稿",
        intent_profile={
            "intent_type": "short_term_delivery",
            "time_horizon": "hours",
            "confidence_score": 0.95,
        },
    )

    assert "3-5 个 Roadmap 阶段" in long_prompt
    assert "planning_context 必须为 null" in short_prompt


def test_phase_planning_feature_flag_can_restore_legacy_output(monkeypatch):
    monkeypatch.setenv("EASYPLAN_PHASE_PLANNING_ENABLED", "false")

    prompt = build_planner_prompt(
        "学习日语 N3",
        intent_profile={"intent_type": "long_term_growth", "time_horizon": "months"},
    )

    assert "功能开关已关闭" in prompt
    assert "planning_context 必须为 null" in prompt


def test_next_phase_prompt_locks_completed_phases_and_profile():
    prompt = build_planner_prompt(
        "学习日语 N3",
        intent_profile={"intent_type": "long_term_growth", "time_horizon": "months"},
        planning_mode="next_phase",
        committed_task_tree=phase_plan(current_order=1),
        current_phase_task_summary="1/1 AI actions completed",
    )

    assert "completed 阶段必须逐字段保持不变" in prompt
    assert "intent_type 和 time_horizon 必须保持不变" in prompt
    assert "只能展开一个新的 current phase" in prompt
    assert "1/1 AI actions completed" in prompt


def test_next_phase_validator_rejects_completed_phase_mutation():
    committed = phase_plan(current_order=2)
    proposed = phase_plan(current_order=3)
    proposed["planning_context"]["roadmap"][0]["objective"] = "Mutated completed phase"

    errors = _validate_task_tree(
        proposed,
        intent_profile={"intent_type": "long_term_growth", "time_horizon": "months"},
        planning_mode="next_phase",
        committed_task_tree=committed,
    )

    assert any("completed phase" in error for error in errors)


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
    error = result["validation_errors"][0]
    assert "错误代码: LOW_VALUE_ICEBREAKER_IN_SPRINT" in error
    assert "intent_type: short_term_delivery" in error
    assert "failed_rule: short_term_delivery 禁止项" in error
    assert "违规任务/组:" in error
    assert "删除低价值破冰" in error


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
    error = action_result["validation_errors"][0]
    assert "错误代码: MISSING_LOW_BARRIER_ICEBREAKER" in error
    assert "intent_type: long_term_growth" in error
    assert "failed_rule: 破冰法则" in error
    assert "第一步改成必须 <= 5 分钟" in error


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
    assert any("错误代码: HORIZON_OVER_EXPANDED" in error for error in result["validation_errors"])
    assert any("intent_type: long_term_growth" in error for error in result["validation_errors"])
    assert any("failed_rule: Scope Horizon" in error for error in result["validation_errors"])
    assert any("只保留当前启动阶段 Phase 1" in error for error in result["validation_errors"])


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
    assert any("错误代码: HORIZON_OVER_EXPANDED" in error for error in result["validation_errors"])
    assert any("24-72" in error for error in result["validation_errors"])


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
    assert any("错误代码: EXPLORATION_PREMATURE_EXECUTION" in error for error in result["validation_errors"])
    assert any("intent_type: exploration_decision" in error for error in result["validation_errors"])
    assert any("低成本验证" in error for error in result["validation_errors"])


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
    assert "错误代码: CHECKLIST_NOT_GROUPED" in result["validation_errors"][0]
    assert "intent_type: context_checklist" in result["validation_errors"][0]
    assert "按位置、工具、顺路关系或时间场景聚合" in result["validation_errors"][0]


def test_validator_rejects_low_actionability_score_with_structured_feedback():
    result = asyncio.run(
        task_tree_validator_node(
            {
                "task_tree": plan_with_action_quality_issue(
                    title="学习语法",
                    verb="学习",
                    estimated_minutes=25,
                    done_criteria=None,
                ),
                "intent_profile": {"intent_type": "short_term_delivery"},
                "replan_attempts": 0,
            }
        )
    )

    assert result["validation_status"] == "needs_replan"
    error = result["validation_errors"][0]
    assert "错误代码: ACTION_QUALITY_LOW_SCORE" in error
    assert "任务标题: 学习语法" in error
    assert "actionability_score:" in error
    assert "quality_issues:" in error
    assert "abstract_task_violation" in error
    assert "missing_done_criteria" in error
    assert "只修复该低质量任务" in error
    assert "不要重写整棵任务树" in error


def test_validator_rejects_abstract_action_even_with_done_criteria():
    result = asyncio.run(
        task_tree_validator_node(
            {
                "task_tree": plan_with_action_quality_issue(
                    title="准备资料",
                    verb="准备",
                    estimated_minutes=15,
                    done_criteria="保存 1 个可打开的资料链接。",
                ),
                "intent_profile": {"intent_type": "short_term_delivery"},
                "replan_attempts": 0,
            }
        )
    )

    assert result["validation_status"] == "needs_replan"
    assert any("错误代码: ACTION_QUALITY_LOW_SCORE" in error for error in result["validation_errors"])
    assert any("abstract_task_violation" in error for error in result["validation_errors"])


def test_validator_rejects_invalid_action_quality_field_values():
    result = asyncio.run(
        task_tree_validator_node(
            {
                "task_tree": plan_with_action_quality_issue(
                    title="列出周报核心进展",
                    verb="列出",
                    estimated_minutes=30,
                    done_criteria="完成任务",
                    start_hint="开始做",
                    fallback_action="少做一点",
                ),
                "intent_profile": {"intent_type": "short_term_delivery"},
                "replan_attempts": 0,
            }
        )
    )

    assert result["validation_status"] == "needs_replan"
    joined_errors = "\n".join(result["validation_errors"])
    assert "错误代码: ACTION_QUALITY_INVALID_FIELD" in joined_errors
    assert "invalid_done_criteria" in joined_errors
    assert "invalid_start_hint" in joined_errors
    assert "invalid_fallback_action" in joined_errors
    assert "给出具体完成标准、可立即执行的第一步和更小替代动作" in joined_errors


def test_validator_rejects_missing_done_criteria_for_long_action_only():
    result = asyncio.run(
        task_tree_validator_node(
            {
                "task_tree": plan_with_action_quality_issue(
                    title="撰写商业计划书核心痛点大纲",
                    verb="撰写",
                    estimated_minutes=30,
                    done_criteria=None,
                ),
                "intent_profile": {"intent_type": "short_term_delivery"},
                "replan_attempts": 0,
            }
        )
    )
    tiny_result = asyncio.run(
        task_tree_validator_node(
            {
                "task_tree": plan_with_action_quality_issue(
                    title="保存会议室门牌照片",
                    verb="保存",
                    estimated_minutes=2,
                    done_criteria=None,
                ),
                "intent_profile": {"intent_type": "context_checklist"},
                "replan_attempts": 0,
            }
        )
    )

    assert result["validation_status"] == "needs_replan"
    assert "错误代码: ACTION_QUALITY_MISSING_DONE_CRITERIA" in result["validation_errors"][0]
    assert "预计时间较长" in result["validation_errors"][0]
    assert tiny_result["validation_status"] == "valid"


def test_validator_accepts_high_quality_action_with_action_quality_fields():
    result = asyncio.run(
        task_tree_validator_node(
            {
                "task_tree": plan_with_action_quality_issue(
                    title="列出周报的 3 项核心进展",
                    verb="列出",
                    estimated_minutes=25,
                    done_criteria="写出 3 项进展，每项包含结果和影响。",
                    start_hint="打开本周任务记录。",
                    fallback_action="如果没时间写 3 项，就先写最重要的 1 项。",
                ),
                "intent_profile": {"intent_type": "short_term_delivery"},
                "replan_attempts": 0,
            }
        )
    )

    assert result["validation_status"] == "valid"


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


def test_route_from_start_skips_profile_for_next_phase():
    assert route_from_start({"planning_mode": "next_phase"}) == "planner"
    assert route_from_start({"planning_mode": "initial"}) == "intent_profiler"
    assert route_from_start({}) == "intent_profiler"


def test_next_phase_graph_skips_profiler_and_interrupt_carries_request_identity():
    planner = CapturingPlanner([phase_plan(current_order=2)])
    profiler = CapturingIntentProfiler(
        {
            "intent_type": "short_term_delivery",
            "time_horizon": "hours",
            "confidence_score": 0.9,
        }
    )
    graph = build_task_graph(
        planner=planner,
        intent_profiler=profiler,
        checkpointer=TenantAwareMemorySaver(),
    )
    config = create_graph_config(user_id="user_1", thread_id="thread_phase")

    chunks = asyncio.run(
        _collect_astream(
            graph.astream(
                {
                    "user_id": "user_1",
                    "thread_id": "thread_phase",
                    "intent_text": "Learn Japanese N3",
                    "intent_profile": {
                        "intent_type": "long_term_growth",
                        "time_horizon": "months",
                        "confidence_score": 0.95,
                    },
                    "planning_mode": "next_phase",
                    "phase_request_id": "11111111-1111-1111-1111-111111111111",
                    "committed_task_tree": phase_plan(current_order=1),
                    "current_phase_task_summary": "1/1 AI actions completed",
                },
                config,
            )
        )
    )

    assert "planner" in chunks[0]
    assert profiler.inputs == []
    interrupt_payload = chunks[-1]["__interrupt__"][0].value
    assert interrupt_payload["planning_mode"] == "next_phase"
    assert interrupt_payload["phase_request_id"] == "11111111-1111-1111-1111-111111111111"


def test_route_after_human_review_rejects_next_phase_through_cancellation_node():
    state = {
        "planning_mode": "next_phase",
        "human_decision": {"action": "reject"},
    }

    assert route_after_human_review(state) == "cancel_phase"


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


def test_exploration_decision_prompt_requires_current_judgment_summary() -> None:
    prompt = build_planner_prompt(
        "我是否要考虑转行产品经理",
        intent_profile={
            "intent_type": "exploration_decision",
            "time_horizon": "days",
            "confidence_score": 0.9,
        },
    )

    assert "先给 1-2 句当前判断" in prompt
    assert "summary" in prompt
    assert "不要直接生成长期执行计划" in prompt
