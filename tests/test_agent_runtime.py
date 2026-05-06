import asyncio
from typing import Any

from app.services.agent_runtime import AgentRuntime


class SyncStreamGraph:
    def __init__(self) -> None:
        self.inputs: list[Any] = []

    def stream(self, input_value, config):
        self.inputs.append((input_value, config))
        yield {"planner": {"reasoning_events": [{"code": "PLAN_STARTED", "message": "planning"}]}}
        yield {"__interrupt__": [type("Interrupt", (), {"value": {"task_tree": {"root": {}}}})()]}

    async def astream(self, input_value, config):  # pragma: no cover - runtime must call graph.stream.
        raise AssertionError("AgentRuntime must use graph.stream from its background worker")


def test_agent_runtime_runs_langgraph_stream_in_background_worker():
    graph = SyncStreamGraph()
    runtime = AgentRuntime(graph_factory=lambda **_: graph)

    asyncio.run(
        runtime.run_new_thread(
            user_id="user-1",
            thread_id="thread-1",
            intent_text="write paper",
            selected_provider="todoist",
        )
    )

    events = asyncio.run(
        _collect_events(
            runtime.stream_thread_events(
                user_id="user-1",
                thread_id="thread-1",
            )
        )
    )

    assert graph.inputs[0][0]["user_id"] == "user-1"
    assert graph.inputs[0][0]["thread_id"] == "thread-1"
    assert "event: reasoning" in "".join(events)
    assert "event: plan_ready" in "".join(events)


def test_agent_runtime_reuses_one_checkpointer_across_initial_run_and_resume():
    checkpointers = []

    def graph_factory(*, checkpointer):
        checkpointers.append(checkpointer)
        return SyncStreamGraph()

    runtime = AgentRuntime(graph_factory=graph_factory)

    asyncio.run(
        runtime.run_new_thread(
            user_id="user-1",
            thread_id="thread-1",
            intent_text="write paper",
            selected_provider="todoist",
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


async def _collect_events(stream):
    return [event async for event in stream]
