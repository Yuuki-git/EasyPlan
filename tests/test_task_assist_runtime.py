import asyncio
import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4

from app.models.task import Task
from app.models.task_assist import TaskAssistRun
from app.services.task_assist_runtime import TaskAssistRuntime


class FakeResult:
    def __init__(self, *, scalar=None, scalars=None):
        self.scalar = scalar
        self.scalar_values = list(scalars or [])

    def scalar_one_or_none(self):
        return self.scalar

    def scalars(self):
        return self

    def all(self):
        return self.scalar_values


class FakeSession:
    def __init__(self, results):
        self.results = list(results)
        self.commits = 0

    async def execute(self, _query):
        return self.results.pop(0)

    async def commit(self):
        self.commits += 1

    async def refresh(self, _value, **_kwargs):
        return None


class SessionContext:
    def __init__(self, session):
        self.session = session

    async def __aenter__(self):
        return self.session

    async def __aexit__(self, *_args):
        return None


class FakeProposalClient:
    def __init__(self, *, delay=0):
        self.delay = delay
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        if not delay:
            self.release.set()

    async def create_task_assist_proposal(self, *, mode, prompt):
        self.started.set()
        if self.delay:
            try:
                await asyncio.wait_for(self.release.wait(), timeout=self.delay)
            except asyncio.TimeoutError:
                pass
        return {
            "schema_version": 1,
            "proposal_type": "start",
            "summary": "先完成一个小步骤",
            "starter_step": {
                "draft_id": "starter",
                "title": "列出三个市场数据来源",
                "description": None,
                "estimated_minutes": 5,
                "done_criteria": "记录三个可核验来源",
                "start_hint": "打开现有调研笔记",
                "fallback_action": None,
            },
        }


class LeaseRenewingRuntime(TaskAssistRuntime):
    async def _renew_durable_lease(self, **_kwargs):
        return True


def _models():
    now = datetime.now(timezone.utc)
    user_id = uuid4()
    task = Task(
        id=uuid4(),
        user_id=user_id,
        thread_id="thread-1",
        parent_task_id=None,
        client_node_id="task-1",
        title="写市场分析",
        description=None,
        node_type="action",
        status="active",
        view_bucket="planned",
        is_in_my_day=False,
        estimated_minutes=30,
        sort_order=0,
        ai_generated=True,
        user_edited=False,
        metadata_={"source": "ai"},
        created_at=now,
        updated_at=now,
    )
    run = TaskAssistRun(
        id=uuid4(),
        user_id=user_id,
        task_id=task.id,
        thread_id=task.thread_id,
        request_id=uuid4(),
        mode="start",
        user_context=None,
        status="running",
        stage="queued",
        target_task_updated_at=now,
        proposal=None,
        apply_receipt=None,
        error_code=None,
        error_message=None,
        created_at=now,
        updated_at=now,
        expires_at=now + timedelta(hours=24),
        applied_at=None,
    )
    thread = SimpleNamespace(
        intent_text="完成商业计划书",
        task_tree={"summary": "完成初稿"},
    )
    return user_id, task, run, thread


def _event_type(event):
    return next(line[7:] for line in event.splitlines() if line.startswith("event: "))


def _event_id(event):
    return next(line[4:] for line in event.splitlines() if line.startswith("id: "))


def _event_data(event):
    line = next(line[6:] for line in event.splitlines() if line.startswith("data: "))
    return json.loads(line)


async def _collect(runtime, run, last_event_id=None):
    events = []
    async for event in runtime.stream(
        thread_id=run.thread_id,
        request_id=run.request_id,
        last_event_id=last_event_id,
    ):
        events.append(event)
    return events


def test_runtime_emits_isolated_sequence_and_terminal_envelope():
    async def scenario():
        user_id, task, run, thread = _models()
        session = FakeSession(
            [
                FakeResult(scalar=run),
                FakeResult(scalar=task),
                FakeResult(scalar=thread),
                FakeResult(scalars=[]),
            ]
        )
        runtime = LeaseRenewingRuntime(
            session_factory=lambda: SessionContext(session),
            proposal_client_factory=FakeProposalClient,
            heartbeat_interval_seconds=60,
        )
        await runtime.run(
            user_id=user_id,
            task_id=task.id,
            thread_id=task.thread_id,
            request_id=run.request_id,
        )
        events = await _collect(runtime, run)
        assert [_event_type(event) for event in events] == [
            "run_started",
            "task_context_ready",
            "assist_generation_started",
            "assist_validation_started",
            "assist_ready",
            "done",
        ]
        assert all(_event_data(event)["run_type"] == "task_assist" for event in events)
        assert all(_event_data(event)["request_id"] == str(run.request_id) for event in events)
        assert run.status == "ready"

    asyncio.run(scenario())


def test_runtime_heartbeat_and_cancel_block_late_ready_persistence():
    async def scenario():
        user_id, task, run, thread = _models()
        client = FakeProposalClient(delay=5)
        session = FakeSession(
            [
                FakeResult(scalar=run),
                FakeResult(scalar=task),
                FakeResult(scalar=thread),
                FakeResult(scalars=[]),
            ]
        )
        runtime = LeaseRenewingRuntime(
            session_factory=lambda: SessionContext(session),
            proposal_client_factory=lambda: client,
            heartbeat_interval_seconds=0.01,
        )
        running = asyncio.create_task(
            runtime.run(
                user_id=user_id,
                task_id=task.id,
                thread_id=task.thread_id,
                request_id=run.request_id,
            )
        )
        await client.started.wait()
        await asyncio.sleep(0.025)
        await runtime.cancel(thread_id=run.thread_id, request_id=run.request_id)
        client.release.set()
        await running
        events = list(runtime._events[next(iter(runtime._events))])
        types = [_event_type(event) for event in events]
        assert "still_running" in types
        assert "assist_ready" not in types
        assert types[-1] == "done"
        assert _event_data(events[-1])["payload"]["status"] == "cancelled"

    asyncio.run(scenario())


def test_runtime_replays_only_events_after_last_event_id():
    async def scenario():
        user_id, task, run, thread = _models()
        session = FakeSession(
            [
                FakeResult(scalar=run),
                FakeResult(scalar=task),
                FakeResult(scalar=thread),
                FakeResult(scalars=[]),
            ]
        )
        runtime = TaskAssistRuntime(
            session_factory=lambda: SessionContext(session),
            proposal_client_factory=FakeProposalClient,
            heartbeat_interval_seconds=60,
        )
        await runtime.run(
            user_id=user_id,
            task_id=task.id,
            thread_id=task.thread_id,
            request_id=run.request_id,
        )
        history = list(runtime._events[next(iter(runtime._events))])
        replay = await _collect(runtime, run, last_event_id=_event_id(history[2]))
        assert replay == history[3:]

    asyncio.run(scenario())


def test_runtime_restores_ready_snapshot_when_memory_buffer_is_missing():
    _user_id, _task, run, _thread = _models()
    run.status = "ready"
    run.stage = "ready"
    run.proposal = {"proposal_type": "start"}
    runtime = TaskAssistRuntime()
    runtime.restore_from_snapshot(run)
    events = list(runtime._events[next(iter(runtime._events))])
    assert [_event_type(event) for event in events] == ["assist_ready", "done"]


def test_durable_stream_turns_expired_running_snapshot_into_interrupted_error():
    async def scenario():
        user_id, task, run, _thread = _models()
        run.status = "running"
        run.stage = "generating"
        run.lease_owner = "dead-worker"
        run.lease_expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        session = FakeSession([FakeResult(scalar=run)])
        runtime = TaskAssistRuntime(session_factory=lambda: SessionContext(session))
        runtime.restore_from_snapshot(run)
        events = []
        async for event in runtime.stream(
            thread_id=run.thread_id,
            request_id=run.request_id,
            user_id=user_id,
            task_id=task.id,
            durable_poll_interval_seconds=0.001,
        ):
            events.append(event)
        assert [_event_type(event) for event in events][-1] == "agent_error"
        assert _event_data(events[-1])["payload"]["code"] == "TASK_ASSIST_INTERRUPTED"
        assert run.status == "failed"

    asyncio.run(scenario())


def test_repeated_cancel_is_idempotent_and_terminal_buffers_are_bounded():
    async def scenario():
        runtime = TaskAssistRuntime(max_retained_terminal_runs=2)
        request_id = uuid4()
        await runtime.cancel(thread_id="thread-1", request_id=request_id)
        await runtime.cancel(thread_id="thread-1", request_id=request_id)
        first_key = next(iter(runtime._events))
        assert len(runtime._events[first_key]) == 1

        await runtime.cancel(thread_id="thread-2", request_id=uuid4())
        await runtime.cancel(thread_id="thread-3", request_id=uuid4())
        assert len(runtime._events) == 2

    asyncio.run(scenario())
