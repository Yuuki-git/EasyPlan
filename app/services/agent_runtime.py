import asyncio
import itertools
import logging
import threading
from collections import defaultdict, deque
from typing import Any, AsyncIterator, Callable

from app.agents.graph import build_task_graph, create_graph_config, resume_with_human_input
from app.api.sse import format_sse_event


logger = logging.getLogger(__name__)


class AgentRuntime:
    """Runs LangGraph and stores lightweight SSE events for thread streams."""

    def __init__(
        self,
        *,
        graph_factory: Callable[[], Any] | None = None,
        max_events_per_thread: int = 200,
    ) -> None:
        self._graph_factory = graph_factory or build_task_graph
        self._events: dict[str, deque[str]] = defaultdict(lambda: deque(maxlen=max_events_per_thread))
        self._counter = itertools.count(1)
        self._lock = threading.Lock()

    async def run_new_thread(
        self,
        *,
        user_id: str,
        thread_id: str,
        intent_text: str,
        selected_provider: str,
    ) -> None:
        await asyncio.to_thread(
            self._run_new_thread_sync,
            user_id=user_id,
            thread_id=thread_id,
            intent_text=intent_text,
            selected_provider=selected_provider,
        )

    def _run_new_thread_sync(
        self,
        *,
        user_id: str,
        thread_id: str,
        intent_text: str,
        selected_provider: str,
    ) -> None:
        graph = self._graph_factory()
        config = create_graph_config(user_id=user_id, thread_id=thread_id)
        initial_state = {
            "user_id": user_id,
            "thread_id": thread_id,
            "intent_text": intent_text,
            "selected_provider": selected_provider,
        }
        try:
            for chunk in graph.stream(initial_state, config):
                self._append_chunk(thread_id, chunk)
        except Exception as exc:
            logger.exception("agent_thread_run_failed", extra={"thread_id": thread_id, "user_id": user_id})
            self._append_error(thread_id, code="AGENT_RUN_FAILED", message=str(exc))

    async def resume_thread(
        self,
        *,
        user_id: str,
        thread_id: str,
        decision: dict[str, Any],
    ) -> None:
        await asyncio.to_thread(
            self._resume_thread_sync,
            user_id=user_id,
            thread_id=thread_id,
            decision=decision,
        )

    def _resume_thread_sync(
        self,
        *,
        user_id: str,
        thread_id: str,
        decision: dict[str, Any],
    ) -> None:
        graph = self._graph_factory()
        config = create_graph_config(user_id=user_id, thread_id=thread_id)
        command = resume_with_human_input(**decision)
        try:
            for chunk in graph.stream(command, config):
                self._append_chunk(thread_id, chunk)
        except Exception as exc:
            logger.exception("agent_thread_resume_failed", extra={"thread_id": thread_id, "user_id": user_id})
            self._append_error(thread_id, code="AGENT_RESUME_FAILED", message=str(exc))

    async def stream_thread_events(
        self,
        *,
        user_id: str,
        thread_id: str,
        last_event_id: str | None = None,
    ) -> AsyncIterator[str]:
        with self._lock:
            events = list(self._events.get(thread_id, []))
        if not events:
            yield format_sse_event("snapshot_required", {"reason": "event_buffer_empty"})
            return
        for event in events:
            yield event

    def _append_chunk(self, thread_id: str, chunk: dict[str, Any]) -> None:
        if "__interrupt__" in chunk:
            interrupt_payload = chunk["__interrupt__"][0].value
            self._append_event(
                thread_id,
                "plan_ready",
                {
                    "state_version": next(self._counter),
                    "thread_id": thread_id,
                    "task_tree": interrupt_payload.get("task_tree"),
                },
            )
            return
        if "failed_validation" in chunk:
            error = chunk["failed_validation"].get("error", {})
            self._append_error(
                thread_id,
                code=error.get("code", "TASK_TREE_VALIDATION_FAILED"),
                message=error.get("message", "Task tree validation failed"),
            )
            return
        for node_name, payload in chunk.items():
            if isinstance(payload, dict) and payload.get("reasoning_events"):
                for event in payload["reasoning_events"]:
                    self._append_event(
                        thread_id,
                        "reasoning",
                        {
                            "state_version": next(self._counter),
                            **event,
                        },
                    )
            else:
                self._append_event(
                    thread_id,
                    "checkpoint",
                    {
                        "state_version": next(self._counter),
                        "node": node_name,
                    },
                )

    def _append_error(self, thread_id: str, *, code: str, message: str) -> None:
        self._append_event(
            thread_id,
            "error",
            {
                "state_version": next(self._counter),
                "code": code,
                "message": message,
            },
        )

    def _append_event(self, thread_id: str, event: str, data: dict[str, Any]) -> None:
        event_id = f"evt_{next(self._counter):08d}"
        with self._lock:
            self._events[thread_id].append(format_sse_event(event, data, event_id=event_id))


agent_runtime = AgentRuntime()
