from __future__ import annotations

import argparse
import asyncio
import inspect
import json
import re
import sys
from dataclasses import asdict, dataclass
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
    valid_task_tree: bool
    top_level_node_count: int | None
    total_node_count: int | None
    max_nodes: int
    top_level_exceeds_max: bool | None
    contains_low_value_icebreaker: bool | None
    short_term_delivery_without_low_value_icebreaker: bool | None
    must_have_icebreaker: bool
    icebreaker_present: bool | None
    validation_error: str | None = None
    runtime_error: str | None = None
    reasoning_event_count: int = 0
    usage_record_count: int = 0

    @property
    def passed(self) -> bool:
        if not self.valid_task_tree:
            return False
        if self.top_level_exceeds_max:
            return False
        if self.short_term_delivery_without_low_value_icebreaker is False:
            return False
        if self.must_have_icebreaker and self.icebreaker_present is False:
            return False
        return True


def load_cases(path: Path) -> list[EvalCase]:
    cases: list[EvalCase] = []
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            stripped = line.strip()
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
) -> list[EvalResult]:
    planner = create_planner_client(provider=provider, model=model)
    results: list[EvalResult] = []
    for case in cases:
        reasoning_sink = ListReasoningSink()
        usage_sink = ListUsageSink()
        try:
            raw_plan = await _create_plan(planner, case.input, reasoning_sink, usage_sink)
            result = evaluate_plan(case, raw_plan)
        except Exception as exc:
            result = EvalResult(
                input=case.input,
                expected_intent_type=case.expected_intent_type,
                expected_horizon=case.expected_horizon,
                valid_task_tree=False,
                top_level_node_count=None,
                total_node_count=None,
                max_nodes=case.max_nodes,
                top_level_exceeds_max=None,
                contains_low_value_icebreaker=None,
                short_term_delivery_without_low_value_icebreaker=None,
                must_have_icebreaker=case.must_have_icebreaker,
                icebreaker_present=None,
                runtime_error=f"{type(exc).__name__}: {exc}",
            )
        result.reasoning_event_count = len(reasoning_sink.events)
        result.usage_record_count = len(usage_sink.records)
        results.append(result)
    return results


async def _create_plan(
    planner: Any,
    user_input: str,
    reasoning_sink: ListReasoningSink,
    usage_sink: ListUsageSink,
) -> dict[str, Any]:
    prompt = build_planner_prompt(user_input)
    parameters = inspect.signature(planner.create_plan).parameters
    if "usage_sink" in parameters:
        return await planner.create_plan(
            prompt,
            reasoning_sink=reasoning_sink,
            usage_sink=usage_sink,
        )
    return await planner.create_plan(prompt, reasoning_sink=reasoning_sink)


def evaluate_plan(case: EvalCase, raw_plan: dict[str, Any]) -> EvalResult:
    try:
        task_tree = TaskTree.model_validate(raw_plan)
    except Exception as exc:
        return EvalResult(
            input=case.input,
            expected_intent_type=case.expected_intent_type,
            expected_horizon=case.expected_horizon,
            valid_task_tree=False,
            top_level_node_count=None,
            total_node_count=None,
            max_nodes=case.max_nodes,
            top_level_exceeds_max=None,
            contains_low_value_icebreaker=None,
            short_term_delivery_without_low_value_icebreaker=None,
            must_have_icebreaker=case.must_have_icebreaker,
            icebreaker_present=None,
            validation_error=str(exc),
        )

    top_level_node_count = len(task_tree.root.children)
    total_node_count = sum(1 for _ in iter_task_nodes(task_tree.root))
    contains_low_value = contains_low_value_icebreaker(task_tree)
    short_term_ok = None
    if case.expected_intent_type == "short_term_delivery":
        short_term_ok = not contains_low_value

    return EvalResult(
        input=case.input,
        expected_intent_type=case.expected_intent_type,
        expected_horizon=case.expected_horizon,
        valid_task_tree=True,
        top_level_node_count=top_level_node_count,
        total_node_count=total_node_count,
        max_nodes=case.max_nodes,
        top_level_exceeds_max=top_level_node_count > case.max_nodes,
        contains_low_value_icebreaker=contains_low_value,
        short_term_delivery_without_low_value_icebreaker=short_term_ok,
        must_have_icebreaker=case.must_have_icebreaker,
        icebreaker_present=has_first_step_icebreaker(task_tree),
    )


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


def summarize(results: list[EvalResult]) -> dict[str, Any]:
    short_term_results = [
        result
        for result in results
        if result.expected_intent_type == "short_term_delivery"
    ]
    return {
        "cases": len(results),
        "passed": sum(1 for result in results if result.passed),
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
    print(json.dumps({"summary": summarize(results)}, ensure_ascii=False, indent=2))


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
        help="Exit with code 1 when any case fails.",
    )
    return parser.parse_args()


async def amain() -> int:
    args = parse_args()
    cases = load_cases(args.cases)
    results = await run_cases(cases, provider=args.provider, model=args.model)
    print_report(results)
    if args.strict_exit and any(not result.passed for result in results):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(amain()))
