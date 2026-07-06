import asyncio
import itertools
import logging
import threading
from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, AsyncIterator, Callable, Literal
from uuid import UUID

from sqlalchemy import select, update

from app.agents.graph import build_task_graph, create_graph_config, resume_with_human_input
from app.agents.nodes import persist_internal_tasks_node
from app.api.sse import format_sse_event
from app.models.thread import AgentThread
from app.services.checkpoint_service import TenantAwareMemorySaver
from app.services.llm_service import create_planner_client


logger = logging.getLogger(__name__)
_global_checkpointer = TenantAwareMemorySaver()
SAFE_PLANNING_ERROR_MESSAGE = "AI 在规划时遇到了一点小麻烦，正在尝试重新组织，请稍候。"
RunType = Literal["initial", "next_phase"]


@dataclass(frozen=True)
class EventRunKey:
    thread_id: str
    run_type: RunType
    request_id: str


@dataclass
class _EventSubscriber:
    loop: asyncio.AbstractEventLoop
    queue: asyncio.Queue[str | None]


class PhaseGenerationCancelled(Exception):
    """Raised when a cancelled next-phase run attempts a late state transition."""


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
        self._events: dict[EventRunKey, deque[str]] = defaultdict(
            lambda: deque(maxlen=max_events_per_thread)
        )
        self._subscribers: dict[EventRunKey, list[_EventSubscriber]] = defaultdict(list)
        self._active_runs: set[EventRunKey] = set()
        self._cancelled_runs: set[EventRunKey] = set()
        self._planner_selection: dict[str, tuple[str | None, str | None]] = {}
        self._counter = itertools.count(1)
        self._lock = threading.Lock()

    async def run_new_thread(
        self,
        *,
        user_id: str,
        thread_id: str,
        request_id: str,
        intent_text: str,
        selected_provider: str,
        planner_provider: str | None = None,
        planner_model: str | None = None,
        user_timezone: str = "UTC",
    ) -> None:
        with self._lock:
            self._planner_selection[thread_id] = (planner_provider, planner_model)
        await self._run_new_thread(
            user_id=user_id,
            thread_id=thread_id,
            request_id=request_id,
            intent_text=intent_text,
            selected_provider=selected_provider,
            planner_provider=planner_provider,
            planner_model=planner_model,
            user_timezone=user_timezone,
        )

    async def _run_new_thread(
        self,
        *,
        user_id: str,
        thread_id: str,
        request_id: str,
        intent_text: str,
        selected_provider: str,
        planner_provider: str | None,
        planner_model: str | None,
        user_timezone: str,
    ) -> None:
        graph = self._build_graph(planner_provider=planner_provider, planner_model=planner_model)
        config = create_graph_config(
            user_id=user_id,
            thread_id=thread_id,
            run_type="initial",
            request_id=request_id,
        )
        initial_state = {
            "user_id": user_id,
            "thread_id": thread_id,
            "intent_text": intent_text,
            "selected_provider": selected_provider,
            "planner_provider": planner_provider,
            "planner_model": planner_model,
            "planning_mode": "initial",
            "user_timezone": user_timezone,
        }
        try:
            interrupted = False
            emitted_terminal = False
            async for chunk in graph.astream(initial_state, config):
                interrupted = interrupted or "__interrupt__" in chunk
                emitted_terminal = emitted_terminal or "failed_validation" in chunk
                await self._append_chunk(
                    user_id=user_id,
                    thread_id=thread_id,
                    chunk=chunk,
                    run_type="initial",
                    request_id=request_id,
                )
            if not interrupted and not emitted_terminal:
                self._append_done(
                    thread_id,
                    status="completed",
                    run_type="initial",
                    request_id=request_id,
                )
        except Exception:
            logger.exception("agent_thread_run_failed", extra={"thread_id": thread_id, "user_id": user_id})
            self._append_error(
                thread_id,
                code="AGENT_RUN_FAILED",
                message=SAFE_PLANNING_ERROR_MESSAGE,
                run_type="initial",
                request_id=request_id,
            )

    async def run_next_phase(
        self,
        *,
        user_id: str,
        thread_id: str,
        request_id: str,
        intent_text: str,
        committed_task_tree: dict[str, Any],
        current_phase_task_summary: str,
        user_timezone: str = "UTC",
    ) -> None:
        run_key = _event_run_key(
            thread_id=thread_id,
            run_type="next_phase",
            request_id=request_id,
        )
        with self._lock:
            self._active_runs.add(run_key)
        try:
            await self._run_active_next_phase(
                user_id=user_id,
                thread_id=thread_id,
                request_id=request_id,
                intent_text=intent_text,
                committed_task_tree=committed_task_tree,
                current_phase_task_summary=current_phase_task_summary,
                user_timezone=user_timezone,
            )
        finally:
            with self._lock:
                self._active_runs.discard(run_key)
                self._cancelled_runs.discard(run_key)

    async def commit_next_phase(
        self,
        *,
        user_id: str,
        thread_id: str,
        request_id: str,
        task_tree: dict[str, Any],
        user_timezone: str = "UTC",
    ) -> None:
        try:
            await persist_internal_tasks_node(
                {
                    "user_id": user_id,
                    "thread_id": thread_id,
                    "task_tree": task_tree,
                    "planning_mode": "next_phase",
                    "phase_request_id": request_id,
                    "user_timezone": user_timezone,
                }
            )
        except Exception:
            logger.exception(
                "agent_next_phase_commit_failed",
                extra={"thread_id": thread_id, "user_id": user_id, "request_id": request_id},
            )
            try:
                await self._release_phase_failure(
                    user_id=user_id,
                    thread_id=thread_id,
                    request_id=request_id,
                )
            except Exception:
                logger.exception(
                    "agent_next_phase_commit_failure_release_failed",
                    extra={
                        "thread_id": thread_id,
                        "user_id": user_id,
                        "request_id": request_id,
                    },
                )
            self._append_error(
                thread_id,
                code="NEXT_PHASE_COMMIT_FAILED",
                message=SAFE_PLANNING_ERROR_MESSAGE,
                run_type="next_phase",
                request_id=request_id,
            )
            return
        self._append_done(
            thread_id,
            status="completed",
            run_type="next_phase",
            request_id=request_id,
        )

    async def _run_active_next_phase(
        self,
        *,
        user_id: str,
        thread_id: str,
        request_id: str,
        intent_text: str,
        committed_task_tree: dict[str, Any],
        current_phase_task_summary: str,
        user_timezone: str,
    ) -> None:
        run_key = _event_run_key(
            thread_id=thread_id,
            run_type="next_phase",
            request_id=request_id,
        )
        with self._lock:
            self._planner_selection[thread_id] = ("deepseek", None)
            # Reclaim old next_phase EventRunKeys and subscribers for this thread_id
            old_keys = [
                k for k in self._events.keys()
                if k.thread_id == thread_id and k.run_type == "next_phase" and k.request_id != request_id
            ]
            for k in old_keys:
                self._events.pop(k, None)
                self._subscribers.pop(k, None)
        if self._is_run_cancelled(run_key):
            return
        graph = self._build_graph(planner_provider="deepseek", planner_model=None)
        config = create_graph_config(
            user_id=user_id,
            thread_id=thread_id,
            run_type="next_phase",
            request_id=request_id,
        )
        planning_context = committed_task_tree.get("planning_context") or {}
        initial_state = {
            "user_id": user_id,
            "thread_id": thread_id,
            "intent_text": intent_text,
            "intent_profile": {
                "intent_type": planning_context.get("intent_type"),
                "time_horizon": planning_context.get("time_horizon"),
                "confidence_score": 1.0,
            },
            "selected_provider": "native",
            "planning_mode": "next_phase",
            "phase_request_id": request_id,
            "committed_task_tree": committed_task_tree,
            "current_phase_task_summary": current_phase_task_summary,
            "user_timezone": user_timezone,
        }
        try:
            interrupted = False
            emitted_terminal = False
            async for chunk in graph.astream(initial_state, config):
                if self._is_run_cancelled(run_key):
                    return
                interrupted = interrupted or "__interrupt__" in chunk
                emitted_terminal = emitted_terminal or "failed_validation" in chunk
                processed = await self._append_chunk(
                    user_id=user_id,
                    thread_id=thread_id,
                    chunk=chunk,
                    run_type="next_phase",
                    request_id=request_id,
                )
                if not processed or self._is_run_cancelled(run_key):
                    return
            if (
                not interrupted
                and not emitted_terminal
                and not self._is_run_cancelled(run_key)
            ):
                self._append_done(
                    thread_id,
                    status="completed",
                    run_type="next_phase",
                    request_id=request_id,
                )
        except PhaseGenerationCancelled:
            return
        except Exception:
            if self._is_run_cancelled(run_key):
                return
            logger.exception(
                "agent_next_phase_run_failed",
                extra={"thread_id": thread_id, "user_id": user_id, "request_id": request_id},
            )
            try:
                await self._release_phase_failure(
                    user_id=user_id,
                    thread_id=thread_id,
                    request_id=request_id,
                )
            except Exception:
                logger.exception(
                    "agent_next_phase_failure_release_failed",
                    extra={"thread_id": thread_id, "user_id": user_id, "request_id": request_id},
                )
            self._append_error(
                thread_id,
                code="NEXT_PHASE_RUN_FAILED",
                message=SAFE_PLANNING_ERROR_MESSAGE,
                run_type="next_phase",
                request_id=request_id,
            )

    def cancel_run(
        self,
        *,
        thread_id: str,
        run_type: RunType,
        request_id: str,
    ) -> None:
        run_key = _event_run_key(
            thread_id=thread_id,
            run_type=run_type,
            request_id=request_id,
        )
        with self._lock:
            if run_key in self._active_runs:
                self._cancelled_runs.add(run_key)
            subscribers = self._subscribers.pop(run_key, [])
        for subscriber in subscribers:
            try:
                subscriber.loop.call_soon_threadsafe(
                    subscriber.queue.put_nowait,
                    None,
                )
            except RuntimeError:
                logger.debug(
                    "sse_subscriber_loop_closed_during_cancellation",
                    extra={
                        "thread_id": thread_id,
                        "run_type": run_type,
                        "request_id": request_id,
                    },
                )

    async def resume_thread(
        self,
        *,
        user_id: str,
        thread_id: str,
        decision: dict[str, Any],
        run_type: RunType = "initial",
        request_id: str,
        user_timezone: str = "UTC",
    ) -> None:
        with self._lock:
            planner_provider, planner_model = self._planner_selection.get(thread_id, (None, None))
        await self._resume_thread(
            user_id=user_id,
            thread_id=thread_id,
            decision=decision,
            planner_provider=planner_provider,
            planner_model=planner_model,
            run_type=run_type,
            request_id=request_id,
        )

    async def _resume_thread(
        self,
        *,
        user_id: str,
        thread_id: str,
        decision: dict[str, Any],
        planner_provider: str | None,
        planner_model: str | None,
        run_type: RunType,
        request_id: str,
    ) -> None:
        graph = self._build_graph(planner_provider=planner_provider, planner_model=planner_model)
        config = create_graph_config(
            user_id=user_id,
            thread_id=thread_id,
            run_type=run_type,
            request_id=request_id,
        )
        command = resume_with_human_input(**decision)
        try:
            interrupted = False
            emitted_terminal = False
            async for chunk in graph.astream(command, config):
                interrupted = interrupted or "__interrupt__" in chunk
                emitted_terminal = emitted_terminal or "failed_validation" in chunk
                await self._append_chunk(
                    user_id=user_id,
                    thread_id=thread_id,
                    chunk=chunk,
                    run_type=run_type,
                    request_id=request_id,
                )
            if not interrupted and not emitted_terminal:
                self._append_done(
                    thread_id,
                    status="completed",
                    run_type=run_type,
                    request_id=request_id,
                )
        except Exception:
            logger.exception("agent_thread_resume_failed", extra={"thread_id": thread_id, "user_id": user_id})
            self._append_error(
                thread_id,
                code="AGENT_RESUME_FAILED",
                message=SAFE_PLANNING_ERROR_MESSAGE,
                run_type=run_type,
                request_id=request_id,
            )

    def _build_graph(self, *, planner_provider: str | None, planner_model: str | None):
        planner = self._planner_client_factory(provider=planner_provider, model=planner_model)
        return self._graph_factory(planner=planner, checkpointer=_global_checkpointer)

    async def stream_thread_events(
        self,
        *,
        user_id: str,
        thread_id: str,
        last_event_id: str | None = None,
        run_type: RunType = "initial",
        request_id: str,
    ) -> AsyncIterator[str]:
        run_key = _event_run_key(
            thread_id=thread_id,
            run_type=run_type,
            request_id=request_id,
        )
        subscriber = _EventSubscriber(loop=asyncio.get_running_loop(), queue=asyncio.Queue())
        snapshot_event: str | None = None
        cancelled = False
        with self._lock:
            cancelled = run_key in self._cancelled_runs
            events = [] if cancelled else list(self._events.get(run_key, []))
            if cancelled:
                pass
            elif not events and last_event_id:
                snapshot_event = self._format_run_event(
                    run_key,
                    "snapshot_required",
                    {"reason": "event_buffer_empty"},
                )
            elif last_event_id:
                replay_start = self._find_event_index(events, last_event_id)
                if replay_start is None:
                    snapshot_event = self._format_run_event(
                        run_key,
                        "snapshot_required",
                        {"reason": "last_event_id_not_found"},
                    )
                else:
                    events = events[replay_start + 1 :]
                    self._subscribers[run_key].append(subscriber)
            else:
                self._subscribers[run_key].append(subscriber)
        if cancelled:
            return
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
                if event is None:
                    return
                yield event
                if _is_terminal_event(event):
                    return
        finally:
            with self._lock:
                subscribers = self._subscribers.get(run_key, [])
                if subscriber in subscribers:
                    subscribers.remove(subscriber)

    async def _append_chunk(
        self,
        *,
        user_id: str,
        thread_id: str,
        chunk: dict[str, Any],
        run_type: RunType,
        request_id: str,
    ) -> bool:
        run_key = _event_run_key(
            thread_id=thread_id,
            run_type=run_type,
            request_id=request_id,
        )
        if self._is_run_cancelled(run_key):
            return False
        if "__interrupt__" in chunk:
            interrupt_payload = chunk["__interrupt__"][0].value
            try:
                await self._persist_interrupt(
                    user_id=user_id,
                    thread_id=thread_id,
                    interrupt_payload=interrupt_payload,
                    run_type=run_type,
                    request_id=request_id,
                )
            except PhaseGenerationCancelled:
                return False
            except Exception:
                logger.exception("agent_thread_interrupt_persist_failed", extra={"thread_id": thread_id, "user_id": user_id})
                if interrupt_payload.get("planning_mode") == "next_phase":
                    try:
                        await self._release_phase_failure(
                            user_id=user_id,
                            thread_id=thread_id,
                            request_id=str(interrupt_payload.get("phase_request_id", "")),
                        )
                    except Exception:
                        logger.exception(
                            "agent_next_phase_interrupt_release_failed",
                            extra={"thread_id": thread_id, "user_id": user_id},
                        )
                self._append_error(
                    thread_id,
                    code="AGENT_INTERRUPT_PERSIST_FAILED",
                    message=SAFE_PLANNING_ERROR_MESSAGE,
                    run_type=run_type,
                    request_id=request_id,
                )
                return False
            return self._append_event(
                thread_id,
                "plan_ready",
                {
                    "state_version": next(self._counter),
                    "thread_id": thread_id,
                    "task_tree": interrupt_payload.get("task_tree"),
                },
                run_type=run_type,
                request_id=request_id,
            )
        if "failed_validation" in chunk:
            error = chunk["failed_validation"].get("error", {})
            logger.warning(
                "agent_validation_failed",
                extra={
                    "thread_id": thread_id,
                    "user_id": user_id,
                    "error_code": error.get("code", "TASK_TREE_VALIDATION_FAILED"),
                    "raw_message": error.get("message"),
                },
            )
            return self._append_error(
                thread_id,
                code=error.get("code", "TASK_TREE_VALIDATION_FAILED"),
                message=_safe_sse_error_message(error.get("message")),
                run_type=run_type,
                request_id=request_id,
            )
        for node_name, payload in chunk.items():
            if isinstance(payload, dict) and payload.get("reasoning_events"):
                for event in payload["reasoning_events"]:
                    if not self._append_event(
                        thread_id,
                        "reasoning",
                        {
                            "state_version": next(self._counter),
                            **event,
                        },
                        run_type=run_type,
                        request_id=request_id,
                    ):
                        return False
            else:
                if not self._append_event(
                    thread_id,
                    "checkpoint",
                    {
                        "state_version": next(self._counter),
                        "node": node_name,
                    },
                    run_type=run_type,
                    request_id=request_id,
                ):
                    return False
        return True

    def _append_error(
        self,
        thread_id: str,
        *,
        code: str,
        message: str,
        run_type: RunType,
        request_id: str,
    ) -> bool:
        return self._append_event(
            thread_id,
            "agent_error",
            {
                "state_version": next(self._counter),
                "code": code,
                "message": message,
            },
            run_type=run_type,
            request_id=request_id,
        )

    def _append_done(
        self,
        thread_id: str,
        *,
        status: str,
        run_type: RunType,
        request_id: str,
    ) -> bool:
        return self._append_event(
            thread_id,
            "done",
            {
                "state_version": next(self._counter),
                "status": status,
            },
            run_type=run_type,
            request_id=request_id,
        )

    def _append_event(
        self,
        thread_id: str,
        event: str,
        data: dict[str, Any],
        *,
        run_type: RunType,
        request_id: str,
    ) -> bool:
        run_key = _event_run_key(
            thread_id=thread_id,
            run_type=run_type,
            request_id=request_id,
        )
        with self._lock:
            if run_key in self._cancelled_runs:
                return False
            event_sequence = next(self._counter)
            event_id = f"evt_{event_sequence:08d}"
            formatted_event = self._format_run_event(
                run_key,
                event,
                data,
                event_id=event_id,
                default_state_version=event_sequence,
            )
            self._events[run_key].append(formatted_event)
            subscribers = list(self._subscribers.get(run_key, []))
        for subscriber in subscribers:
            try:
                subscriber.loop.call_soon_threadsafe(subscriber.queue.put_nowait, formatted_event)
            except RuntimeError:
                logger.debug(
                    "sse_subscriber_loop_closed",
                    extra={
                        "thread_id": thread_id,
                        "run_type": run_key.run_type,
                        "request_id": run_key.request_id,
                    },
                )
        return True

    def _format_run_event(
        self,
        run_key: EventRunKey,
        event: str,
        data: dict[str, Any],
        *,
        event_id: str | None = None,
        default_state_version: int | None = None,
    ) -> str:
        payload = dict(data)
        payload.setdefault(
            "state_version",
            default_state_version if default_state_version is not None else next(self._counter),
        )
        payload.update(
            thread_id=run_key.thread_id,
            run_type=run_key.run_type,
            request_id=run_key.request_id,
        )
        return format_sse_event(event, payload, event_id=event_id)

    async def _persist_interrupt(
        self,
        *,
        user_id: str,
        thread_id: str,
        interrupt_payload: dict[str, Any],
        run_type: RunType,
        request_id: str,
    ) -> None:
        from app.db.session import async_session

        now = datetime.now(timezone.utc)
        awaitable_session = async_session()
        async with awaitable_session as session:
            update_values: dict[str, Any] = {
                "status": "awaiting_confirmation",
                "current_node": "human_review",
                "interrupted_at": now,
                "updated_at": now,
            }
            if interrupt_payload.get("planning_mode") == "next_phase":
                interrupt_request_id = str(
                    interrupt_payload.get("phase_request_id") or ""
                )
                if interrupt_request_id != request_id:
                    raise PhaseGenerationCancelled
                result = await session.execute(
                    select(AgentThread)
                    .where(
                        AgentThread.user_id == UUID(user_id),
                        AgentThread.thread_id == thread_id,
                    )
                    .with_for_update()
                )
                thread = result.scalar_one_or_none()
                if thread is None:
                    raise PhaseGenerationCancelled
                existing_payload = (
                    thread.interrupt_payload
                    if isinstance(thread.interrupt_payload, dict)
                    else {}
                )
                history = dict(existing_payload.get("history") or {})
                history_entry = history.get(request_id)
                if (
                    existing_payload.get("type") != "phase_generation_state"
                    or str(existing_payload.get("request_id") or "") != request_id
                    or existing_payload.get("status") != "running"
                    or (
                        isinstance(history_entry, dict)
                        and history_entry.get("status") == "cancelled"
                    )
                ):
                    raise PhaseGenerationCancelled
                thread.status = "awaiting_confirmation"
                thread.current_node = "human_review"
                thread.interrupted_at = now
                thread.updated_at = now
                thread.interrupt_payload = {
                    "type": "next_phase_review",
                    "request_id": request_id,
                    "status": "awaiting_confirmation",
                    "base_phase_id": existing_payload.get("base_phase_id"),
                    "task_tree": interrupt_payload.get("task_tree"),
                    "history": history,
                }
                await session.commit()
                return
            else:
                update_values.update(
                    task_tree=interrupt_payload.get("task_tree"),
                    interrupt_payload={
                        **interrupt_payload,
                        "request_id": request_id,
                        "run_type": run_type,
                    },
                )
            await session.execute(
                update(AgentThread)
                .where(
                    AgentThread.user_id == UUID(user_id),
                    AgentThread.thread_id == thread_id,
                )
                .values(**update_values)
            )
            await session.commit()

    def _is_run_cancelled(self, run_key: EventRunKey) -> bool:
        with self._lock:
            return run_key in self._cancelled_runs

    async def _release_phase_failure(
        self,
        *,
        user_id: str,
        thread_id: str,
        request_id: str,
    ) -> None:
        from app.db.session import async_session

        now = datetime.now(timezone.utc)
        async with async_session() as session:
            result = await session.execute(
                select(AgentThread)
                .where(
                    AgentThread.user_id == UUID(user_id),
                    AgentThread.thread_id == thread_id,
                )
                .with_for_update()
            )
            thread = result.scalar_one_or_none()
            current_payload = (
                thread.interrupt_payload
                if thread is not None and isinstance(thread.interrupt_payload, dict)
                else {}
            )
            history_entry = dict(current_payload.get("history") or {}).get(request_id)
            is_generation_failure = (
                current_payload.get("type") == "phase_generation_state"
                and current_payload.get("status") == "running"
            )
            is_commit_failure = (
                current_payload.get("type") == "next_phase_review"
                and current_payload.get("status") == "confirming"
            )
            if (
                thread is None
                or str(current_payload.get("request_id") or "") != request_id
                or not (is_generation_failure or is_commit_failure)
                or (
                    isinstance(history_entry, dict)
                    and history_entry.get("status") == "cancelled"
                )
            ):
                return
            failed_payload: dict[str, Any] = {
                "type": "phase_generation_state",
                "request_id": request_id,
                "status": "failed",
                "history": dict(current_payload.get("history") or {}),
            }
            if isinstance(current_payload.get("base_phase_id"), str):
                failed_payload["base_phase_id"] = current_payload["base_phase_id"]
            await session.execute(
                update(AgentThread)
                .where(
                    AgentThread.user_id == UUID(user_id),
                    AgentThread.thread_id == thread_id,
                )
                .values(
                    status="succeeded",
                    current_node="next_phase_failed",
                    lease_owner=None,
                    lease_expires_at=None,
                    interrupt_payload=failed_payload,
                    error_code="NEXT_PHASE_RUN_FAILED",
                    error_message=SAFE_PLANNING_ERROR_MESSAGE,
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


def _event_run_key(
    *,
    thread_id: str,
    run_type: RunType,
    request_id: str,
) -> EventRunKey:
    if not request_id:
        raise ValueError("request_id is required for event streams")
    return EventRunKey(
        thread_id=thread_id,
        run_type=run_type,
        request_id=request_id,
    )


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
    return _extract_event_type(event) in {"done", "agent_error"}


def _safe_sse_error_message(message: Any) -> str:
    if not isinstance(message, str) or not message.strip():
        return SAFE_PLANNING_ERROR_MESSAGE
    if "错误代码:" in message:
        return message
    lowered = message.lower()
    sensitive_markers = (
        "validation error",
        "traceback",
        "estimated_minutes",
        "input should",
        "pydantic",
        "sql",
        "database",
        "planning_context",
        "intentprofile",
        "committed_task_tree",
        "current_phase",
        "roadmap",
        "phase planning",
        "next_phase requires",
        "must match intentprofile",
    )
    if any(marker in lowered for marker in sensitive_markers):
        return SAFE_PLANNING_ERROR_MESSAGE
    return message


agent_runtime = AgentRuntime()
