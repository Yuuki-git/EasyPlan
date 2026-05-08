import asyncio
from typing import Any

import pytest

from app.services.agent_runtime import AgentRuntime


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


class FakeAsyncSession:
    def __init__(self) -> None:
        self.statements = []
        self.commits = 0

    async def execute(self, statement):
        self.statements.append(statement)

    async def commit(self):
        self.commits += 1


class FakeAsyncSessionContext:
    def __init__(self, session: FakeAsyncSession) -> None:
        self.session = session

    async def __aenter__(self):
        return self.session

    async def __aexit__(self, exc_type, exc, tb):
        return False


def _patch_async_session(monkeypatch):
    session = FakeAsyncSession()

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

    assert "event: error" in event
    assert "AI 在规划时遇到了一点小麻烦，正在尝试重新组织，请稍候。" in event
    assert "validation error" not in event.lower()
    assert "estimated_minutes" not in event


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
