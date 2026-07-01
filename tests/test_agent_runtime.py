import asyncio
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy.sql import Select, Update

from app.services.agent_runtime import (
    INITIAL_RUN_REQUEST_ID,
    AgentRuntime,
    EventRunKey,
    SAFE_PLANNING_ERROR_MESSAGE,
)


class AsyncStreamGraph:
    def __init__(self) -> None:
        self.inputs: list[Any] = []

    def stream(self, input_value, config):
        raise AssertionError("AgentRuntime must use graph.astream")

    async def astream(self, input_value, config):
        self.inputs.append((input_value, config))
        yield {"planner": {"reasoning_events": [{"code": "PLAN_STARTED", "message": "planning"}]}}
        yield {"__interrupt__": [type("Interrupt", (), {"value": {"task_tree": {"root": {}}}})()]}


class CompleteGraph:
    def stream(self, input_value, config):
        raise AssertionError("AgentRuntime must use graph.astream")

    async def astream(self, input_value, config):
        yield {"planner": {"reasoning_events": [{"code": "PLAN_STARTED", "message": "planning"}]}}


class FailingAsyncGraph:
    def stream(self, input_value, config):
        raise AssertionError("AgentRuntime must use graph.astream")

    async def astream(self, input_value, config):
        if False:
            yield {}
        raise ValueError(
            "1 validation error for TaskTree\n"
            "root.children.0.estimated_minutes\n"
            "Input should be less than 5"
        )


class NextPhaseInterruptGraph:
    def __init__(self, request_id: str) -> None:
        self.request_id = request_id
        self.inputs: list[Any] = []

    async def astream(self, input_value, config):
        self.inputs.append((input_value, config))
        yield {
            "__interrupt__": [
                type(
                    "Interrupt",
                    (),
                    {
                        "value": {
                            "task_tree": {"summary": "proposed phase"},
                            "planning_mode": "next_phase",
                            "phase_request_id": self.request_id,
                        }
                    },
                )()
            ]
        }


class ValidationFailureGraph:
    async def astream(self, input_value, config):
        yield {
            "failed_validation": {
                "error": {
                    "code": "TASK_TREE_VALIDATION_FAILED",
                    "message": "planning_context time_horizon must match IntentProfile",
                }
            }
        }


def test_agent_runtime_runs_langgraph_astream_in_background_worker(monkeypatch):
    graph = AsyncStreamGraph()
    runtime = AgentRuntime(graph_factory=lambda **_: graph)
    _patch_async_session(monkeypatch)

    asyncio.run(
        runtime.run_new_thread(
            user_id="11111111-1111-1111-1111-111111111111",
            thread_id="thread-1",
            intent_text="write paper",
            selected_provider="native",
            planner_provider="openai",
            planner_model=None,
        )
    )

    events = asyncio.run(
        _collect_until(
            runtime.stream_thread_events(
                user_id="user-1",
                thread_id="thread-1",
            ),
            lambda event: "event: plan_ready" in event,
        )
    )

    assert graph.inputs[0][0]["user_id"] == "11111111-1111-1111-1111-111111111111"
    assert graph.inputs[0][0]["thread_id"] == "thread-1"
    assert graph.inputs[0][0]["planning_mode"] == "initial"
    assert "event: reasoning" in "".join(events)
    assert "event: plan_ready" in "".join(events)


def test_agent_runtime_reuses_one_checkpointer_across_initial_run_and_resume():
    checkpointers = []

    def graph_factory(*, planner, checkpointer):
        checkpointers.append(checkpointer)
        return AsyncStreamGraph()

    runtime = AgentRuntime(graph_factory=graph_factory)

    asyncio.run(
        runtime.run_new_thread(
            user_id="user-1",
            thread_id="thread-1",
            intent_text="write paper",
            selected_provider="native",
            planner_provider="openai",
            planner_model=None,
        )
    )
    asyncio.run(
        runtime.resume_thread(
            user_id="user-1",
            thread_id="thread-1",
            decision={"action": "refine", "feedback": "make it smaller"},
        )
    )

    assert len(checkpointers) == 2
    assert checkpointers[0] is checkpointers[1]


def test_agent_runtime_builds_planner_from_requested_provider_and_model():
    created_planners = []
    graph_planners = []

    def planner_client_factory(*, provider, model):
        planner = object()
        created_planners.append({"provider": provider, "model": model, "planner": planner})
        return planner

    def graph_factory(*, planner, checkpointer):
        graph_planners.append(planner)
        return AsyncStreamGraph()

    runtime = AgentRuntime(
        graph_factory=graph_factory,
        planner_client_factory=planner_client_factory,
    )

    asyncio.run(
        runtime.run_new_thread(
            user_id="user-1",
            thread_id="thread-1",
            intent_text="write paper",
            selected_provider="native",
            planner_provider="deepseek",
            planner_model="deepseek-reasoner",
        )
    )

    assert created_planners[0]["provider"] == "deepseek"
    assert created_planners[0]["model"] == "deepseek-reasoner"
    assert graph_planners[0] is created_planners[0]["planner"]


def test_agent_runtime_defers_missing_planner_provider_to_factory_default():
    created_planners = []

    def planner_client_factory(*, provider, model):
        created_planners.append({"provider": provider, "model": model})
        return object()

    runtime = AgentRuntime(
        graph_factory=lambda **_: CompleteGraph(),
        planner_client_factory=planner_client_factory,
    )

    asyncio.run(
        runtime.run_new_thread(
            user_id="user-1",
            thread_id="thread-1",
            intent_text="write paper",
            selected_provider="native",
        )
    )

    assert created_planners[0] == {"provider": None, "model": None}


def test_agent_runtime_resume_without_cached_selection_uses_factory_default():
    created_planners = []

    def planner_client_factory(*, provider, model):
        created_planners.append({"provider": provider, "model": model})
        return object()

    runtime = AgentRuntime(
        graph_factory=lambda **_: CompleteGraph(),
        planner_client_factory=planner_client_factory,
    )

    asyncio.run(
        runtime.resume_thread(
            user_id="user-1",
            thread_id="thread-1",
            decision={"action": "approve"},
        )
    )

    assert created_planners[0] == {"provider": None, "model": None}


def test_next_phase_runtime_uses_deepseek_and_preserves_committed_tree(monkeypatch):
    request_id = "22222222-2222-2222-2222-222222222222"
    graph = NextPhaseInterruptGraph(request_id)
    created_planners = []

    def planner_client_factory(*, provider, model):
        created_planners.append({"provider": provider, "model": model})
        return object()

    committed_tree = {"summary": "committed phase"}
    session = _patch_async_session(
        monkeypatch,
        thread=SimpleNamespace(
            interrupt_payload={
                "type": "phase_generation_state",
                "request_id": request_id,
                "status": "running",
                "history": {},
            }
        ),
    )
    runtime = AgentRuntime(
        graph_factory=lambda **_: graph,
        planner_client_factory=planner_client_factory,
    )

    asyncio.run(
        runtime.run_next_phase(
            user_id="11111111-1111-1111-1111-111111111111",
            thread_id="thread-1",
            request_id=request_id,
            intent_text="学习日语 N3",
            committed_task_tree=committed_tree,
            current_phase_task_summary="1/1 AI actions completed",
        )
    )

    assert created_planners == [{"provider": "deepseek", "model": None}]
    assert graph.inputs[0][0]["planning_mode"] == "next_phase"
    assert graph.inputs[0][0]["committed_task_tree"] == committed_tree
    update_statement = next(statement for statement in session.statements if isinstance(statement, Update))
    update_values = list(update_statement.compile().params.values())
    assert committed_tree not in update_values
    envelope = next(
        value
        for value in update_values
        if isinstance(value, dict) and value.get("type") == "next_phase_review"
    )
    assert envelope["status"] == "awaiting_confirmation"
    assert envelope["task_tree"] == {"summary": "proposed phase"}


def test_next_phase_runtime_failure_releases_lease_without_overwriting_tree(monkeypatch):
    request_id = "22222222-2222-2222-2222-222222222222"
    session = _patch_async_session(
        monkeypatch,
        thread=SimpleNamespace(
            interrupt_payload={
                "type": "phase_generation_state",
                "request_id": request_id,
                "status": "running",
                "history": {},
            }
        ),
    )
    runtime = AgentRuntime(
        graph_factory=lambda **_: FailingAsyncGraph(),
        planner_client_factory=lambda **_: object(),
    )

    asyncio.run(
        runtime.run_next_phase(
            user_id="11111111-1111-1111-1111-111111111111",
            thread_id="thread-1",
            request_id=request_id,
            intent_text="学习日语 N3",
            committed_task_tree={"summary": "committed phase"},
            current_phase_task_summary="1/1 AI actions completed",
        )
    )

    update_statement = next(statement for statement in session.statements if isinstance(statement, Update))
    params = update_statement.compile().params
    assert "succeeded" in params.values()
    assert request_id in params.values() or any(
        isinstance(value, dict) and value.get("request_id") == request_id
        for value in params.values()
    )
    assert not any(
        isinstance(value, dict) and value.get("summary") == "committed phase"
        for value in params.values()
    )
    event = asyncio.run(
        _next_event(
            runtime.stream_thread_events(
                user_id="user-1",
                thread_id="thread-1",
                run_type="next_phase",
                request_id=request_id,
            )
        )
    )
    assert "event: agent_error" in event
    assert '"thread_id":"thread-1"' in event
    assert '"run_type":"next_phase"' in event
    assert f'"request_id":"{request_id}"' in event


def test_agent_runtime_stream_keeps_connection_open_for_new_events_until_done():
    runtime = AgentRuntime(graph_factory=lambda **_: AsyncStreamGraph())
    runtime._append_event("thread-1", "reasoning", {"message": "first"})

    async def collect_live_events():
        stream = runtime.stream_thread_events(user_id="user-1", thread_id="thread-1")
        iterator = stream.__aiter__()
        first = await iterator.__anext__()
        pending_next = asyncio.create_task(iterator.__anext__())
        await asyncio.sleep(0)
        assert not pending_next.done()

        runtime._append_event("thread-1", "reasoning", {"message": "second"})
        second = await asyncio.wait_for(pending_next, timeout=1)

        pending_done = asyncio.create_task(iterator.__anext__())
        await asyncio.sleep(0)
        assert not pending_done.done()
        runtime._append_event("thread-1", "done", {"status": "completed"})
        done = await asyncio.wait_for(pending_done, timeout=1)

        with pytest.raises(StopAsyncIteration):
            await asyncio.wait_for(iterator.__anext__(), timeout=1)
        return first, second, done

    first, second, done = asyncio.run(collect_live_events())

    assert "first" in first
    assert "second" in second
    assert "event: done" in done


def test_agent_runtime_stream_closes_on_agent_error_event():
    runtime = AgentRuntime(graph_factory=lambda **_: AsyncStreamGraph())

    async def collect_live_error_event():
        stream = runtime.stream_thread_events(user_id="user-1", thread_id="thread-1")
        iterator = stream.__aiter__()
        pending_error = asyncio.create_task(iterator.__anext__())
        await asyncio.sleep(0)
        assert not pending_error.done()

        runtime._append_error("thread-1", code="AGENT_RUN_FAILED", message="friendly failure")
        event = await asyncio.wait_for(pending_error, timeout=1)

        with pytest.raises(StopAsyncIteration):
            await asyncio.wait_for(iterator.__anext__(), timeout=1)
        return event

    event = asyncio.run(collect_live_error_event())

    assert "event: agent_error" in event
    assert "event: error" not in event
    assert "AGENT_RUN_FAILED" in event


class FakeAsyncSession:
    def __init__(self, *, thread=None) -> None:
        self.thread = thread
        self.statements = []
        self.commits = 0
        self.rollbacks = 0

    async def execute(self, statement):
        self.statements.append(statement)
        if isinstance(statement, Select):
            return FakeScalarResult(self.thread)

    async def commit(self):
        self.commits += 1

    async def rollback(self):
        self.rollbacks += 1


class FakeScalarResult:
    def __init__(self, value) -> None:
        self.value = value

    def scalar_one_or_none(self):
        return self.value


class FakeAsyncSessionContext:
    def __init__(self, session: FakeAsyncSession) -> None:
        self.session = session

    async def __aenter__(self):
        return self.session

    async def __aexit__(self, exc_type, exc, tb):
        return False


def _patch_async_session(monkeypatch, *, thread=None):
    session = FakeAsyncSession(thread=thread)

    def fake_async_session():
        return FakeAsyncSessionContext(session)

    import app.db.session as db_session

    monkeypatch.setattr(db_session, "async_session", fake_async_session, raising=False)
    return session


def test_agent_runtime_persists_interrupt_to_agent_thread(monkeypatch):
    session = _patch_async_session(monkeypatch)
    runtime = AgentRuntime(graph_factory=lambda **_: AsyncStreamGraph())

    asyncio.run(
        runtime.run_new_thread(
            user_id="11111111-1111-1111-1111-111111111111",
            thread_id="thread-1",
            intent_text="write paper",
            selected_provider="native",
        )
    )

    assert session.commits == 1
    compiled = session.statements[0].compile().params
    assert "awaiting_confirmation" in compiled.values()
    assert {"root": {}} in compiled.values()


def test_agent_runtime_streams_only_events_after_last_event_id():
    runtime = AgentRuntime(graph_factory=lambda **_: AsyncStreamGraph())
    runtime._append_event("thread-1", "reasoning", {"message": "first"})
    runtime._append_event("thread-1", "reasoning", {"message": "second"})
    runtime._append_event("thread-1", "reasoning", {"message": "third"})

    event = asyncio.run(
        _next_event(
            runtime.stream_thread_events(
                user_id="user-1",
                thread_id="thread-1",
                last_event_id="evt_00000002",
            )
        )
    )

    assert "third" in event
    assert "first" not in event
    assert "second" not in event


def test_new_next_phase_stream_excludes_historical_terminal_event_from_previous_request():
    runtime = AgentRuntime(graph_factory=lambda **_: AsyncStreamGraph())
    runtime._append_done(
        "thread-1",
        status="completed",
        run_type="next_phase",
        request_id="request-a",
    )
    runtime._append_event(
        "thread-1",
        "plan_ready",
        {"task_tree": {"root": {}}},
        run_type="next_phase",
        request_id="request-b",
    )
    runtime._append_done(
        "thread-1",
        status="completed",
        run_type="next_phase",
        request_id="request-b",
    )

    events = asyncio.run(
        _collect_events(
            runtime.stream_thread_events(
                user_id="user-1",
                thread_id="thread-1",
                run_type="next_phase",
                request_id="request-b",
            )
        )
    )

    payload = "\n".join(events)
    assert '"thread_id":"thread-1"' in payload
    assert '"run_type":"next_phase"' in payload
    assert '"request_id":"request-a"' not in payload
    assert '"request_id":"request-b"' in payload
    assert "event: plan_ready" in payload
    assert payload.count("event: done") == 1


def test_next_phase_stream_rejects_cursor_from_a_different_request():
    runtime = AgentRuntime(graph_factory=lambda **_: AsyncStreamGraph())
    runtime._append_event(
        "thread-1",
        "reasoning",
        {"message": "request A"},
        run_type="next_phase",
        request_id="request-a",
    )
    runtime._append_event(
        "thread-1",
        "reasoning",
        {"message": "request B"},
        run_type="next_phase",
        request_id="request-b",
    )

    event = asyncio.run(
        _next_event(
            runtime.stream_thread_events(
                user_id="user-1",
                thread_id="thread-1",
                run_type="next_phase",
                request_id="request-b",
                last_event_id="evt_00000001",
            )
        )
    )

    assert "event: snapshot_required" in event
    assert '"request_id":"request-b"' in event
    assert "request A" not in event


def test_agent_runtime_sanitizes_internal_graph_errors_in_sse(caplog):
    runtime = AgentRuntime(graph_factory=lambda **_: FailingAsyncGraph())

    asyncio.run(
        runtime.run_new_thread(
            user_id="user-1",
            thread_id="thread-1",
            intent_text="write paper",
            selected_provider="native",
        )
    )

    event = asyncio.run(
        _next_event(
            runtime.stream_thread_events(
                user_id="user-1",
                thread_id="thread-1",
            )
        )
    )

    assert "event: agent_error" in event
    assert "event: error" not in event
    assert "AI 在规划时遇到了一点小麻烦，正在尝试重新组织，请稍候。" in event
    assert "validation error" not in event.lower()
    assert "estimated_minutes" not in event


def test_runtime_sanitizes_internal_phase_contract_errors_before_sse_emit():
    runtime = AgentRuntime(
        graph_factory=lambda **_: ValidationFailureGraph(),
        planner_client_factory=lambda **_: object(),
    )

    asyncio.run(
        runtime.run_new_thread(
            user_id="00000000-0000-0000-0000-000000000001",
            thread_id="thread_contract_error",
            intent_text="我是否要考虑转行产品经理",
            selected_provider="native",
            planner_provider="deepseek",
            planner_model=None,
        )
    )

    event = runtime._events[
        EventRunKey(
            thread_id="thread_contract_error",
            run_type="initial",
            request_id=INITIAL_RUN_REQUEST_ID,
        )
    ][-1]
    assert "event: agent_error" in event
    assert SAFE_PLANNING_ERROR_MESSAGE in event
    assert "planning_context time_horizon must match IntentProfile" not in event
    assert "IntentProfile" not in event


async def _collect_events(stream):
    return [event async for event in stream]


async def _next_event(stream):
    iterator = stream.__aiter__()
    try:
        return await asyncio.wait_for(iterator.__anext__(), timeout=1)
    finally:
        await stream.aclose()


async def _collect_until(stream, predicate, limit: int = 10):
    events = []
    iterator = stream.__aiter__()
    try:
        for _ in range(limit):
            event = await asyncio.wait_for(iterator.__anext__(), timeout=1)
            events.append(event)
            if predicate(event):
                return events
        raise AssertionError("stream predicate was not reached")
    finally:
        await stream.aclose()
