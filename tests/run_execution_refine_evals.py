from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Literal
from uuid import NAMESPACE_URL, UUID, uuid5


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CASES = PROJECT_ROOT / "tests" / "evals" / "execution_refine_cases.jsonl"
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.api.schemas import (  # noqa: E402
    ExecutionRefineProposal,
    ExecutionRefineRequest,
)
from app.models.task import Task  # noqa: E402
from app.services.execution_refine import (  # noqa: E402
    MAX_EXECUTION_REFINE_REPAIRS,
    ExecutionRefineScope,
    ExecutionRefineValidationIssue,
    build_execution_refine_prompt,
    build_execution_refine_scope,
    normalize_execution_refine_proposal,
    validate_execution_refine_proposal,
)
from app.services.llm_service import (  # noqa: E402
    DeepSeekExecutionRefineClient,
    LLMStructuredOutputError,
)


ExecutionRefineMode = Literal["time_budget", "progress_recovery", "context_change"]
METRICS = (
    "json_parse_success",
    "mode_match",
    "reference_integrity",
    "mutation_scope_compliance",
    "constraint_preservation",
    "capacity_compliance",
    "history_immutability",
    "action_quality",
)


@dataclass(frozen=True)
class ExecutionRefineEvalCase:
    case_id: str
    mode: ExecutionRefineMode
    description: str
    intent_type: str = "short_term_delivery"
    available_minutes: int | None = None
    new_deadline: str | None = None
    user_context: str | None = None
    priority_slots: list[int] | None = None
    blocked_slots: list[int] | None = None
    scenario_tags: list[str] | None = None
    expected_terms: list[str] | None = None


@dataclass
class ExecutionRefineEvalResult:
    case_id: str
    mode: str
    json_parse_success: bool
    mode_match: bool
    reference_integrity: bool
    mutation_scope_compliance: bool
    constraint_preservation: bool
    capacity_compliance: bool
    history_immutability: bool
    action_quality: bool
    repair_attempts: int
    error_codes: list[str]
    operation_preview: list[dict[str, Any]]
    error: str | None = None

    @property
    def passed(self) -> bool:
        return all(bool(getattr(self, metric)) for metric in METRICS) and not self.error_codes


def load_env_file(path: Path = PROJECT_ROOT / ".env") -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def load_cases(path: Path) -> list[ExecutionRefineEvalCase]:
    cases: list[ExecutionRefineEvalCase] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            cases.append(ExecutionRefineEvalCase(**json.loads(line)))
        except (json.JSONDecodeError, TypeError) as exc:
            raise ValueError(
                f"invalid execution refine eval case at line {line_number}: {exc}"
            ) from exc
    return cases


def build_case_context(
    case: ExecutionRefineEvalCase,
) -> tuple[ExecutionRefineRequest, ExecutionRefineScope, set[str]]:
    tags = set(case.scenario_tags or [])
    task_ids = [_case_uuid(case.case_id, f"task-{index}") for index in (1, 2)]
    request_payload: dict[str, Any] = {
        "request_id": str(_case_uuid(case.case_id, "request")),
        "mode": case.mode,
        "priority_task_ids": [str(task_ids[index]) for index in case.priority_slots or []],
        "blocked_task_ids": [str(task_ids[index]) for index in case.blocked_slots or []],
        "user_context": case.user_context,
    }
    if case.available_minutes is not None:
        request_payload["available_minutes"] = case.available_minutes
    if case.new_deadline is not None:
        request_payload["new_deadline"] = case.new_deadline
    request = ExecutionRefineRequest.model_validate(request_payload)

    now = datetime(2026, 7, 14, 9, 0, tzinfo=timezone.utc)
    phase_id = (
        "phase-1"
        if case.intent_type in {"long_term_growth", "exploration_decision"}
        else None
    )
    children = [
        _tree_action(
            "task-1",
            "写出演示稿的三条核心结论",
            30 if "no_task_fits" not in tags else 45,
            [],
        ),
        _tree_action(
            "task-2",
            "整理五页演示稿并保存初稿",
            35 if "no_task_fits" not in tags else 60,
            ["task-1"] if "dependency" in tags else [],
        ),
    ]
    task_tree = {
        "root": {
            "client_node_id": "root",
            "title": "完成当前项目交付",
            "description": None,
            "verb": "完成",
            "estimated_minutes": sum(item["estimated_minutes"] for item in children),
            "node_type": "group",
            "depends_on": [],
            "children": children,
            "done_criteria": None,
            "start_hint": None,
            "fallback_action": None,
        },
        "summary": "完成当前阶段的可验证交付物",
        "assumptions": [],
        "planning_context": _planning_context(case.intent_type),
        "strategy_context": None,
    }
    thread = SimpleNamespace(
        thread_id=f"eval-{case.case_id}",
        intent_text=_intent_text(case.intent_type),
        task_tree=task_tree,
    )
    tasks = [
        _task(
            task_id=task_ids[0],
            client_node_id="task-1",
            title=children[0]["title"],
            minutes=children[0]["estimated_minutes"],
            sort_order=0,
            phase_id=phase_id,
            now=now,
            status="completed" if "completed" in tags else "active",
            assist_rollup="assist" in tags,
        ),
        _task(
            task_id=task_ids[1],
            client_node_id="task-2",
            title=children[1]["title"],
            minutes=children[1]["estimated_minutes"],
            sort_order=1,
            phase_id=phase_id,
            now=now,
        ),
    ]
    dependencies: list[SimpleNamespace] = []
    if "dependency" in tags:
        dependencies.append(
            SimpleNamespace(task_id=task_ids[1], depends_on_task_id=task_ids[0])
        )
    if "history" in tags:
        tasks.append(
            _task(
                task_id=_case_uuid(case.case_id, "history"),
                client_node_id="history-task",
                title="归档上一阶段访谈记录",
                minutes=40,
                sort_order=2,
                phase_id="phase-0",
                now=now,
                status="completed",
            )
        )
    if "manual" in tags:
        tasks.append(
            _task(
                task_id=_case_uuid(case.case_id, "manual"),
                client_node_id="manual-task",
                title="核对用户手工添加的会议安排",
                minutes=20,
                sort_order=3,
                phase_id=None,
                now=now,
                source="manual",
                ai_generated=False,
            )
        )
    if "assist" in tags:
        tasks.append(
            _task(
                task_id=_case_uuid(case.case_id, "assist-child"),
                client_node_id="assist-child",
                title="列出演示稿可用的三个数据来源",
                minutes=5,
                sort_order=0,
                phase_id=phase_id,
                now=now,
                source="task_assist",
                parent_task_id=task_ids[0],
            )
        )
    if "practice" in tags:
        tasks.append(
            _task(
                task_id=_case_uuid(case.case_id, "practice"),
                client_node_id="practice-occurrence",
                title="完成今天的口语练习并记录次数",
                minutes=10,
                sort_order=4,
                phase_id=phase_id,
                now=now,
                source="practice",
                is_in_my_day=True,
                extra_metadata={"practice_loop_id": str(_case_uuid(case.case_id, "loop"))},
            )
        )

    scope = build_execution_refine_scope(
        thread=thread,
        tasks=tasks,
        dependencies=dependencies,
        phase_reviews=[],
        request=request,
    )
    external_ids = (
        {str(_case_uuid(case.case_id, "cross-project-my-day"))}
        if "cross_project" in tags
        else set()
    )
    return request, scope, external_ids


async def evaluate_case(
    client: Any,
    case: ExecutionRefineEvalCase,
) -> ExecutionRefineEvalResult:
    request, scope, external_ids = build_case_context(case)
    issues = []
    proposal: ExecutionRefineProposal | None = None
    repair_base_proposal: ExecutionRefineProposal | None = None
    try:
        for attempt in range(MAX_EXECUTION_REFINE_REPAIRS + 1):
            prompt = build_execution_refine_prompt(
                request=request,
                scope=scope,
                repair_issues=issues,
                repair_base_proposal=repair_base_proposal,
            )
            try:
                payload = await client.create_execution_refine_proposal(prompt=prompt)
            except LLMStructuredOutputError:
                issues = [
                    ExecutionRefineValidationIssue(
                        error_code="EXECUTION_REFINE_SCHEMA_INVALID",
                        message="provider output did not match the strict schema",
                        fix_suggestion="return only one JSON object matching the schema",
                    )
                ]
                if attempt == MAX_EXECUTION_REFINE_REPAIRS:
                    raise
                continue
            proposal = ExecutionRefineProposal.model_validate(payload)
            proposal = normalize_execution_refine_proposal(
                proposal=proposal,
                request=request,
                scope=scope,
                enforce_capacity_fallback=(
                    attempt == MAX_EXECUTION_REFINE_REPAIRS
                ),
            )
            issues = validate_execution_refine_proposal(
                proposal=proposal,
                request=request,
                scope=scope,
                repair_base_proposal=repair_base_proposal,
            )
            if not issues:
                break
            repair_base_proposal = proposal
    except Exception as exc:
        return _parse_failure(case, exc)

    assert proposal is not None
    codes = [issue.error_code for issue in issues]
    serialized = json.dumps(proposal.model_dump(mode="json"), ensure_ascii=False)
    referenced = _proposal_references(proposal)
    reference_integrity = not _has_code(
        codes,
        "REFERENCE",
        "DEPENDENCY",
        "DRAFT_ID",
        "CLIENT_NODE_ID",
    ) and not bool(referenced & external_ids)
    mutation_scope = not _has_code(
        codes,
        "MUTATION_FORBIDDEN",
        "ADD_LIMIT",
        "SIBLING_SET",
        "TREE_REFERENCE",
        "DUPLICATE_TARGET",
        "NOOP_UPDATE",
        "RESULT_TREE_INVALID",
    )
    constraints = not _has_code(
        codes,
        "CONSTRAINT_LOST",
        "BLOCKED_CONSTRAINT",
        "PRIORITY_CONSTRAINT",
        "DEADLINE_CONSTRAINT",
    ) and all(term in serialized for term in case.expected_terms or [])
    capacity = not _has_code(
        codes,
        "BUFFER",
        "CAPACITY",
        "FOCUS",
        "MY_DAY_FOCUS",
    )
    history = not _has_code(codes, "HISTORY_MUTATION") and not any(
        issue.error_code == "EXECUTION_REFINE_MUTATION_FORBIDDEN"
        and issue.task_ref in scope.task_records
        and scope.task_records[issue.task_ref]["protected_reason"] is not None
        for issue in issues
    )
    return ExecutionRefineEvalResult(
        case_id=case.case_id,
        mode=case.mode,
        json_parse_success=True,
        mode_match=proposal.mode == case.mode,
        reference_integrity=reference_integrity,
        mutation_scope_compliance=mutation_scope,
        constraint_preservation=constraints,
        capacity_compliance=capacity,
        history_immutability=history,
        action_quality=not _has_code(codes, "ACTION_QUALITY"),
        repair_attempts=attempt,
        error_codes=codes,
        operation_preview=[
            operation.model_dump(mode="json", exclude_unset=True)
            for operation in proposal.operations[:3]
        ],
    )


async def run_cases(
    cases: list[ExecutionRefineEvalCase],
    client: Any,
) -> list[ExecutionRefineEvalResult]:
    results = []
    for case in cases:
        results.append(await evaluate_case(client, case))
    return results


def summarize(results: list[ExecutionRefineEvalResult]) -> dict[str, Any]:
    total = len(results)

    def rate(field: str) -> float:
        return sum(bool(getattr(result, field)) for result in results) / total if total else 0.0

    summary = {
        "cases": total,
        "passed": sum(result.passed for result in results),
        "pass_rate": rate("passed"),
    }
    summary.update({metric: rate(metric) for metric in METRICS})
    return summary


def release_gate_failed(summary: dict[str, Any], *, expected_cases: int = 24) -> bool:
    return (
        summary["cases"] != expected_cases
        or summary["passed"] != expected_cases
        or any(summary[metric] < 1.0 for metric in METRICS)
    )


def print_report(
    results: list[ExecutionRefineEvalResult],
    *,
    diagnostics_jsonl: Path | None = None,
) -> None:
    lines = [json.dumps(asdict(result), ensure_ascii=False) for result in results]
    for line in lines:
        print(line)
    print(json.dumps({"summary": summarize(results)}, ensure_ascii=False, indent=2))
    if diagnostics_jsonl is not None:
        diagnostics_jsonl.parent.mkdir(parents=True, exist_ok=True)
        diagnostics_jsonl.write_text("\n".join(lines) + "\n", encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run EasyPlan Execution Refine evals")
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES)
    parser.add_argument("--case-id", action="append", default=[])
    parser.add_argument("--provider", default="deepseek", choices=["deepseek"])
    parser.add_argument("--model", default=None)
    parser.add_argument("--diagnostics-jsonl", type=Path, default=None)
    parser.add_argument("--strict-exit", action="store_true")
    return parser.parse_args()


async def amain() -> int:
    load_env_file()
    args = parse_args()
    cases = load_cases(args.cases)
    if args.case_id:
        selected = set(args.case_id)
        cases = [case for case in cases if case.case_id in selected]
    client = DeepSeekExecutionRefineClient(model=args.model)
    results = await run_cases(cases, client)
    print_report(results, diagnostics_jsonl=args.diagnostics_jsonl)
    if args.strict_exit and release_gate_failed(summarize(results)):
        return 1
    return 0


def _case_uuid(case_id: str, label: str) -> UUID:
    return uuid5(NAMESPACE_URL, f"easyplan-execution-refine:{case_id}:{label}")


def _tree_action(client_node_id: str, title: str, minutes: int, depends_on: list[str]):
    return {
        "client_node_id": client_node_id,
        "title": title,
        "description": "形成可检查的项目产出",
        "verb": title[:2],
        "estimated_minutes": minutes,
        "node_type": "action",
        "depends_on": depends_on,
        "children": [],
        "done_criteria": "保存一份可打开且内容完整的交付物",
        "start_hint": "打开现有项目材料并定位对应章节",
        "fallback_action": "如果精力不足，先完成第一条并保存",
    }


def _planning_context(intent_type: str) -> dict[str, Any] | None:
    if intent_type not in {"long_term_growth", "exploration_decision"}:
        return None
    horizon = {
        "long_term_growth": "months",
        "exploration_decision": "weeks",
        "context_checklist": "days",
    }.get(intent_type, "days")
    return {
        "schema_version": 1,
        "intent_type": intent_type,
        "time_horizon": horizon,
        "roadmap": [
            {
                "phase_id": "phase-1",
                "order": 1,
                "title": "当前执行阶段",
                "objective": "形成第一份可验证产出",
                "status": "current",
            },
            {
                "phase_id": "phase-2",
                "order": 2,
                "title": "后续复盘阶段",
                "objective": "根据结果决定后续行动",
                "status": "planned",
            },
            {
                "phase_id": "phase-3",
                "order": 3,
                "title": "结果收口阶段",
                "objective": "形成最终结论或交付物",
                "status": "planned",
            },
        ],
        "current_phase": {
            "phase_id": "phase-1",
            "title": "当前执行阶段",
            "objective": "形成第一份可验证产出",
            "completion_rule": "all_ai_actions_completed",
            "estimated_duration_weeks": None,
        },
        "next_action_client_node_id": "task-1",
        "practice_loops": [],
        "outcome_checkpoints": [],
        "phase_gate": None,
    }


def _intent_text(intent_type: str) -> str:
    return {
        "long_term_growth": "逐步提升公开演示能力",
        "exploration_decision": "判断是否应该调整产品方向",
        "context_checklist": "整理今天外出和手机处理的事项",
    }.get(intent_type, "完成本周演示稿交付")


def _task(
    *,
    task_id: UUID,
    client_node_id: str,
    title: str,
    minutes: int,
    sort_order: int,
    phase_id: str | None,
    now: datetime,
    status: str = "active",
    source: str = "ai",
    ai_generated: bool = True,
    parent_task_id: UUID | None = None,
    is_in_my_day: bool = False,
    assist_rollup: bool = False,
    extra_metadata: dict[str, Any] | None = None,
) -> Task:
    metadata = {
        "source": source,
        "phase_id": phase_id,
        "done_criteria": "保存一份可打开且内容完整的交付物",
        "start_hint": "打开现有项目材料并定位对应章节",
        "fallback_action": "如果精力不足，先完成第一条并保存",
        **(extra_metadata or {}),
    }
    if assist_rollup:
        metadata["assist_rollup"] = True
    return Task(
        id=task_id,
        user_id=_case_uuid("shared", "user"),
        thread_id="eval-thread",
        parent_task_id=parent_task_id,
        client_node_id=client_node_id,
        title=title,
        description="形成可检查的项目产出",
        node_type="action",
        status=status,
        view_bucket="planned",
        is_in_my_day=is_in_my_day,
        estimated_minutes=minutes,
        sort_order=sort_order,
        ai_generated=ai_generated,
        user_edited=not ai_generated,
        metadata_=metadata,
        created_at=now,
        updated_at=now,
    )


def _proposal_references(proposal: ExecutionRefineProposal) -> set[str]:
    values: set[str] = set()
    for operation in proposal.operations:
        payload = operation.model_dump(mode="json", exclude_unset=True)
        for field in (
            "task_id",
            "parent_task_id",
            "insert_after_task_id",
        ):
            if payload.get(field):
                values.add(str(payload[field]))
        values.update(str(value) for value in payload.get("ordered_task_ids", []))
        values.update(str(value) for value in payload.get("depends_on_refs", []))
    values.update(str(value) for value in proposal.focus_task_ids)
    return values


def _has_code(codes: list[str], *tokens: str) -> bool:
    return any(any(token in code for token in tokens) for code in codes)


def _parse_failure(
    case: ExecutionRefineEvalCase,
    error: Exception,
) -> ExecutionRefineEvalResult:
    return ExecutionRefineEvalResult(
        case_id=case.case_id,
        mode=case.mode,
        json_parse_success=False,
        mode_match=False,
        reference_integrity=False,
        mutation_scope_compliance=False,
        constraint_preservation=False,
        capacity_compliance=False,
        history_immutability=False,
        action_quality=False,
        repair_attempts=0,
        error_codes=[type(error).__name__],
        operation_preview=[],
        error=str(error)[:500],
    )


if __name__ == "__main__":
    raise SystemExit(asyncio.run(amain()))
