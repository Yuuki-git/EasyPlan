from __future__ import annotations

import argparse
import asyncio
import inspect
import json
import os
import re
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CASES_PATH = PROJECT_ROOT / "tests" / "evals" / "planning_cases.jsonl"
DEFAULT_PHASE_CASES_PATH = PROJECT_ROOT / "tests" / "evals" / "phase_planning_cases.jsonl"

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.agents.nodes import (  # noqa: E402
    MAX_REPLAN_ATTEMPTS,
    _validate_task_tree,
    build_planner_prompt,
)
from app.api.schemas import TaskNode, TaskTree  # noqa: E402
from app.services.action_quality import summarize_action_quality  # noqa: E402
from app.services.llm_service import (  # noqa: E402
    ListReasoningSink,
    ListUsageSink,
    create_planner_client,
)


LOW_VALUE_ICEBREAKER_PATTERNS = [
    re.compile(pattern)
    for pattern in (
        r"打开.{0,8}(电脑|文档|文件|软件|浏览器|编辑器)",
        r"启动.{0,8}(电脑|软件|浏览器|编辑器)",
        r"新建.{0,8}(文档|文件)",
        r"创建.{0,8}(文档|文件)",
        r"准备.{0,8}(环境|工具|资料)",
        r"(坐下|深呼吸|喝水|整理桌面|清空桌面)",
    )
]
LONG_TERM_HORIZON_PATTERNS = [
    re.compile(pattern)
    for pattern in (
        r"第[一二三四五六七八九十\d]+周",
        r"第[一二三四五六七八九十\d]+个月",
        r"[一二三四五六七八九十\d]+\s*个月.{0,6}计划",
        r"(每天|每日|每周|每月).{0,12}(坚持|学习|训练|复习|背|练)",
        r"(完整|全部|全年|长期).{0,8}(周期|计划|路线|课程)",
    )
]
LONG_TERM_V2_HORIZON_PATTERNS = [
    re.compile(pattern)
    for pattern in (
        r"第[一二三四五六七八九十\d]+周",
        r"第[一二三四五六七八九十\d]+个月",
        r"[一二三四五六七八九十\d]+\s*个月.{0,6}计划",
        r"(每天|每日).{0,12}(坚持|学习|训练|复习|背|练)",
        r"(完整|全部|全年|长期).{0,8}(周期|计划|路线|课程)",
    )
]
LONG_TERM_STAGE_TERMS = (
    "基础",
    "训练",
    "模拟",
    "复盘",
    "强化",
    "冲刺",
    "课程",
    "长期",
)
EXPLORATION_EXECUTION_PATTERNS = [
    re.compile(pattern)
    for pattern in (
        r"[三四五六七八九十\d]+\s*个月.{0,8}(转行|创业|学习|执行).{0,8}计划",
        r"(直接|立即).{0,6}(辞职|转行|创业|报名|投递|执行)",
        r"(转行|创业|长期学习).{0,8}(执行计划|学习计划|路线图)",
    )
]
EXPLORATION_EXECUTION_NEGATION_SUFFIX = re.compile(
    r"(?:暂不建议|不建议|不应|不宜|避免|不要|无需|不必|不是|并非|暂不适合|不适合)"
    r"[^，。；！？]{0,16}$"
)
EXPLORATION_EXECUTION_CAUTION_PREFIX = re.compile(
    r"^(?:的)?(?:风险|成本|代价|不确定性)(?:很|较|过于)?(?:高|大)"
)
EXPLORATION_DISCOVERY_TERMS = (
    "澄清",
    "写下",
    "列出",
    "收集",
    "调研",
    "访谈",
    "聊",
    "找",
    "JD",
    "岗位",
    "比较",
    "成本收益",
    "小实验",
    "验证",
    "担忧",
    "原因",
    "决策",
)


@dataclass(frozen=True)
class EvalCase:
    input: str
    expected_intent_type: str
    expected_horizon: str
    must_have_icebreaker: bool
    max_nodes: int
    description: str
    case_id: str | None = None
    expected_loop_min: int | None = None
    expected_loop_max: int | None = None
    expected_weekly_target: int | None = None
    require_outcome_checkpoints: bool = False
    require_capacity_assumption: bool = False
    forbid_future_occurrences: bool = False


@dataclass
class EvalResult:
    input: str
    expected_intent_type: str
    expected_horizon: str
    actual_intent_type: str | None
    actual_time_horizon: str | None
    intent_type_matches_expected: bool | None
    time_horizon_matches_expected: bool | None
    valid_task_tree: bool
    top_level_node_count: int | None
    total_node_count: int | None
    max_nodes: int
    top_level_exceeds_max: bool | None
    strategy_compliant: bool | None
    contains_low_value_icebreaker: bool | None
    short_term_delivery_without_low_value_icebreaker: bool | None
    must_have_icebreaker: bool
    icebreaker_present: bool | None
    strategy_errors: list[str] = field(default_factory=list)
    horizon_errors: list[str] = field(default_factory=list)
    top_level_preview: list[dict[str, Any]] = field(default_factory=list)
    first_action_snapshot: dict[str, Any] | None = None
    validation_error: str | None = None
    runtime_error: str | None = None
    reasoning_event_count: int = 0
    usage_record_count: int = 0
    action_quality_action_count: int = 0
    action_quality_pass_count: int = 0
    action_quality_score_total: int = 0
    action_quality_done_criteria_count: int = 0
    action_quality_abstract_violation_count: int = 0
    action_quality_pass_rate: float | None = None
    average_actionability_score: float | None = None
    done_criteria_coverage: float | None = None
    abstract_task_violation_rate: float | None = None
    loop_count: int | None = None
    checkpoint_count: int | None = None
    commitment_count: int | None = None
    weekly_target_matches: bool | None = None
    outcome_evidence_present: bool | None = None
    future_occurrence_violation: bool | None = None

    @property
    def passed(self) -> bool:
        if self.intent_type_matches_expected is False:
            return False
        if self.time_horizon_matches_expected is False:
            return False
        if not self.valid_task_tree:
            return False
        if self.strategy_compliant is False:
            return False
        return True


@dataclass(frozen=True)
class PhaseEvalCase:
    case_id: str
    mode: str
    intent_text: str
    intent_profile: dict[str, Any]
    expect_roadmap_visible: bool
    expect_current_phase_only: bool
    expect_completed_phase_immutable: bool
    committed_task_tree: dict[str, Any] | None = None


@dataclass
class PhaseEvalResult:
    case_id: str
    mode: str
    roadmap_visible: bool
    current_phase_horizon_ok: bool
    completed_phase_immutable: bool
    json_parse_success: bool
    action_quality_pass_rate: float
    done_criteria_coverage: float
    validation_error: str | None = None
    runtime_error: str | None = None

    @property
    def passed(self) -> bool:
        return (
            self.json_parse_success
            and self.roadmap_visible
            and self.current_phase_horizon_ok
            and self.completed_phase_immutable
        )


def load_env_file(path: Path = PROJECT_ROOT / ".env") -> None:
    if not path.exists():
        return
    with path.open("r", encoding="utf-8") as handle:
        for raw_line in handle:
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            if not key:
                continue
            value = value.strip()
            if (
                len(value) >= 2
                and value[0] == value[-1]
                and value[0] in {"'", '"'}
            ):
                value = value[1:-1]
            os.environ.setdefault(key, value)


def load_cases(path: Path) -> list[EvalCase]:
    cases: list[EvalCase] = []
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            stripped = line.strip().lstrip("\ufeff")
            if not stripped:
                continue
            raw_case = json.loads(stripped)
            try:
                cases.append(EvalCase(**raw_case))
            except TypeError as exc:
                raise ValueError(f"Invalid eval case at {path}:{line_number}: {exc}") from exc
    return cases


def load_phase_cases(path: Path) -> list[PhaseEvalCase]:
    cases: list[PhaseEvalCase] = []
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            stripped = line.strip().lstrip("\ufeff")
            if not stripped:
                continue
            raw_case = json.loads(stripped)
            try:
                case = PhaseEvalCase(**raw_case)
            except TypeError as exc:
                raise ValueError(
                    f"Invalid phase eval case at {path}:{line_number}: {exc}"
                ) from exc
            if case.mode not in {"initial", "next_phase"}:
                raise ValueError(
                    f"Invalid phase eval mode at {path}:{line_number}: {case.mode}"
                )
            if case.mode == "next_phase" and case.committed_task_tree is None:
                raise ValueError(
                    f"next_phase case requires committed_task_tree at {path}:{line_number}"
                )
            cases.append(case)
    return cases


async def run_cases(
    cases: list[EvalCase],
    *,
    provider: str | None,
    model: str | None,
    planner: Any | None = None,
) -> list[EvalResult]:
    planner = planner or create_planner_client(provider=provider, model=model)
    results: list[EvalResult] = []
    for case in cases:
        reasoning_sink = ListReasoningSink()
        usage_sink = ListUsageSink()
        try:
            intent_profile = await _profile_intent(planner, case.input, reasoning_sink, usage_sink)
            validation_errors: list[str] | None = None
            for _ in range(MAX_REPLAN_ATTEMPTS + 1):
                raw_plan = await _create_plan(
                    planner,
                    case.input,
                    intent_profile,
                    reasoning_sink,
                    usage_sink,
                    validation_errors=validation_errors,
                )
                validation_errors = _validate_task_tree(
                    raw_plan,
                    intent_text=case.input,
                    intent_profile=intent_profile,
                    planning_mode="initial",
                )
                if not validation_errors:
                    break
            result = evaluate_plan(case, raw_plan, intent_profile=intent_profile)
        except Exception as exc:
            result = EvalResult(
                input=case.input,
                expected_intent_type=case.expected_intent_type,
                expected_horizon=case.expected_horizon,
                actual_intent_type=None,
                actual_time_horizon=None,
                intent_type_matches_expected=None,
                time_horizon_matches_expected=None,
                valid_task_tree=False,
                top_level_node_count=None,
                total_node_count=None,
                max_nodes=case.max_nodes,
                top_level_exceeds_max=None,
                strategy_compliant=None,
                contains_low_value_icebreaker=None,
                short_term_delivery_without_low_value_icebreaker=None,
                must_have_icebreaker=case.must_have_icebreaker,
                icebreaker_present=None,
                strategy_errors=[],
                horizon_errors=[],
                top_level_preview=[],
                first_action_snapshot=None,
                runtime_error=f"{type(exc).__name__}: {exc}",
            )
        result.reasoning_event_count = len(reasoning_sink.events)
        result.usage_record_count = len(usage_sink.records)
        results.append(result)
    return results


async def run_phase_cases(
    cases: list[PhaseEvalCase],
    *,
    provider: str | None,
    model: str | None,
    planner: Any | None = None,
) -> list[PhaseEvalResult]:
    selected_provider = (provider or os.getenv("EASYPLAN_LLM_PROVIDER", "deepseek")).lower()
    if selected_provider != "deepseek":
        raise ValueError("Phase planning evals must use the deepseek provider")
    planner = planner or create_planner_client(provider="deepseek", model=model)
    results: list[PhaseEvalResult] = []
    for case in cases:
        reasoning_sink = ListReasoningSink()
        usage_sink = ListUsageSink()
        try:
            raw_plan = await _create_phase_plan(
                planner,
                case,
                reasoning_sink,
                usage_sink,
            )
            result = evaluate_phase_case(case, raw_plan)
        except Exception as exc:
            result = PhaseEvalResult(
                case_id=case.case_id,
                mode=case.mode,
                roadmap_visible=False,
                current_phase_horizon_ok=False,
                completed_phase_immutable=False,
                json_parse_success=False,
                action_quality_pass_rate=0.0,
                done_criteria_coverage=0.0,
                runtime_error=f"{type(exc).__name__}: {exc}",
            )
        results.append(result)
    return results


async def _profile_intent(
    planner: Any,
    user_input: str,
    reasoning_sink: ListReasoningSink,
    usage_sink: ListUsageSink,
) -> dict[str, Any]:
    if not hasattr(planner, "profile_intent"):
        return {
            "intent_type": "general",
            "time_horizon": None,
            "confidence_score": 0.0,
        }
    parameters = inspect.signature(planner.profile_intent).parameters
    kwargs: dict[str, Any] = {}
    if "reasoning_sink" in parameters:
        kwargs["reasoning_sink"] = reasoning_sink
    if "usage_sink" in parameters:
        kwargs["usage_sink"] = usage_sink
    return await planner.profile_intent(user_input, **kwargs)


async def _create_plan(
    planner: Any,
    user_input: str,
    intent_profile: dict[str, Any] | None,
    reasoning_sink: ListReasoningSink,
    usage_sink: ListUsageSink,
    validation_errors: list[str] | None = None,
) -> dict[str, Any]:
    prompt = build_planner_prompt(
        user_input,
        intent_profile=intent_profile,
        validation_errors=validation_errors,
    )
    parameters = inspect.signature(planner.create_plan).parameters
    if "usage_sink" in parameters:
        return await planner.create_plan(
            prompt,
            reasoning_sink=reasoning_sink,
            usage_sink=usage_sink,
        )
    return await planner.create_plan(prompt, reasoning_sink=reasoning_sink)


async def _create_phase_plan(
    planner: Any,
    case: PhaseEvalCase,
    reasoning_sink: ListReasoningSink,
    usage_sink: ListUsageSink,
) -> dict[str, Any]:
    prompt = build_planner_prompt(
        case.intent_text,
        intent_profile=case.intent_profile,
        planning_mode=case.mode,
        committed_task_tree=case.committed_task_tree,
        current_phase_task_summary=(
            "all current phase AI actions completed"
            if case.mode == "next_phase"
            else None
        ),
    )
    parameters = inspect.signature(planner.create_plan).parameters
    kwargs: dict[str, Any] = {"reasoning_sink": reasoning_sink}
    if "usage_sink" in parameters:
        kwargs["usage_sink"] = usage_sink
    return await planner.create_plan(prompt, **kwargs)


def evaluate_phase_case(
    case: PhaseEvalCase,
    raw_plan: dict[str, Any],
) -> PhaseEvalResult:
    try:
        task_tree = TaskTree.model_validate(raw_plan)
    except Exception as exc:
        return PhaseEvalResult(
            case_id=case.case_id,
            mode=case.mode,
            roadmap_visible=False,
            current_phase_horizon_ok=False,
            completed_phase_immutable=False,
            json_parse_success=False,
            action_quality_pass_rate=0.0,
            done_criteria_coverage=0.0,
            validation_error=str(exc),
        )

    context = task_tree.planning_context
    roadmap_visible = context is not None and 3 <= len(context.roadmap) <= 5
    text = task_tree_text(task_tree)
    horizon_violation = (
        any(pattern.search(text) for pattern in LONG_TERM_HORIZON_PATTERNS)
        if case.intent_profile.get("intent_type") == "long_term_growth"
        else _contains_non_negated_pattern(text, EXPLORATION_EXECUTION_PATTERNS)
    )
    current_phase_horizon_ok = (
        context is not None
        and context.current_phase is not None
        and not horizon_violation
        and len(task_tree.root.children) <= 12
    )
    completed_phase_immutable = _completed_phases_are_immutable(
        case.committed_task_tree,
        task_tree,
    )
    action_quality = summarize_action_quality(task_tree)
    return PhaseEvalResult(
        case_id=case.case_id,
        mode=case.mode,
        roadmap_visible=(roadmap_visible == case.expect_roadmap_visible),
        current_phase_horizon_ok=(
            current_phase_horizon_ok == case.expect_current_phase_only
        ),
        completed_phase_immutable=(
            completed_phase_immutable == case.expect_completed_phase_immutable
        ),
        json_parse_success=True,
        action_quality_pass_rate=action_quality.action_quality_pass_rate,
        done_criteria_coverage=action_quality.done_criteria_coverage,
    )


def _completed_phases_are_immutable(
    committed_task_tree: dict[str, Any] | None,
    proposed: TaskTree,
) -> bool:
    if committed_task_tree is None:
        return True
    try:
        committed = TaskTree.model_validate(committed_task_tree)
    except Exception:
        return False
    if committed.planning_context is None or proposed.planning_context is None:
        return False
    proposed_by_id = {
        phase.phase_id: phase for phase in proposed.planning_context.roadmap
    }
    for phase in committed.planning_context.roadmap:
        if phase.status != "completed":
            continue
        proposed_phase = proposed_by_id.get(phase.phase_id)
        if proposed_phase is None or proposed_phase.model_dump() != phase.model_dump():
            return False
    return True


def evaluate_plan(
    case: EvalCase,
    raw_plan: dict[str, Any],
    *,
    intent_profile: dict[str, Any] | None = None,
) -> EvalResult:
    actual_intent_type = _profile_value(intent_profile, "intent_type")
    actual_time_horizon = _profile_value(intent_profile, "time_horizon")
    intent_type_matches_expected = _matches_expected(
        expected=case.expected_intent_type,
        actual=actual_intent_type,
    )
    try:
        task_tree = TaskTree.model_validate(raw_plan)
    except Exception as exc:
        return EvalResult(
            input=case.input,
            expected_intent_type=case.expected_intent_type,
            expected_horizon=case.expected_horizon,
            actual_intent_type=actual_intent_type,
            actual_time_horizon=actual_time_horizon,
            intent_type_matches_expected=intent_type_matches_expected,
            time_horizon_matches_expected=None,
            valid_task_tree=False,
            top_level_node_count=None,
            total_node_count=None,
            max_nodes=case.max_nodes,
            top_level_exceeds_max=None,
            strategy_compliant=None,
            contains_low_value_icebreaker=None,
            short_term_delivery_without_low_value_icebreaker=None,
            must_have_icebreaker=case.must_have_icebreaker,
            icebreaker_present=None,
            strategy_errors=[],
            horizon_errors=[],
            top_level_preview=[],
            first_action_snapshot=None,
            validation_error=str(exc),
        )

    top_level_node_count = len(task_tree.root.children)
    total_node_count = sum(1 for _ in iter_task_nodes(task_tree.root))
    contains_low_value = contains_low_value_icebreaker(task_tree)
    short_term_ok = None
    if case.expected_intent_type == "short_term_delivery":
        short_term_ok = not contains_low_value
    icebreaker_present = has_first_step_icebreaker(task_tree)
    horizon_errors = collect_horizon_errors(case, task_tree)
    time_horizon_matches_expected = not horizon_errors
    strategy_errors = collect_strategy_errors(
        case,
        task_tree,
        top_level_node_count=top_level_node_count,
        contains_low_value_icebreaker=contains_low_value,
        icebreaker_present=icebreaker_present,
    )
    top_level_preview = preview_top_level_tasks(task_tree)
    first_action_snapshot = snapshot_first_action(task_tree)
    action_quality = summarize_action_quality(task_tree)
    context = task_tree.planning_context
    loops = list(context.practice_loops) if context is not None else []
    checkpoints = (
        list(context.outcome_checkpoints)
        if context is not None
        else []
    )
    action_count = sum(
        node.node_type == "action"
        for node in iter_task_nodes(task_tree.root)
    )
    weekly_target_matches = (
        any(
            loop.target_per_week == case.expected_weekly_target
            for loop in loops
        )
        if case.expected_weekly_target is not None
        else None
    )
    future_occurrence_violation = (
        _contains_future_occurrence_nodes(task_tree)
        if case.forbid_future_occurrences
        else None
    )

    return EvalResult(
        input=case.input,
        expected_intent_type=case.expected_intent_type,
        expected_horizon=case.expected_horizon,
        actual_intent_type=actual_intent_type,
        actual_time_horizon=actual_time_horizon,
        intent_type_matches_expected=intent_type_matches_expected,
        time_horizon_matches_expected=time_horizon_matches_expected,
        valid_task_tree=True,
        top_level_node_count=top_level_node_count,
        total_node_count=total_node_count,
        max_nodes=case.max_nodes,
        top_level_exceeds_max=top_level_node_count > case.max_nodes,
        strategy_compliant=not strategy_errors,
        contains_low_value_icebreaker=contains_low_value,
        short_term_delivery_without_low_value_icebreaker=short_term_ok,
        must_have_icebreaker=case.must_have_icebreaker,
        icebreaker_present=icebreaker_present,
        strategy_errors=strategy_errors,
        horizon_errors=horizon_errors,
        top_level_preview=top_level_preview,
        first_action_snapshot=first_action_snapshot,
        action_quality_action_count=action_quality.action_count,
        action_quality_pass_count=action_quality.pass_count,
        action_quality_score_total=action_quality.score_total,
        action_quality_done_criteria_count=action_quality.done_criteria_count,
        action_quality_abstract_violation_count=action_quality.abstract_violation_count,
        action_quality_pass_rate=action_quality.action_quality_pass_rate,
        average_actionability_score=action_quality.average_actionability_score,
        done_criteria_coverage=action_quality.done_criteria_coverage,
        abstract_task_violation_rate=action_quality.abstract_task_violation_rate,
        loop_count=(
            len(loops)
            if case.expected_intent_type == "long_term_growth"
            else None
        ),
        checkpoint_count=(
            len(checkpoints)
            if case.expected_intent_type == "long_term_growth"
            else None
        ),
        commitment_count=(
            action_count + len(loops) + len(checkpoints)
            if case.expected_intent_type == "long_term_growth"
            else None
        ),
        weekly_target_matches=weekly_target_matches,
        outcome_evidence_present=(
            bool(checkpoints)
            if case.expected_intent_type == "long_term_growth"
            else None
        ),
        future_occurrence_violation=future_occurrence_violation,
    )


def _profile_value(intent_profile: dict[str, Any] | None, key: str) -> str | None:
    if not isinstance(intent_profile, dict):
        return None
    value = intent_profile.get(key)
    return value if isinstance(value, str) else None


def _matches_expected(*, expected: str, actual: str | None) -> bool | None:
    if actual is None:
        return None
    return actual == expected


def iter_task_nodes(node: TaskNode):
    yield node
    for child in node.children:
        yield from iter_task_nodes(child)


def contains_low_value_icebreaker(task_tree: TaskTree) -> bool:
    first_action = next(
        (node for node in iter_task_nodes(task_tree.root) if node.node_type == "action"),
        None,
    )
    if first_action is None:
        return False
    text = " ".join(
        value
        for value in (
            first_action.title,
            first_action.description or "",
            first_action.verb,
        )
        if value
    )
    return any(pattern.search(text) for pattern in LOW_VALUE_ICEBREAKER_PATTERNS)


def has_first_step_icebreaker(task_tree: TaskTree) -> bool:
    first_action = next(
        (node for node in iter_task_nodes(task_tree.root) if node.node_type == "action"),
        None,
    )
    return first_action is not None and first_action.estimated_minutes <= 5


def preview_top_level_tasks(task_tree: TaskTree) -> list[dict[str, Any]]:
    return [
        {
            "title": node.title,
            "estimated_minutes": node.estimated_minutes,
            "node_type": node.node_type,
            "children_count": len(node.children),
        }
        for node in task_tree.root.children[:3]
    ]


def snapshot_first_action(task_tree: TaskTree) -> dict[str, Any] | None:
    first_action = next(
        (node for node in iter_task_nodes(task_tree.root) if node.node_type == "action"),
        None,
    )
    if first_action is None:
        return None
    return {
        "title": first_action.title,
        "estimated_minutes": first_action.estimated_minutes,
        "done_criteria": first_action.done_criteria,
    }


def collect_horizon_errors(case: EvalCase, task_tree: TaskTree) -> list[str]:
    text = task_tree_text(task_tree)
    errors: list[str] = []
    if case.expected_intent_type == "long_term_growth":
        context = task_tree.planning_context
        patterns = (
            LONG_TERM_V2_HORIZON_PATTERNS
            if context is not None and context.schema_version == 2
            else LONG_TERM_HORIZON_PATTERNS
        )
        if any(pattern.search(text) for pattern in patterns):
            errors.append(
                "long_term_growth output includes long-cycle scheduling; expected only current 24-72h Phase 1 tasks"
            )
        if top_level_looks_like_long_term_curriculum(task_tree):
            errors.append(
                "long_term_growth top-level tasks look like a full curriculum roadmap instead of Phase 1 actions"
            )
    elif case.expected_intent_type == "exploration_decision":
        if _contains_non_negated_pattern(text, EXPLORATION_EXECUTION_PATTERNS):
            errors.append(
                "exploration_decision output assumes an execution decision instead of staying in the clarification phase"
            )
    return errors


def collect_strategy_errors(
    case: EvalCase,
    task_tree: TaskTree,
    *,
    top_level_node_count: int,
    contains_low_value_icebreaker: bool,
    icebreaker_present: bool,
) -> list[str]:
    errors: list[str] = []
    if top_level_node_count > case.max_nodes:
        errors.append(
            f"top-level node count {top_level_node_count} exceeds case max_nodes {case.max_nodes}"
        )
    if case.expected_intent_type == "short_term_delivery" and contains_low_value_icebreaker:
        errors.append("short_term_delivery contains a low-value icebreaker")
    if (
        case.expected_intent_type == "long_term_growth"
        and case.must_have_icebreaker
        and not icebreaker_present
    ):
        errors.append("expected a <=5 minute first-step icebreaker")
    if case.expected_intent_type == "context_checklist":
        top_level_nodes = task_tree.root.children
        if len(top_level_nodes) > 1 and not any(node.node_type == "group" for node in top_level_nodes):
            errors.append("context_checklist related actions should be grouped by context")
        if _task_tree_depth(task_tree.root) > 3:
            errors.append("context_checklist task tree is too deep")
    if case.expected_intent_type == "exploration_decision":
        text = task_tree_text(task_tree)
        if _contains_non_negated_pattern(text, EXPLORATION_EXECUTION_PATTERNS):
            errors.append("exploration_decision should not produce a long-term execution plan")
        if not any(term in text for term in EXPLORATION_DISCOVERY_TERMS):
            errors.append("exploration_decision lacks clarification, information gathering, experiment, or decision nodes")
        errors.extend(exploration_summary_errors(task_tree.summary))
    if case.expected_intent_type == "long_term_growth":
        context = task_tree.planning_context
        loops = list(context.practice_loops) if context is not None else []
        checkpoints = (
            list(context.outcome_checkpoints)
            if context is not None
            else []
        )
        action_count = sum(
            node.node_type == "action"
            for node in iter_task_nodes(task_tree.root)
        )
        commitment_count = action_count + len(loops) + len(checkpoints)
        if (
            case.expected_loop_min is not None
            and len(loops) < case.expected_loop_min
        ):
            errors.append(
                f"loop_count: expected at least {case.expected_loop_min}, got {len(loops)}"
            )
        if (
            case.expected_loop_max is not None
            and len(loops) > case.expected_loop_max
        ):
            errors.append(
                f"loop_count: expected at most {case.expected_loop_max}, got {len(loops)}"
            )
        if commitment_count > 5:
            errors.append(
                f"commitment_count: {commitment_count} exceeds maximum 5"
            )
        if case.require_outcome_checkpoints and not checkpoints:
            errors.append("missing_outcome_evidence: no outcome checkpoint generated")
        if case.expected_weekly_target is not None and not any(
            loop.target_per_week == case.expected_weekly_target
            for loop in loops
        ):
            errors.append(
                "weekly_target_mismatch: "
                f"expected {case.expected_weekly_target}"
            )
        if case.require_capacity_assumption and not task_tree.assumptions:
            errors.append(
                "weekly_target_mismatch: conservative capacity assumption is missing"
            )
        if (
            case.forbid_future_occurrences
            and _contains_future_occurrence_nodes(task_tree)
        ):
            errors.append(
                "future_occurrence: planner expanded dated future practice instances"
            )
    return errors


FUTURE_OCCURRENCE_PATTERN = re.compile(
    r"(第\s*[一二三四五六七八九十\d]+\s*(天|周)|"
    r"周[一二三四五六日天](?!次|气)|"
    r"\d{4}[-/年]\d{1,2}[-/月]\d{1,2})"
)


def _contains_future_occurrence_nodes(task_tree: TaskTree) -> bool:
    for node in iter_task_nodes(task_tree.root):
        if FUTURE_OCCURRENCE_PATTERN.search(
            " ".join((node.title, node.description or ""))
        ):
            return True
    return False


def task_tree_text(task_tree: TaskTree) -> str:
    parts = [task_tree.summary, *task_tree.assumptions]
    for node in iter_task_nodes(task_tree.root):
        parts.extend(
            value
            for value in (node.title, node.description or "", node.verb)
            if value
        )
    return " ".join(parts)


def _contains_non_negated_pattern(
    text: str,
    patterns: tuple[re.Pattern[str], ...] | list[re.Pattern[str]],
) -> bool:
    for pattern in patterns:
        for match in pattern.finditer(text):
            prefix = text[max(0, match.start() - 32) : match.start()]
            suffix = text[match.end() : match.end() + 16]
            if (
                EXPLORATION_EXECUTION_NEGATION_SUFFIX.search(prefix)
                or EXPLORATION_EXECUTION_CAUTION_PREFIX.search(suffix)
            ):
                continue
            return True
    return False


def exploration_summary_errors(summary: str) -> list[str]:
    if not isinstance(summary, str):
        return ["exploration_decision summary is missing"]
    normalized = re.sub(r"\s+", "", summary)
    current_index = normalized.find("当前判断：")
    basis_index = normalized.find("判断依据：")
    next_index = normalized.find("下一步探索：")
    if min(current_index, basis_index, next_index) < 0:
        return [
            "exploration_decision summary must answer the question first with 当前判断 / 判断依据 / 下一步探索"
        ]
    if not (current_index <= basis_index <= next_index):
        return [
            "exploration_decision summary must keep 当前判断 before 判断依据 before 下一步探索"
        ]
    current_text = normalized[current_index + len("当前判断：") : basis_index]
    basis_text = normalized[basis_index + len("判断依据：") : next_index]
    next_text = normalized[next_index + len("下一步探索：") :]
    if not current_text or not basis_text or not next_text:
        return [
            "exploration_decision summary must include non-empty 当前判断 / 判断依据 / 下一步探索 sections"
        ]
    if current_text.startswith(("下一步", "先", "然后", "再", "最后")):
        return [
            "exploration_decision summary must answer the question first instead of only listing routes"
        ]
    return []


def top_level_looks_like_long_term_curriculum(task_tree: TaskTree) -> bool:
    stage_like_nodes = [
        node
        for node in task_tree.root.children
        if any(term in f"{node.title} {node.description or ''}" for term in LONG_TERM_STAGE_TERMS)
    ]
    return len(stage_like_nodes) >= 3


def _task_tree_depth(node: TaskNode) -> int:
    if not node.children:
        return 1
    return 1 + max(_task_tree_depth(child) for child in node.children)


def summarize(results: list[EvalResult]) -> dict[str, Any]:
    short_term_results = [
        result
        for result in results
        if result.expected_intent_type == "short_term_delivery"
    ]
    intent_classified = [
        result for result in results if result.intent_type_matches_expected is not None
    ]
    horizon_classified = [
        result for result in results if result.time_horizon_matches_expected is not None
    ]
    intent_accuracy = (
        sum(1 for result in intent_classified if result.intent_type_matches_expected)
        / len(intent_classified)
        if intent_classified
        else 0.0
    )
    horizon_accuracy = (
        sum(1 for result in horizon_classified if result.time_horizon_matches_expected)
        / len(horizon_classified)
        if horizon_classified
        else 0.0
    )
    pass_rate = (
        sum(1 for result in results if result.passed) / len(results)
        if results
        else 0.0
    )
    total_cases = len(results)
    intent_classification_accuracy = (
        sum(1 for result in results if result.intent_type_matches_expected is True)
        / total_cases
        if total_cases
        else 0.0
    )
    strategy_compliance_rate = (
        sum(1 for result in results if result.strategy_compliant is True)
        / total_cases
        if total_cases
        else 0.0
    )
    json_parse_success_rate = (
        sum(1 for result in results if result.valid_task_tree)
        / total_cases
        if total_cases
        else 0.0
    )
    return {
        "cases": len(results),
        "passed": sum(1 for result in results if result.passed),
        "pass_rate": pass_rate,
        "intent_classification_accuracy": intent_classification_accuracy,
        "strategy_compliance_rate": strategy_compliance_rate,
        "json_parse_success_rate": json_parse_success_rate,
        "intent_accuracy": intent_accuracy,
        "horizon_accuracy": horizon_accuracy,
        "valid_task_tree": sum(1 for result in results if result.valid_task_tree),
        "top_level_within_max": sum(
            1 for result in results if result.top_level_exceeds_max is False
        ),
        "short_term_delivery_without_low_value_icebreaker": sum(
            1
            for result in short_term_results
            if result.short_term_delivery_without_low_value_icebreaker is True
        ),
        "short_term_delivery_cases": len(short_term_results),
        "action_quality_pass_rate": _rate(
            sum(result.action_quality_pass_count for result in results),
            sum(result.action_quality_action_count for result in results),
        ),
        "average_actionability_score": _rate(
            sum(result.action_quality_score_total for result in results),
            sum(result.action_quality_action_count for result in results),
        ),
        "done_criteria_coverage": _rate(
            sum(result.action_quality_done_criteria_count for result in results),
            sum(result.action_quality_action_count for result in results),
        ),
        "abstract_task_violation_rate": _rate(
            sum(result.action_quality_abstract_violation_count for result in results),
            sum(result.action_quality_action_count for result in results),
        ),
        "long_term_loop_contract_pass_rate": _rate(
            sum(
                1
                for result in results
                if result.loop_count is not None
                and not any(
                    error.startswith("loop_count:")
                    for error in result.strategy_errors
                )
            ),
            sum(result.loop_count is not None for result in results),
        ),
        "outcome_checkpoint_coverage": _rate(
            sum(result.outcome_evidence_present is True for result in results),
            sum(
                result.outcome_evidence_present is not None
                for result in results
            ),
        ),
        "action_quality_targets": {
            "action_quality_pass_rate": 0.85,
            "average_actionability_score": 80,
            "done_criteria_coverage": 0.90,
            "abstract_task_violation_rate": 0.05,
        },
    }


def _rate(numerator: int, denominator: int) -> float:
    return numerator / denominator if denominator else 0.0


def print_report(results: list[EvalResult]) -> None:
    for index, result in enumerate(results, start=1):
        print(json.dumps({"case": index, **asdict(result)}, ensure_ascii=False))
    diagnostics = build_failure_diagnostics(results)
    if diagnostics:
        print(json.dumps({"failure_diagnostics": diagnostics}, ensure_ascii=False, indent=2))
    print(json.dumps({"summary": summarize(results)}, ensure_ascii=False, indent=2))


def summarize_phase_results(results: list[PhaseEvalResult]) -> dict[str, Any]:
    total = len(results)
    return {
        "cases": total,
        "passed": sum(result.passed for result in results),
        "pass_rate": _rate(sum(result.passed for result in results), total),
        "roadmap_visibility_accuracy": _rate(
            sum(result.roadmap_visible for result in results), total
        ),
        "current_phase_horizon_accuracy": _rate(
            sum(result.current_phase_horizon_ok for result in results), total
        ),
        "completed_phase_immutability": _rate(
            sum(result.completed_phase_immutable for result in results), total
        ),
        "json_parse_success_rate": _rate(
            sum(result.json_parse_success for result in results), total
        ),
        "action_quality_pass_rate": _rate(
            sum(result.action_quality_pass_rate for result in results), total
        ),
        "done_criteria_coverage": _rate(
            sum(result.done_criteria_coverage for result in results), total
        ),
    }


def print_phase_report(results: list[PhaseEvalResult]) -> None:
    for result in results:
        print(json.dumps(asdict(result), ensure_ascii=False))
    print(
        json.dumps(
            {"phase_summary": summarize_phase_results(results)},
            ensure_ascii=False,
            indent=2,
        )
    )


def build_failure_diagnostics(results: list[EvalResult]) -> list[dict[str, Any]]:
    diagnostics: list[dict[str, Any]] = []
    for index, result in enumerate(results, start=1):
        if result.passed:
            continue
        failed_metrics: list[str] = []
        if result.intent_type_matches_expected is False:
            failed_metrics.append("intent_classification")
        if result.time_horizon_matches_expected is False:
            failed_metrics.append("horizon_accuracy")
        if not result.valid_task_tree:
            failed_metrics.append("json_parse")
        if result.strategy_compliant is False:
            failed_metrics.append("strategy_compliance")
        diagnostics.append(
            {
                "case_id": index,
                "user_input": result.input,
                "expected_intent_type": result.expected_intent_type,
                "actual_intent_type": result.actual_intent_type,
                "failed_metrics": failed_metrics,
                "horizon_failure_reason": "; ".join(result.horizon_errors)
                if result.horizon_errors
                else _default_horizon_failure_reason(result),
                "strategy_compliance_failure_reason": "; ".join(result.strategy_errors)
                if result.strategy_errors
                else None,
                "planner_top_level_tasks": result.top_level_preview,
                "first_action": result.first_action_snapshot,
                "loop_count": result.loop_count,
                "checkpoint_count": result.checkpoint_count,
                "commitment_count": result.commitment_count,
                "weekly_target_matches": result.weekly_target_matches,
                "outcome_evidence_present": result.outcome_evidence_present,
                "future_occurrence_violation": result.future_occurrence_violation,
            }
        )
    return diagnostics


def _default_horizon_failure_reason(result: EvalResult) -> str | None:
    if result.time_horizon_matches_expected is False:
        return (
            f"expected_horizon={result.expected_horizon}, "
            f"actual_profile_time_horizon={result.actual_time_horizon}"
        )
    return None


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run EasyPlan planner eval cases.")
    parser.add_argument(
        "--cases",
        type=Path,
        default=DEFAULT_CASES_PATH,
        help="Path to JSONL eval cases.",
    )
    parser.add_argument(
        "--provider",
        default=None,
        help="Planner provider. Defaults to EASYPLAN_LLM_PROVIDER or llm_service default.",
    )
    parser.add_argument(
        "--phase-cases",
        nargs="?",
        type=Path,
        const=DEFAULT_PHASE_CASES_PATH,
        default=None,
        help=(
            "Run the DeepSeek-only phase planning suite. Optionally provide a JSONL path; "
            "the flag without a value uses tests/evals/phase_planning_cases.jsonl."
        ),
    )
    parser.add_argument(
        "--model",
        default=None,
        help="Optional planner model override.",
    )
    parser.add_argument(
        "--strict-exit",
        action="store_true",
        help="Exit with code 1 when accuracy or pass rate is below --min-accuracy.",
    )
    parser.add_argument(
        "--min-accuracy",
        type=float,
        default=0.85,
        help="Legacy fallback threshold for intent accuracy and pass rate.",
    )
    parser.add_argument(
        "--min-intent-accuracy",
        type=float,
        default=0.875,
        help="Minimum acceptable intent classification accuracy for strict mode.",
    )
    parser.add_argument(
        "--min-strategy-compliance",
        type=float,
        default=0.8,
        help="Minimum acceptable strategy compliance rate for strict mode.",
    )
    parser.add_argument(
        "--min-json-parse-success",
        type=float,
        default=1.0,
        help="Minimum acceptable JSON parse success rate for strict mode.",
    )
    parser.add_argument(
        "--min-horizon-accuracy",
        type=float,
        default=0.8,
        help="Minimum acceptable scope horizon accuracy for strict mode.",
    )
    parser.add_argument(
        "--min-pass-rate",
        type=float,
        default=0.7,
        help="Minimum acceptable overall pass rate for strict mode.",
    )
    return parser.parse_args()


async def amain() -> int:
    load_env_file()
    args = parse_args()
    if args.phase_cases is not None:
        phase_cases = load_phase_cases(args.phase_cases)
        phase_results = await run_phase_cases(
            phase_cases,
            provider=args.provider,
            model=args.model,
        )
        print_phase_report(phase_results)
        if args.strict_exit:
            phase_summary = summarize_phase_results(phase_results)
            if (
                phase_summary["roadmap_visibility_accuracy"] < 1.0
                or phase_summary["current_phase_horizon_accuracy"] < 1.0
                or phase_summary["completed_phase_immutability"] < 1.0
                or phase_summary["json_parse_success_rate"] < 1.0
                or phase_summary["action_quality_pass_rate"] < 0.85
                or phase_summary["done_criteria_coverage"] < 0.90
            ):
                return 1
        return 0

    cases = load_cases(args.cases)
    results = await run_cases(cases, provider=args.provider, model=args.model)
    print_report(results)
    if args.strict_exit:
        summary = summarize(results)
        if (
            summary["intent_classification_accuracy"] < args.min_intent_accuracy
            or summary["strategy_compliance_rate"] < args.min_strategy_compliance
            or summary["json_parse_success_rate"] < args.min_json_parse_success
            or summary["horizon_accuracy"] < args.min_horizon_accuracy
            or summary["pass_rate"] < args.min_pass_rate
        ):
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(amain()))
