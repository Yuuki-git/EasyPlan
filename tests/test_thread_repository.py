import asyncio
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from sqlalchemy.dialects import postgresql
from sqlalchemy.sql import Select

from app.models.task import Task
from app.models.thread import AgentThread
from app.services.thread_repository import AgentThreadRepository


def test_start_next_phase_generation_locks_thread_and_acquires_lease():
    user_id = uuid4()
    thread = _phase_thread(user_id=user_id, thread_id="thread-1")
    task = _phase_task(user_id=user_id, thread_id="thread-1", status="completed")
    session = FakeThreadSession([thread, [task]])
    repository = AgentThreadRepository(session)
    request_id = uuid4()

    result = asyncio.run(
        repository.start_next_phase_generation(
            user_id=user_id,
            thread_id="thread-1",
            request_id=request_id,
        )
    )

    assert result is not None
    assert result.should_schedule is True
    assert result.status == "running"
    assert result.current_phase_task_summary == "1/1 AI actions completed"
    assert thread.lease_owner == str(request_id)
    assert session.commit_count == 1
    assert "FOR UPDATE" in _compile(session.select_statements[0])


def test_start_next_phase_generation_replays_same_request_without_task_query():
    user_id = uuid4()
    request_id = uuid4()
    thread = _phase_thread(user_id=user_id, thread_id="thread-1")
    thread.interrupt_payload = {
        "type": "phase_generation_state",
        "request_id": str(request_id),
        "status": "running",
        "history": {},
    }
    session = FakeThreadSession([thread])
    repository = AgentThreadRepository(session)

    result = asyncio.run(
        repository.start_next_phase_generation(
            user_id=user_id,
            thread_id="thread-1",
            request_id=request_id,
        )
    )

    assert result is not None
    assert result.should_schedule is False
    assert result.status == "running"
    assert len(session.select_statements) == 1
    assert session.commit_count == 0


def test_start_next_phase_generation_reports_remaining_ai_actions():
    user_id = uuid4()
    thread = _phase_thread(user_id=user_id, thread_id="thread-1")
    active = _phase_task(user_id=user_id, thread_id="thread-1", status="active")
    session = FakeThreadSession([thread, [active]])
    repository = AgentThreadRepository(session)

    result = asyncio.run(
        repository.start_next_phase_generation(
            user_id=user_id,
            thread_id="thread-1",
            request_id=uuid4(),
        )
    )

    assert result is not None
    assert result.error_code == "PHASE_INCOMPLETE"
    assert result.remaining_ai_actions == 1
    assert session.commit_count == 0


def test_start_next_phase_generation_handles_naive_active_lease_datetime():
    user_id = uuid4()
    thread = _phase_thread(user_id=user_id, thread_id="thread-1")
    thread.lease_owner = str(uuid4())
    thread.lease_expires_at = datetime.now() + timedelta(minutes=5)
    session = FakeThreadSession([thread])
    repository = AgentThreadRepository(session)

    result = asyncio.run(
        repository.start_next_phase_generation(
            user_id=user_id,
            thread_id="thread-1",
            request_id=uuid4(),
        )
    )

    assert result is not None
    assert result.error_code == "PHASE_GENERATION_IN_PROGRESS"


def test_start_next_phase_generation_rejects_cancelled_request_id_with_tombstone():
    user_id = uuid4()
    request_id = uuid4()
    thread = _phase_thread(user_id=user_id, thread_id="thread-1")
    thread.interrupt_payload = {
        "type": "phase_generation_state",
        "request_id": str(request_id),
        "status": "cancelled",
        "history": {
            str(request_id): {
                "status": "cancelled",
                "cancelled_at": "2026-06-26T00:00:00+00:00",
            }
        },
    }
    session = FakeThreadSession([thread])
    repository = AgentThreadRepository(session)

    result = asyncio.run(
        repository.start_next_phase_generation(
            user_id=user_id,
            thread_id="thread-1",
            request_id=request_id,
        )
    )

    assert result is not None
    assert result.should_schedule is False
    assert result.error_code == "REQUEST_CANCELLED"
    assert session.commit_count == 0
    assert len(session.select_statements) == 1


def test_start_next_phase_generation_allows_new_request_after_cancelled_tombstone():
    user_id = uuid4()
    cancelled_request_id = uuid4()
    new_request_id = uuid4()
    thread = _phase_thread(user_id=user_id, thread_id="thread-1")
    completed = _phase_task(user_id=user_id, thread_id="thread-1", status="completed")
    thread.interrupt_payload = {
        "type": "phase_generation_state",
        "request_id": str(cancelled_request_id),
        "status": "cancelled",
        "history": {
            str(cancelled_request_id): {
                "status": "cancelled",
                "cancelled_at": "2026-06-26T00:00:00+00:00",
            }
        },
    }
    session = FakeThreadSession([thread, [completed]])
    repository = AgentThreadRepository(session)

    result = asyncio.run(
        repository.start_next_phase_generation(
            user_id=user_id,
            thread_id="thread-1",
            request_id=new_request_id,
        )
    )

    assert result is not None
    assert result.should_schedule is True
    assert result.status == "running"
    assert thread.lease_owner == str(new_request_id)
    assert thread.interrupt_payload["history"][str(cancelled_request_id)]["status"] == "cancelled"
    assert session.commit_count == 1


def test_mark_confirmation_accepted_binds_next_phase_request_id_and_marks_confirming():
    user_id = uuid4()
    request_id = "22222222-2222-2222-2222-222222222222"
    thread = _phase_thread(user_id=user_id, thread_id="thread-1")
    thread.status = "awaiting_confirmation"
    thread.current_node = "human_review"
    thread.interrupt_payload = {
        "type": "next_phase_review",
        "request_id": request_id,
        "status": "awaiting_confirmation",
        "task_tree": {"summary": "preview"},
        "history": {},
    }
    session = FakeThreadSession([])
    repository = AgentThreadRepository(session)

    asyncio.run(repository.mark_confirmation_accepted(thread=thread, request_id=request_id))

    assert thread.status == "running"
    assert thread.interrupt_payload["request_id"] == request_id
    assert thread.interrupt_payload["status"] == "confirming"
    assert session.commit_count == 1


def test_mark_confirmation_accepted_rejects_next_phase_request_id_mismatch():
    user_id = uuid4()
    thread = _phase_thread(user_id=user_id, thread_id="thread-1")
    thread.status = "awaiting_confirmation"
    thread.interrupt_payload = {
        "type": "next_phase_review",
        "request_id": "22222222-2222-2222-2222-222222222222",
        "status": "awaiting_confirmation",
        "task_tree": {"summary": "preview"},
        "history": {},
    }
    session = FakeThreadSession([])
    repository = AgentThreadRepository(session)

    with pytest.raises(RuntimeError, match="request_id"):
        asyncio.run(
            repository.mark_confirmation_accepted(
                thread=thread,
                request_id="33333333-3333-3333-3333-333333333333",
            )
        )

    assert thread.status == "awaiting_confirmation"
    assert thread.interrupt_payload["status"] == "awaiting_confirmation"
    assert session.commit_count == 0


def test_mark_confirmation_accepted_rejects_duplicate_next_phase_confirmation():
    user_id = uuid4()
    request_id = "22222222-2222-2222-2222-222222222222"
    thread = _phase_thread(user_id=user_id, thread_id="thread-1")
    thread.status = "running"
    thread.interrupt_payload = {
        "type": "next_phase_review",
        "request_id": request_id,
        "status": "confirming",
        "task_tree": {"summary": "preview"},
        "history": {},
    }
    session = FakeThreadSession([])
    repository = AgentThreadRepository(session)

    with pytest.raises(RuntimeError, match="already"):
        asyncio.run(repository.mark_confirmation_accepted(thread=thread, request_id=request_id))

    assert thread.status == "running"
    assert thread.interrupt_payload["status"] == "confirming"
    assert session.commit_count == 0


def test_cancel_pending_preview_records_cancelled_tombstone_and_restores_thread():
    user_id = uuid4()
    request_id = "22222222-2222-2222-2222-222222222222"
    thread = _phase_thread(user_id=user_id, thread_id="thread-1")
    thread.status = "awaiting_confirmation"
    thread.current_node = "human_review"
    thread.lease_owner = request_id
    thread.lease_expires_at = datetime.now(timezone.utc) + timedelta(minutes=5)
    thread.interrupt_payload = {
        "type": "next_phase_review",
        "request_id": request_id,
        "status": "awaiting_confirmation",
        "task_tree": {"summary": "preview"},
        "history": {
            "11111111-1111-1111-1111-111111111111": {
                "status": "confirmed",
                "updated_at": "2026-06-25T00:00:00+00:00",
            }
        },
    }
    committed_task_tree = thread.task_tree
    session = FakeThreadSession([])
    repository = AgentThreadRepository(session)

    result = asyncio.run(repository.cancel_pending_preview(thread=thread))

    assert result is thread
    assert thread.status == "succeeded"
    assert thread.current_node == "persist_internal_tasks"
    assert thread.interrupt_payload["type"] == "phase_generation_state"
    assert thread.interrupt_payload["request_id"] == request_id
    assert thread.interrupt_payload["status"] == "cancelled"
    assert thread.interrupt_payload["history"]["11111111-1111-1111-1111-111111111111"]["status"] == "confirmed"
    assert thread.interrupt_payload["history"][request_id]["status"] == "cancelled"
    assert "cancelled_at" in thread.interrupt_payload["history"][request_id]
    assert thread.lease_owner is None
    assert thread.lease_expires_at is None
    assert thread.task_tree == committed_task_tree
    assert session.commit_count == 1


def test_cancel_pending_preview_rejects_when_no_pending_preview_exists():
    user_id = uuid4()
    thread = _phase_thread(user_id=user_id, thread_id="thread-1")
    committed_task_tree = thread.task_tree
    session = FakeThreadSession([])
    repository = AgentThreadRepository(session)

    with pytest.raises(RuntimeError, match="pending preview"):
        asyncio.run(repository.cancel_pending_preview(thread=thread))

    assert thread.task_tree == committed_task_tree
    assert session.commit_count == 0


class FakeThreadSession:
    def __init__(self, scalar_results: list) -> None:
        self.scalar_results = list(scalar_results)
        self.select_statements = []
        self.commit_count = 0
        self.rollback_count = 0

    async def execute(self, statement):
        assert isinstance(statement, Select)
        self.select_statements.append(statement)
        return FakeScalarResult(self.scalar_results.pop(0))

    async def commit(self):
        self.commit_count += 1

    async def rollback(self):
        self.rollback_count += 1


class FakeScalarResult:
    def __init__(self, value) -> None:
        self.value = value

    def scalar_one_or_none(self):
        return self.value

    def scalars(self):
        return self

    def all(self):
        return self.value if isinstance(self.value, list) else []


def _phase_thread(*, user_id, thread_id: str) -> AgentThread:
    return AgentThread(
        user_id=user_id,
        thread_id=thread_id,
        intent_text="Long-term goal",
        status="succeeded",
        current_node="persist_internal_tasks",
        next_nodes=[],
        interrupt_payload=None,
        latest_checkpoint_id=None,
        task_tree={
            "root": {
                "client_node_id": "phase_01_root",
                "title": "Phase 1",
                "verb": "Start",
                "estimated_minutes": 5,
                "node_type": "group",
                "children": [],
            },
            "summary": "Phase 1",
            "assumptions": [],
            "planning_context": {
                "schema_version": 1,
                "intent_type": "long_term_growth",
                "time_horizon": "months",
                "roadmap": [
                    {"phase_id": "phase_01", "order": 1, "title": "Phase 1", "objective": "Start", "status": "current"},
                    {"phase_id": "phase_02", "order": 2, "title": "Phase 2", "objective": "Build", "status": "planned"},
                    {"phase_id": "phase_03", "order": 3, "title": "Phase 3", "objective": "Finish", "status": "planned"},
                ],
                "current_phase": {
                    "phase_id": "phase_01",
                    "title": "Phase 1",
                    "objective": "Start",
                    "completion_rule": "all_ai_actions_completed",
                },
                "next_action_client_node_id": None,
            },
        },
        error_code=None,
        error_message=None,
        expires_at=None,
        interrupted_at=None,
        completed_at=None,
    )


def _phase_task(*, user_id, thread_id: str, status: str) -> Task:
    return Task(
        id=uuid4(),
        user_id=user_id,
        thread_id=thread_id,
        parent_task_id=None,
        client_node_id="phase_01_action_01",
        title="Complete current action",
        description=None,
        node_type="action",
        status=status,
        view_bucket="planned",
        is_in_my_day=False,
        estimated_minutes=5,
        sort_order=0,
        ai_generated=True,
        user_edited=False,
        metadata_={"source": "ai", "phase_id": "phase_01", "phase_order": 1},
    )


def _compile(statement) -> str:
    return str(statement.compile(dialect=postgresql.dialect()))
