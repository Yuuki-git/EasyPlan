from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import json
from typing import Any
from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas import TaskTree
from app.models.practice import PhaseReview
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


@dataclass(frozen=True)
class NextPhaseCommitReceiptState:
    thread_id: str
    request_id: str
    status: str
    current_phase_id: str | None
    task_tree: dict[str, Any] | None
    tasks: list[Task]


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

    async def get_next_phase_commit_receipt(
        self,
        *,
        user_id: UUID,
        thread_id: str,
        request_id: str,
    ) -> NextPhaseCommitReceiptState | None:
        thread = await self.get_thread_for_user(user_id=user_id, thread_id=thread_id)
        if thread is None:
            return None

        payload = thread.interrupt_payload if isinstance(thread.interrupt_payload, dict) else {}
        is_current_request = str(payload.get("request_id") or "") == request_id
        lifecycle_status = str(payload.get("status") or "unknown") if is_current_request else "unknown"
        base_phase_id = payload.get("base_phase_id")
        current_phase_id: str | None = None
        parsed_tree: TaskTree | None = None
        try:
            parsed_tree = TaskTree.model_validate(thread.task_tree)
            if parsed_tree.planning_context and parsed_tree.planning_context.current_phase:
                current_phase_id = parsed_tree.planning_context.current_phase.phase_id
        except Exception:
            parsed_tree = None

        tasks: list[Task] = []
        if lifecycle_status == "confirmed":
            task_result = await self.session.execute(
                select(Task)
                .where(
                    Task.user_id == user_id,
                    Task.view_bucket == "planned",
                )
                .order_by(Task.sort_order.asc(), Task.created_at.asc())
            )
            tasks = list(task_result.scalars().all())

            expected_client_node_ids = _task_tree_client_node_ids(parsed_tree)
            persisted_client_node_ids = {
                task.client_node_id
                for task in tasks
                if task.thread_id == thread_id
            }
            if (
                current_phase_id is None
                or (
                    isinstance(base_phase_id, str)
                    and current_phase_id == base_phase_id
                )
                or not expected_client_node_ids
                or not expected_client_node_ids.issubset(persisted_client_node_ids)
            ):
                lifecycle_status = "incomplete"

        if lifecycle_status not in {
            "confirmed",
            "incomplete",
            "running",
            "awaiting_confirmation",
            "confirming",
            "cancelled",
            "failed",
        }:
            lifecycle_status = "unknown"

        return NextPhaseCommitReceiptState(
            thread_id=thread_id,
            request_id=request_id,
            status=lifecycle_status,
            current_phase_id=current_phase_id,
            task_tree=thread.task_tree,
            tasks=tasks,
        )

    async def mark_confirmation_accepted(
        self,
        *,
        thread: AgentThread,
        request_id: str,
        action: str | None = None,
    ) -> bool:
        payload = thread.interrupt_payload if isinstance(thread.interrupt_payload, dict) else None
        if payload and payload.get("type") == "next_phase_review":
            expected_request_id = str(payload.get("request_id") or "")
            if expected_request_id != request_id:
                raise ThreadStateConflictError(
                    code="REQUEST_ID_MISMATCH",
                    message="Next-phase preview request_id does not match the current pending preview",
                )
            if payload.get("status") == "confirming":
                return True
            if payload.get("status") != "awaiting_confirmation":
                raise ThreadStateConflictError(
                    code="PREVIEW_ALREADY_CONFIRMED",
                    message="This next-phase preview has already been confirmed or cancelled",
                )
            thread.interrupt_payload = {
                **payload,
                "status": "confirming",
            }
            thread.current_node = "next_phase_planner"
        elif payload and payload.get("type") == "phase_generation_state":
            expected_request_id = str(payload.get("request_id") or "")
            if expected_request_id == request_id and payload.get("status") == "confirmed":
                return False
            raise ThreadStateConflictError(
                code="PREVIEW_ALREADY_CONFIRMED",
                message="This next-phase preview has already been confirmed, failed, or cancelled",
            )
        elif payload and payload.get("type") == "task_tree_review":
            next_payload = {
                **payload,
                "request_id": request_id,
            }
            if action == "refine":
                next_payload["status"] = "regenerating"
                thread.current_node = "planner"
            elif action == "edit":
                next_payload["status"] = "editing"
                thread.current_node = "validator"
            elif action == "approve":
                next_payload["status"] = "confirming"
                thread.current_node = "persist_internal_tasks"
            elif action == "reject":
                next_payload["status"] = "cancelled"
            thread.interrupt_payload = next_payload
        thread.status = "running"
        thread.updated_at = datetime.now(timezone.utc)
        try:
            await self.session.commit()
        except Exception:
            await self.session.rollback()
            raise
        return True

    async def cancel_next_phase_request(
        self,
        *,
        user_id: UUID,
        thread_id: str,
        request_id: str,
    ) -> AgentThread | None:
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

        payload = (
            thread.interrupt_payload
            if isinstance(thread.interrupt_payload, dict)
            else {}
        )
        current_request_id = str(payload.get("request_id") or "")
        if current_request_id != request_id:
            raise ThreadStateConflictError(
                code="REQUEST_ID_MISMATCH",
                message="Next-phase request_id does not match the current lifecycle",
            )

        payload_type = payload.get("type")
        payload_status = payload.get("status")
        if payload_type == "phase_generation_state" and payload_status == "cancelled":
            return thread
        cancellable = (
            payload_type == "phase_generation_state"
            and payload_status == "running"
        ) or (
            payload_type == "next_phase_review"
            and payload_status == "awaiting_confirmation"
        )
        if not cancellable:
            if payload_status in {"confirming", "confirmed"}:
                code = "PREVIEW_ALREADY_CONFIRMED"
                message = "This next-phase request has already been confirmed"
            else:
                code = "NO_CANCELLABLE_PHASE"
                message = "Thread has no cancellable next-phase lifecycle for this request"
            raise ThreadStateConflictError(code=code, message=message)

        now = datetime.now(timezone.utc)
        thread.status = "succeeded"
        thread.current_node = "persist_internal_tasks"
        thread.interrupt_payload = _cancelled_phase_envelope(
            payload,
            request_id=request_id,
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

        if context.schema_version == 2:
            review = await self._latest_finalized_phase_review(
                user_id=user_id,
                thread_id=thread_id,
                phase_id=context.current_phase.phase_id,
            )
            if review is None or review.decision not in {"proceed", "override"}:
                return _phase_generation_conflict(
                    thread,
                    code="PHASE_REVIEW_REQUIRED",
                    message=(
                        "Finalize the current phase review before generating "
                        "the next phase"
                    ),
                )
            current_phase_task_summary = json.dumps(
                {
                    "decision": review.decision,
                    "recommendation": review.recommendation,
                    **dict(review.statistics or {}),
                    "difficulty": review.difficulty,
                    "next_capacity": review.next_capacity,
                },
                ensure_ascii=False,
                separators=(",", ":"),
            )
        else:
            task_result = await self.session.execute(
                select(Task).where(
                    Task.user_id == user_id,
                    Task.thread_id == thread_id,
                )
            )
            tasks = list(task_result.scalars().all())
            progress = calculate_phase_progress(
                tasks,
                context.current_phase.phase_id,
            )
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
                    message=(
                        "Current phase must be completed before unlocking "
                        "the next phase"
                    ),
                    remaining_ai_actions=(
                        progress.total_ai_actions
                        - progress.completed_ai_actions
                    ),
                )
            current_phase_task_summary = (
                f"{progress.completed_ai_actions}/{progress.total_ai_actions} "
                "AI actions completed"
            )

        thread.status = "running"
        thread.current_node = "next_phase_planner"
        thread.lease_owner = request_id_text
        thread.lease_expires_at = now + timedelta(seconds=lease_seconds)
        thread.interrupt_payload = {
            "type": "phase_generation_state",
            "request_id": request_id_text,
            "status": "running",
            "base_phase_id": context.current_phase.phase_id,
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
            current_phase_task_summary=current_phase_task_summary,
        )

    async def _latest_finalized_phase_review(
        self,
        *,
        user_id: UUID,
        thread_id: str,
        phase_id: str,
    ) -> PhaseReview | None:
        result = await self.session.execute(
            select(PhaseReview)
            .where(
                PhaseReview.user_id == user_id,
                PhaseReview.thread_id == thread_id,
                PhaseReview.phase_id == phase_id,
                PhaseReview.status == "finalized",
            )
            .order_by(PhaseReview.updated_at.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

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
        "status": _snapshot_status(thread),
        "state_version": 0,
        "last_event_id": None,
        "server_time": datetime.now(timezone.utc),
        "intent_text": thread.intent_text,
        "task_tree": thread.task_tree,
        "interrupt_payload": thread.interrupt_payload,
        "latest_checkpoint_id": thread.latest_checkpoint_id,
    }


def _task_tree_client_node_ids(task_tree: TaskTree | None) -> set[str]:
    if task_tree is None:
        return set()

    node_ids: set[str] = set()

    def visit(node: Any) -> None:
        node_ids.add(node.client_node_id)
        for child in node.children:
            visit(child)

    visit(task_tree.root)
    return node_ids


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


def _snapshot_status(thread: AgentThread) -> str:
    payload = thread.interrupt_payload if isinstance(thread.interrupt_payload, dict) else {}
    payload_status = payload.get("status")
    if isinstance(payload_status, str) and payload_status == "cancelled":
        return "cancelled"
    if isinstance(payload_status, str) and payload_status == "failed":
        return "failed"
    if thread.error_code:
        return "failed"
    if thread.status == "awaiting_confirmation":
        return "awaiting_confirmation"
    if _is_stalled_thread(thread):
        return "stalled"
    return thread.status


def _is_stalled_thread(thread: AgentThread) -> bool:
    payload = thread.interrupt_payload if isinstance(thread.interrupt_payload, dict) else {}
    if thread.status != "running":
        return False
    if payload.get("type") != "phase_generation_state" or payload.get("status") != "running":
        return False
    if thread.lease_expires_at is None:
        return False
    return not _lease_is_active(thread.lease_expires_at, datetime.now(timezone.utc))


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
