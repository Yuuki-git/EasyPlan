import pytest
from pydantic import ValidationError

from app.api.schemas import TaskTree


def _phase_tree() -> dict:
    return {
        "root": {
            "client_node_id": "phase_01_root",
            "title": "起步阶段",
            "verb": "推进",
            "estimated_minutes": 5,
            "node_type": "group",
            "children": [
                {
                    "client_node_id": "phase_01_action_01",
                    "title": "完成 5 道 N3 摸底题",
                    "verb": "完成",
                    "estimated_minutes": 5,
                    "node_type": "action",
                    "done_criteria": "完成 5 题并记录不会的词",
                    "start_hint": "打开浏览器搜索 N3 词汇测试",
                    "fallback_action": "先完成第 1 题",
                }
            ],
        },
        "summary": "先完成低成本摸底",
        "assumptions": [],
        "planning_context": {
            "schema_version": 1,
            "intent_type": "long_term_growth",
            "time_horizon": "months",
            "roadmap": [
                {
                    "phase_id": "phase_01",
                    "order": 1,
                    "title": "起步",
                    "objective": "完成摸底",
                    "status": "current",
                },
                {
                    "phase_id": "phase_02",
                    "order": 2,
                    "title": "基础",
                    "objective": "建立基础",
                    "status": "planned",
                },
                {
                    "phase_id": "phase_03",
                    "order": 3,
                    "title": "强化",
                    "objective": "完成强化",
                    "status": "planned",
                },
            ],
            "current_phase": {
                "phase_id": "phase_01",
                "title": "起步",
                "objective": "完成摸底",
                "completion_rule": "all_ai_actions_completed",
            },
            "next_action_client_node_id": "phase_01_action_01",
        },
    }


def test_task_tree_accepts_optional_planning_context_and_legacy_tree():
    phase_tree = TaskTree.model_validate(_phase_tree())
    legacy = _phase_tree()
    legacy.pop("planning_context")

    assert phase_tree.planning_context is not None
    assert phase_tree.planning_context.intent_type == "long_term_growth"
    assert TaskTree.model_validate(legacy).planning_context is None


def test_task_tree_rejects_multiple_current_roadmap_phases():
    payload = _phase_tree()
    payload["planning_context"]["roadmap"][1]["status"] = "current"

    with pytest.raises(ValidationError, match="exactly one current roadmap phase"):
        TaskTree.model_validate(payload)


def test_task_tree_rejects_current_phase_that_does_not_match_roadmap():
    payload = _phase_tree()
    payload["planning_context"]["current_phase"]["objective"] = "不一致的目标"

    with pytest.raises(ValidationError, match="current_phase fields must match roadmap"):
        TaskTree.model_validate(payload)


def test_task_tree_rejects_unsupported_planning_intent_type():
    payload = _phase_tree()
    payload["planning_context"]["intent_type"] = "short_term_delivery"

    with pytest.raises(ValidationError):
        TaskTree.model_validate(payload)


def test_task_tree_accepts_completed_roadmap_without_current_phase():
    payload = _phase_tree()
    payload["planning_context"]["current_phase"] = None
    payload["planning_context"]["next_action_client_node_id"] = None
    for phase in payload["planning_context"]["roadmap"]:
        phase["status"] = "completed"

    parsed = TaskTree.model_validate(payload)

    assert parsed.planning_context is not None
    assert parsed.planning_context.current_phase is None

