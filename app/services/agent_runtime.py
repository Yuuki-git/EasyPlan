import asyncio
import itertools
import logging
import threading
from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, AsyncIterator, Callable
from uuid import UUID

from sqlalchemy import update

from app.agents.graph import build_task_graph, create_graph_config, resume_with_human_input
from app.api.sse import format_sse_event
from app.models.thread import AgentThread
from app.services.checkpoint_service import TenantAwareMemorySaver
from app.services.llm_service import create_planner_client


logger = logging.getLogger(__name__)
_global_checkpointer = TenantAwareMemorySaver()


@dataclass
class _EventSubscriber:
    loop: asyncio.AbstractEventLoop
    queue: asyncio.Queue[str]


class AgentRuntime:
    """Runs LangGraph and stores lightweight SSE events for thread streams."""

    def __init__(
        self,
        *,
        graph_factory: Callable[..., Any] | None = None,
        planner_client_factory: Callable[..., Any] | None = None,
        max_events_per_thread: int = 200,
    ) -> None:
        self._graph_factory = graph_factory or build_task_graph
        self._planner_client_factory = planner_client_factory or create_planner_client
        self._events: dict[str, deque[str]] = defaultdict(lambda: deque(maxlen=max_events_per_thread))
        self._subscribers: dict[str, list[_EventSubscriber]] = defaultdict(list)
        self._planner_selection: dict[str, tuple[str | None, str | None]] = {}
        self._counter = itertools.count(1)
        self._lock = threading.Lock()

    async def run_new_thread(
        self,
        *,
        user_id: str,
        thread_id: str,
        intent_text: str,
        selected_provider: str,
        planner_provider: str | None = None,
        planner_model: str | None = None,
    ) -> None:
        with self._lock:
            self._planner_selection[thread_id] = (planner_provider, planner_model)
        await asyncio.to_thread(
            self._run_new_thread_sync,
            user_id=user_id,
            thread_id=thread_id,
            intent_text=intent_text,
            selected_provider=selected_provider,
            planner_provider=planner_provider,
            planner_model=planner_model,
        )

    def _run_new_thread_sync(
        self,
        *,
        user_id: str,
        thread_id: str,
        intent_text: str,
        selected_provider: str,
        planner_provider: str | None,
        planner_model: str | None,
    ) -> None:
        graph = self._build_graph(planner_provider=planner_provider, planner_model=planner_model)
        config = create_graph_config(user_id=user_id, thread_id=thread_id)
        initial_state = {
            "user_id": user_id,
            "thread_id": thread_id,
            "intent_text": intent_text,
            "selected_provider": selected_provider,
            "planner_provider": planner_provider,
            "planner_model": planner_model,
        }
        try:
            interrupted = False
            emitted_terminal = False
            for chunk in graph.stream(initial_state, config):
                interrupted = interrupted or "__interrupt__" in chunk
                emitted_terminal = emitted_terminal or "failed_validation" in chunk
                self._append_chunk(user_id=user_id, thread_id=thread_id, chunk=chunk)
            if not interrupted and not emitted_terminal:
                self._append_done(thread_id, status="completed")
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
        with self._lock:
            planner_provider, planner_model = self._planner_selection.get(thread_id, (None, None))
        await asyncio.to_thread(
            self._resume_thread_sync,
            user_id=user_id,
            thread_id=thread_id,
            decision=decision,
            planner_provider=planner_provider,
            planner_model=planner_model,
        )

    def _resume_thread_sync(
        self,
        *,
        user_id: str,
        thread_id: str,
        decision: dict[str, Any],
        planner_provider: str | None,
        planner_model: str | None,
    ) -> None:
        graph = self._build_graph(planner_provider=planner_provider, planner_model=planner_model)
        config = create_graph_config(user_id=user_id, thread_id=thread_id)
        command = resume_with_human_input(**decision)
        try:
            interrupted = False
            emitted_terminal = False
            for chunk in graph.stream(command, config):
                interrupted = interrupted or "__interrupt__" in chunk
                emitted_terminal = emitted_terminal or "failed_validation" in chunk
                self._append_chunk(user_id=user_id, thread_id=thread_id, chunk=chunk)
            if not interrupted and not emitted_terminal:
                self._append_done(thread_id, status="completed")
        except Exception as exc:
            logger.exception("agent_thread_resume_failed", extra={"thread_id": thread_id, "user_id": user_id})
            self._append_error(thread_id, code="AGENT_RESUME_FAILED", message=str(exc))

    def _build_graph(self, *, planner_provider: str | None, planner_model: str | None):
        planner = self._planner_client_factory(provider=planner_provider, model=planner_model)
        return self._graph_factory(planner=planner, checkpointer=_global_checkpointer)

    async def stream_thread_events(
        self,
        *,
        user_id: str,
        thread_id: str,
        last_event_id: str | None = None,
    ) -> AsyncIterator[str]:
        subscriber = _EventSubscriber(loop=asyncio.get_running_loop(), queue=asyncio.Queue())
        snapshot_event: str | None = None
        with self._lock:
            events = list(self._events.get(thread_id, []))
            if not events and last_event_id:
                snapshot_event = format_sse_event("snapshot_required", {"reason": "event_buffer_empty"})
            elif last_event_id:
                replay_start = self._find_event_index(events, last_event_id)
                if replay_start is None:
                    snapshot_event = format_sse_event("snapshot_required", {"reason": "last_event_id_not_found"})
                else:
                    events = events[replay_start + 1 :]
                    self._subscribers[thread_id].append(subscriber)
            else:
                self._subscribers[thread_id].append(subscriber)
        if snapshot_event:
            yield snapshot_event
            return
        try:
            for event in events:
                yield event
                if _is_terminal_event(event):
                    return
            while True:
                event = await subscriber.queue.get()
                yield event
                if _is_terminal_event(event):
                    return
        finally:
            with self._lock:
                subscribers = self._subscribers.get(thread_id, [])
                if subscriber in subscribers:
                    subscribers.remove(subscriber)

    def _append_chunk(self, *, user_id: str, thread_id: str, chunk: dict[str, Any]) -> None:
        if "__interrupt__" in chunk:
            interrupt_payload = chunk["__interrupt__"][0].value
            try:
                self._persist_interrupt_sync(
                    user_id=user_id,
                    thread_id=thread_id,
                    interrupt_payload=interrupt_payload,
                )
            except Exception as exc:
                logger.exception("agent_thread_interrupt_persist_failed", extra={"thread_id": thread_id, "user_id": user_id})
                self._append_error(thread_id, code="AGENT_INTERRUPT_PERSIST_FAILED", message=str(exc))
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

    def _append_done(self, thread_id: str, *, status: str) -> None:
        self._append_event(
            thread_id,
            "done",
            {
                "state_version": next(self._counter),
                "status": status,
            },
        )

    def _append_event(self, thread_id: str, event: str, data: dict[str, Any]) -> None:
        event_id = f"evt_{next(self._counter):08d}"
        formatted_event = format_sse_event(event, data, event_id=event_id)
        with self._lock:
            self._events[thread_id].append(formatted_event)
            subscribers = list(self._subscribers.get(thread_id, []))
        for subscriber in subscribers:
            try:
                subscriber.loop.call_soon_threadsafe(subscriber.queue.put_nowait, formatted_event)
            except RuntimeError:
                logger.debug("sse_subscriber_loop_closed", extra={"thread_id": thread_id})

    def _persist_interrupt_sync(
        self,
        *,
        user_id: str,
        thread_id: str,
        interrupt_payload: dict[str, Any],
    ) -> None:
        asyncio.run(
            self._persist_interrupt(
                user_id=user_id,
                thread_id=thread_id,
                interrupt_payload=interrupt_payload,
            )
        )

    async def _persist_interrupt(
        self,
        *,
        user_id: str,
        thread_id: str,
        interrupt_payload: dict[str, Any],
    ) -> None:
        from app.db.session import async_session

        now = datetime.now(timezone.utc)
        awaitable_session = async_session()
        async with awaitable_session as session:
            await session.execute(
                update(AgentThread)
                .where(
                    AgentThread.user_id == UUID(user_id),
                    AgentThread.thread_id == thread_id,
                )
                .values(
                    status="awaiting_confirmation",
                    current_node="human_review",
                    task_tree=interrupt_payload.get("task_tree"),
                    interrupt_payload=interrupt_payload,
                    interrupted_at=now,
                    updated_at=now,
                )
            )
            await session.commit()

    @staticmethod
    def _find_event_index(events: list[str], event_id: str) -> int | None:
        for index, event in enumerate(events):
            if _extract_event_id(event) == event_id:
                return index
        return None


def _extract_event_id(event: str) -> str | None:
    for line in event.splitlines():
        if line.startswith("id: "):
            return line[4:]
    return None


def _extract_event_type(event: str) -> str | None:
    for line in event.splitlines():
        if line.startswith("event: "):
            return line[7:]
    return None


def _is_terminal_event(event: str) -> bool:
    return _extract_event_type(event) in {"done", "error"}


agent_runtime = AgentRuntime()
