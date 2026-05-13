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
    assert result.top_level_node_count == 2
    assert result.top_level_exceeds_max is True
    assert result.contains_low_value_icebreaker is True
    assert result.short_term_delivery_without_low_value_icebreaker is False


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


def test_summarize_reports_intent_accuracy_and_threshold_readiness():
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
        contains_low_value_icebreaker=False,
        short_term_delivery_without_low_value_icebreaker=None,
        must_have_icebreaker=False,
        icebreaker_present=True,
    )

    summary = runner.summarize([passed, failed_profile])

    assert summary["intent_accuracy"] == 0.5
    assert summary["horizon_accuracy"] == 1.0
    assert summary["passed"] == 1


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
