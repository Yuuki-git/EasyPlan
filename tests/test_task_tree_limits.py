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
