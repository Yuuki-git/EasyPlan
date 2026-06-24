import asyncio
from datetime import datetime, timedelta, timezone
from uuid import uuid4

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
