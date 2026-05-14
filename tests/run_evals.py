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

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.agents.nodes import build_planner_prompt  # noqa: E402
from app.api.schemas import TaskNode, TaskTree  # noqa: E402
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
            raw_plan = await _create_plan(
                planner,
                case.input,
                intent_profile,
                reasoning_sink,
                usage_sink,
            )
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
) -> dict[str, Any]:
    prompt = build_planner_prompt(user_input, intent_profile=intent_profile)
    parameters = inspect.signature(planner.create_plan).parameters
    if "usage_sink" in parameters:
        return await planner.create_plan(
            prompt,
            reasoning_sink=reasoning_sink,
            usage_sink=usage_sink,
        )
    return await planner.create_plan(prompt, reasoning_sink=reasoning_sink)


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
    for node in iter_task_nodes(task_tree.root):
        text = " ".join(
            value
            for value in (node.title, node.description or "", node.verb)
            if value
        )
        if any(pattern.search(text) for pattern in LOW_VALUE_ICEBREAKER_PATTERNS):
            return True
    return False


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
        "done_criteria": None,
    }


def collect_horizon_errors(case: EvalCase, task_tree: TaskTree) -> list[str]:
    text = task_tree_text(task_tree)
    errors: list[str] = []
    if case.expected_intent_type == "long_term_growth":
        if any(pattern.search(text) for pattern in LONG_TERM_HORIZON_PATTERNS):
            errors.append(
                "long_term_growth output includes long-cycle scheduling; expected only current 24-72h Phase 1 tasks"
            )
        if top_level_looks_like_long_term_curriculum(task_tree):
            errors.append(
                "long_term_growth top-level tasks look like a full curriculum roadmap instead of Phase 1 actions"
            )
    elif case.expected_intent_type == "exploration_decision":
        if any(pattern.search(text) for pattern in EXPLORATION_EXECUTION_PATTERNS):
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
        if any(pattern.search(text) for pattern in EXPLORATION_EXECUTION_PATTERNS):
            errors.append("exploration_decision should not produce a long-term execution plan")
        if not any(term in text for term in EXPLORATION_DISCOVERY_TERMS):
            errors.append("exploration_decision lacks clarification, information gathering, experiment, or decision nodes")
    return errors


def task_tree_text(task_tree: TaskTree) -> str:
    parts = [task_tree.summary, *task_tree.assumptions]
    for node in iter_task_nodes(task_tree.root):
        parts.extend(
            value
            for value in (node.title, node.description or "", node.verb)
            if value
        )
    return " ".join(parts)


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
    }


def print_report(results: list[EvalResult]) -> None:
    for index, result in enumerate(results, start=1):
        print(json.dumps({"case": index, **asdict(result)}, ensure_ascii=False))
    diagnostics = build_failure_diagnostics(results)
    if diagnostics:
        print(json.dumps({"failure_diagnostics": diagnostics}, ensure_ascii=False, indent=2))
    print(json.dumps({"summary": summarize(results)}, ensure_ascii=False, indent=2))


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
