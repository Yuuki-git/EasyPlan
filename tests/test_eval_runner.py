import importlib.util
import sys
from pathlib import Path
from typing import Any

import pytest


def _load_eval_runner():
    module_path = Path(__file__).parent / "run_evals.py"
    spec = importlib.util.spec_from_file_location("eval_runner", module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_evaluate_plan_flags_valid_tree_top_level_limit_and_low_value_icebreaker():
    runner = _load_eval_runner()
    case = runner.EvalCase(
        input="今天下午4点前必须把商业计划书写完",
        expected_intent_type="short_term_delivery",
        expected_profile_horizon="hours",
        scope_horizon_rule="short_term_delivery_window",
        must_have_icebreaker=False,
        max_nodes=1,
        description="短期交付不能有低智破冰动作",
    )
    plan = {
        "root": {
            "client_node_id": "root",
            "title": "完成商业计划书",
            "description": None,
            "verb": "完成",
            "estimated_minutes": 120,
            "node_type": "group",
            "depends_on": [],
            "children": [
                {
                    "client_node_id": "open-doc",
                    "title": "打开电脑和文档",
                    "description": None,
                    "verb": "打开",
                    "estimated_minutes": 1,
                    "node_type": "action",
                    "depends_on": [],
                    "children": [],
                },
                {
                    "client_node_id": "draft-core",
                    "title": "撰写核心模块",
                    "description": None,
                    "verb": "撰写",
                    "estimated_minutes": 20,
                    "node_type": "action",
                    "depends_on": [],
                    "children": [],
                },
            ],
        },
        "summary": "完成商业计划书",
        "assumptions": [],
    }

    result = runner.evaluate_plan(
        case,
        plan,
        intent_profile={
            "intent_type": "short_term_delivery",
            "time_horizon": "hours",
            "confidence_score": 0.92,
        },
    )

    assert result.valid_task_tree is True
    assert result.actual_intent_type == "short_term_delivery"
    assert result.intent_type_matches_expected is True
    assert result.actual_time_horizon == "hours"
    assert result.time_horizon_matches_expected is True
    assert result.strategy_compliant is False
    assert "top-level node count" in result.strategy_errors[0]
    assert result.top_level_node_count == 2
    assert result.top_level_exceeds_max is True
    assert result.contains_low_value_icebreaker is True
    assert result.short_term_delivery_without_low_value_icebreaker is False


def test_low_value_icebreaker_detection_only_checks_first_action():
    runner = _load_eval_runner()
    task_tree = runner.TaskTree.model_validate(
        {
            "root": {
                "client_node_id": "root",
                "title": "完成面试准备",
                "description": None,
                "verb": "完成",
                "estimated_minutes": 60,
                "node_type": "group",
                "depends_on": [],
                "children": [
                    {
                        "client_node_id": "draft",
                        "title": "撰写自我介绍",
                        "description": "形成可朗读的完整稿件。",
                        "verb": "撰写",
                        "estimated_minutes": 30,
                        "node_type": "action",
                        "depends_on": [],
                        "children": [],
                    },
                    {
                        "client_node_id": "materials",
                        "title": "准备面试资料",
                        "description": "汇总作品和项目证据。",
                        "verb": "准备",
                        "estimated_minutes": 20,
                        "node_type": "action",
                        "depends_on": ["draft"],
                        "children": [],
                    },
                ],
            },
            "summary": "完成面试准备",
            "assumptions": [],
        }
    )

    assert runner.contains_low_value_icebreaker(task_tree) is False


def test_evaluate_plan_reports_invalid_task_tree_without_raising():
    runner = _load_eval_runner()
    case = runner.EvalCase(
        input="我想学日语",
        expected_intent_type="long_term_growth",
        expected_profile_horizon="months",
        scope_horizon_rule="long_term_phase_1_72h",
        must_have_icebreaker=True,
        max_nodes=12,
        description="invalid output should be captured",
    )

    result = runner.evaluate_plan(case, {"root": {"title": "missing fields"}})

    assert result.valid_task_tree is False
    assert result.validation_error
    assert result.strategy_compliant is None


def test_load_cases_reads_planning_jsonl_fixture():
    runner = _load_eval_runner()

    cases = runner.load_cases(Path(__file__).parent / "evals" / "planning_cases.jsonl")

    assert len(cases) == 54
    intent_counts = {
        intent_type: sum(1 for case in cases if case.expected_intent_type == intent_type)
        for intent_type in {
            "long_term_growth",
            "short_term_delivery",
            "context_checklist",
            "exploration_decision",
        }
    }
    assert all(count >= 8 for count in intent_counts.values())
    long_term_v2_cases = [
        case
        for case in cases
        if case.case_id is not None and 33 <= int(case.case_id) <= 42
    ]
    assert [case.case_id for case in long_term_v2_cases] == [
        str(index) for index in range(33, 43)
    ]
    assert all(case.require_outcome_checkpoints for case in long_term_v2_cases)
    strategy_cases = [case for case in cases if case.case_id and int(case.case_id) >= 43]
    assert [case.case_id for case in strategy_cases] == [str(index) for index in range(43, 55)]
    assert sum(case.expected_intent_type == "short_term_delivery" for case in strategy_cases) == 6
    assert sum(case.expected_intent_type == "exploration_decision" for case in strategy_cases) == 6


def test_eval_case_rejects_legacy_horizon_field() -> None:
    runner = _load_eval_runner()

    with pytest.raises(TypeError):
        runner.EvalCase(
            input="legacy",
            expected_intent_type="long_term_growth",
            expected_horizon="72h",
            must_have_icebreaker=True,
            max_nodes=5,
            description="legacy mixed contract",
        )


@pytest.mark.parametrize(
    ("profile_horizon", "scope_rule"),
    [
        ("72h", "long_term_phase_1_72h"),
        ("months", "unsupported_scope"),
        ("months", "short_term_delivery_window"),
    ],
)
def test_eval_case_rejects_invalid_profile_or_scope_enum(profile_horizon, scope_rule) -> None:
    runner = _load_eval_runner()

    with pytest.raises(ValueError):
        runner.EvalCase(
            input="invalid enum",
            expected_intent_type="long_term_growth",
            expected_profile_horizon=profile_horizon,
            scope_horizon_rule=scope_rule,
            must_have_icebreaker=True,
            max_nodes=5,
            description="strict enum",
        )


def test_long_term_eval_reports_loop_checkpoint_and_capacity_metrics():
    runner = _load_eval_runner()
    case = runner.EvalCase(
        input="半年后跑半马，每周可训练 4 次",
        expected_intent_type="long_term_growth",
        expected_profile_horizon="months",
        scope_horizon_rule="long_term_phase_1_72h",
        must_have_icebreaker=True,
        max_nodes=5,
        description="loop contract",
        case_id="34",
        expected_loop_min=1,
        expected_loop_max=2,
        expected_weekly_target=4,
        require_outcome_checkpoints=True,
        forbid_future_occurrences=True,
    )
    plan = {
        "root": {
            "client_node_id": "root",
            "title": "启动半马训练",
            "description": None,
            "verb": "启动",
            "estimated_minutes": 5,
            "node_type": "group",
            "depends_on": [],
            "children": [
                {
                    "client_node_id": "first",
                    "title": "保存一条附近跑步路线",
                    "description": "记录起点和终点",
                    "verb": "保存",
                    "estimated_minutes": 5,
                    "node_type": "action",
                    "depends_on": [],
                    "children": [],
                    "done_criteria": "路线已保存",
                    "start_hint": "打开地图",
                }
            ],
        },
        "summary": "Phase 1 启动计划",
        "assumptions": [],
        "planning_context": {
            "schema_version": 2,
            "intent_type": "long_term_growth",
            "time_horizon": "months",
            "roadmap": [
                {
                    "phase_id": "phase_01",
                    "order": 1,
                    "title": "启动",
                    "objective": "建立基线",
                    "status": "current",
                },
                {
                    "phase_id": "phase_02",
                    "order": 2,
                    "title": "积累",
                    "objective": "提高耐力",
                    "status": "planned",
                },
                {
                    "phase_id": "phase_03",
                    "order": 3,
                    "title": "验证",
                    "objective": "完成测试",
                    "status": "planned",
                },
            ],
            "current_phase": {
                "phase_id": "phase_01",
                "title": "启动",
                "objective": "建立基线",
                "completion_rule": "long_term_execution_gate",
                "estimated_duration_weeks": 4,
            },
            "next_action_client_node_id": "first",
            "practice_loops": [
                {
                    "loop_id": "running",
                    "title": "完成一次跑步训练",
                    "target_per_week": 3,
                    "duration_weeks": 4,
                    "done_criteria": "完成训练并记录距离",
                }
            ],
            "outcome_checkpoints": [
                {
                    "checkpoint_id": "distance",
                    "title": "完成一次距离测试",
                    "evidence_type": "numeric",
                    "unit": "km",
                    "operator": "gte",
                    "target_value": 5,
                }
            ],
            "phase_gate": {
                "process_threshold": 0.8,
                "outcome_rule": "all_required",
            },
        },
    }

    result = runner.evaluate_plan(
        case,
        plan,
        intent_profile={
            "intent_type": "long_term_growth",
            "time_horizon": "months",
        },
    )

    assert result.valid_task_tree is True
    assert result.loop_count == 1
    assert result.checkpoint_count == 1
    assert result.commitment_count == 3
    assert result.weekly_target_matches is False
    assert result.outcome_evidence_present is True
    assert any(
        error.startswith("weekly_target_mismatch:")
        for error in result.strategy_errors
    )


def test_schema_v2_horizon_allows_loop_cadence_without_future_occurrences():
    runner = _load_eval_runner()
    case = runner.EvalCase(
        input="今年想把英语口语练到可以参加海外面试",
        expected_intent_type="long_term_growth",
        expected_profile_horizon="months",
        scope_horizon_rule="long_term_phase_1_72h",
        must_have_icebreaker=True,
        max_nodes=5,
        description="schema v2 cadence is not an expanded schedule",
    )
    task_tree = runner.TaskTree.model_validate(
        {
            "root": {
                "client_node_id": "root",
                "title": "启动口语练习",
                "description": None,
                "verb": "启动",
                "estimated_minutes": 5,
                "node_type": "group",
                "depends_on": [],
                "children": [
                    {
                        "client_node_id": "schedule",
                        "title": "制定每周 3 次口语练习计划",
                        "description": "只确定练习节奏，不展开未来日期。",
                        "verb": "制定",
                        "estimated_minutes": 10,
                        "node_type": "action",
                        "depends_on": [],
                        "children": [],
                    }
                ],
            },
            "summary": "建立可执行的口语练习节奏",
            "assumptions": [],
            "planning_context": {
                "schema_version": 2,
                "intent_type": "long_term_growth",
                "time_horizon": "months",
                "roadmap": [
                    {
                        "phase_id": "phase_01",
                        "order": 1,
                        "title": "启动",
                        "objective": "建立节奏",
                        "status": "current",
                    },
                    {
                        "phase_id": "phase_02",
                        "order": 2,
                        "title": "积累",
                        "objective": "持续练习",
                        "status": "planned",
                    },
                    {
                        "phase_id": "phase_03",
                        "order": 3,
                        "title": "验证",
                        "objective": "完成模拟面试",
                        "status": "planned",
                    },
                ],
                "current_phase": {
                    "phase_id": "phase_01",
                    "title": "启动",
                    "objective": "建立节奏",
                    "completion_rule": "long_term_execution_gate",
                    "estimated_duration_weeks": 4,
                },
                "next_action_client_node_id": "schedule",
                "practice_loops": [
                    {
                        "loop_id": "speaking",
                        "title": "完成一次口语练习",
                        "target_per_week": 3,
                        "duration_weeks": 4,
                        "done_criteria": "完成练习并保存录音",
                    }
                ],
                "outcome_checkpoints": [
                    {
                        "checkpoint_id": "confidence",
                        "title": "完成口语自评",
                        "evidence_type": "self_assessment",
                        "operator": "gte",
                        "target_value": 3,
                    }
                ],
                "phase_gate": {
                    "process_threshold": 0.8,
                    "outcome_rule": "all_required",
                },
            },
        }
    )

    assert runner.collect_horizon_errors(case, task_tree) == []


def test_future_occurrence_detection_distinguishes_weekly_count_from_weekday():
    runner = _load_eval_runner()

    def tree_with_title(title: str):
        return runner.TaskTree.model_validate(
            {
                "root": {
                    "client_node_id": "root",
                    "title": "启动训练",
                    "description": None,
                    "verb": "启动",
                    "estimated_minutes": 5,
                    "node_type": "group",
                    "depends_on": [],
                    "children": [
                        {
                            "client_node_id": "task",
                            "title": title,
                            "description": None,
                            "verb": "制定",
                            "estimated_minutes": 10,
                            "node_type": "action",
                            "depends_on": [],
                            "children": [],
                        }
                    ],
                },
                "summary": "启动训练",
                "assumptions": [],
            }
        )

    assert runner._contains_future_occurrence_nodes(
        tree_with_title("制定本周三次训练时间表")
    ) is False
    assert runner._contains_future_occurrence_nodes(
        tree_with_title("打开跑步 App 查看本周天气")
    ) is False
    assert runner._contains_future_occurrence_nodes(
        tree_with_title("安排周三训练")
    ) is True


def test_run_cases_profiles_intent_before_building_strategy_prompt():
    runner = _load_eval_runner()
    case = runner.EvalCase(
        input="今天下午4点前必须把商业计划书写完",
        expected_intent_type="short_term_delivery",
        expected_profile_horizon="hours",
        scope_horizon_rule="short_term_delivery_window",
        must_have_icebreaker=False,
        max_nodes=8,
        description="短期交付必须走时间盒策略",
    )
    planner = FakePlanner()

    results = runner.asyncio.run(
        runner.run_cases([case], provider=None, model=None, planner=planner)
    )

    assert planner.profile_inputs == [case.input]
    assert len(planner.prompts) == 1
    assert "时间盒法则" in planner.prompts[0]
    assert results[0].actual_intent_type == "short_term_delivery"
    assert results[0].intent_type_matches_expected is True
    assert results[0].strategy_compliant is True


def test_run_cases_replans_with_runtime_validator_feedback():
    runner = _load_eval_runner()

    class ReplanningPlanner:
        def __init__(self) -> None:
            self.prompts: list[str] = []

        async def profile_intent(self, intent_text: str, **_: Any):
            return {
                "intent_type": "short_term_delivery",
                "time_horizon": "hours",
                "confidence_score": 0.95,
            }

        async def create_plan(self, prompt: str, **_: Any):
            self.prompts.append(prompt)
            title = "打开 Word 准备开始" if len(self.prompts) == 1 else "撰写核心大纲"
            return {
                "root": {
                    "client_node_id": "root",
                    "title": "完成交付",
                    "description": None,
                    "verb": "完成",
                    "estimated_minutes": 30,
                    "node_type": "group",
                    "depends_on": [],
                    "children": [
                        {
                            "client_node_id": "action",
                            "title": title,
                            "description": None,
                            "verb": "撰写",
                            "estimated_minutes": 15,
                            "node_type": "action",
                            "depends_on": [],
                            "children": [],
                            "done_criteria": "保存一份包含三个要点的大纲",
                            "start_hint": "先写第一个核心要点",
                        }
                    ],
                },
                "summary": "完成交付",
                "assumptions": [],
            }

    case = runner.EvalCase(
        input="今天完成交付",
        expected_intent_type="short_term_delivery",
        expected_profile_horizon="hours",
        scope_horizon_rule="short_term_delivery_window",
        must_have_icebreaker=False,
        max_nodes=8,
        description="runtime validator feedback",
    )
    planner = ReplanningPlanner()

    results = runner.asyncio.run(
        runner.run_cases([case], provider=None, model=None, planner=planner)
    )

    assert len(planner.prompts) == 2
    assert "LOW_VALUE_ICEBREAKER_IN_SPRINT" in planner.prompts[1]
    assert results[0].passed is True


def test_summarize_reports_core_eval_metrics_and_threshold_readiness():
    runner = _load_eval_runner()
    passed = runner.EvalResult(
        input="a",
        expected_intent_type="short_term_delivery",
        expected_profile_horizon="hours",
        actual_intent_type="short_term_delivery",
        actual_time_horizon="hours",
        intent_type_matches_expected=True,
        time_horizon_matches_expected=True,
        valid_task_tree=True,
        top_level_node_count=1,
        total_node_count=2,
        max_nodes=8,
        top_level_exceeds_max=False,
        strategy_compliant=True,
        contains_low_value_icebreaker=False,
        short_term_delivery_without_low_value_icebreaker=True,
        must_have_icebreaker=False,
        icebreaker_present=True,
    )
    failed_profile = runner.EvalResult(
        input="b",
        expected_intent_type="context_checklist",
        expected_profile_horizon="hours",
        actual_intent_type="short_term_delivery",
        actual_time_horizon="hours",
        intent_type_matches_expected=False,
        time_horizon_matches_expected=True,
        valid_task_tree=True,
        top_level_node_count=1,
        total_node_count=2,
        max_nodes=8,
        top_level_exceeds_max=False,
        strategy_compliant=False,
        strategy_errors=["context_checklist: related actions should be grouped by context"],
        contains_low_value_icebreaker=False,
        short_term_delivery_without_low_value_icebreaker=None,
        must_have_icebreaker=False,
        icebreaker_present=True,
    )
    failed_json = runner.EvalResult(
        input="c",
        expected_intent_type="long_term_growth",
        expected_profile_horizon="months",
        actual_intent_type="long_term_growth",
        actual_time_horizon="days",
        intent_type_matches_expected=True,
        time_horizon_matches_expected=True,
        valid_task_tree=False,
        top_level_node_count=None,
        total_node_count=None,
        max_nodes=12,
        top_level_exceeds_max=None,
        strategy_compliant=None,
        contains_low_value_icebreaker=None,
        short_term_delivery_without_low_value_icebreaker=None,
        must_have_icebreaker=True,
        icebreaker_present=None,
    )

    summary = runner.summarize([passed, failed_profile, failed_json])

    assert summary["intent_classification_accuracy"] == 2 / 3
    assert summary["strategy_compliance_rate"] == 1 / 3
    assert summary["json_parse_success_rate"] == 2 / 3
    assert summary["passed"] == 1
    assert "action_quality_pass_rate" in summary
    assert "average_actionability_score" in summary
    assert "done_criteria_coverage" in summary
    assert "abstract_task_violation_rate" in summary


@pytest.mark.parametrize(
    "metric",
    ["profile_horizon_accuracy", "scope_horizon_compliance_rate"],
)
def test_strict_gate_requires_profile_and_scope_horizon_to_each_be_perfect(metric):
    runner = _load_eval_runner()
    summary = {
        "intent_classification_accuracy": 1.0,
        "strategy_compliance_rate": 1.0,
        "json_parse_success_rate": 1.0,
        "horizon_accuracy": 1.0,
        "profile_horizon_accuracy": 1.0,
        "scope_horizon_compliance_rate": 1.0,
        "pass_rate": 1.0,
        "strategy_context_coverage": 1.0,
        "delivery_contract_pass_rate": 1.0,
        "decision_contract_pass_rate": 1.0,
        "explicit_constraint_preservation_rate": 1.0,
        "strategy_reference_integrity_rate": 1.0,
    }
    args = runner.argparse.Namespace(
        min_intent_accuracy=0.875,
        min_strategy_compliance=0.8,
        min_json_parse_success=1.0,
        min_horizon_accuracy=0.8,
        min_pass_rate=0.7,
    )

    assert runner.core_strict_gate_failed(summary, args) is False
    summary[metric] = 0.99
    assert runner.core_strict_gate_failed(summary, args) is True


def test_exploration_decision_does_not_require_five_minute_icebreaker():
    runner = _load_eval_runner()
    case = runner.EvalCase(
        input="我不确定要不要转行产品经理",
        expected_intent_type="exploration_decision",
        expected_profile_horizon="days",
        scope_horizon_rule="exploration_decision_window",
        must_have_icebreaker=True,
        max_nodes=6,
        description="探索决策不强制 <=5 分钟破冰",
    )
    plan = {
        "root": {
            "client_node_id": "root",
            "title": "澄清转行决策",
            "description": None,
            "verb": "澄清",
            "estimated_minutes": 90,
            "node_type": "group",
            "depends_on": [],
            "children": [
                {
                    "client_node_id": "reasons",
                    "title": "写下转行产品经理的 3 个原因",
                    "description": "澄清动机和担忧。",
                    "verb": "写下",
                    "estimated_minutes": 10,
                    "node_type": "action",
                    "depends_on": [],
                    "children": [],
                },
                {
                    "client_node_id": "jd",
                    "title": "找 3 个产品经理 JD",
                    "description": "收集岗位要求。",
                    "verb": "找",
                    "estimated_minutes": 20,
                    "node_type": "action",
                    "depends_on": [],
                    "children": [],
                },
            ],
        },
        "summary": (
            "当前判断：这个方向值得先继续澄清，但还不建议现在就做最终转行决定。"
            "判断依据：目前还缺少岗位要求和个人成本收益对比。"
            "下一步探索：先写下原因，再收集 JD 和现实信息。"
        ),
        "assumptions": [],
    }

    result = runner.evaluate_plan(
        case,
        plan,
        intent_profile={
            "intent_type": "exploration_decision",
            "time_horizon": "days",
            "confidence_score": 0.9,
        },
    )

    assert result.icebreaker_present is False
    assert not any("<=5 minute first-step icebreaker" in error for error in result.strategy_errors)
    assert not any("answer the question first" in error for error in result.strategy_errors)
    assert result.passed is True


def test_exploration_decision_eval_rejects_route_only_summary_without_answer_first():
    runner = _load_eval_runner()
    case = runner.EvalCase(
        input="我是否要考虑转行产品经理",
        expected_intent_type="exploration_decision",
        expected_profile_horizon="days",
        scope_horizon_rule="exploration_decision_window",
        must_have_icebreaker=False,
        max_nodes=6,
        description="探索决策必须先回答当前判断，再给依据和下一步探索",
    )
    plan = {
        "root": {
            "client_node_id": "root",
            "title": "转行产品经理探索",
            "description": None,
            "verb": "探索",
            "estimated_minutes": 60,
            "node_type": "group",
            "depends_on": [],
            "children": [
                {
                    "client_node_id": "jd",
                    "title": "找 3 个产品经理 JD",
                    "description": "收集岗位要求。",
                    "verb": "找",
                    "estimated_minutes": 20,
                    "node_type": "action",
                    "depends_on": [],
                    "children": [],
                }
            ],
        },
        "summary": "下一步探索：先找 3 个 JD，再访谈从业者，最后比较转行成本收益。",
        "assumptions": [],
    }

    result = runner.evaluate_plan(
        case,
        plan,
        intent_profile={
            "intent_type": "exploration_decision",
            "time_horizon": "days",
            "confidence_score": 0.9,
        },
    )

    assert result.strategy_compliant is False
    assert any("answer the question first" in error for error in result.strategy_errors)


def test_exploration_eval_accepts_negated_immediate_execution_judgment():
    runner = _load_eval_runner()
    case = runner.EvalCase(
        input="我是否应该辞职转行",
        expected_intent_type="exploration_decision",
        expected_profile_horizon="days",
        scope_horizon_rule="exploration_decision_window",
        must_have_icebreaker=False,
        max_nodes=6,
        description="否定立即执行是判断，不是提前执行",
    )
    plan = {
        "root": {
            "client_node_id": "root",
            "title": "澄清转行决策",
            "description": None,
            "verb": "澄清",
            "estimated_minutes": 30,
            "node_type": "group",
            "depends_on": [],
            "children": [
                {
                    "client_node_id": "research",
                    "title": "收集 3 个目标岗位 JD",
                    "description": "补齐岗位现实信息。",
                    "verb": "收集",
                    "estimated_minutes": 20,
                    "node_type": "action",
                    "depends_on": [],
                    "children": [],
                }
            ],
        },
        "summary": (
            "当前判断：现在并不是直接辞职转行的时机，更适合先做低成本探索。"
            "判断依据：岗位要求和个人成本收益仍缺少可靠信息。"
            "下一步探索：先收集岗位信息，再形成阶段性判断。"
        ),
        "assumptions": [],
    }

    result = runner.evaluate_plan(
        case,
        plan,
        intent_profile={
            "intent_type": "exploration_decision",
            "time_horizon": "days",
            "confidence_score": 0.9,
        },
    )

    assert result.strategy_compliant is True
    assert result.time_horizon_matches_expected is True
    assert result.passed is True


def test_exploration_eval_accepts_immediate_execution_risk_warning():
    runner = _load_eval_runner()

    assert (
        runner._contains_non_negated_pattern(
            "判断依据：信息不足，直接辞职风险高，应该先做低成本验证。",
            runner.EXPLORATION_EXECUTION_PATTERNS,
        )
        is False
    )
    assert (
        runner._contains_non_negated_pattern(
            "当前判断：建议立即辞职，风险可控。",
            runner.EXPLORATION_EXECUTION_PATTERNS,
        )
        is True
    )


def test_failure_diagnostics_include_actionable_eval_context():
    runner = _load_eval_runner()
    result = runner.EvalResult(
        input="我想明年考过日语 N3",
        expected_intent_type="long_term_growth",
        expected_profile_horizon="months",
        actual_intent_type="long_term_growth",
        actual_time_horizon="days",
        intent_type_matches_expected=True,
        time_horizon_matches_expected=False,
        valid_task_tree=True,
        top_level_node_count=3,
        total_node_count=5,
        max_nodes=12,
        top_level_exceeds_max=False,
        strategy_compliant=False,
        contains_low_value_icebreaker=False,
        short_term_delivery_without_low_value_icebreaker=None,
        must_have_icebreaker=True,
        icebreaker_present=False,
        strategy_errors=["long_term_growth first action is not low-barrier"],
        horizon_errors=["long_term_growth output covers full cycle"],
        top_level_preview=[
            {"title": "第1周背单词", "estimated_minutes": 120, "node_type": "action"}
        ],
        first_action_snapshot={
            "title": "第1周背单词",
            "estimated_minutes": 120,
            "done_criteria": None,
        },
    )

    diagnostics = runner.build_failure_diagnostics([result])

    assert diagnostics[0]["case_id"] == 1
    assert diagnostics[0]["failed_metrics"] == [
        "horizon_accuracy",
        "strategy_compliance",
    ]
    assert "full cycle" in diagnostics[0]["horizon_failure_reason"]
    assert diagnostics[0]["planner_top_level_tasks"][0]["title"] == "第1周背单词"
    assert diagnostics[0]["first_action"]["estimated_minutes"] == 120


def test_evaluate_plan_reports_action_quality_metrics_without_affecting_pass_status():
    runner = _load_eval_runner()
    case = runner.EvalCase(
        input="今天下午前写完项目复盘",
        expected_intent_type="short_term_delivery",
        expected_profile_horizon="hours",
        scope_horizon_rule="short_term_delivery_window",
        must_have_icebreaker=False,
        max_nodes=8,
        description="Action quality metrics should be observational only",
    )
    plan = {
        "root": {
            "client_node_id": "root",
            "title": "完成项目复盘",
            "description": None,
            "verb": "完成",
            "estimated_minutes": 60,
            "node_type": "group",
            "depends_on": [],
            "children": [
                {
                    "client_node_id": "vague",
                    "title": "学习语法",
                    "description": None,
                    "verb": "学习",
                    "estimated_minutes": 20,
                    "node_type": "action",
                    "depends_on": [],
                    "children": [],
                }
            ],
        },
        "summary": "完成项目复盘",
        "assumptions": [],
    }

    result = runner.evaluate_plan(
        case,
        plan,
        intent_profile={
            "intent_type": "short_term_delivery",
            "time_horizon": "hours",
            "confidence_score": 0.9,
        },
    )

    assert result.valid_task_tree is True
    assert result.strategy_compliant is True
    assert result.passed is True
    assert result.action_quality_pass_rate == 0.0
    assert result.done_criteria_coverage == 0.0
    assert result.abstract_task_violation_rate == 1.0


def test_load_env_file_sets_missing_environment_values(tmp_path, monkeypatch):
    runner = _load_eval_runner()
    env_path = tmp_path / ".env"
    env_path.write_text(
        "EASYPLAN_LLM_PROVIDER=xiaomi\n"
        "EASYPLAN_XIAOMI_MIMO_MODEL=\"mimo-v2.5-pro\"\n"
        "EXISTING_VALUE=from_file\n",
        encoding="utf-8",
    )
    monkeypatch.delenv("EASYPLAN_LLM_PROVIDER", raising=False)
    monkeypatch.delenv("EASYPLAN_XIAOMI_MIMO_MODEL", raising=False)
    monkeypatch.setenv("EXISTING_VALUE", "from_env")

    runner.load_env_file(env_path)

    assert runner.os.environ["EASYPLAN_LLM_PROVIDER"] == "xiaomi"
    assert runner.os.environ["EASYPLAN_XIAOMI_MIMO_MODEL"] == "mimo-v2.5-pro"
    assert runner.os.environ["EXISTING_VALUE"] == "from_env"


def test_eval_uses_shared_delivery_contract_and_reports_new_metrics(monkeypatch):
    runner = _load_eval_runner()
    monkeypatch.setenv("EASYPLAN_STRATEGY_CONTEXT_ENABLED", "true")
    case = runner.EvalCase(
        input="我只有 2 小时，今天下午 5 点前交 PPT",
        expected_intent_type="short_term_delivery",
        expected_profile_horizon="hours",
        scope_horizon_rule="short_term_delivery_window",
        must_have_icebreaker=False,
        max_nodes=4,
        description="delivery contract",
        case_id="contract-delivery",
        require_explicit_constraint_preservation=True,
    )
    plan = {
        "root": {
            "client_node_id": "root",
            "title": "交付 PPT",
            "description": None,
            "verb": "交付",
            "estimated_minutes": 100,
            "node_type": "group",
            "depends_on": [],
            "children": [
                {
                    "client_node_id": "draft",
                    "title": "撰写 PPT 核心内容",
                    "description": "形成可评审的核心页面。",
                    "verb": "撰写",
                    "estimated_minutes": 100,
                    "node_type": "action",
                    "depends_on": [],
                    "children": [],
                    "done_criteria": "保存包含结论与证据的 PPT。",
                    "start_hint": "先写结论页标题。",
                    "fallback_action": "先完成结论页与一页证据。",
                }
            ],
        },
        "summary": "按时交付 PPT。",
        "assumptions": [],
        "planning_context": None,
        "strategy_context": {
            "schema_version": 1,
            "strategy_type": "delivery",
            "deliverable": {
                "title": "PPT",
                "format": "PPT",
                "quality_bar": ["包含结论与证据"],
            },
            "deadline": {"text": "今天下午 5 点前", "is_explicit": True},
            "time_plan": {
                "available_minutes": 120,
                "planned_minutes": 100,
                "buffer_minutes": 20,
            },
            "scope": {
                "must_have": ["核心结论"],
                "should_have": [],
                "can_cut": ["视觉润色"],
            },
            "workstreams": [
                {
                    "workstream_id": "drafting",
                    "title": "内容撰写",
                    "output": "可评审 PPT",
                    "task_client_node_ids": ["draft"],
                }
            ],
            "critical_path_client_node_ids": ["draft"],
        },
    }

    result = runner.evaluate_plan(
        case,
        plan,
        intent_profile={
            "intent_type": "short_term_delivery",
            "time_horizon": "hours",
            "confidence_score": 0.95,
        },
    )
    summary = runner.summarize([result])

    assert result.strategy_context_error_codes == []
    assert result.strategy_context_covered is True
    assert result.delivery_contract_passed is True
    assert result.explicit_constraints_preserved is True
    assert result.strategy_reference_integrity is True
    assert summary["strategy_context_coverage"] == 1.0
    assert summary["delivery_contract_pass_rate"] == 1.0
    assert summary["explicit_constraint_preservation_rate"] == 1.0
    assert summary["strategy_reference_integrity_rate"] == 1.0


def test_eval_prints_stable_strategy_reference_error_code(monkeypatch):
    runner = _load_eval_runner()
    monkeypatch.setenv("EASYPLAN_STRATEGY_CONTEXT_ENABLED", "true")
    case = runner.EvalCase(
        input="今天完成报告",
        expected_intent_type="short_term_delivery",
        expected_profile_horizon="hours",
        scope_horizon_rule="short_term_delivery_window",
        must_have_icebreaker=False,
        max_nodes=4,
        description="invalid reference",
        case_id="invalid-reference",
    )
    plan = {
        "root": {
            "client_node_id": "root",
            "title": "完成报告",
            "description": None,
            "verb": "完成",
            "estimated_minutes": 30,
            "node_type": "group",
            "depends_on": [],
            "children": [
                {
                    "client_node_id": "draft",
                    "title": "撰写报告",
                    "description": "输出报告初稿。",
                    "verb": "撰写",
                    "estimated_minutes": 30,
                    "node_type": "action",
                    "depends_on": [],
                    "children": [],
                    "done_criteria": "保存报告初稿。",
                    "start_hint": "先写核心结论。",
                    "fallback_action": "先写一条结论。",
                }
            ],
        },
        "summary": "完成报告。",
        "assumptions": [],
        "strategy_context": {
            "schema_version": 1,
            "strategy_type": "delivery",
            "deliverable": {
                "title": "报告",
                "format": "文档",
                "quality_bar": ["可评审"],
            },
            "deadline": {"text": "今天", "is_explicit": True},
            "time_plan": {
                "available_minutes": None,
                "planned_minutes": 30,
                "buffer_minutes": 0,
            },
            "scope": {"must_have": ["结论"], "should_have": [], "can_cut": []},
            "workstreams": [
                {
                    "workstream_id": "drafting",
                    "title": "撰写",
                    "output": "报告",
                    "task_client_node_ids": ["missing"],
                }
            ],
            "critical_path_client_node_ids": ["missing"],
        },
    }

    result = runner.evaluate_plan(
        case,
        plan,
        intent_profile={
            "intent_type": "short_term_delivery",
            "time_horizon": "hours",
            "confidence_score": 0.95,
        },
    )
    diagnostics = runner.build_failure_diagnostics([result])

    assert "DELIVERY_WORKSTREAM_REFERENCE_INVALID" in result.strategy_context_error_codes
    assert result.strategy_reference_integrity is False
    assert diagnostics[0]["case_id"] == "invalid-reference"
    assert "DELIVERY_WORKSTREAM_REFERENCE_INVALID" in diagnostics[0]["strategy_context_error_codes"]


def test_horizon_fails_when_profile_horizon_does_not_match_expected():
    runner = _load_eval_runner()
    case = runner.EvalCase(
        input="今天完成报告",
        expected_intent_type="short_term_delivery",
        expected_profile_horizon="hours",
        scope_horizon_rule="short_term_delivery_window",
        must_have_icebreaker=False,
        max_nodes=4,
        description="profile horizon mismatch",
    )

    result = runner.evaluate_plan(
        case,
        _simple_horizon_plan(),
        intent_profile={
            "intent_type": "short_term_delivery",
            "time_horizon": "days",
            "confidence_score": 0.95,
        },
    )

    assert result.profile_horizon_matches_expected is False
    assert result.scope_horizon_compliant is True
    assert result.time_horizon_matches_expected is False


def test_horizon_fails_when_scope_expands_despite_matching_profile():
    runner = _load_eval_runner()
    case = runner.EvalCase(
        input="三个月通过考试",
        expected_intent_type="long_term_growth",
        expected_profile_horizon="months",
        scope_horizon_rule="long_term_phase_1_72h",
        must_have_icebreaker=True,
        max_nodes=5,
        description="scope horizon violation",
    )
    plan = _simple_horizon_plan(title="第1周完成全部基础训练", minutes=5)

    result = runner.evaluate_plan(
        case,
        plan,
        intent_profile={
            "intent_type": "long_term_growth",
            "time_horizon": "months",
            "confidence_score": 0.95,
        },
    )

    assert result.profile_horizon_matches_expected is True
    assert result.scope_horizon_compliant is False
    assert result.time_horizon_matches_expected is False


def test_horizon_passes_only_when_profile_and_scope_both_pass():
    runner = _load_eval_runner()
    case = runner.EvalCase(
        input="今天完成报告",
        expected_intent_type="short_term_delivery",
        expected_profile_horizon="hours",
        scope_horizon_rule="short_term_delivery_window",
        must_have_icebreaker=False,
        max_nodes=4,
        description="profile and scope horizon pass",
    )

    result = runner.evaluate_plan(
        case,
        _simple_horizon_plan(),
        intent_profile={
            "intent_type": "short_term_delivery",
            "time_horizon": "hours",
            "confidence_score": 0.95,
        },
    )
    summary = runner.summarize([result])

    assert result.profile_horizon_matches_expected is True
    assert result.scope_horizon_compliant is True
    assert result.time_horizon_matches_expected is True
    assert summary["profile_horizon_accuracy"] == 1.0
    assert summary["scope_horizon_compliance_rate"] == 1.0
    assert summary["horizon_accuracy"] == 1.0


def _simple_horizon_plan(*, title: str = "撰写报告结论", minutes: int = 20) -> dict:
    return {
        "root": {
            "client_node_id": "root",
            "title": "执行计划",
            "description": None,
            "verb": "执行",
            "estimated_minutes": minutes,
            "node_type": "group",
            "depends_on": [],
            "children": [
                {
                    "client_node_id": "action",
                    "title": title,
                    "description": "形成可检查结果。",
                    "verb": "撰写",
                    "estimated_minutes": minutes,
                    "node_type": "action",
                    "depends_on": [],
                    "children": [],
                    "done_criteria": "保存一份可检查结果。",
                    "start_hint": "先写第一条内容。",
                    "fallback_action": "先写标题和一条要点。",
                }
            ],
        },
        "summary": "完成当前计划。",
        "assumptions": [],
    }


def test_phase_metrics_detect_completed_phase_mutation():
    runner = _load_eval_runner()
    committed = _phase_eval_tree(current_order=2)
    proposed = _phase_eval_tree(current_order=3)
    proposed["planning_context"]["roadmap"][0]["objective"] = "Mutated completed objective"
    case = runner.PhaseEvalCase(
        case_id="phase_next_keep_completed",
        mode="next_phase",
        intent_text="学习日语 N3",
        intent_profile={"intent_type": "long_term_growth", "time_horizon": "months"},
        committed_task_tree=committed,
        expect_roadmap_visible=True,
        expect_current_phase_only=True,
        expect_completed_phase_immutable=True,
    )

    metrics = runner.evaluate_phase_case(case, proposed)

    assert metrics.completed_phase_immutable is False
    assert metrics.current_phase_horizon_ok is True
    assert metrics.json_parse_success is True


def test_load_phase_cases_reads_twelve_fixed_deepseek_cases():
    runner = _load_eval_runner()

    cases = runner.load_phase_cases(
        Path(__file__).parent / "evals" / "phase_planning_cases.jsonl"
    )

    assert len(cases) == 12
    assert {case.mode for case in cases} == {"initial", "next_phase"}
    assert sum(case.mode == "next_phase" for case in cases) == 4


def _phase_eval_tree(*, current_order: int) -> dict[str, Any]:
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
    return {
        "root": {
            "client_node_id": f"phase_{current_order:02d}_root",
            "title": f"Phase {current_order}",
            "description": None,
            "verb": "推进",
            "estimated_minutes": 30,
            "node_type": "group",
            "depends_on": [],
            "children": [
                {
                    "client_node_id": f"phase_{current_order:02d}_action_01",
                    "title": "保存一份可打开的参考资料",
                    "description": None,
                    "verb": "保存",
                    "estimated_minutes": 5,
                    "node_type": "action",
                    "depends_on": [],
                    "children": [],
                    "done_criteria": "保存 1 个可正常打开的资料链接",
                    "start_hint": "打开浏览器搜索目标关键词",
                    "fallback_action": "只收藏搜索结果中的第一个链接",
                }
            ],
        },
        "summary": "当前阶段行动",
        "assumptions": [],
        "planning_context": {
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
        },
    }


class FakePlanner:
    def __init__(self) -> None:
        self.profile_inputs: list[str] = []
        self.prompts: list[str] = []

    async def profile_intent(self, intent_text: str, reasoning_sink: Any | None = None, usage_sink: Any | None = None):
        self.profile_inputs.append(intent_text)
        return {
            "intent_type": "short_term_delivery",
            "time_horizon": "hours",
            "confidence_score": 0.94,
        }

    async def create_plan(self, prompt: str, reasoning_sink: Any | None = None, usage_sink: Any | None = None):
        self.prompts.append(prompt)
        return {
            "root": {
                "client_node_id": "root",
                "title": "完成商业计划书",
                "description": None,
                "verb": "完成",
                "estimated_minutes": 120,
                "node_type": "group",
                "depends_on": [],
                "children": [
                    {
                        "client_node_id": "outline",
                        "title": "列出商业计划书核心痛点大纲",
                        "description": None,
                        "verb": "列出",
                        "estimated_minutes": 15,
                        "node_type": "action",
                        "depends_on": [],
                        "children": [],
                    }
                ],
            },
            "summary": "完成商业计划书",
            "assumptions": [],
        }
