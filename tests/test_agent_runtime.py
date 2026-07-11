import asyncio
import json
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy.sql import Select, Update

from app.services.agent_runtime import (
    AgentRuntime,
    EventRunKey,
    SAFE_PLANNING_ERROR_MESSAGE,
)


INITIAL_REQUEST_ID = "11111111-1111-1111-1111-111111111111"


class AsyncStreamGraph:
    def __init__(self) -> None:
        self.inputs: list[Any] = []

    def stream(self, input_value, config):
        raise AssertionError("AgentRuntime must use graph.astream")

    async def astream(self, input_value, config):
        self.inputs.append((input_value, config))
        yield {"planner": {"reasoning_events": [{"code": "PLAN_STARTED", "message": "planning"}]}}
        yield {
            "__interrupt__": [
                type(
                    "Interrupt",
                    (),
                    {
                        "value": {
                            "type": "task_tree_review",
                            "task_tree": {"root": {}},
                            "planning_mode": "initial",
                        }
                    },
                )()
            ]
        }


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


class BlockingNextPhaseGraph:
    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def astream(self, input_value, config):
        self.started.set()
        await self.release.wait()
        yield {
            "planner": {
                "reasoning_events": [
                    {"code": "PLAN_STARTED", "message": "late request A"}
                ]
            }
        }


class SlowInitialGraph:
    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    def stream(self, input_value, config):
        raise AssertionError("AgentRuntime must use graph.astream")

    async def astream(self, input_value, config):
        self.started.set()
        await self.release.wait()
        yield {
            "__interrupt__": [
                type(
                    "Interrupt",
                    (),
                    {
                        "value": {
                            "type": "task_tree_review",
                            "task_tree": {"root": {}},
                            "planning_mode": "initial",
                        }
                    },
                )()
            ]
        }


class SlowResumeGraph:
    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.inputs: list[Any] = []

    def stream(self, input_value, config):
        raise AssertionError("AgentRuntime must use graph.astream")

    async def astream(self, input_value, config):
        self.inputs.append((input_value, config))
        self.started.set()
        await self.release.wait()
        yield {"planner": {"reasoning_events": [{"code": "PLAN_STARTED", "message": "planning"}]}}


class SlowApproveResumeGraph:
    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    def stream(self, input_value, config):
        raise AssertionError("AgentRuntime must use graph.astream")

    async def astream(self, input_value, config):
        self.started.set()
        await self.release.wait()
        yield {"persist_tasks": {"task_persistence_status": "succeeded"}}


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


def test_agent_runtime_sse_envelope_uses_run_scoped_sequence_and_event_id():
    runtime = AgentRuntime(graph_factory=lambda **_: AsyncStreamGraph())
    request_a = "request-a"
    request_b = "request-b"

    runtime._append_event(
        "thread-1",
        "plan_ready",
        {"task_tree": {"summary": "request A"}},
        run_type="initial",
        request_id=request_a,
    )
    runtime._append_done(
        "thread-1",
        status="completed",
        run_type="initial",
        request_id=request_a,
    )
    runtime._append_error(
        "thread-1",
        code="AGENT_RUN_FAILED",
        message="friendly",
        run_type="initial",
        request_id=request_b,
    )

    first, second = [
        _parse_sse_event(event)
        for event in runtime._events[
            EventRunKey(thread_id="thread-1", run_type="initial", request_id=request_a)
        ]
    ]
    third = _parse_sse_event(
        runtime._events[
            EventRunKey(thread_id="thread-1", run_type="initial", request_id=request_b)
        ][0]
    )

    assert first["id"] == "thread-1:initial:request-a:000001"
    assert first["event"] == "plan_ready"
    assert first["data"]["event_id"] == first["id"]
    assert first["data"]["thread_id"] == "thread-1"
    assert first["data"]["request_id"] == request_a
    assert first["data"]["run_type"] == "initial"
    assert first["data"]["event_type"] == "plan_ready"
    assert first["data"]["seq"] == 1
    assert first["data"]["created_at"].endswith("Z")
    assert first["data"]["payload"]["task_tree"] == {"summary": "request A"}
    assert second["id"] == "thread-1:initial:request-a:000002"
    assert second["data"]["seq"] == 2
    assert second["data"]["payload"]["status"] == "completed"
    assert third["id"] == "thread-1:initial:request-b:000001"
    assert third["data"]["seq"] == 1
    assert third["data"]["payload"]["code"] == "AGENT_RUN_FAILED"


def test_plan_ready_sse_retains_strategy_context(monkeypatch):
    runtime = AgentRuntime(graph_factory=lambda **_: AsyncStreamGraph())
    strategy_context = {
        "schema_version": 1,
        "strategy_type": "delivery",
        "deliverable": {"title": "Report"},
    }

    async def fake_persist_interrupt(**_):
        return None

    monkeypatch.setattr(runtime, "_persist_interrupt", fake_persist_interrupt)
    appended = asyncio.run(
        runtime._append_chunk(
            user_id="11111111-1111-1111-1111-111111111111",
            thread_id="thread-strategy",
            chunk={
                "__interrupt__": [
                    SimpleNamespace(
                        value={
                            "type": "task_tree_review",
                            "planning_mode": "initial",
                            "task_tree": {
                                "summary": "Delivery plan",
                                "strategy_context": strategy_context,
                            },
                        }
                    )
                ]
            },
            run_type="initial",
            request_id="request-strategy",
        )
    )

    event = _parse_sse_event(
        runtime._events[
            EventRunKey(
                thread_id="thread-strategy",
                run_type="initial",
                request_id="request-strategy",
            )
        ][0]
    )

    assert appended is True
    assert event["event"] == "plan_ready"
    assert event["data"]["payload"]["task_tree"]["strategy_context"] == strategy_context


def test_initial_run_emits_stage_events_before_slow_planner_finishes(monkeypatch):
    async def collect_before_release():
        graph = SlowInitialGraph()
        runtime = AgentRuntime(
            graph_factory=lambda **_: graph,
            heartbeat_interval_seconds=60,
        )
        _patch_async_session(monkeypatch)
        run_task = asyncio.create_task(
            runtime.run_new_thread(
                user_id="11111111-1111-1111-1111-111111111111",
                thread_id="thread-1",
                request_id=INITIAL_REQUEST_ID,
                intent_text="write paper",
                selected_provider="native",
            )
        )
        await asyncio.wait_for(graph.started.wait(), timeout=1)
        events = list(
            runtime._events[
                EventRunKey(
                    thread_id="thread-1",
                    run_type="initial",
                    request_id=INITIAL_REQUEST_ID,
                )
            ]
        )
        assert any("event: run_started" in event for event in events)
        assert any("event: intent_profile_started" in event for event in events)
        graph.release.set()
        await asyncio.wait_for(run_task, timeout=1)
        return events

    events = asyncio.run(collect_before_release())
    parsed = [_parse_sse_event(event)["data"] for event in events]
    assert [event["event_type"] for event in parsed[:2]] == [
        "run_started",
        "intent_profile_started",
    ]


def test_next_phase_run_emits_stage_events_before_slow_planner_finishes():
    async def collect_before_release():
        graph = BlockingNextPhaseGraph()
        runtime = AgentRuntime(
            graph_factory=lambda **_: graph,
            planner_client_factory=lambda **_: object(),
            heartbeat_interval_seconds=60,
        )
        request_id = "request-a"
        run_task = asyncio.create_task(
            runtime.run_next_phase(
                user_id="11111111-1111-1111-1111-111111111111",
                thread_id="thread-1",
                request_id=request_id,
                intent_text="continue plan",
                committed_task_tree={"planning_context": {}},
                current_phase_task_summary="1/1 AI actions completed",
            )
        )
        await asyncio.wait_for(graph.started.wait(), timeout=1)
        events = list(
            runtime._events[
                EventRunKey(
                    thread_id="thread-1",
                    run_type="next_phase",
                    request_id=request_id,
                )
            ]
        )
        graph.release.set()
        await asyncio.wait_for(run_task, timeout=1)
        return events

    parsed = [_parse_sse_event(event)["data"] for event in asyncio.run(collect_before_release())]
    assert [event["event_type"] for event in parsed[:2]] == [
        "run_started",
        "planning_started",
    ]


def test_intent_profile_chunk_advances_to_strategy_and_planning_stage():
    runtime = AgentRuntime(graph_factory=lambda **_: AsyncStreamGraph())
    request_id = "request-a"

    processed = asyncio.run(
        runtime._append_chunk(
            user_id="11111111-1111-1111-1111-111111111111",
            thread_id="thread-1",
            chunk={
                "intent_profiler": {
                    "intent_profile": {
                        "intent_type": "long_term_growth",
                        "time_horizon": "months",
                    }
                }
            },
            run_type="initial",
            request_id=request_id,
        )
    )

    assert processed is True
    events = [
        _parse_sse_event(event)["data"]
        for event in runtime._events[
            EventRunKey(thread_id="thread-1", run_type="initial", request_id=request_id)
        ]
    ]
    assert [event["event_type"] for event in events] == [
        "intent_profile_completed",
        "strategy_selected",
        "planning_started",
    ]
    assert events[1]["payload"]["strategy"] == "long_term_growth"


def test_slow_run_emits_and_stops_heartbeat(monkeypatch):
    async def collect_heartbeat_events():
        graph = SlowInitialGraph()
        runtime = AgentRuntime(
            graph_factory=lambda **_: graph,
            heartbeat_interval_seconds=0.01,
        )
        _patch_async_session(monkeypatch)
        run_task = asyncio.create_task(
            runtime.run_new_thread(
                user_id="11111111-1111-1111-1111-111111111111",
                thread_id="thread-1",
                request_id=INITIAL_REQUEST_ID,
                intent_text="write paper",
                selected_provider="native",
            )
        )
        await asyncio.wait_for(graph.started.wait(), timeout=1)
        await asyncio.sleep(0.03)
        run_key = EventRunKey(
            thread_id="thread-1",
            run_type="initial",
            request_id=INITIAL_REQUEST_ID,
        )
        heartbeat_events = [
            event for event in runtime._events[run_key] if "event: still_running" in event
        ]
        assert heartbeat_events
        graph.release.set()
        await asyncio.wait_for(run_task, timeout=1)
        event_count_after_done = len(runtime._events[run_key])
        await asyncio.sleep(0.03)
        return runtime._events[run_key], event_count_after_done

    events, event_count_after_done = asyncio.run(collect_heartbeat_events())
    assert len(events) == event_count_after_done
    assert any("event: still_running" in event for event in events)
    assert any("event: plan_ready" in event for event in events)


def test_agent_runtime_runs_langgraph_astream_in_background_worker(monkeypatch):
    graph = AsyncStreamGraph()
    runtime = AgentRuntime(graph_factory=lambda **_: graph)
    _patch_async_session(monkeypatch)

    asyncio.run(
        runtime.run_new_thread(
            user_id="11111111-1111-1111-1111-111111111111",
            thread_id="thread-1",
            request_id=INITIAL_REQUEST_ID,
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
                request_id=INITIAL_REQUEST_ID,
            ),
            lambda event: "event: plan_ready" in event,
        )
    )

    assert graph.inputs[0][0]["user_id"] == "11111111-1111-1111-1111-111111111111"
    assert graph.inputs[0][0]["thread_id"] == "thread-1"
    assert graph.inputs[0][0]["planning_mode"] == "initial"
    assert (
        graph.inputs[0][1]["configurable"]["checkpoint_ns"]
        == "initial"
    )
    assert "event: run_started" in "".join(events)
    assert "event: intent_profile_started" in "".join(events)
    assert "event: validation_started" in "".join(events)
    assert "event: plan_ready" in "".join(events)


def test_agent_runtime_reuses_one_checkpointer_across_initial_run_and_resume():
    checkpointers = []
    graphs = []

    def graph_factory(*, planner, checkpointer):
        checkpointers.append(checkpointer)
        graph = AsyncStreamGraph()
        graphs.append(graph)
        return graph

    runtime = AgentRuntime(graph_factory=graph_factory)

    asyncio.run(
        runtime.run_new_thread(
            user_id="user-1",
            thread_id="thread-1",
            request_id=INITIAL_REQUEST_ID,
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
            request_id="22222222-2222-2222-2222-222222222222",
        )
    )

    assert len(checkpointers) == 2
    assert checkpointers[0] is checkpointers[1]
    assert (
        graphs[0].inputs[0][1]["configurable"]["checkpoint_ns"]
        == graphs[1].inputs[0][1]["configurable"]["checkpoint_ns"]
        == "initial"
    )


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
            request_id=INITIAL_REQUEST_ID,
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
            request_id=INITIAL_REQUEST_ID,
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
            request_id=INITIAL_REQUEST_ID,
        )
    )

    assert created_planners[0] == {"provider": None, "model": None}


def test_resume_refine_uses_run_lifecycle_and_heartbeat():
    async def collect_refine_events():
        graph = SlowResumeGraph()
        runtime = AgentRuntime(
            graph_factory=lambda **_: graph,
            heartbeat_interval_seconds=0.01,
        )
        request_id = "22222222-2222-2222-2222-222222222222"
        run_key = EventRunKey(
            thread_id="thread-1",
            run_type="refine",
            request_id=request_id,
        )
        run_task = asyncio.create_task(
            runtime.resume_thread(
                user_id="user-1",
                thread_id="thread-1",
                decision={"action": "refine", "feedback": "make it smaller"},
                run_type="refine",
                request_id=request_id,
            )
        )
        await asyncio.wait_for(graph.started.wait(), timeout=1)
        await asyncio.sleep(0.03)
        in_flight_events = list(runtime._events[run_key])
        assert run_key in runtime._active_runs
        graph.release.set()
        await asyncio.wait_for(run_task, timeout=1)
        assert run_key not in runtime._active_runs
        return in_flight_events, runtime._events[run_key]

    in_flight_events, final_events = asyncio.run(collect_refine_events())
    in_flight_types = [
        _parse_sse_event(event)["data"]["event_type"]
        for event in in_flight_events
    ]
    final_types = [
        _parse_sse_event(event)["data"]["event_type"]
        for event in final_events
    ]
    assert in_flight_types[:2] == ["run_started", "planning_started"]
    assert "still_running" in in_flight_types
    assert final_types[-1] == "done"


def test_resume_approve_emits_persistence_started_before_persist_finishes():
    async def collect_approve_events():
        graph = SlowApproveResumeGraph()
        runtime = AgentRuntime(
            graph_factory=lambda **_: graph,
            heartbeat_interval_seconds=60,
        )
        request_id = "22222222-2222-2222-2222-222222222222"
        run_key = EventRunKey(
            thread_id="thread-1",
            run_type="initial",
            request_id=request_id,
        )
        run_task = asyncio.create_task(
            runtime.resume_thread(
                user_id="user-1",
                thread_id="thread-1",
                decision={"action": "approve"},
                run_type="initial",
                request_id=request_id,
            )
        )
        await asyncio.wait_for(graph.started.wait(), timeout=1)
        in_flight_events = list(runtime._events[run_key])
        graph.release.set()
        await asyncio.wait_for(run_task, timeout=1)
        return in_flight_events, list(runtime._events[run_key])

    in_flight_events, final_events = asyncio.run(collect_approve_events())
    in_flight_types = [
        _parse_sse_event(event)["data"]["event_type"]
        for event in in_flight_events
    ]
    final_payloads = [_parse_sse_event(event)["data"] for event in final_events]
    final_types = [event["event_type"] for event in final_payloads]

    assert in_flight_types[:3] == ["run_started", "persistence_started", "sync_status"]
    assert "sync_complete" not in in_flight_types
    assert final_types[-2:] == ["sync_complete", "done"]
    sync_complete = next(event for event in final_payloads if event["event_type"] == "sync_complete")
    assert sync_complete["payload"]["stage"] == "sync_complete"
    assert sync_complete["payload"]["label"] == "已完成计划保存"
    assert "state_version" in sync_complete["payload"]
    assert "status" not in sync_complete["payload"]


def test_next_phase_runtime_uses_deepseek_and_preserves_committed_tree(monkeypatch):
    request_id = "22222222-2222-2222-2222-222222222222"
    graph = NextPhaseInterruptGraph(request_id)
    created_planners = []

    def planner_client_factory(*, provider, model):
        created_planners.append({"provider": provider, "model": model})
        return object()

    committed_tree = {"summary": "committed phase"}
    thread = SimpleNamespace(
        status="running",
        current_node="next_phase_planner",
        task_tree=committed_tree,
        interrupted_at=None,
        updated_at=None,
        interrupt_payload={
            "type": "phase_generation_state",
            "request_id": request_id,
            "status": "running",
            "base_phase_id": "phase_01",
            "history": {},
        },
    )
    session = _patch_async_session(
        monkeypatch,
        thread=thread,
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
    assert (
        graph.inputs[0][1]["configurable"]["checkpoint_ns"]
        == f"next_phase:{request_id}"
    )
    assert thread.task_tree == committed_tree
    assert thread.status == "awaiting_confirmation"
    assert thread.interrupt_payload["type"] == "next_phase_review"
    assert thread.interrupt_payload["status"] == "awaiting_confirmation"
    assert thread.interrupt_payload["base_phase_id"] == "phase_01"
    assert thread.interrupt_payload["task_tree"] == {"summary": "proposed phase"}
    locked_select = next(
        statement for statement in session.statements if isinstance(statement, Select)
    )
    assert locked_select._for_update_arg is not None
    assert session.commits == 1


def test_commit_next_phase_persists_durable_preview_without_graph_checkpoint(monkeypatch):
    request_id = "22222222-2222-2222-2222-222222222222"
    preview_tree = {"summary": "durable phase preview"}
    persisted_states = []

    async def fake_persist_internal_tasks_node(state):
        persisted_states.append(state)
        return {"task_persistence_status": "succeeded"}

    import app.services.agent_runtime as agent_runtime_module

    monkeypatch.setattr(
        agent_runtime_module,
        "persist_internal_tasks_node",
        fake_persist_internal_tasks_node,
        raising=False,
    )
    runtime = AgentRuntime(
        graph_factory=lambda **_: pytest.fail("durable phase commit must not build a graph")
    )

    asyncio.run(
        runtime.commit_next_phase(
            user_id="11111111-1111-1111-1111-111111111111",
            thread_id="thread-1",
            request_id=request_id,
            task_tree=preview_tree,
        )
    )

    assert persisted_states == [
        {
            "user_id": "11111111-1111-1111-1111-111111111111",
            "thread_id": "thread-1",
                "task_tree": preview_tree,
                "planning_mode": "next_phase",
                "phase_request_id": request_id,
                "user_timezone": "UTC",
            }
        ]
    terminal_event = runtime._events[
        EventRunKey(
            thread_id="thread-1",
            run_type="next_phase",
            request_id=request_id,
        )
    ][-1]
    assert "event: done" in terminal_event


def test_commit_next_phase_failure_releases_confirmation_and_emits_error(monkeypatch):
    request_id = "22222222-2222-2222-2222-222222222222"
    released = []

    async def fail_persist_internal_tasks_node(state):
        raise RuntimeError("database write failed")

    async def capture_release(**kwargs):
        released.append(kwargs)

    import app.services.agent_runtime as agent_runtime_module

    monkeypatch.setattr(
        agent_runtime_module,
        "persist_internal_tasks_node",
        fail_persist_internal_tasks_node,
    )
    runtime = AgentRuntime()
    monkeypatch.setattr(runtime, "_release_phase_failure", capture_release)

    asyncio.run(
        runtime.commit_next_phase(
            user_id="11111111-1111-1111-1111-111111111111",
            thread_id="thread-1",
            request_id=request_id,
            task_tree={"summary": "durable phase preview"},
        )
    )

    assert released == [
        {
            "user_id": "11111111-1111-1111-1111-111111111111",
            "thread_id": "thread-1",
            "request_id": request_id,
        }
    ]
    terminal_event = runtime._events[
        EventRunKey(
            thread_id="thread-1",
            run_type="next_phase",
            request_id=request_id,
        )
    ][-1]
    assert "event: agent_error" in terminal_event
    assert "event: done" not in terminal_event


def test_release_phase_failure_marks_confirming_preview_failed(monkeypatch):
    request_id = "22222222-2222-2222-2222-222222222222"
    session = _patch_async_session(
        monkeypatch,
        thread=SimpleNamespace(
            interrupt_payload={
                "type": "next_phase_review",
                "request_id": request_id,
                "status": "confirming",
                "base_phase_id": "phase_01",
                "task_tree": {"summary": "durable phase preview"},
                "history": {},
            }
        ),
    )
    runtime = AgentRuntime()

    asyncio.run(
        runtime._release_phase_failure(
            user_id="11111111-1111-1111-1111-111111111111",
            thread_id="thread-1",
            request_id=request_id,
        )
    )

    update_statement = next(
        statement for statement in session.statements if isinstance(statement, Update)
    )
    values = list(update_statement.compile().params.values())
    failed_payload = next(
        value
        for value in values
        if isinstance(value, dict) and value.get("request_id") == request_id
    )
    assert failed_payload["type"] == "phase_generation_state"
    assert failed_payload["status"] == "failed"
    assert failed_payload["base_phase_id"] == "phase_01"
    assert "task_tree" not in failed_payload


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
    events = asyncio.run(
        _collect_until(
            runtime.stream_thread_events(
                user_id="user-1",
                thread_id="thread-1",
                run_type="next_phase",
                request_id=request_id,
            ),
            lambda event: "event: agent_error" in event,
        )
    )
    event = events[-1]
    assert "event: agent_error" in event
    assert '"thread_id":"thread-1"' in event
    assert '"run_type":"next_phase"' in event
    assert f'"request_id":"{request_id}"' in event


def test_agent_runtime_stream_keeps_connection_open_for_new_events_until_done():
    runtime = AgentRuntime(graph_factory=lambda **_: AsyncStreamGraph())
    runtime._append_event(
        "thread-1",
        "reasoning",
        {"message": "first"},
        run_type="initial",
        request_id=INITIAL_REQUEST_ID,
    )

    async def collect_live_events():
        stream = runtime.stream_thread_events(
            user_id="user-1",
            thread_id="thread-1",
            run_type="initial",
            request_id=INITIAL_REQUEST_ID,
        )
        iterator = stream.__aiter__()
        first = await iterator.__anext__()
        pending_next = asyncio.create_task(iterator.__anext__())
        await asyncio.sleep(0)
        assert not pending_next.done()

        runtime._append_event(
            "thread-1",
            "reasoning",
            {"message": "second"},
            run_type="initial",
            request_id=INITIAL_REQUEST_ID,
        )
        second = await asyncio.wait_for(pending_next, timeout=1)

        pending_done = asyncio.create_task(iterator.__anext__())
        await asyncio.sleep(0)
        assert not pending_done.done()
        runtime._append_event(
            "thread-1",
            "done",
            {"status": "completed"},
            run_type="initial",
            request_id=INITIAL_REQUEST_ID,
        )
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
        stream = runtime.stream_thread_events(
            user_id="user-1",
            thread_id="thread-1",
            run_type="initial",
            request_id=INITIAL_REQUEST_ID,
        )
        iterator = stream.__aiter__()
        pending_error = asyncio.create_task(iterator.__anext__())
        await asyncio.sleep(0)
        assert not pending_error.done()

        runtime._append_error(
            "thread-1",
            code="AGENT_RUN_FAILED",
            message="friendly failure",
            run_type="initial",
            request_id=INITIAL_REQUEST_ID,
        )
        event = await asyncio.wait_for(pending_error, timeout=1)

        with pytest.raises(StopAsyncIteration):
            await asyncio.wait_for(iterator.__anext__(), timeout=1)
        return event

    event = asyncio.run(collect_live_error_event())

    assert "event: agent_error" in event
    assert "event: error" not in event
    assert "AGENT_RUN_FAILED" in event


def test_agent_runtime_tracks_cancellation_only_while_next_phase_run_is_active():
    async def cancel_matching_active_run():
        graph = BlockingNextPhaseGraph()
        runtime = AgentRuntime(
            graph_factory=lambda **_: graph,
            planner_client_factory=lambda **_: object(),
        )
        run_key = EventRunKey(
            thread_id="thread-1",
            run_type="next_phase",
            request_id="request-a",
        )
        run_task = asyncio.create_task(
            runtime.run_next_phase(
                user_id="11111111-1111-1111-1111-111111111111",
                thread_id="thread-1",
                request_id="request-a",
                intent_text="continue plan",
                committed_task_tree={"planning_context": {}},
                current_phase_task_summary="1/1 AI actions completed",
            )
        )
        await asyncio.wait_for(graph.started.wait(), timeout=1)
        assert run_key in runtime._active_runs

        stream = runtime.stream_thread_events(
            user_id="user-1",
            thread_id="thread-1",
            run_type="next_phase",
            request_id="request-a",
        )
        iterator = stream.__aiter__()
        assert "event: run_started" in await asyncio.wait_for(iterator.__anext__(), timeout=1)
        assert "event: planning_started" in await asyncio.wait_for(iterator.__anext__(), timeout=1)
        pending_event = asyncio.create_task(iterator.__anext__())
        await asyncio.sleep(0)
        assert not pending_event.done()

        runtime.cancel_run(
            thread_id="thread-1",
            run_type="next_phase",
            request_id="request-a",
        )
        assert run_key in runtime._cancelled_runs

        with pytest.raises(StopAsyncIteration):
            await asyncio.wait_for(pending_event, timeout=1)
        assert runtime._append_event(
            "thread-1",
            "plan_ready",
            {"task_tree": {"summary": "late request A"}},
            run_type="next_phase",
            request_id="request-a",
        ) is False

        graph.release.set()
        await asyncio.wait_for(run_task, timeout=1)
        assert run_key not in runtime._active_runs
        assert run_key not in runtime._cancelled_runs

        assert runtime._append_event(
            "thread-1",
            "plan_ready",
            {"task_tree": {"summary": "request B"}},
            run_type="next_phase",
            request_id="request-b",
        ) is True
        return runtime, await _next_event(
            runtime.stream_thread_events(
                user_id="user-1",
                thread_id="thread-1",
                run_type="next_phase",
                request_id="request-b",
            )
        )

    runtime, request_b_event = asyncio.run(cancel_matching_active_run())

    assert "request B" in request_b_event
    assert "late request A" not in request_b_event
    assert not runtime._events.get(
        EventRunKey(
            thread_id="thread-1",
            run_type="next_phase",
            request_id="request-a",
        )
    )


def test_agent_runtime_does_not_retain_cancellation_for_inactive_run():
    runtime = AgentRuntime(graph_factory=lambda **_: AsyncStreamGraph())
    run_key = EventRunKey(
        thread_id="thread-1",
        run_type="next_phase",
        request_id="request-inactive",
    )

    runtime.cancel_run(
        thread_id=run_key.thread_id,
        run_type=run_key.run_type,
        request_id=run_key.request_id,
    )

    assert run_key not in runtime._cancelled_runs


def test_cancelled_next_phase_rejects_late_interrupt_without_terminal_events(monkeypatch):
    request_id = "request-a"
    committed_tree = {"summary": "committed phase"}
    thread = SimpleNamespace(
        status="succeeded",
        current_node="persist_internal_tasks",
        task_tree=committed_tree,
        interrupted_at=None,
        updated_at=None,
        interrupt_payload={
            "type": "phase_generation_state",
            "request_id": request_id,
            "status": "cancelled",
            "history": {
                request_id: {
                    "status": "cancelled",
                    "cancelled_at": "2026-07-02T00:00:00+00:00",
                }
            },
        },
    )
    session = _patch_async_session(monkeypatch, thread=thread)
    runtime = AgentRuntime(graph_factory=lambda **_: AsyncStreamGraph())
    chunk = {
        "__interrupt__": [
            SimpleNamespace(
                value={
                    "task_tree": {"summary": "late preview"},
                    "planning_mode": "next_phase",
                    "phase_request_id": request_id,
                }
            )
        ]
    }

    persisted = asyncio.run(
        runtime._append_chunk(
            user_id="11111111-1111-1111-1111-111111111111",
            thread_id="thread-1",
            chunk=chunk,
            run_type="next_phase",
            request_id=request_id,
        )
    )

    assert persisted is False
    assert thread.task_tree == committed_tree
    assert thread.interrupt_payload["status"] == "cancelled"
    assert session.commits == 0
    events = runtime._events.get(
        EventRunKey(
            thread_id="thread-1",
            run_type="next_phase",
            request_id=request_id,
        ),
        [],
    )
    assert not any(
        terminal in event
        for event in events
        for terminal in ("event: plan_ready", "event: done", "event: agent_error")
    )


def test_cancelled_next_phase_rejects_late_failure_release(monkeypatch):
    request_id = "request-a"
    cancelled_payload = {
        "type": "phase_generation_state",
        "request_id": request_id,
        "status": "cancelled",
        "history": {
            request_id: {
                "status": "cancelled",
                "cancelled_at": "2026-07-02T00:00:00+00:00",
            }
        },
    }
    thread = SimpleNamespace(interrupt_payload=cancelled_payload)
    session = _patch_async_session(monkeypatch, thread=thread)
    runtime = AgentRuntime(graph_factory=lambda **_: AsyncStreamGraph())

    asyncio.run(
        runtime._release_phase_failure(
            user_id="11111111-1111-1111-1111-111111111111",
            thread_id="thread-1",
            request_id=request_id,
        )
    )

    assert thread.interrupt_payload == cancelled_payload
    assert session.commits == 0
    assert not any(isinstance(statement, Update) for statement in session.statements)


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
            request_id=INITIAL_REQUEST_ID,
            intent_text="write paper",
            selected_provider="native",
        )
    )

    assert session.commits == 1
    compiled = session.statements[0].compile().params
    assert "awaiting_confirmation" in compiled.values()
    assert {"root": {}} in compiled.values()
    envelope = next(
        value
        for value in compiled.values()
        if isinstance(value, dict) and value.get("type") == "task_tree_review"
    )
    assert envelope["request_id"] == INITIAL_REQUEST_ID
    assert envelope["run_type"] == "initial"


def test_agent_runtime_streams_only_events_after_last_event_id():
    runtime = AgentRuntime(graph_factory=lambda **_: AsyncStreamGraph())
    runtime._append_event(
        "thread-1",
        "reasoning",
        {"message": "first"},
        run_type="initial",
        request_id=INITIAL_REQUEST_ID,
    )
    runtime._append_event(
        "thread-1",
        "reasoning",
        {"message": "second"},
        run_type="initial",
        request_id=INITIAL_REQUEST_ID,
    )
    runtime._append_event(
        "thread-1",
        "reasoning",
        {"message": "third"},
        run_type="initial",
        request_id=INITIAL_REQUEST_ID,
    )

    event = asyncio.run(
        _next_event(
            runtime.stream_thread_events(
                user_id="user-1",
                thread_id="thread-1",
                last_event_id=(
                    f"thread-1:initial:{INITIAL_REQUEST_ID}:000002"
                ),
                run_type="initial",
                request_id=INITIAL_REQUEST_ID,
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


def test_new_initial_refine_stream_excludes_previous_initial_run_events():
    runtime = AgentRuntime(graph_factory=lambda **_: AsyncStreamGraph())
    runtime._append_event(
        "thread-1",
        "plan_ready",
        {"task_tree": {"summary": "request A"}},
        run_type="initial",
        request_id="request-a",
    )
    runtime._append_done(
        "thread-1",
        status="completed",
        run_type="initial",
        request_id="request-a",
    )
    runtime._append_event(
        "thread-1",
        "plan_ready",
        {"task_tree": {"summary": "request B"}},
        run_type="initial",
        request_id="request-b",
    )
    runtime._append_done(
        "thread-1",
        status="completed",
        run_type="initial",
        request_id="request-b",
    )

    events = asyncio.run(
        _collect_events(
            runtime.stream_thread_events(
                user_id="user-1",
                thread_id="thread-1",
                run_type="initial",
                request_id="request-b",
            )
        )
    )

    payload = "\n".join(events)
    assert '"request_id":"request-a"' not in payload
    assert '"request_id":"request-b"' in payload
    assert "request A" not in payload
    assert "request B" in payload
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
                last_event_id="thread-1:next_phase:request-a:000001",
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
            request_id=INITIAL_REQUEST_ID,
            intent_text="write paper",
            selected_provider="native",
        )
    )

    events = asyncio.run(
        _collect_until(
            runtime.stream_thread_events(
                user_id="user-1",
                thread_id="thread-1",
                run_type="initial",
                request_id=INITIAL_REQUEST_ID,
            ),
            lambda event: "event: agent_error" in event,
        )
    )
    event = events[-1]

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
            request_id=INITIAL_REQUEST_ID,
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
            request_id=INITIAL_REQUEST_ID,
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


def _parse_sse_event(event: str) -> dict[str, Any]:
    parsed: dict[str, Any] = {}
    for line in event.splitlines():
        if line.startswith("id: "):
            parsed["id"] = line[4:]
        elif line.startswith("event: "):
            parsed["event"] = line[7:]
        elif line.startswith("data: "):
            parsed["data"] = json.loads(line[6:])
    return parsed
