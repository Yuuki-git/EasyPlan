from __future__ import annotations

from dataclasses import dataclass
import os
import re
from typing import Literal

from app.api.schemas import (
    DecisionStrategyContext,
    DeliveryStrategyContext,
    TaskNode,
    TaskTree,
)


StrategyType = Literal["delivery", "decision"]

INTENT_STRATEGY_TYPE: dict[str, StrategyType | None] = {
    "long_term_growth": None,
    "short_term_delivery": "delivery",
    "context_checklist": None,
    "exploration_decision": "decision",
}


@dataclass(frozen=True)
class StrategyContextValidationError:
    code: str
    offender: str
    failed_rule: str
    message: str
    fix_suggestion: str


def strategy_context_enabled() -> bool:
    value = os.getenv("EASYPLAN_STRATEGY_CONTEXT_ENABLED", "false")
    return value.strip().lower() in {"1", "true", "yes", "on"}


def expected_strategy_type(intent_type: str) -> StrategyType | None:
    return INTENT_STRATEGY_TYPE.get(intent_type)


def validate_strategy_context(
    task_tree: TaskTree,
    *,
    intent_type: str,
    intent_text: str | None = None,
    enabled: bool | None = None,
) -> list[StrategyContextValidationError]:
    if enabled is None:
        enabled = strategy_context_enabled()
    if not enabled:
        return []

    errors: list[StrategyContextValidationError] = []
    expected_type = expected_strategy_type(intent_type)
    context = task_tree.strategy_context

    if expected_type is None:
        if context is not None:
            errors.append(
                _error(
                    code="STRATEGY_CONTEXT_FORBIDDEN",
                    offender="strategy_context",
                    failed_rule="intent-to-context matrix",
                    message=f"{intent_type} must not include strategy_context.",
                    fix="Remove strategy_context without changing the TaskTree or planning_context.",
                )
            )
        return errors

    if context is None:
        code = (
            "DELIVERY_CONTEXT_MISSING"
            if expected_type == "delivery"
            else "DECISION_CONTEXT_MISSING"
        )
        errors.append(
            _error(
                code=code,
                offender="strategy_context",
                failed_rule="intent-to-context matrix",
                message=f"{intent_type} requires a {expected_type} strategy_context.",
                fix=f"Add only the {expected_type} strategy_context for the existing tasks.",
            )
        )
        return errors

    if context.strategy_type != expected_type:
        errors.append(
            _error(
                code="STRATEGY_CONTEXT_TYPE_MISMATCH",
                offender="strategy_context.strategy_type",
                failed_rule="intent-to-context matrix",
                message=(
                    f"Expected strategy_type={expected_type}, got {context.strategy_type}."
                ),
                fix=f"Replace only strategy_context with strategy_type={expected_type}.",
            )
        )
        return errors

    action_ids, duplicate_action_ids = _collect_action_ids(task_tree.root)
    for client_node_id in sorted(duplicate_action_ids):
        errors.append(
            _error(
                code="STRATEGY_TASK_ID_DUPLICATE",
                offender=client_node_id,
                failed_rule="TaskNode reference identity",
                message=f"Action client_node_id {client_node_id!r} is duplicated.",
                fix="Give every Action a unique client_node_id and update only its references.",
            )
        )

    if isinstance(context, DeliveryStrategyContext):
        errors.extend(
            _validate_delivery(
                task_tree,
                context,
                action_ids=action_ids,
                intent_text=intent_text,
            )
        )
    elif isinstance(context, DecisionStrategyContext):
        errors.extend(_validate_decision(task_tree, context, action_ids=action_ids))
    return errors


def _validate_delivery(
    task_tree: TaskTree,
    context: DeliveryStrategyContext,
    *,
    action_ids: set[str],
    intent_text: str | None,
) -> list[StrategyContextValidationError]:
    errors: list[StrategyContextValidationError] = []
    if task_tree.planning_context is not None:
        errors.append(
            _error(
                code="DELIVERY_PLANNING_CONTEXT_FORBIDDEN",
                offender="planning_context",
                failed_rule="delivery phase isolation",
                message="short_term_delivery must keep planning_context null.",
                fix="Remove planning_context; keep the delivery strategy and tasks unchanged.",
            )
        )

    planned_minutes = sum(
        node.estimated_minutes
        for node in _iter_nodes(task_tree.root)
        if node.node_type == "action"
    )
    action_count = sum(
        node.node_type == "action" for node in _iter_nodes(task_tree.root)
    )
    if _is_single_small_delivery(intent_text) and action_count > 1:
        errors.append(
            _error(
                code="DELIVERY_SMALL_TASK_OVERPLANNED",
                offender="TaskTree.root.children",
                failed_rule="small delivery scope",
                message=(
                    f"A single small deliverable was expanded into {action_count} Actions."
                ),
                fix=(
                    "Keep exactly one Action for the small deliverable; combine drafting, "
                    "review, and sending into its done_criteria instead of separate tasks."
                ),
            )
        )
    if context.time_plan.planned_minutes != planned_minutes:
        errors.append(
            _error(
                code="DELIVERY_PLANNED_MINUTES_MISMATCH",
                offender="strategy_context.time_plan.planned_minutes",
                failed_rule="delivery time arithmetic",
                message=(
                    f"planned_minutes={context.time_plan.planned_minutes} but Action total="
                    f"{planned_minutes}."
                ),
                fix=f"Set planned_minutes to {planned_minutes}; do not rewrite valid tasks.",
            )
        )

    available = context.time_plan.available_minutes
    if available is not None and planned_minutes + context.time_plan.buffer_minutes > available:
        errors.append(
            _error(
                code="DELIVERY_TIME_BUDGET_EXCEEDED",
                offender="strategy_context.time_plan",
                failed_rule="delivery time budget",
                message=(
                    f"Action total {planned_minutes} plus buffer "
                    f"{context.time_plan.buffer_minutes} exceeds available {available}."
                ),
                fix=(
                    "Keep buffer_minutes greater than zero. Remove or shorten can_cut/should_have "
                    "Actions, time-box must_have depth if necessary, then reduce Action "
                    "estimated_minutes so planned_minutes + buffer_minutes fits available_minutes. "
                    "Recompute planned_minutes exactly; preserve the user's must_have scope."
                ),
            )
        )
    if planned_minutes >= 60 and context.time_plan.buffer_minutes == 0:
        errors.append(
            _error(
                code="DELIVERY_BUFFER_MISSING",
                offender="strategy_context.time_plan.buffer_minutes",
                failed_rule="delivery buffer",
                message="A delivery plan of at least 60 minutes requires a nonzero buffer.",
                fix="Reserve a small nonzero buffer and trim can_cut work if necessary.",
            )
        )

    workstream_ids: set[str] = set()
    referenced_by_workstreams: set[str] = set()
    for workstream in context.workstreams:
        if workstream.workstream_id in workstream_ids:
            errors.append(
                _error(
                    code="DELIVERY_WORKSTREAM_REFERENCE_INVALID",
                    offender=workstream.workstream_id,
                    failed_rule="delivery workstream identity",
                    message="workstream_id values must be unique.",
                    fix="Rename only the duplicate workstream_id.",
                )
            )
        workstream_ids.add(workstream.workstream_id)
        errors.extend(
            _validate_references(
                refs=workstream.task_client_node_ids,
                action_ids=action_ids,
                code="DELIVERY_WORKSTREAM_REFERENCE_INVALID",
                offender=f"workstreams.{workstream.workstream_id}.task_client_node_ids",
                failed_rule="delivery workstream references",
                fix="Keep only unique client_node_id values that reference Actions in this TaskTree.",
            )
        )
        referenced_by_workstreams.update(workstream.task_client_node_ids)

    errors.extend(
        _validate_references(
            refs=context.critical_path_client_node_ids,
            action_ids=action_ids,
            code="DELIVERY_CRITICAL_PATH_MISSING",
            offender="strategy_context.critical_path_client_node_ids",
            failed_rule="delivery critical path references",
            fix="Reference at least one unique Action already included in a workstream.",
        )
    )
    outside_workstreams = set(context.critical_path_client_node_ids) - referenced_by_workstreams
    if outside_workstreams:
        errors.append(
            _error(
                code="DELIVERY_CRITICAL_PATH_MISSING",
                offender="strategy_context.critical_path_client_node_ids",
                failed_rule="delivery critical path membership",
                message=(
                    "Critical path Actions are not covered by a workstream: "
                    + ", ".join(sorted(outside_workstreams))
                ),
                fix="Keep critical-path IDs as a subset of the existing workstream Action references.",
            )
        )

    errors.extend(_validate_explicit_delivery_constraints(context, intent_text))
    return errors


def _validate_decision(
    task_tree: TaskTree,
    context: DecisionStrategyContext,
    *,
    action_ids: set[str],
) -> list[StrategyContextValidationError]:
    errors: list[StrategyContextValidationError] = []
    planning_context = task_tree.planning_context
    if (
        planning_context is None
        or planning_context.schema_version != 1
        or planning_context.intent_type != "exploration_decision"
    ):
        errors.append(
            _error(
                code="DECISION_PLANNING_CONTEXT_INVALID",
                offender="planning_context",
                failed_rule="decision planning schema",
                message="exploration_decision requires planning_context schema v1.",
                fix="Restore schema v1 exploration planning_context without changing the decision context.",
            )
        )

    statement = context.current_judgment.statement.strip()
    normalized_statement = _normalize_text(statement)
    normalized_summary = _normalize_text(task_tree.summary)
    non_answer = re.search(
        r"^(?:还|仍)?(?:需要|需|要)(?:更多|补充)?(?:信息|资料|调研)|"
        r"^(?:more|additional) information (?:is )?needed",
        statement,
        flags=re.IGNORECASE,
    )
    if non_answer or normalized_statement not in normalized_summary:
        errors.append(
            _error(
                code="DECISION_ANSWER_MISSING",
                offender="strategy_context.current_judgment.statement",
                failed_rule="decision answer first",
                message="The current judgment must directly answer the question and appear in summary.",
                fix="Write one provisional answer first, then keep basis and next exploration steps after it.",
            )
        )

    if re.search(
        r"(?:绝对|一定|必然|毫无疑问|保证|definitely|certainly|guaranteed)",
        statement,
        flags=re.IGNORECASE,
    ):
        errors.append(
            _error(
                code="DECISION_OVERCONFIDENT",
                offender="strategy_context.current_judgment",
                failed_rule="provisional judgment",
                message="The judgment uses absolute or guaranteed language.",
                fix="Make the answer provisional, preserve missing information, and lower certainty if needed.",
            )
        )

    experiment_ids: set[str] = set()
    for experiment in context.experiments:
        if experiment.experiment_id in experiment_ids:
            errors.append(
                _error(
                    code="DECISION_EXPERIMENT_REFERENCE_INVALID",
                    offender=experiment.experiment_id,
                    failed_rule="decision experiment identity",
                    message="experiment_id values must be unique.",
                    fix="Rename only the duplicate experiment_id.",
                )
            )
        experiment_ids.add(experiment.experiment_id)
        errors.extend(
            _validate_references(
                refs=experiment.task_client_node_ids,
                action_ids=action_ids,
                code="DECISION_EXPERIMENT_REFERENCE_INVALID",
                offender=f"experiments.{experiment.experiment_id}.task_client_node_ids",
                failed_rule="decision experiment references",
                fix="Keep only unique client_node_id values that reference Actions in this TaskTree.",
            )
        )

    if not context.decision_gate.proceed_if or not context.decision_gate.stop_if:
        errors.append(
            _error(
                code="DECISION_GATE_INCOMPLETE",
                offender="strategy_context.decision_gate",
                failed_rule="decision gate completeness",
                message="decision_gate requires both proceed_if and stop_if branches.",
                fix="Add at least one concrete proceed condition and one concrete stop condition.",
            )
        )

    for index, basis in enumerate(context.basis):
        if basis.basis_type == "working_assumption" and not re.search(
            r"(?:假设|推测|暂定|assum(?:e|ption))",
            basis.statement,
            flags=re.IGNORECASE,
        ):
            errors.append(
                _error(
                    code="DECISION_ASSUMPTION_UNLABELED",
                    offender=f"strategy_context.basis[{index}]",
                    failed_rule="working assumption labeling",
                    message="A working_assumption must explicitly identify itself as an assumption.",
                    fix="Prefix the statement with '假设：' or equivalent wording; do not turn it into a fact.",
                )
            )
    return errors


def _validate_explicit_delivery_constraints(
    context: DeliveryStrategyContext,
    intent_text: str | None,
) -> list[StrategyContextValidationError]:
    if not intent_text:
        return []
    issues: list[str] = []
    expected_minutes = _extract_available_minutes(intent_text)
    if expected_minutes is not None and context.time_plan.available_minutes != expected_minutes:
        issues.append(
            f"available_minutes must preserve explicit budget {expected_minutes}"
        )

    format_tokens = _extract_format_tokens(intent_text)
    format_text = _normalize_text(
        f"{context.deliverable.title} {context.deliverable.format}"
    )
    missing_formats = [token for token in format_tokens if _normalize_text(token) not in format_text]
    if missing_formats:
        issues.append("deliverable format lost: " + ", ".join(missing_formats))

    deadline_tokens = _extract_deadline_tokens(intent_text)
    deadline_text = _normalize_text(context.deadline.text)
    if deadline_tokens and not context.deadline.is_explicit:
        issues.append("deadline.is_explicit must be true")
    missing_deadline_tokens = [
        token for token in deadline_tokens if _normalize_text(token) not in deadline_text
    ]
    if missing_deadline_tokens:
        issues.append("deadline text lost: " + ", ".join(missing_deadline_tokens))

    if not issues:
        return []
    return [
        _error(
            code="DELIVERY_EXPLICIT_CONSTRAINT_DRIFT",
            offender="strategy_context",
            failed_rule="explicit user constraint preservation",
            message="; ".join(issues),
            fix="Copy the user's explicit deadline, available time, and output format into strategy_context without paraphrasing them away.",
        )
    ]


def _validate_references(
    *,
    refs: list[str],
    action_ids: set[str],
    code: str,
    offender: str,
    failed_rule: str,
    fix: str,
) -> list[StrategyContextValidationError]:
    issues: list[str] = []
    duplicates = sorted({value for value in refs if refs.count(value) > 1})
    missing = sorted(set(refs) - action_ids)
    if duplicates:
        issues.append("duplicate references: " + ", ".join(duplicates))
    if missing:
        issues.append("non-Action or missing references: " + ", ".join(missing))
    if not issues:
        return []
    return [
        _error(
            code=code,
            offender=offender,
            failed_rule=failed_rule,
            message="; ".join(issues),
            fix=fix,
        )
    ]


def _collect_action_ids(root: TaskNode) -> tuple[set[str], set[str]]:
    action_ids: set[str] = set()
    duplicates: set[str] = set()
    for node in _iter_nodes(root):
        if node.node_type != "action":
            continue
        if node.client_node_id in action_ids:
            duplicates.add(node.client_node_id)
        action_ids.add(node.client_node_id)
    return action_ids, duplicates


def _iter_nodes(root: TaskNode):
    yield root
    for child in root.children:
        yield from _iter_nodes(child)


def _extract_available_minutes(text: str) -> int | None:
    patterns = (
        r"(?:只有|仅有|可用|能投入|时间预算(?:是|为)?|only have|available(?: time)?(?: is)?)\s*"
        r"(\d+(?:\.\d+)?)\s*(分钟|分|小时|钟头|minutes?|mins?|hours?|hrs?)",
    )
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            value = float(match.group(1))
            unit = match.group(2).lower()
            return round(value * 60) if unit in {"小时", "钟头", "hour", "hours", "hr", "hrs"} else round(value)
    return None


def _is_single_small_delivery(intent_text: str | None) -> bool:
    if not intent_text:
        return False
    return bool(
        re.search(
            r"(?:一|1)\s*封[^，。；]{0,20}(?:邮件|email)|"
            r"(?:send|write|draft)\s+(?:a|one)\s+[^.]{0,20}email",
            intent_text,
            flags=re.IGNORECASE,
        )
    )


def _extract_format_tokens(text: str) -> list[str]:
    tokens = re.findall(
        r"\b(?:PPT|PDF|Word|Excel|Markdown|CSV)\b|邮件|报告|文档|表格|演示稿|商业计划书",
        text,
        flags=re.IGNORECASE,
    )
    return list(dict.fromkeys(tokens))


def _extract_deadline_tokens(text: str) -> list[str]:
    if not re.search(r"(?:截止|之前|以前|前完成|前交付|\bby\b|deadline)", text, flags=re.IGNORECASE):
        return []
    tokens = re.findall(
        r"今天|明天|后天|周[一二三四五六日天]|星期[一二三四五六日天]|"
        r"Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday|"
        r"\d{1,2}(?::\d{2})?\s*(?:am|pm|点|时)",
        text,
        flags=re.IGNORECASE,
    )
    return list(dict.fromkeys(tokens))


def _normalize_text(value: str) -> str:
    return re.sub(r"[\s，。；：、,.!?;:'\"()（）-]+", "", value).lower()


def _error(
    *,
    code: str,
    offender: str,
    failed_rule: str,
    message: str,
    fix: str,
) -> StrategyContextValidationError:
    return StrategyContextValidationError(
        code=code,
        offender=offender,
        failed_rule=failed_rule,
        message=message,
        fix_suggestion=fix,
    )
