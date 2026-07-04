import importlib.util
import sys
from pathlib import Path
from typing import Any


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
        expected_horizon="hours",
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
        expected_horizon="72h",
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

    assert len(cases) >= 32
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


def test_run_cases_profiles_intent_before_building_strategy_prompt():
    runner = _load_eval_runner()
    case = runner.EvalCase(
        input="今天下午4点前必须把商业计划书写完",
        expected_intent_type="short_term_delivery",
        expected_horizon="hours",
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


def test_summarize_reports_core_eval_metrics_and_threshold_readiness():
    runner = _load_eval_runner()
    passed = runner.EvalResult(
        input="a",
        expected_intent_type="short_term_delivery",
        expected_horizon="hours",
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
        expected_horizon="hours",
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
        expected_horizon="72h",
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


def test_exploration_decision_does_not_require_five_minute_icebreaker():
    runner = _load_eval_runner()
    case = runner.EvalCase(
        input="我不确定要不要转行产品经理",
        expected_intent_type="exploration_decision",
        expected_horizon="days",
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
        expected_horizon="days",
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
        expected_horizon="days",
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
        expected_horizon="72h",
        actual_intent_type="long_term_growth",
        actual_time_horizon="months",
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
        expected_horizon="hours",
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
