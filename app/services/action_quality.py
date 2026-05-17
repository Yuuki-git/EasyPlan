from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from app.api.schemas import TaskNode, TaskTree


ACTION_QUALITY_PASS_SCORE = 80
ABSTRACT_TASK_TERMS = (
    "学习",
    "研究",
    "准备",
    "完善",
    "提升",
    "了解",
    "思考",
    "开始",
    "优化",
    "整理一下",
    "搞一下",
    "看一下",
)
GENERIC_OBJECT_TERMS = (
    "语法",
    "资料",
    "内容",
    "东西",
    "事项",
    "任务",
    "计划",
    "一下",
)
SPECIFIC_CONTEXT_PATTERNS = (
    re.compile(r"[A-Za-z]\d|\d+|[一二三四五六七八九十]+个"),
    re.compile(r"[「『《“\"](.+?)[」』》”\"]"),
)
CONCRETE_OUTPUT_TERMS = (
    "写出",
    "列出",
    "提交",
    "保存",
    "完成",
    "标出",
    "记录",
    "生成",
    "对比",
    "整理成",
    "画出",
    "发给",
    "创建",
)


@dataclass(frozen=True)
class ActionQualityResult:
    score: int
    has_explicit_verb: bool
    has_explicit_object: bool
    has_reasonable_estimate: bool
    has_done_criteria: bool
    has_abstract_violation: bool
    reasons: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class ActionQualitySummary:
    action_count: int
    pass_count: int
    score_total: int
    done_criteria_count: int
    abstract_violation_count: int

    @property
    def action_quality_pass_rate(self) -> float:
        return _ratio(self.pass_count, self.action_count)

    @property
    def average_actionability_score(self) -> float:
        return _ratio(self.score_total, self.action_count)

    @property
    def done_criteria_coverage(self) -> float:
        return _ratio(self.done_criteria_count, self.action_count)

    @property
    def abstract_task_violation_rate(self) -> float:
        return _ratio(self.abstract_violation_count, self.action_count)


def score_action_node(node: TaskNode) -> ActionQualityResult:
    has_explicit_verb = bool(_clean(getattr(node, "verb", None)))
    has_explicit_object = _has_explicit_object(node)
    has_reasonable_estimate = _has_reasonable_estimate(node)
    has_done_criteria = bool(_clean(getattr(node, "done_criteria", None)))
    has_abstract_violation = has_abstract_task_violation(node)

    dimensions = (
        (has_explicit_verb, "missing_explicit_verb"),
        (has_explicit_object, "missing_explicit_object"),
        (has_reasonable_estimate, "unreasonable_estimated_minutes"),
        (has_done_criteria, "missing_done_criteria"),
        (not has_abstract_violation, "abstract_task_violation"),
    )
    score = sum(20 for passed, _reason in dimensions if passed)
    reasons = [reason for passed, reason in dimensions if not passed]
    return ActionQualityResult(
        score=score,
        has_explicit_verb=has_explicit_verb,
        has_explicit_object=has_explicit_object,
        has_reasonable_estimate=has_reasonable_estimate,
        has_done_criteria=has_done_criteria,
        has_abstract_violation=has_abstract_violation,
        reasons=reasons,
    )


def summarize_action_quality(task_tree: TaskTree) -> ActionQualitySummary:
    action_results = [
        score_action_node(node)
        for node in _iter_task_nodes(task_tree.root)
        if node.node_type == "action"
    ]
    return ActionQualitySummary(
        action_count=len(action_results),
        pass_count=sum(1 for result in action_results if result.score >= ACTION_QUALITY_PASS_SCORE),
        score_total=sum(result.score for result in action_results),
        done_criteria_count=sum(1 for result in action_results if result.has_done_criteria),
        abstract_violation_count=sum(1 for result in action_results if result.has_abstract_violation),
    )


def has_abstract_task_violation(node: TaskNode) -> bool:
    title = _clean(getattr(node, "title", None))
    verb = _clean(getattr(node, "verb", None))
    description = _clean(getattr(node, "description", None))
    text = f"{title} {description}".strip()
    matched_terms = [
        term
        for term in ABSTRACT_TASK_TERMS
        if title.startswith(term) or verb == term or title == term or f"{term}一下" in title
    ]
    if not matched_terms:
        return False
    return not _has_specific_context(text)


def _has_explicit_object(node: TaskNode) -> bool:
    title = _clean(getattr(node, "title", None))
    verb = _clean(getattr(node, "verb", None))
    if not title:
        return False
    remainder = title
    if verb and remainder.startswith(verb):
        remainder = remainder[len(verb):]
    for term in ABSTRACT_TASK_TERMS:
        if remainder.startswith(term):
            remainder = remainder[len(term):]
            break
    remainder = remainder.strip(" ：:，,。.!！的")
    if not remainder or remainder in GENERIC_OBJECT_TERMS:
        return False
    return len(remainder) >= 2


def _has_reasonable_estimate(node: TaskNode) -> bool:
    estimated_minutes = getattr(node, "estimated_minutes", None)
    return isinstance(estimated_minutes, int) and 1 <= estimated_minutes <= 120


def _has_specific_context(text: str) -> bool:
    if any(pattern.search(text) for pattern in SPECIFIC_CONTEXT_PATTERNS):
        return any(term in text for term in CONCRETE_OUTPUT_TERMS) or "并" in text
    return any(term in text for term in CONCRETE_OUTPUT_TERMS) and len(text) >= 12


def _iter_task_nodes(node: Any):
    yield node
    for child in getattr(node, "children", []) or []:
        yield from _iter_task_nodes(child)


def _clean(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _ratio(numerator: int, denominator: int) -> float:
    return numerator / denominator if denominator else 0.0
