from dataclasses import dataclass, field
from datetime import datetime, timezone
from uuid import UUID, uuid4

from app.api.schemas import PlanningContext
from app.services.phase_planning import (
    calculate_phase_progress,
    choose_next_action,
    complete_final_phase,
    long_term_execution_enabled,
    uses_long_term_execution,
    validate_next_phase_transition,
)


@dataclass
class PhaseTask:
    id: UUID
    client_node_id: str
    node_type: str = "action"
    status: str = "active"
    sort_order: int = 0
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    ai_generated: bool = True
    metadata_: dict = field(default_factory=lambda: {"source": "ai", "phase_id": "phase_01"})


def make_task(
    client_node_id: str,
    *,
    status: str = "active",
    sort_order: int = 0,
    ai_generated: bool = True,
    source: str = "ai",
    phase_id: str = "phase_01",
    node_type: str = "action",
) -> PhaseTask:
    return PhaseTask(
        id=uuid4(),
        client_node_id=client_node_id,
        node_type=node_type,
        status=status,
        sort_order=sort_order,
        ai_generated=ai_generated,
        metadata_={"source": source, "phase_id": phase_id},
    )


def test_manual_tasks_do_not_block_phase_completion():
    tasks = [
        make_task("ai-action", status="completed"),
        make_task("manual", ai_generated=False, source="manual", status="active"),
    ]

    progress = calculate_phase_progress(tasks, "phase_01")

    assert progress.total_ai_actions == 1
    assert progress.completed_ai_actions == 1
    assert progress.is_complete is True


def test_task_assist_children_do_not_double_count_phase_progress():
    tasks = [
        make_task("parent-ai-action", status="active"),
        make_task(
            "assist-child",
            source="task_assist",
            status="completed",
        ),
    ]

    progress = calculate_phase_progress(tasks, "phase_01")

    assert progress.total_ai_actions == 1
    assert progress.completed_ai_actions == 0
    assert progress.is_complete is False


def test_phase_without_ai_actions_is_not_complete():
    progress = calculate_phase_progress([], "phase_01")

    assert progress.total_ai_actions == 0
    assert progress.completed_ai_actions == 0
    assert progress.is_complete is False


def test_long_term_execution_flag_defaults_on_and_can_be_disabled(monkeypatch):
    monkeypatch.delenv("EASYPLAN_LONG_TERM_EXECUTION_ENABLED", raising=False)
    assert long_term_execution_enabled() is True

    monkeypatch.setenv("EASYPLAN_LONG_TERM_EXECUTION_ENABLED", "false")
    assert long_term_execution_enabled() is False


def test_schema_v1_does_not_use_long_term_execution():
    assert uses_long_term_execution(_planning_context()) is False


def test_phase_progress_requires_ai_action_source_and_matching_phase():
    tasks = [
        make_task("group", node_type="group", status="completed"),
        make_task("wrong-source", source="manual", status="completed"),
        make_task("wrong-phase", phase_id="phase_02", status="completed"),
        make_task("valid", status="completed"),
    ]

    progress = calculate_phase_progress(tasks, "phase_01")

    assert progress.total_ai_actions == 1
    assert progress.completed_ai_actions == 1


def test_choose_next_action_skips_unmet_dependency():
    first = make_task("first", sort_order=1)
    second = make_task("second", sort_order=0)
    dependencies = {second.id: {first.id}}

    assert choose_next_action([first, second], dependencies, "phase_01") is first
    first.status = "completed"
    assert choose_next_action([first, second], dependencies, "phase_01") is second


def test_choose_next_action_uses_stable_ordering():
    later = make_task("later", sort_order=2)
    earlier = make_task("earlier", sort_order=1)

    selected = choose_next_action([later, earlier], {}, "phase_01")

    assert selected is earlier


def test_validate_next_phase_transition_rejects_completed_phase_mutation():
    committed = _planning_context()
    committed.roadmap[0].status = "completed"
    committed.roadmap[1].status = "current"
    committed.current_phase = committed.current_phase.model_copy(
        update={
            "phase_id": "phase_02",
            "title": "基础",
            "objective": "建立基础",
        }
    )
    proposed = committed.model_copy(deep=True)
    proposed.roadmap[0].objective = "偷偷修改已完成阶段"

    errors = validate_next_phase_transition(committed, proposed)

    assert any("completed phase" in error for error in errors)


def test_validate_next_phase_transition_locks_intent_and_horizon():
    committed = _planning_context()
    proposed = _next_phase_context()
    proposed.time_horizon = "weeks"

    errors = validate_next_phase_transition(committed, proposed)

    assert any("time_horizon" in error for error in errors)


def test_validate_next_phase_transition_locks_schema_version():
    base = _planning_context()
    committed = base.model_copy(
        update={
            "schema_version": 2,
            "current_phase": base.current_phase.model_copy(
                update={
                    "completion_rule": "long_term_execution_gate",
                    "estimated_duration_weeks": 4,
                }
            ),
            "practice_loops": [
                {
                    "loop_id": "practice",
                    "title": "完成一次练习",
                    "target_per_week": 3,
                    "duration_weeks": 4,
                    "done_criteria": "完成并记录结果",
                }
            ],
            "outcome_checkpoints": [
                {
                    "checkpoint_id": "confidence",
                    "title": "完成自评",
                    "evidence_type": "self_assessment",
                    "operator": "gte",
                    "target_value": 3,
                }
            ],
            "phase_gate": {
                "process_threshold": 0.8,
                "outcome_rule": "all_required",
            },
        }
    )

    errors = validate_next_phase_transition(committed, _next_phase_context())

    assert any("schema_version" in error for error in errors)


def test_validate_next_phase_transition_accepts_next_current_phase():
    errors = validate_next_phase_transition(_planning_context(), _next_phase_context())

    assert errors == []


def test_complete_final_phase_returns_copy_without_mutating_input():
    original = _planning_context()
    original.roadmap[0].status = "completed"
    original.roadmap[1].status = "completed"
    original.roadmap[2].status = "current"
    original.current_phase = original.current_phase.model_copy(
        update={
            "phase_id": "phase_03",
            "title": "强化",
            "objective": "完成强化",
        }
    )

    completed = complete_final_phase(original)

    assert original.current_phase is not None
    assert original.roadmap[2].status == "current"
    assert completed.current_phase is None
    assert completed.next_action_client_node_id is None
    assert all(phase.status == "completed" for phase in completed.roadmap)


def _planning_context() -> PlanningContext:
    return PlanningContext.model_validate(
        {
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
        }
    )


def _next_phase_context() -> PlanningContext:
    context = _planning_context().model_copy(deep=True)
    context.roadmap[0].status = "completed"
    context.roadmap[1].status = "current"
    context.current_phase = context.current_phase.model_copy(
        update={
            "phase_id": "phase_02",
            "title": "基础",
            "objective": "建立基础",
        }
    )
    context.next_action_client_node_id = "phase_02_action_01"
    return PlanningContext.model_validate(context.model_dump(mode="json"))
