import asyncio
import importlib.util
import json
import sys
from pathlib import Path
from uuid import uuid4


def _runner():
    path = Path(__file__).parent / "run_execution_refine_evals.py"
    spec = importlib.util.spec_from_file_location("execution_refine_eval_runner", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class FakeClient:
    def __init__(self, payload):
        self.payload = payload
        self.prompts = []

    async def create_execution_refine_proposal(self, *, prompt):
        self.prompts.append(prompt)
        return self.payload


def _valid_payload(runner, case):
    request, scope, _external = runner.build_case_context(case)
    first_id = next(iter(scope.task_records))
    return {
        "schema_version": 1,
        "proposal_type": "execution_refine",
        "mode": request.mode,
        "summary": "缩小当前动作并保留全部已有约束。",
        "user_facing_reasons": ["先完成当前最明确的交付动作。"],
        "preserved_constraints": ["历史、阶段和依赖关系保持不变。"],
        "operations": [
            {
                "operation_type": "update_task",
                "task_id": first_id,
                "changes": {"estimated_minutes": 25},
                "reason": "缩小当前动作的执行范围。",
            }
        ],
        "focus_task_ids": [],
        "estimated_focus_minutes": 0,
        "buffer_minutes": 0,
        "warnings": [],
    }


def _result(runner, case_id, *, passed=True):
    values = {metric: passed for metric in runner.METRICS}
    return runner.ExecutionRefineEvalResult(
        case_id=case_id,
        mode="progress_recovery",
        repair_attempts=0,
        error_codes=[] if passed else ["EXECUTION_REFINE_REFERENCE_INVALID"],
        operation_preview=[],
        **values,
    )


def test_loads_twenty_four_cases_split_evenly_and_covers_required_scenarios():
    runner = _runner()
    cases = runner.load_cases(runner.DEFAULT_CASES)
    assert len(cases) == 24
    assert {
        mode: sum(case.mode == mode for case in cases)
        for mode in {"time_budget", "progress_recovery", "context_change"}
    } == {"time_budget": 8, "progress_recovery": 8, "context_change": 8}
    assert {case.available_minutes for case in cases if case.available_minutes} >= {
        10,
        20,
        45,
        90,
        240,
    }
    tags = {tag for case in cases for tag in case.scenario_tags or []}
    assert {
        "completed",
        "history",
        "manual",
        "assist",
        "practice",
        "cross_project",
        "dependency",
        "no_task_fits",
    } <= tags


def test_evaluate_case_uses_runtime_validator_and_reports_eight_metrics(monkeypatch):
    runner = _runner()
    case = next(
        case
        for case in runner.load_cases(runner.DEFAULT_CASES)
        if case.case_id == "recovery-01"
    )
    payload = _valid_payload(runner, case)
    calls = 0
    original = runner.validate_execution_refine_proposal

    def wrapped(**kwargs):
        nonlocal calls
        calls += 1
        return original(**kwargs)

    monkeypatch.setattr(runner, "validate_execution_refine_proposal", wrapped)
    result = asyncio.run(runner.evaluate_case(FakeClient(payload), case))
    assert calls == 1
    assert result.passed is True
    assert all(getattr(result, metric) is True for metric in runner.METRICS)


def test_time_05_prompt_separates_remaining_capacity_from_read_only_assist_work():
    runner = _runner()
    case = next(
        case
        for case in runner.load_cases(runner.DEFAULT_CASES)
        if case.case_id == "time-05"
    )
    request, scope, _external = runner.build_case_context(case)
    prompt = runner.build_execution_refine_prompt(request=request, scope=scope)

    assert '"available_minutes":240' in prompt
    assert '"buffer_minutes":20' in prompt
    assert '"protected_commitment_minutes":0' in prompt
    assert '"remaining_focus_minutes":220' in prompt
    assert '"protected_reason":"task_assist_child"' in prompt
    assert "read_only_not_capacity_counted" in prompt
    assert "不得重复计时" in prompt


def test_invalid_reference_is_repaired_at_most_twice_and_fails_reference_metric():
    runner = _runner()
    case = next(
        case
        for case in runner.load_cases(runner.DEFAULT_CASES)
        if case.case_id == "recovery-01"
    )
    payload = _valid_payload(runner, case)
    payload["operations"][0]["task_id"] = str(uuid4())
    client = FakeClient(payload)
    result = asyncio.run(runner.evaluate_case(client, case))
    assert len(client.prompts) == runner.MAX_EXECUTION_REFINE_REPAIRS + 1
    assert result.json_parse_success is True
    assert result.reference_integrity is False
    assert "EXECUTION_REFINE_REFERENCE_INVALID" in result.error_codes
    assert result.passed is False


def test_release_gate_rejects_twenty_three_of_twenty_four():
    runner = _runner()
    results = [_result(runner, f"case-{index}") for index in range(23)]
    results.append(_result(runner, "case-24", passed=False))
    summary = runner.summarize(results)
    assert summary["passed"] == 23
    assert runner.release_gate_failed(summary) is True

    all_passed = [_result(runner, f"case-{index}") for index in range(24)]
    assert runner.release_gate_failed(runner.summarize(all_passed)) is False


def test_diagnostic_jsonl_contains_each_case_and_operation_preview(tmp_path):
    runner = _runner()
    results = [_result(runner, "a"), _result(runner, "b", passed=False)]
    path = tmp_path / "execution-refine-diagnostics.jsonl"
    runner.print_report(results, diagnostics_jsonl=path)
    payloads = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert [payload["case_id"] for payload in payloads] == ["a", "b"]
    assert payloads[1]["error_codes"] == ["EXECUTION_REFINE_REFERENCE_INVALID"]
