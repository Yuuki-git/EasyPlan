import importlib.util
import sys
from pathlib import Path


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

    result = runner.evaluate_plan(case, plan)

    assert result.valid_task_tree is True
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

    assert len(cases) == 4
    assert cases[1].expected_intent_type == "short_term_delivery"
    assert cases[1].max_nodes == 8
