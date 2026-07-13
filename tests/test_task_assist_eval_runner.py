import asyncio
import importlib.util
import sys
from pathlib import Path


def _runner():
    path = Path(__file__).parent / "run_task_assist_evals.py"
    spec = importlib.util.spec_from_file_location("task_assist_eval_runner", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class FakeClient:
    async def create_task_assist_proposal(self, *, mode, prompt):
        if mode == "start":
            return {
                "proposal_type": "start",
                "summary": "先完成一步",
                "starter_step": _draft("starter", "列出三个市场数据来源"),
            }
        if mode == "unstick":
            return {
                "proposal_type": "unstick",
                "obstacle_summary": "资料不足",
                "recommended_option_id": "a",
                "options": [
                    {
                        "option_id": "a",
                        "title": "列资料缺口",
                        "action": "列出三个缺少的数据点",
                        "estimated_minutes": 5,
                        "tradeoff": "先缩小范围",
                    },
                    {
                        "option_id": "b",
                        "title": "查内部资料",
                        "action": "搜索项目目录中的调研记录",
                        "estimated_minutes": 10,
                        "tradeoff": "信息可能不完整",
                    },
                ],
            }
        return {
            "proposal_type": "decompose",
            "summary": "拆成两步",
            "completion_rule": "all_subtasks_completed",
            "subtasks": [
                _draft("a", "列出三个市场数据来源"),
                _draft("b", "写出市场规模判断"),
            ],
            "dependencies": [
                {"task_draft_id": "b", "depends_on_draft_id": "a"}
            ],
        }


def _draft(draft_id, title):
    return {
        "draft_id": draft_id,
        "title": title,
        "description": None,
        "estimated_minutes": 5,
        "done_criteria": "保存一个可核验结果",
        "start_hint": "打开现有材料",
        "fallback_action": None,
    }


def test_loads_eighteen_cases_split_evenly_across_modes():
    runner = _runner()
    cases = runner.load_cases(Path(__file__).parent / "evals" / "task_assist_cases.jsonl")
    assert len(cases) == 18
    assert {mode: sum(case.mode == mode for case in cases) for mode in {"start", "unstick", "decompose"}} == {
        "start": 6,
        "unstick": 6,
        "decompose": 6,
    }


def test_evaluate_case_reuses_runtime_validator_and_reports_six_metrics():
    runner = _runner()
    case = runner.TaskAssistEvalCase(
        case_id="start-test",
        mode="start",
        task={"title": "写市场分析", "estimated_minutes": 30},
        user_context=None,
        expected_terms=[],
        description="test",
    )
    result = asyncio.run(runner.evaluate_case(FakeClient(), case))
    assert result.passed is True
    assert result.json_parse_success is True
    assert result.mode_match is True
    assert result.actionability is True
    assert result.scope_compliance is True
    assert result.reference_integrity is True
    assert result.explicit_constraint_preservation is True


def test_summary_requires_every_metric_to_pass():
    runner = _runner()
    passed = runner.TaskAssistEvalResult(
        case_id="a",
        mode="start",
        json_parse_success=True,
        mode_match=True,
        actionability=True,
        scope_compliance=True,
        reference_integrity=True,
        explicit_constraint_preservation=True,
        error_codes=[],
    )
    failed = runner.TaskAssistEvalResult(
        case_id="b",
        mode="decompose",
        json_parse_success=True,
        mode_match=True,
        actionability=True,
        scope_compliance=False,
        reference_integrity=True,
        explicit_constraint_preservation=True,
        error_codes=["TASK_ASSIST_SCOPE_EXPANSION"],
    )
    summary = runner.summarize([passed, failed])
    assert summary["passed"] == 1
    assert summary["pass_rate"] == 0.5
    assert summary["scope_compliance"] == 0.5


def test_existing_planning_eval_contract_is_not_imported_or_modified():
    runner = _runner()
    assert not hasattr(runner.TaskAssistEvalCase, "expected_intent_type")
    assert runner.DEFAULT_CASES.name == "task_assist_cases.jsonl"
