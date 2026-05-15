import pytest
from pydantic import ValidationError

from app.api.schemas import MAX_TASK_TREE_DEPTH, MAX_TASK_TREE_SIBLINGS, TaskTree
from tests.test_agent_graph import valid_plan


def test_task_tree_rejects_excessive_depth():
    tree = valid_plan()
    node = tree["root"]
    for depth in range(MAX_TASK_TREE_DEPTH + 1):
        child = {
            "client_node_id": f"deep-{depth}",
            "title": "继续拆解",
            "description": None,
            "verb": "继续",
            "estimated_minutes": 1,
            "node_type": "group" if depth < MAX_TASK_TREE_DEPTH else "action",
            "depends_on": [],
            "children": [],
        }
        node["children"] = [child]
        node = child

    with pytest.raises(ValidationError, match="maximum depth"):
        TaskTree.model_validate(tree)


def test_task_node_rejects_too_many_children():
    tree = valid_plan()
    tree["root"]["children"] = [
        {
            "client_node_id": f"task-{index}",
            "title": "打开文档",
            "description": None,
            "verb": "打开",
            "estimated_minutes": 1,
            "node_type": "action",
            "depends_on": [],
            "children": [],
        }
        for index in range(MAX_TASK_TREE_SIBLINGS + 1)
    ]

    with pytest.raises(ValidationError):
        TaskTree.model_validate(tree)


def test_task_tree_schema_allows_long_estimates_for_langgraph_validator():
    tree = valid_plan()
    tree["root"]["estimated_minutes"] = 120
    tree["root"]["children"][0]["estimated_minutes"] = 8

    parsed = TaskTree.model_validate(tree)

    assert parsed.root.estimated_minutes == 120
    assert parsed.root.children[0].estimated_minutes == 8


def test_task_tree_schema_rejects_zero_estimate_for_action_nodes():
    tree = valid_plan()
    tree["root"]["children"][0]["estimated_minutes"] = 0

    with pytest.raises(ValidationError, match="action estimated_minutes must be greater than or equal to 1"):
        TaskTree.model_validate(tree)


def test_task_tree_schema_accepts_legacy_nodes_without_action_quality_fields():
    tree = valid_plan()

    parsed = TaskTree.model_validate(tree)

    first_action = parsed.root.children[0]
    assert first_action.done_criteria is None
    assert first_action.start_hint is None
    assert first_action.fallback_action is None


def test_task_tree_schema_accepts_action_quality_fields_without_new_planning_layers():
    tree = valid_plan()
    tree["root"]["children"][0].update(
        {
            "done_criteria": "A draft file exists with three bullet points.",
            "start_hint": "Open the existing notes file first.",
            "fallback_action": "Write one rough bullet if the full draft feels too large.",
        }
    )

    parsed = TaskTree.model_validate(tree)
    first_action = parsed.root.children[0]

    assert first_action.done_criteria == "A draft file exists with three bullet points."
    assert first_action.start_hint == "Open the existing notes file first."
    assert first_action.fallback_action == "Write one rough bullet if the full draft feels too large."
    assert "roadmap" not in TaskTree.model_fields
    assert "current_phase" not in TaskTree.model_fields
    assert "next_action" not in TaskTree.model_fields
