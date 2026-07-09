import asyncio
import contextlib
import itertools
import logging
import threading
from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import date, datetime, timezone
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
RunType = Literal["initial", "next_phase", "refine"]
DEFAULT_HEARTBEAT_INTERVAL_SECONDS = 6.0

STAGE_LABELS: dict[str, str] = {
    "run_started": "正在理解你的目标",
    "intent_profile_started": "正在判断目标类型",
    "intent_profile_completed": "已识别目标类型",
    "strategy_selected": "正在选择规划策略",
    "planning_started": "正在生成任务",
    "validation_started": "正在检查任务是否可执行",
    "repair_started": "正在根据校验结果修复计划",
    "persistence_started": "正在保存计划",
    "still_running": "还在处理中，请稍候",
    "sync_status": "正在同步计划状态",
    "sync_complete": "已完成计划保存",
    "plan_ready": "计划已生成，请查阅",
    "done": "已完成规划",
    "agent_error": "生成遇到问题",
    "snapshot_required": "需要重新对齐计划状态",
}


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
        heartbeat_interval_seconds: float = DEFAULT_HEARTBEAT_INTERVAL_SECONDS,
    ) -> None:
        self._graph_factory = graph_factory or build_task_graph
        self._planner_client_factory = planner_client_factory or create_planner_client
        self._heartbeat_interval_seconds = heartbeat_interval_seconds
        self._events: dict[EventRunKey, deque[str]] = defaultdict(
            lambda: deque(maxlen=max_events_per_thread)
        )
        self._subscribers: dict[EventRunKey, list[_EventSubscriber]] = defaultdict(list)
        self._active_runs: set[EventRunKey] = set()
        self._cancelled_runs: set[EventRunKey] = set()
        self._run_sequences: dict[EventRunKey, int] = defaultdict(int)
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
        run_key = _event_run_key(
            thread_id=thread_id,
            run_type="initial",
            request_id=request_id,
        )
        self._activate_run(run_key)
        heartbeat_stop, heartbeat_task = self._start_heartbeat(run_key)
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
            self._append_stage_event(
                run_key,
                "run_started",
                {"planning_mode": "initial"},
            )
            self._append_stage_event(run_key, "intent_profile_started")
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
        finally:
            await self._stop_heartbeat(heartbeat_stop, heartbeat_task)
            self._deactivate_run(run_key)

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
        self._activate_run(run_key)
        heartbeat_stop, heartbeat_task = self._start_heartbeat(run_key)
        try:
            self._append_stage_event(
                run_key,
                "run_started",
                {"planning_mode": "next_phase"},
            )
            self._append_stage_event(run_key, "planning_started")
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
            await self._stop_heartbeat(heartbeat_stop, heartbeat_task)
            self._deactivate_run(run_key)

    async def commit_next_phase(
        self,
        *,
        user_id: str,
        thread_id: str,
        request_id: str,
        task_tree: dict[str, Any],
        user_timezone: str = "UTC",
    ) -> None:
        run_key = _event_run_key(
            thread_id=thread_id,
            run_type="next_phase",
            request_id=request_id,
        )
        try:
            self._append_stage_event(run_key, "persistence_started")
            self._append_stage_event(run_key, "sync_status")
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
        self._append_stage_event(run_key, "sync_complete")
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
            self._events.pop(run_key, None)
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
        run_key = _event_run_key(
            thread_id=thread_id,
            run_type=run_type,
            request_id=request_id,
        )
        self._activate_run(run_key)
        heartbeat_stop, heartbeat_task = self._start_heartbeat(run_key)
        graph = self._build_graph(planner_provider=planner_provider, planner_model=planner_model)
        config = create_graph_config(
            user_id=user_id,
            thread_id=thread_id,
            run_type=run_type,
            request_id=request_id,
        )
        command = resume_with_human_input(**decision)
        try:
            action = decision.get("action")
            self._append_stage_event(
                run_key,
                "run_started",
                {
                    "planning_mode": run_type,
                    "action": action,
                },
            )
            if action == "refine":
                self._append_stage_event(run_key, "planning_started")
            elif action == "edit":
                self._append_stage_event(run_key, "validation_started")
            elif action == "approve":
                self._append_stage_event(run_key, "persistence_started")
                self._append_stage_event(run_key, "sync_status")
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
        finally:
            await self._stop_heartbeat(heartbeat_stop, heartbeat_task)
            self._deactivate_run(run_key)

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
                snapshot_event = self._format_transient_event(
                    run_key,
                    "snapshot_required",
                    {"reason": "event_buffer_empty"},
                )
            elif last_event_id:
                replay_start = self._find_event_index(events, last_event_id)
                if replay_start is None:
                    snapshot_event = self._format_transient_event(
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
            if not self._append_node_stage(run_key, node_name, payload):
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
            seq = self._next_run_sequence(run_key)
            state_version = next(self._counter)
            formatted_event = self._format_run_event(
                run_key,
                event,
                data,
                seq=seq,
                state_version=state_version,
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

    def _append_stage_event(
        self,
        run_key: EventRunKey,
        event: str,
        data: dict[str, Any] | None = None,
    ) -> bool:
        payload = {
            "stage": event,
            "label": STAGE_LABELS.get(event, event),
        }
        if data:
            payload.update(data)
        return self._append_event(
            run_key.thread_id,
            event,
            payload,
            run_type=run_key.run_type,
            request_id=run_key.request_id,
        )

    def _append_node_stage(
        self,
        run_key: EventRunKey,
        node_name: str,
        payload: Any,
    ) -> bool:
        payload_dict = payload if isinstance(payload, dict) else {}
        if node_name == "intent_profiler":
            intent_profile = payload_dict.get("intent_profile") or {}
            intent_type = (
                intent_profile.get("intent_type")
                if isinstance(intent_profile, dict)
                else None
            )
            if not self._append_stage_event(
                run_key,
                "intent_profile_completed",
                {"intent_type": intent_type} if intent_type else None,
            ):
                return False
            if not self._append_stage_event(
                run_key,
                "strategy_selected",
                {"strategy": intent_type} if intent_type else None,
            ):
                return False
            return self._append_stage_event(run_key, "planning_started")
        if node_name == "planner":
            return self._append_stage_event(run_key, "validation_started")
        if node_name == "validator":
            if payload_dict.get("validation_status") == "needs_replan":
                errors = payload_dict.get("validation_errors")
                error_codes = _extract_validation_error_codes(errors)
                data = {"error_codes": error_codes} if error_codes else None
                return self._append_stage_event(run_key, "repair_started", data)
            return True
        if node_name == "persist_tasks":
            return self._append_stage_event(run_key, "sync_complete")
        return True

    def _format_run_event(
        self,
        run_key: EventRunKey,
        event: str,
        data: dict[str, Any],
        *,
        seq: int,
        state_version: int,
    ) -> str:
        event_id = _format_run_event_id(run_key, seq)
        payload = dict(data)
        payload.setdefault("state_version", state_version)
        envelope = {
            "event_id": event_id,
            "thread_id": run_key.thread_id,
            "request_id": run_key.request_id,
            "run_type": run_key.run_type,
            "event_type": event,
            "seq": seq,
            "created_at": _utc_now_iso(),
            "payload": payload,
        }
        return format_sse_event(
            event,
            _json_safe(envelope),
            event_id=event_id,
        )

    def _format_transient_event(
        self,
        run_key: EventRunKey,
        event: str,
        data: dict[str, Any],
    ) -> str:
        seq = self._next_run_sequence(run_key)
        state_version = next(self._counter)
        return self._format_run_event(
            run_key,
            event,
            {
                "stage": event,
                "label": STAGE_LABELS.get(event, event),
                **data,
            },
            seq=seq,
            state_version=state_version,
        )

    def _next_run_sequence(self, run_key: EventRunKey) -> int:
        self._run_sequences[run_key] += 1
        return self._run_sequences[run_key]

    def _activate_run(self, run_key: EventRunKey) -> None:
        with self._lock:
            self._active_runs.add(run_key)

    def _deactivate_run(self, run_key: EventRunKey) -> None:
        with self._lock:
            self._active_runs.discard(run_key)
            self._cancelled_runs.discard(run_key)

    def _start_heartbeat(
        self,
        run_key: EventRunKey,
    ) -> tuple[asyncio.Event, asyncio.Task[None]]:
        stop_event = asyncio.Event()

        async def heartbeat_loop() -> None:
            try:
                while True:
                    try:
                        await asyncio.wait_for(
                            stop_event.wait(),
                            timeout=self._heartbeat_interval_seconds,
                        )
                        return
                    except asyncio.TimeoutError:
                        if self._is_run_cancelled(run_key):
                            return
                        if not self._append_stage_event(run_key, "still_running"):
                            return
            except asyncio.CancelledError:
                return

        return stop_event, asyncio.create_task(heartbeat_loop())

    async def _stop_heartbeat(
        self,
        stop_event: asyncio.Event,
        heartbeat_task: asyncio.Task[None],
    ) -> None:
        stop_event.set()
        heartbeat_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await heartbeat_task

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


def _format_run_event_id(run_key: EventRunKey, seq: int) -> str:
    return f"{run_key.thread_id}:{run_key.run_type}:{run_key.request_id}:{seq:06d}"


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(item) for item in value]
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, UUID):
        return str(value)
    if hasattr(value, "model_dump"):
        return _json_safe(value.model_dump(mode="json"))
    return value


def _extract_validation_error_codes(errors: Any) -> list[str]:
    if not isinstance(errors, list):
        return []
    codes: list[str] = []
    for error in errors:
        if isinstance(error, str) and "错误代码:" in error:
            code = error.split("错误代码:", 1)[1].splitlines()[0].strip()
            if code:
                codes.append(code)
        elif isinstance(error, dict) and isinstance(error.get("code"), str):
            codes.append(error["code"])
    return codes


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
