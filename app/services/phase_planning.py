from dataclasses import dataclass
import os
from typing import Any, Mapping, Sequence
from uuid import UUID

from app.api.schemas import PlanningContext


@dataclass(frozen=True)
class PhaseProgress:
    total_ai_actions: int
    completed_ai_actions: int
    is_complete: bool


def phase_planning_enabled() -> bool:
    value = os.getenv("EASYPLAN_PHASE_PLANNING_ENABLED", "true").strip().lower()
    return value not in {"0", "false", "no", "off"}


def is_ai_phase_action(task: Any, phase_id: str) -> bool:
    metadata = getattr(task, "metadata_", None)
    return (
        getattr(task, "ai_generated", False) is True
        and getattr(task, "node_type", None) == "action"
        and isinstance(metadata, dict)
        and metadata.get("source") == "ai"
        and metadata.get("phase_id") == phase_id
    )


def calculate_phase_progress(tasks: Sequence[Any], phase_id: str) -> PhaseProgress:
    actions = [task for task in tasks if is_ai_phase_action(task, phase_id)]
    completed = sum(task.status == "completed" for task in actions)
    return PhaseProgress(
        total_ai_actions=len(actions),
        completed_ai_actions=completed,
        is_complete=bool(actions) and completed == len(actions),
    )


def choose_next_action(
    tasks: Sequence[Any],
    dependencies_by_task_id: Mapping[UUID, set[UUID]],
    phase_id: str,
) -> Any | None:
    status_by_id = {task.id: task.status for task in tasks}
    candidates = [
        task
        for task in tasks
        if is_ai_phase_action(task, phase_id) and task.status == "active"
    ]
    ready = [
        task
        for task in candidates
        if all(
            status_by_id.get(dependency_id) == "completed"
            for dependency_id in dependencies_by_task_id.get(task.id, set())
        )
    ]
    return min(
        ready,
        key=lambda task: (task.sort_order, task.created_at, str(task.id)),
        default=None,
    )


def validate_next_phase_transition(
    committed: PlanningContext,
    proposed: PlanningContext,
) -> list[str]:
    errors: list[str] = []
    if proposed.intent_type != committed.intent_type:
        errors.append("intent_type must remain unchanged")
    if proposed.time_horizon != committed.time_horizon:
        errors.append("time_horizon must remain unchanged")

    proposed_by_id = {phase.phase_id: phase for phase in proposed.roadmap}
    for phase in committed.roadmap:
        if phase.status != "completed":
            continue
        proposed_phase = proposed_by_id.get(phase.phase_id)
        if proposed_phase is None or proposed_phase.model_dump() != phase.model_dump():
            errors.append(f"completed phase {phase.phase_id} must remain unchanged")

    proposed_current = [phase for phase in proposed.roadmap if phase.status == "current"]
    if proposed.current_phase is None or len(proposed_current) != 1:
        errors.append("proposed roadmap must contain exactly one current phase")
        return errors

    if committed.current_phase is None:
        errors.append("completed roadmap cannot generate another phase")
        return errors

    if proposed.current_phase.phase_id == committed.current_phase.phase_id:
        errors.append("next phase must advance to a newly current phase")

    committed_current = next(
        (
            phase
            for phase in committed.roadmap
            if phase.phase_id == committed.current_phase.phase_id
        ),
        None,
    )
    proposed_previous = proposed_by_id.get(committed.current_phase.phase_id)
    if committed_current is None or proposed_previous is None:
        errors.append("previous current phase must remain in the roadmap")
    else:
        expected_previous = committed_current.model_copy(update={"status": "completed"})
        if proposed_previous.model_dump() != expected_previous.model_dump():
            errors.append("previous current phase must become completed without other changes")

    return errors


def complete_final_phase(context: PlanningContext) -> PlanningContext:
    completed = context.model_copy(deep=True)
    if completed.current_phase is None:
        return completed

    for phase in completed.roadmap:
        if phase.phase_id == completed.current_phase.phase_id:
            phase.status = "completed"
            break
    completed.current_phase = None
    completed.next_action_client_node_id = None
    return PlanningContext.model_validate(completed.model_dump(mode="json"))
