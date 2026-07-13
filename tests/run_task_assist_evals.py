from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Literal


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CASES = PROJECT_ROOT / "tests" / "evals" / "task_assist_cases.jsonl"
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.api.schemas import TaskAssistMode  # noqa: E402
from app.services.llm_service import DeepSeekTaskAssistClient  # noqa: E402
from app.services.task_assist import (  # noqa: E402
    TASK_ASSIST_PROPOSAL_ADAPTER,
    TaskAssistContext,
    build_task_assist_prompt,
    validate_task_assist_proposal,
)


@dataclass(frozen=True)
class TaskAssistEvalCase:
    case_id: str
    mode: TaskAssistMode
    task: dict[str, Any]
    user_context: str | None
    expected_terms: list[str]
    description: str


@dataclass
class TaskAssistEvalResult:
    case_id: str
    mode: str
    json_parse_success: bool
    mode_match: bool
    actionability: bool
    scope_compliance: bool
    reference_integrity: bool
    explicit_constraint_preservation: bool
    error_codes: list[str]

    @property
    def passed(self) -> bool:
        return all(
            (
                self.json_parse_success,
                self.mode_match,
                self.actionability,
                self.scope_compliance,
                self.reference_integrity,
                self.explicit_constraint_preservation,
            )
        )


def load_env_file(path: Path = PROJECT_ROOT / ".env") -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def load_cases(path: Path) -> list[TaskAssistEvalCase]:
    cases: list[TaskAssistEvalCase] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        payload = json.loads(line)
        try:
            cases.append(TaskAssistEvalCase(**payload))
        except TypeError as exc:
            raise ValueError(f"invalid task assist eval case at line {line_number}: {exc}") from exc
    return cases


async def evaluate_case(client: Any, case: TaskAssistEvalCase) -> TaskAssistEvalResult:
    context = TaskAssistContext(
        task=case.task,
        ancestors=[],
        project={},
        existing_children=[],
        user_context=case.user_context,
    )
    prompt = build_task_assist_prompt(mode=case.mode, context=context)
    try:
        payload = await client.create_task_assist_proposal(mode=case.mode, prompt=prompt)
        proposal = TASK_ASSIST_PROPOSAL_ADAPTER.validate_python(payload)
    except Exception as exc:
        return TaskAssistEvalResult(
            case_id=case.case_id,
            mode=case.mode,
            json_parse_success=False,
            mode_match=False,
            actionability=False,
            scope_compliance=False,
            reference_integrity=False,
            explicit_constraint_preservation=False,
            error_codes=[type(exc).__name__],
        )

    errors = validate_task_assist_proposal(
        mode=case.mode,
        proposal=proposal,
        parent_estimated_minutes=case.task.get("estimated_minutes"),
    )
    serialized = json.dumps(proposal.model_dump(mode="json"), ensure_ascii=False)
    action_errors = [
        error
        for error in errors
        if "ABSTRACT_ACTION" in error or "INVALID_" in error
    ]
    scope_errors = [error for error in errors if "SCOPE_EXPANSION" in error]
    reference_errors = [
        error
        for error in errors
        if "REFERENCE_INVALID" in error or "DEPENDENCY_CYCLE" in error
    ]
    return TaskAssistEvalResult(
        case_id=case.case_id,
        mode=case.mode,
        json_parse_success=True,
        mode_match=proposal.proposal_type == case.mode,
        actionability=not action_errors,
        scope_compliance=not scope_errors,
        reference_integrity=not reference_errors,
        explicit_constraint_preservation=all(term in serialized for term in case.expected_terms),
        error_codes=errors,
    )


async def run_cases(cases: list[TaskAssistEvalCase], client: Any) -> list[TaskAssistEvalResult]:
    results = []
    for case in cases:
        results.append(await evaluate_case(client, case))
    return results


def summarize(results: list[TaskAssistEvalResult]) -> dict[str, Any]:
    total = len(results)

    def rate(field: str) -> float:
        return sum(bool(getattr(result, field)) for result in results) / total if total else 0.0

    return {
        "cases": total,
        "passed": sum(result.passed for result in results),
        "pass_rate": rate("passed"),
        "json_parse_success": rate("json_parse_success"),
        "mode_match": rate("mode_match"),
        "actionability": rate("actionability"),
        "scope_compliance": rate("scope_compliance"),
        "reference_integrity": rate("reference_integrity"),
        "explicit_constraint_preservation": rate("explicit_constraint_preservation"),
    }


def print_report(results: list[TaskAssistEvalResult]) -> None:
    for result in results:
        print(json.dumps(asdict(result), ensure_ascii=False))
    print(json.dumps({"summary": summarize(results)}, ensure_ascii=False, indent=2))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run EasyPlan Task Assist evals")
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES)
    parser.add_argument("--provider", default="deepseek", choices=["deepseek"])
    parser.add_argument("--model", default=None)
    parser.add_argument("--strict-exit", action="store_true")
    return parser.parse_args()


async def amain() -> int:
    load_env_file()
    args = parse_args()
    cases = load_cases(args.cases)
    client = DeepSeekTaskAssistClient(model=args.model)
    results = await run_cases(cases, client)
    print_report(results)
    summary = summarize(results)
    if args.strict_exit and (
        summary["passed"] != summary["cases"]
        or any(
            summary[metric] < 1.0
            for metric in (
                "json_parse_success",
                "mode_match",
                "actionability",
                "scope_compliance",
                "reference_integrity",
                "explicit_constraint_preservation",
            )
        )
    ):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(amain()))
