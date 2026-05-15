from app.api.schemas import TaskNode, TaskTree
from app.services.action_quality import (
    ACTION_QUALITY_PASS_SCORE,
    has_abstract_task_violation,
    score_action_node,
    summarize_action_quality,
)


def action_node(
    *,
    title: str,
    verb: str,
    estimated_minutes: int = 25,
    done_criteria: str | None = "A concrete artifact exists.",
) -> TaskNode:
    return TaskNode(
        client_node_id="action-1",
        title=title,
        description=None,
        verb=verb,
        estimated_minutes=estimated_minutes,
        node_type="action",
        depends_on=[],
        children=[],
        done_criteria=done_criteria,
        start_hint=None,
        fallback_action=None,
    )


def test_high_quality_action_gets_high_score():
    result = score_action_node(
        action_node(
            title="撰写商业计划书的核心痛点大纲",
            verb="撰写",
            estimated_minutes=30,
            done_criteria="大纲包含 3 个核心痛点和每个痛点的一句话解释。",
        )
    )

    assert result.score >= ACTION_QUALITY_PASS_SCORE
    assert result.has_explicit_verb is True
    assert result.has_explicit_object is True
    assert result.has_reasonable_estimate is True
    assert result.has_done_criteria is True
    assert result.has_abstract_violation is False


def test_vague_abstract_tasks_get_low_scores():
    examples = [
        action_node(title="学习语法", verb="学习", done_criteria=None),
        action_node(title="准备资料", verb="准备", done_criteria=None),
        action_node(title="研究一下", verb="研究", done_criteria=None),
    ]

    scores = [score_action_node(node).score for node in examples]

    assert all(score < ACTION_QUALITY_PASS_SCORE for score in scores)
    assert all(has_abstract_task_violation(node) for node in examples)


def test_done_criteria_increases_actionability_score():
    without_done_criteria = action_node(
        title="列出商业计划书的核心痛点大纲",
        verb="列出",
        done_criteria=None,
    )
    with_done_criteria = action_node(
        title="列出商业计划书的核心痛点大纲",
        verb="列出",
        done_criteria="列出 3 个痛点，每个痛点有一句用户场景说明。",
    )

    assert score_action_node(with_done_criteria).score > score_action_node(without_done_criteria).score


def test_abstract_word_in_specific_context_is_not_rejected():
    node = action_node(
        title="学习 N3 语法「〜ために」并写出 2 个例句",
        verb="学习",
        estimated_minutes=20,
        done_criteria="写出 2 个包含「〜ために」的例句。",
    )

    result = score_action_node(node)

    assert result.has_abstract_violation is False
    assert result.score >= ACTION_QUALITY_PASS_SCORE


def test_summarize_action_quality_reports_rates_for_action_nodes_only():
    tree = TaskTree.model_validate(
        {
            "root": {
                "client_node_id": "root",
                "title": "Prepare plan",
                "description": None,
                "verb": "Prepare",
                "estimated_minutes": 60,
                "node_type": "group",
                "depends_on": [],
                "children": [
                    {
                        "client_node_id": "good",
                        "title": "列出商业计划书的核心痛点大纲",
                        "description": None,
                        "verb": "列出",
                        "estimated_minutes": 20,
                        "node_type": "action",
                        "depends_on": [],
                        "children": [],
                        "done_criteria": "列出 3 个痛点。",
                    },
                    {
                        "client_node_id": "bad",
                        "title": "准备资料",
                        "description": None,
                        "verb": "准备",
                        "estimated_minutes": 20,
                        "node_type": "action",
                        "depends_on": [],
                        "children": [],
                    },
                ],
            },
            "summary": "Action quality sample",
            "assumptions": [],
        }
    )

    summary = summarize_action_quality(tree)

    assert summary.action_count == 2
    assert summary.done_criteria_coverage == 0.5
    assert summary.abstract_task_violation_rate == 0.5
    assert summary.average_actionability_score < 100
