import asyncio
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from sqlalchemy.dialects import postgresql
from sqlalchemy.sql import Select

from app.models.task import Task
from app.models.practice import PhaseReview
from app.models.thread import AgentThread
from app.services.thread_repository import (
    AgentThreadRepository,
    ThreadStateConflictError,
    thread_to_snapshot_payload,
)


def test_get_next_phase_commit_receipt_returns_confirmed_tree_and_tasks_atomically():
    user_id = uuid4()
    request_id = str(uuid4())
    thread = _phase_thread(user_id=user_id, thread_id="thread-1")
    thread.task_tree["root"]["client_node_id"] = "phase_02_root"
    thread.task_tree["root"]["title"] = "Phase 2"
    context = thread.task_tree["planning_context"]
    context["roadmap"][0]["status"] = "completed"
    context["roadmap"][1]["status"] = "current"
    context["current_phase"] = {
        "phase_id": "phase_02",
        "title": "Phase 2",
        "objective": "Build",
        "completion_rule": "all_ai_actions_completed",
    }
    thread.interrupt_payload = {
        "type": "phase_generation_state",
        "request_id": request_id,
        "status": "confirmed",
        "base_phase_id": "phase_01",
        "history": {request_id: {"status": "confirmed"}},
    }
    task = _phase_task(user_id=user_id, thread_id="thread-1", status="active")
    task.client_node_id = "phase_02_root"
    task.metadata_ = {"source": "ai", "phase_id": "phase_02", "phase_order": 2}
    session = FakeThreadSession([thread, [task]])
    repository = AgentThreadRepository(session)

    receipt = asyncio.run(
        repository.get_next_phase_commit_receipt(
            user_id=user_id,
            thread_id="thread-1",
            request_id=request_id,
        )
    )

    assert receipt is not None
    assert receipt.status == "confirmed"
    assert receipt.current_phase_id == "phase_02"
    assert receipt.task_tree == thread.task_tree
    assert receipt.tasks == [task]
    assert len(session.select_statements) == 2


def test_get_next_phase_commit_receipt_rejects_confirmed_tree_without_phase_advance():
    user_id = uuid4()
    request_id = str(uuid4())
    thread = _phase_thread(user_id=user_id, thread_id="thread-1")
    thread.interrupt_payload = {
        "type": "phase_generation_state",
        "request_id": request_id,
        "status": "confirmed",
        "base_phase_id": "phase_01",
        "history": {request_id: {"status": "confirmed"}},
    }
    task = _phase_task(user_id=user_id, thread_id="thread-1", status="active")
    task.client_node_id = "phase_01_root"
    session = FakeThreadSession([thread, [task]])
    repository = AgentThreadRepository(session)

    receipt = asyncio.run(
        repository.get_next_phase_commit_receipt(
            user_id=user_id,
            thread_id="thread-1",
            request_id=request_id,
        )
    )

    assert receipt is not None
    assert receipt.status == "incomplete"


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
    assert thread.interrupt_payload["base_phase_id"] == "phase_01"
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


def test_schema_v2_next_phase_requires_finalized_proceed_review():
    user_id = uuid4()
    thread = _phase_thread(user_id=user_id, thread_id="thread-v2")
    _make_thread_schema_v2(thread)
    session = FakeThreadSession([thread, None])
    repository = AgentThreadRepository(session)

    result = asyncio.run(
        repository.start_next_phase_generation(
            user_id=user_id,
            thread_id=thread.thread_id,
            request_id=uuid4(),
        )
    )

    assert result is not None
    assert result.error_code == "PHASE_REVIEW_REQUIRED"
    assert session.commit_count == 0


def test_schema_v2_next_phase_accepts_finalized_proceed_review():
    user_id = uuid4()
    thread = _phase_thread(user_id=user_id, thread_id="thread-v2")
    _make_thread_schema_v2(thread)
    review = PhaseReview(
        id=uuid4(),
        user_id=user_id,
        thread_id=thread.thread_id,
        phase_id="phase_01",
        status="finalized",
        recommendation="ready",
        decision="proceed",
        evidence={},
        statistics={"process_ready": True, "outcome_ready": True},
    )
    session = FakeThreadSession([thread, review])
    repository = AgentThreadRepository(session)

    result = asyncio.run(
        repository.start_next_phase_generation(
            user_id=user_id,
            thread_id=thread.thread_id,
            request_id=uuid4(),
        )
    )

    assert result is not None
    assert result.should_schedule is True
    assert "process_ready" in result.current_phase_task_summary
    assert session.commit_count == 1


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

    should_schedule = asyncio.run(
        repository.mark_confirmation_accepted(thread=thread, request_id=request_id)
    )

    assert should_schedule is True
    assert thread.status == "running"
    assert thread.interrupt_payload["request_id"] == request_id
    assert thread.interrupt_payload["status"] == "confirming"
    assert session.commit_count == 1


def test_mark_confirmation_accepted_marks_initial_refine_as_regenerating() -> None:
    user_id = uuid4()
    thread = _phase_thread(user_id=user_id, thread_id="thread-1")
    thread.status = "awaiting_confirmation"
    thread.current_node = "human_review"
    thread.interrupt_payload = {
        "type": "task_tree_review",
        "task_tree": {"summary": "preview"},
        "allowed_actions": ["approve", "edit", "refine", "reject"],
    }
    session = FakeThreadSession([])
    repository = AgentThreadRepository(session)

    asyncio.run(
        repository.mark_confirmation_accepted(
            thread=thread,
            request_id="req_refine_12345678",
            action="refine",
        )
    )

    assert thread.status == "running"
    assert thread.current_node == "planner"
    assert thread.interrupt_payload["request_id"] == "req_refine_12345678"
    assert thread.interrupt_payload["status"] == "regenerating"
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


def test_mark_confirmation_accepted_retries_same_confirming_next_phase_request():
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

    should_schedule = asyncio.run(
        repository.mark_confirmation_accepted(thread=thread, request_id=request_id)
    )

    assert should_schedule is True
    assert thread.status == "running"
    assert thread.interrupt_payload["status"] == "confirming"
    assert session.commit_count == 0


def test_mark_confirmation_accepted_does_not_reschedule_confirmed_next_phase_request():
    user_id = uuid4()
    request_id = "22222222-2222-2222-2222-222222222222"
    thread = _phase_thread(user_id=user_id, thread_id="thread-1")
    thread.status = "succeeded"
    thread.interrupt_payload = {
        "type": "phase_generation_state",
        "request_id": request_id,
        "status": "confirmed",
        "history": {request_id: {"status": "confirmed"}},
    }
    session = FakeThreadSession([])
    repository = AgentThreadRepository(session)

    should_schedule = asyncio.run(
        repository.mark_confirmation_accepted(thread=thread, request_id=request_id)
    )

    assert should_schedule is False
    assert session.commit_count == 0


@pytest.mark.parametrize(
    ("payload_type", "payload_status", "thread_status", "lease_expires_at"),
    [
        (
            "phase_generation_state",
            "running",
            "running",
            datetime.now(timezone.utc) + timedelta(minutes=5),
        ),
        (
            "phase_generation_state",
            "running",
            "running",
            datetime.now(timezone.utc) - timedelta(seconds=5),
        ),
        (
            "next_phase_review",
            "awaiting_confirmation",
            "awaiting_confirmation",
            datetime.now(timezone.utc) + timedelta(minutes=5),
        ),
    ],
)
def test_cancel_next_phase_request_records_tombstone_for_cancellable_lifecycle(
    payload_type,
    payload_status,
    thread_status,
    lease_expires_at,
):
    user_id = uuid4()
    request_id = "request-a"
    thread = _phase_thread(user_id=user_id, thread_id="thread-1")
    committed_task_tree = thread.task_tree
    thread.status = thread_status
    thread.current_node = (
        "human_review" if payload_type == "next_phase_review" else "next_phase_planner"
    )
    thread.lease_owner = request_id
    thread.lease_expires_at = lease_expires_at
    thread.interrupt_payload = {
        "type": payload_type,
        "request_id": request_id,
        "status": payload_status,
        "history": {
            "request-old": {
                "status": "confirmed",
                "updated_at": "2026-07-01T00:00:00+00:00",
            }
        },
    }
    session = FakeThreadSession([thread])
    repository = AgentThreadRepository(session)

    result = asyncio.run(
        repository.cancel_next_phase_request(
            user_id=user_id,
            thread_id=thread.thread_id,
            request_id=request_id,
        )
    )

    assert result is thread
    assert thread.status == "succeeded"
    assert thread.current_node == "persist_internal_tasks"
    assert thread.task_tree == committed_task_tree
    assert thread.lease_owner is None
    assert thread.lease_expires_at is None
    assert thread.interrupt_payload["type"] == "phase_generation_state"
    assert thread.interrupt_payload["request_id"] == request_id
    assert thread.interrupt_payload["status"] == "cancelled"
    assert thread.interrupt_payload["history"]["request-old"]["status"] == "confirmed"
    assert thread.interrupt_payload["history"][request_id]["status"] == "cancelled"
    assert session.commit_count == 1
    assert "FOR UPDATE" in _compile(session.select_statements[0])


def test_cancel_next_phase_request_is_idempotent_for_same_cancelled_request():
    user_id = uuid4()
    request_id = "request-a"
    thread = _phase_thread(user_id=user_id, thread_id="thread-1")
    thread.status = "succeeded"
    thread.interrupt_payload = {
        "type": "phase_generation_state",
        "request_id": request_id,
        "status": "cancelled",
        "history": {
            request_id: {
                "status": "cancelled",
                "cancelled_at": "2026-07-01T00:00:00+00:00",
            }
        },
    }
    original_payload = thread.interrupt_payload
    session = FakeThreadSession([thread])
    repository = AgentThreadRepository(session)

    result = asyncio.run(
        repository.cancel_next_phase_request(
            user_id=user_id,
            thread_id=thread.thread_id,
            request_id=request_id,
        )
    )

    assert result is thread
    assert thread.interrupt_payload == original_payload
    assert session.commit_count == 0


@pytest.mark.parametrize(
    "payload",
    [
        {
            "type": "phase_generation_state",
            "request_id": "request-a",
            "status": "running",
            "history": {},
        },
        {
            "type": "next_phase_review",
            "request_id": "request-a",
            "status": "confirming",
            "history": {},
        },
        {
            "type": "phase_generation_state",
            "request_id": "request-a",
            "status": "failed",
            "history": {},
        },
    ],
)
def test_cancel_next_phase_request_rejects_mismatch_confirmed_and_failed_states(payload):
    user_id = uuid4()
    thread = _phase_thread(user_id=user_id, thread_id="thread-1")
    thread.interrupt_payload = payload
    committed_task_tree = thread.task_tree
    session = FakeThreadSession([thread])
    repository = AgentThreadRepository(session)
    request_id = (
        "request-b"
        if payload["status"] == "running"
        else payload["request_id"]
    )

    with pytest.raises(ThreadStateConflictError):
        asyncio.run(
            repository.cancel_next_phase_request(
                user_id=user_id,
                thread_id=thread.thread_id,
                request_id=request_id,
            )
        )

    assert thread.interrupt_payload == payload
    assert thread.task_tree == committed_task_tree
    assert session.commit_count == 0


def test_thread_snapshot_payload_reports_cancelled_phase_preview() -> None:
    user_id = uuid4()
    thread = _phase_thread(user_id=user_id, thread_id="thread-1")
    thread.status = "succeeded"
    thread.interrupt_payload = {
        "type": "phase_generation_state",
        "request_id": "req-cancelled",
        "status": "cancelled",
        "history": {"req-cancelled": {"status": "cancelled"}},
    }

    payload = thread_to_snapshot_payload(thread)

    assert payload["status"] == "cancelled"


def test_thread_snapshot_payload_reports_failed_generation_state() -> None:
    user_id = uuid4()
    thread = _phase_thread(user_id=user_id, thread_id="thread-1")
    thread.status = "succeeded"
    thread.error_code = "NEXT_PHASE_RUN_FAILED"
    thread.interrupt_payload = {
        "type": "phase_generation_state",
        "request_id": "req-failed",
        "status": "failed",
        "history": {"req-failed": {"status": "failed"}},
    }

    payload = thread_to_snapshot_payload(thread)

    assert payload["status"] == "failed"


def test_thread_snapshot_payload_reports_stalled_generation_when_lease_expires() -> None:
    user_id = uuid4()
    thread = _phase_thread(user_id=user_id, thread_id="thread-1")
    thread.status = "running"
    thread.current_node = "next_phase_planner"
    thread.lease_owner = "req-stalled"
    thread.lease_expires_at = datetime.now(timezone.utc) - timedelta(seconds=5)
    thread.interrupt_payload = {
        "type": "phase_generation_state",
        "request_id": "req-stalled",
        "status": "running",
        "history": {},
    }

    payload = thread_to_snapshot_payload(thread)

    assert payload["status"] == "stalled"


def test_thread_snapshot_payload_retains_strategy_context_without_migration() -> None:
    thread = _phase_thread(user_id=uuid4(), thread_id="thread-strategy")
    thread.task_tree["strategy_context"] = {
        "schema_version": 1,
        "strategy_type": "delivery",
        "deliverable": {
            "title": "Report",
            "format": "Document",
            "quality_bar": ["Reviewable"],
        },
        "deadline": {"text": "No explicit deadline", "is_explicit": False},
        "time_plan": {
            "available_minutes": None,
            "planned_minutes": 30,
            "buffer_minutes": 0,
        },
        "scope": {"must_have": ["Finding"], "should_have": [], "can_cut": []},
        "workstreams": [
            {
                "workstream_id": "report",
                "title": "Report",
                "output": "Reviewable report",
                "task_client_node_ids": ["phase_01_root"],
            }
        ],
        "critical_path_client_node_ids": ["phase_01_root"],
    }

    payload = thread_to_snapshot_payload(thread)

    assert payload["task_tree"]["strategy_context"] == thread.task_tree["strategy_context"]


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


def _make_thread_schema_v2(thread: AgentThread) -> None:
    context = thread.task_tree["planning_context"]
    context["schema_version"] = 2
    context["current_phase"]["completion_rule"] = "long_term_execution_gate"
    context["current_phase"]["estimated_duration_weeks"] = 4
    context["practice_loops"] = []
    context["outcome_checkpoints"] = [
        {
            "checkpoint_id": "artifact",
            "title": "Submit one artifact",
            "evidence_type": "artifact",
            "operator": "exists",
        }
    ]
    context["phase_gate"] = {
        "process_threshold": 0.8,
        "outcome_rule": "all_required",
    }


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
