from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas import TaskTree
from app.models.task import Task
from app.models.thread import AgentThread
from app.services.phase_planning import calculate_phase_progress


@dataclass(frozen=True)
class PhaseGenerationStart:
    thread: AgentThread
    status: str
    should_schedule: bool
    current_phase_task_summary: str = ""
    error_code: str | None = None
    error_message: str | None = None
    remaining_ai_actions: int | None = None


class ThreadStateConflictError(RuntimeError):
    def __init__(self, *, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class AgentThreadRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create_thread(
        self,
        *,
        user_id: UUID,
        thread_id: str,
        intent_text: str,
        selected_provider: str,
    ) -> AgentThread:
        thread = AgentThread(
            user_id=user_id,
            thread_id=thread_id,
            intent_text=intent_text,
            status="running",
            current_node="planner",
            next_nodes=[],
            interrupt_payload=None,
            latest_checkpoint_id=None,
            task_tree=None,
            error_code=None,
            error_message=None,
            expires_at=None,
            interrupted_at=None,
            completed_at=None,
        )
        self.session.add(thread)
        await self.session.commit()
        await self.session.refresh(thread)
        return thread

    async def get_thread_for_user(
        self,
        *,
        user_id: UUID,
        thread_id: str,
    ) -> AgentThread | None:
        result = await self.session.execute(
            select(AgentThread).where(
                AgentThread.user_id == user_id,
                AgentThread.thread_id == thread_id,
            )
        )
        return result.scalar_one_or_none()

    async def mark_confirmation_accepted(self, *, thread: AgentThread, request_id: str) -> None:
        payload = thread.interrupt_payload if isinstance(thread.interrupt_payload, dict) else None
        if payload and payload.get("type") == "next_phase_review":
            expected_request_id = str(payload.get("request_id") or "")
            if expected_request_id != request_id:
                raise ThreadStateConflictError(
                    code="REQUEST_ID_MISMATCH",
                    message="Next-phase preview request_id does not match the current pending preview",
                )
            if payload.get("status") != "awaiting_confirmation":
                raise ThreadStateConflictError(
                    code="PREVIEW_ALREADY_CONFIRMED",
                    message="This next-phase preview has already been confirmed or cancelled",
                )
            thread.interrupt_payload = {
                **payload,
                "status": "confirming",
            }
        thread.status = "running"
        thread.updated_at = datetime.now(timezone.utc)
        try:
            await self.session.commit()
        except Exception:
            await self.session.rollback()
            raise

    async def cancel_pending_preview(self, *, thread: AgentThread) -> AgentThread:
        payload = thread.interrupt_payload if isinstance(thread.interrupt_payload, dict) else None
        if not payload or payload.get("type") != "next_phase_review":
            raise ThreadStateConflictError(
                code="NO_PENDING_PREVIEW",
                message="Thread has no pending preview to cancel",
            )
        if payload.get("status") != "awaiting_confirmation":
            raise ThreadStateConflictError(
                code="PREVIEW_ALREADY_CONFIRMED",
                message="This next-phase preview has already been confirmed or cancelled",
            )

        now = datetime.now(timezone.utc)
        thread.status = "succeeded"
        thread.current_node = "persist_internal_tasks"
        thread.interrupt_payload = _cancelled_phase_envelope(
            payload,
            request_id=str(payload.get("request_id") or ""),
            now=now,
        )
        thread.lease_owner = None
        thread.lease_expires_at = None
        thread.updated_at = now
        try:
            await self.session.commit()
        except Exception:
            await self.session.rollback()
            raise
        return thread

    async def start_next_phase_generation(
        self,
        *,
        user_id: UUID,
        thread_id: str,
        request_id: UUID,
        lease_seconds: int = 300,
    ) -> PhaseGenerationStart | None:
        now = datetime.now(timezone.utc)
        result = await self.session.execute(
            select(AgentThread)
            .where(
                AgentThread.user_id == user_id,
                AgentThread.thread_id == thread_id,
            )
            .with_for_update()
        )
        thread = result.scalar_one_or_none()
        if thread is None:
            return None

        request_id_text = str(request_id)
        envelope = thread.interrupt_payload if isinstance(thread.interrupt_payload, dict) else {}
        history = dict(envelope.get("history") or {})
        if envelope.get("request_id") == request_id_text and envelope.get("status") in {
            "running",
            "awaiting_confirmation",
        }:
            return PhaseGenerationStart(
                thread=thread,
                status=str(envelope["status"]),
                should_schedule=False,
            )
        history_entry = history.get(request_id_text)
        if isinstance(history_entry, dict) and history_entry.get("status") == "confirmed":
            return PhaseGenerationStart(
                thread=thread,
                status=str(history_entry["status"]),
                should_schedule=False,
            )
        if isinstance(history_entry, dict) and history_entry.get("status") == "cancelled":
            return _phase_generation_conflict(
                thread,
                code="REQUEST_CANCELLED",
                message="This next-phase request was cancelled. Generate a new request_id before trying again",
            )

        if (
            thread.lease_owner
            and thread.lease_owner != request_id_text
            and thread.lease_expires_at is not None
            and _lease_is_active(thread.lease_expires_at, now)
        ):
            return _phase_generation_conflict(
                thread,
                code="PHASE_GENERATION_IN_PROGRESS",
                message="Another next-phase request is already running",
            )

        try:
            tree = TaskTree.model_validate(thread.task_tree)
        except Exception:
            return _phase_generation_conflict(
                thread,
                code="PHASE_UNSUPPORTED",
                message="Thread does not contain a valid phase planning context",
            )
        context = tree.planning_context
        if context is None:
            return _phase_generation_conflict(
                thread,
                code="PHASE_UNSUPPORTED",
                message="Thread does not support phase planning",
            )
        if context.current_phase is None:
            return _phase_generation_conflict(
                thread,
                code="GOAL_COMPLETED",
                message="All roadmap phases are already completed",
            )

        task_result = await self.session.execute(
            select(Task).where(
                Task.user_id == user_id,
                Task.thread_id == thread_id,
            )
        )
        tasks = list(task_result.scalars().all())
        progress = calculate_phase_progress(tasks, context.current_phase.phase_id)
        if progress.total_ai_actions == 0:
            return _phase_generation_conflict(
                thread,
                code="PHASE_DATA_INVALID",
                message="Current phase contains no identifiable AI actions",
            )
        if not progress.is_complete:
            return _phase_generation_conflict(
                thread,
                code="PHASE_INCOMPLETE",
                message="Current phase must be completed before unlocking the next phase",
                remaining_ai_actions=(
                    progress.total_ai_actions - progress.completed_ai_actions
                ),
            )

        thread.status = "running"
        thread.current_node = "next_phase_planner"
        thread.lease_owner = request_id_text
        thread.lease_expires_at = now + timedelta(seconds=lease_seconds)
        thread.interrupt_payload = {
            "type": "phase_generation_state",
            "request_id": request_id_text,
            "status": "running",
            "history": history,
        }
        thread.updated_at = now
        try:
            await self.session.commit()
        except Exception:
            await self.session.rollback()
            raise
        return PhaseGenerationStart(
            thread=thread,
            status="running",
            should_schedule=True,
            current_phase_task_summary=(
                f"{progress.completed_ai_actions}/{progress.total_ai_actions} "
                "AI actions completed"
            ),
        )

    async def delete_thread_for_user(self, *, user_id: UUID, thread_id: str) -> bool:
        thread = await self.get_thread_for_user(user_id=user_id, thread_id=thread_id)
        if thread is None:
            return False
        try:
            await self.session.execute(
                delete(Task).where(
                    Task.user_id == user_id,
                    Task.thread_id == thread_id,
                )
            )
            await self.session.execute(
                delete(AgentThread).where(
                    AgentThread.user_id == user_id,
                    AgentThread.thread_id == thread_id,
                )
            )
            await self.session.commit()
        except Exception:
            await self.session.rollback()
            raise
        return True


def thread_to_snapshot_payload(thread: AgentThread) -> dict[str, Any]:
    return {
        "thread_id": thread.thread_id,
        "status": thread.status,
        "state_version": 0,
        "last_event_id": None,
        "server_time": datetime.now(timezone.utc),
        "intent_text": thread.intent_text,
        "task_tree": thread.task_tree,
        "interrupt_payload": thread.interrupt_payload,
        "latest_checkpoint_id": thread.latest_checkpoint_id,
    }


def _phase_generation_conflict(
    thread: AgentThread,
    *,
    code: str,
    message: str,
    remaining_ai_actions: int | None = None,
) -> PhaseGenerationStart:
    return PhaseGenerationStart(
        thread=thread,
        status="conflict",
        should_schedule=False,
        error_code=code,
        error_message=message,
        remaining_ai_actions=remaining_ai_actions,
    )


def _lease_is_active(expires_at: datetime, now: datetime) -> bool:
    normalized_expires_at = (
        expires_at.replace(tzinfo=timezone.utc)
        if expires_at.tzinfo is None
        else expires_at.astimezone(timezone.utc)
    )
    return normalized_expires_at > now


def _cancelled_phase_envelope(
    payload: dict[str, Any],
    *,
    request_id: str,
    now: datetime,
) -> dict[str, Any]:
    history = dict(payload.get("history") or {})
    history[request_id] = {
        "status": "cancelled",
        "cancelled_at": now.isoformat(),
    }
    return {
        "type": "phase_generation_state",
        "request_id": request_id,
        "status": "cancelled",
        "history": history,
    }
