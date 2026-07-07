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


def _long_term_v2_tree() -> dict:
    payload = _phase_tree()
    context = payload["planning_context"]
    context["schema_version"] = 2
    context["current_phase"]["completion_rule"] = "long_term_execution_gate"
    context["current_phase"]["estimated_duration_weeks"] = 4
    context["practice_loops"] = [
        {
            "loop_id": "n3_vocab",
            "title": "完成一次 N3 词汇练习",
            "target_per_week": 3,
            "duration_weeks": 4,
            "done_criteria": "完成 20 道题并记录错词",
        }
    ]
    context["outcome_checkpoints"] = [
        {
            "checkpoint_id": "vocab_test",
            "title": "完成词汇测试",
            "evidence_type": "numeric",
            "unit": "percent",
            "operator": "gte",
            "target_value": 65,
        }
    ]
    context["phase_gate"] = {
        "process_threshold": 0.8,
        "outcome_rule": "all_required",
    }
    return payload


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


def test_task_tree_accepts_long_term_execution_schema_v2():
    parsed = TaskTree.model_validate(_long_term_v2_tree())

    assert parsed.planning_context.schema_version == 2
    assert parsed.planning_context.practice_loops[0].target_per_week == 3
    assert parsed.planning_context.outcome_checkpoints[0].target_value == 65


def test_schema_v1_remains_valid_without_execution_fields():
    parsed = TaskTree.model_validate(_phase_tree())

    assert parsed.planning_context.schema_version == 1
    assert parsed.planning_context.practice_loops == []


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("target_per_week", 0),
        ("target_per_week", 8),
        ("duration_weeks", 0),
        ("duration_weeks", 13),
    ],
)
def test_schema_v2_rejects_loop_bounds(field: str, value: int):
    payload = _long_term_v2_tree()
    payload["planning_context"]["practice_loops"][0][field] = value

    with pytest.raises(ValidationError):
        TaskTree.model_validate(payload)


def test_schema_v2_rejects_more_than_two_loops():
    payload = _long_term_v2_tree()
    payload["planning_context"]["practice_loops"] *= 3

    with pytest.raises(ValidationError):
        TaskTree.model_validate(payload)


def test_schema_v2_rejects_more_than_two_checkpoints():
    payload = _long_term_v2_tree()
    payload["planning_context"]["outcome_checkpoints"] *= 3

    with pytest.raises(ValidationError):
        TaskTree.model_validate(payload)


def test_schema_v2_rejects_exploration_decision():
    payload = _long_term_v2_tree()
    payload["planning_context"]["intent_type"] = "exploration_decision"

    with pytest.raises(ValidationError, match="only valid for long_term_growth"):
        TaskTree.model_validate(payload)


def test_schema_v1_rejects_v2_only_fields():
    payload = _long_term_v2_tree()
    payload["planning_context"]["schema_version"] = 1

    with pytest.raises(ValidationError, match="schema version 1"):
        TaskTree.model_validate(payload)


def test_task_response_exposes_practice_loop_metadata():
    from uuid import uuid4

    from app.api.schemas import TaskResponse

    loop_id = uuid4()
    task = {
        "id": uuid4(),
        "user_id": uuid4(),
        "thread_id": "thread-practice",
        "parent_task_id": None,
        "client_node_id": "practice-occurrence",
        "title": "Practice vocabulary",
        "description": None,
        "node_type": "action",
        "status": "active",
        "view_bucket": "planned",
        "is_in_my_day": True,
        "estimated_minutes": None,
        "sort_order": 1,
        "metadata_": {
            "source": "practice_loop",
            "practice_loop_id": str(loop_id),
        },
    }

    response = TaskResponse.model_validate(task)

    assert response.source == "practice_loop"
    assert response.practice_loop_id == loop_id
