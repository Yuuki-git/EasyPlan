from __future__ import annotations

import asyncio
import contextlib
import itertools
import logging
import threading
from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, AsyncIterator, Callable
from uuid import UUID, uuid4

from app.api.sse import format_sse_event
from app.db.session import async_session
from app.services.llm_service import DeepSeekTaskAssistClient
from app.services.task_assist import (
    TASK_ASSIST_SAFE_PROVIDER_MESSAGE,
    TaskAssistError,
    TaskAssistRepository,
    TaskAssistService,
)


logger = logging.getLogger(__name__)
TERMINAL_EVENTS = {"done", "agent_error"}


@dataclass(frozen=True)
class TaskAssistRunKey:
    thread_id: str
    request_id: str


@dataclass
class _Subscriber:
    queue: asyncio.Queue[str]
    loop: asyncio.AbstractEventLoop


class TaskAssistRuntime:
    def __init__(
        self,
        *,
        session_factory: Callable[[], Any] = async_session,
        proposal_client_factory: Callable[[], Any] = DeepSeekTaskAssistClient,
        heartbeat_interval_seconds: float = 6.0,
        max_events_per_run: int = 100,
        max_retained_terminal_runs: int = 500,
    ) -> None:
        self._session_factory = session_factory
        self._proposal_client_factory = proposal_client_factory
        self._heartbeat_interval_seconds = heartbeat_interval_seconds
        self._events: dict[TaskAssistRunKey, deque[str]] = defaultdict(
            lambda: deque(maxlen=max_events_per_run)
        )
        self._subscribers: dict[TaskAssistRunKey, list[_Subscriber]] = defaultdict(list)
        self._active_runs: set[TaskAssistRunKey] = set()
        self._cancelled_runs: set[TaskAssistRunKey] = set()
        self._cancelled_order: deque[TaskAssistRunKey] = deque()
        self._terminal_order: deque[TaskAssistRunKey] = deque()
        self._max_retained_terminal_runs = max(1, max_retained_terminal_runs)
        self._sequences: dict[TaskAssistRunKey, int] = defaultdict(int)
        self._state_versions = itertools.count(1)
        self._lease_owner = f"task-assist:{uuid4()}"
        self._lock = threading.Lock()

    @property
    def lease_owner(self) -> str:
        return self._lease_owner

    async def run(
        self,
        *,
        user_id: UUID,
        task_id: UUID,
        thread_id: str,
        request_id: UUID,
    ) -> None:
        key = TaskAssistRunKey(thread_id=thread_id, request_id=str(request_id))
        with self._lock:
            if key in self._active_runs:
                return
            self._active_runs.add(key)
            self._cancelled_runs.discard(key)
        stop_heartbeat = asyncio.Event()
        heartbeat_task: asyncio.Task[None] | None = None
        try:
            self._append(key, "run_started", {"stage": "queued"})
            async with self._session_factory() as session:
                repository = TaskAssistRepository(session)
                run = await repository.get_owned(
                    user_id=user_id,
                    task_id=task_id,
                    request_id=request_id,
                )
                if run is None or run.status != "running":
                    return
                if not await repository.claim_lease(
                    run,
                    lease_owner=self._lease_owner,
                ):
                    return
                await repository.mark_stage(
                    run,
                    "context_ready",
                    lease_owner=self._lease_owner,
                )
                if self._is_cancelled(key):
                    return
                self._append(key, "task_context_ready", {"stage": "context_ready"})
                await repository.mark_stage(
                    run,
                    "generating",
                    lease_owner=self._lease_owner,
                )
                self._append(
                    key,
                    "assist_generation_started",
                    {"stage": "generating", "mode": run.mode},
                )
                heartbeat_task = asyncio.create_task(
                    self._heartbeat(
                        key=key,
                        stop_event=stop_heartbeat,
                        user_id=user_id,
                        task_id=task_id,
                        request_id=request_id,
                    )
                )
                service = TaskAssistService(
                    session,
                    proposal_client=self._proposal_client_factory(),
                )
                proposal = await service.generate_proposal(user_id=user_id, run=run)
                if self._is_cancelled(key):
                    return
                await repository.mark_stage(
                    run,
                    "validating",
                    lease_owner=self._lease_owner,
                )
                self._append(
                    key,
                    "assist_validation_started",
                    {"stage": "validating"},
                )
                if not await repository.save_proposal(
                    run,
                    proposal,
                    lease_owner=self._lease_owner,
                ):
                    return
                self._append(
                    key,
                    "assist_ready",
                    {
                        "stage": "ready",
                        "proposal": proposal.model_dump(mode="json"),
                    },
                )
                self._append(key, "done", {"status": "ready"})
        except TaskAssistError as exc:
            logger.warning(
                "task_assist_generation_rejected",
                extra={"code": exc.code, "task_id": str(task_id), "request_id": str(request_id)},
            )
            await self._persist_failure(
                user_id=user_id,
                task_id=task_id,
                request_id=request_id,
                code=exc.code,
                message=exc.message,
                lease_owner=self._lease_owner,
            )
            self._append_error(key, code=exc.code, message=exc.message)
        except Exception:
            logger.exception(
                "task_assist_generation_failed",
                extra={"task_id": str(task_id), "request_id": str(request_id)},
            )
            await self._persist_failure(
                user_id=user_id,
                task_id=task_id,
                request_id=request_id,
                code="TASK_ASSIST_PROVIDER_FAILED",
                message=TASK_ASSIST_SAFE_PROVIDER_MESSAGE,
                lease_owner=self._lease_owner,
            )
            self._append_error(
                key,
                code="TASK_ASSIST_PROVIDER_FAILED",
                message=TASK_ASSIST_SAFE_PROVIDER_MESSAGE,
            )
        finally:
            stop_heartbeat.set()
            if heartbeat_task is not None:
                heartbeat_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await heartbeat_task
            with self._lock:
                self._active_runs.discard(key)
                self._cancelled_runs.discard(key)
                with contextlib.suppress(ValueError):
                    self._cancelled_order.remove(key)

    async def cancel(self, *, thread_id: str, request_id: UUID) -> None:
        key = TaskAssistRunKey(thread_id=thread_id, request_id=str(request_id))
        with self._lock:
            if any(
                _event_type(event) == "done" and '"cancelled"' in event
                for event in self._events.get(key, ())
            ):
                return
            self._cancelled_runs.add(key)
            self._cancelled_order.append(key)
            while len(self._cancelled_order) > self._max_retained_terminal_runs:
                stale = self._cancelled_order.popleft()
                if stale not in self._active_runs:
                    self._cancelled_runs.discard(stale)
        self._append(key, "done", {"status": "cancelled"}, allow_cancelled=True)

    def restore_from_snapshot(self, run: Any) -> None:
        key = TaskAssistRunKey(thread_id=run.thread_id, request_id=str(run.request_id))
        with self._lock:
            history = list(self._events.get(key, ()))
            if any(_event_type(event) in TERMINAL_EVENTS for event in history):
                return
        if run.status == "ready" and isinstance(run.proposal, dict):
            self._append(
                key,
                "assist_ready",
                {"stage": "ready", "proposal": run.proposal},
            )
            self._append(key, "done", {"status": "ready"})
        elif run.status == "failed":
            self._append_error(
                key,
                code=run.error_code or "TASK_ASSIST_PROVIDER_FAILED",
                message=run.error_message or TASK_ASSIST_SAFE_PROVIDER_MESSAGE,
            )
        elif run.status in {"applied", "cancelled", "expired"}:
            self._append(key, "done", {"status": run.status}, allow_cancelled=True)
        else:
            if not history:
                self._append(key, "run_started", {"stage": run.stage or "queued"})
                self._append(key, "still_running", {"stage": run.stage or "queued"})

    async def stream(
        self,
        *,
        thread_id: str,
        request_id: UUID,
        last_event_id: str | None = None,
        user_id: UUID | None = None,
        task_id: UUID | None = None,
        durable_poll_interval_seconds: float = 2.0,
    ) -> AsyncIterator[str]:
        key = TaskAssistRunKey(thread_id=thread_id, request_id=str(request_id))
        queue: asyncio.Queue[str] = asyncio.Queue()
        subscriber = _Subscriber(queue=queue, loop=asyncio.get_running_loop())
        with self._lock:
            history = list(self._events.get(key, ()))
            self._subscribers[key].append(subscriber)
        try:
            start = _history_start(history, last_event_id)
            for event in history[start:]:
                yield event
                if _event_type(event) in TERMINAL_EVENTS:
                    return
            while True:
                if user_id is None or task_id is None:
                    event = await queue.get()
                else:
                    try:
                        event = await asyncio.wait_for(
                            queue.get(),
                            timeout=durable_poll_interval_seconds,
                        )
                    except asyncio.TimeoutError:
                        await self._recover_durable_snapshot(
                            user_id=user_id,
                            task_id=task_id,
                            request_id=request_id,
                            key=key,
                        )
                        continue
                yield event
                if _event_type(event) in TERMINAL_EVENTS:
                    return
        finally:
            with self._lock:
                subscribers = self._subscribers.get(key, [])
                if subscriber in subscribers:
                    subscribers.remove(subscriber)
                if not subscribers:
                    self._subscribers.pop(key, None)

    async def _heartbeat(
        self,
        *,
        key: TaskAssistRunKey,
        stop_event: asyncio.Event,
        user_id: UUID,
        task_id: UUID,
        request_id: UUID,
    ) -> None:
        try:
            while True:
                try:
                    await asyncio.wait_for(
                        stop_event.wait(),
                        timeout=self._heartbeat_interval_seconds,
                    )
                    return
                except asyncio.TimeoutError:
                    if self._is_cancelled(key):
                        return
                    if not await self._renew_durable_lease(
                        user_id=user_id,
                        task_id=task_id,
                        request_id=request_id,
                    ):
                        return
                    self._append(key, "still_running", {"stage": "generating"})
        except asyncio.CancelledError:
            return

    async def _persist_failure(
        self,
        *,
        user_id: UUID,
        task_id: UUID,
        request_id: UUID,
        code: str,
        message: str,
        lease_owner: str | None = None,
    ) -> None:
        try:
            async with self._session_factory() as session:
                repository = TaskAssistRepository(session)
                run = await repository.get_owned(
                    user_id=user_id,
                    task_id=task_id,
                    request_id=request_id,
                )
                if run is not None:
                    await repository.fail(
                        run,
                        code=code,
                        message=message,
                        lease_owner=lease_owner,
                    )
        except Exception:
            logger.exception("task_assist_failure_persistence_failed")

    async def _renew_durable_lease(
        self,
        *,
        user_id: UUID,
        task_id: UUID,
        request_id: UUID,
    ) -> bool:
        try:
            async with self._session_factory() as session:
                repository = TaskAssistRepository(session)
                run = await repository.get_owned(
                    user_id=user_id,
                    task_id=task_id,
                    request_id=request_id,
                )
                if run is None:
                    return False
                return await repository.renew_lease(
                    run,
                    lease_owner=self._lease_owner,
                )
        except Exception:
            logger.exception("task_assist_lease_renewal_failed")
            return False

    async def _recover_durable_snapshot(
        self,
        *,
        user_id: UUID,
        task_id: UUID,
        request_id: UUID,
        key: TaskAssistRunKey,
    ) -> None:
        try:
            async with self._session_factory() as session:
                repository = TaskAssistRepository(session)
                run = await repository.get_owned(
                    user_id=user_id,
                    task_id=task_id,
                    request_id=request_id,
                )
                if run is None:
                    self._append_error(
                        key,
                        code="TASK_ASSIST_RUN_NOT_FOUND",
                        message="辅助请求不存在。",
                    )
                    return
                await repository.fail_interrupted_if_lease_expired(run)
                await repository.expire_if_needed(run)
                self.restore_from_snapshot(run)
        except Exception:
            logger.exception("task_assist_snapshot_recovery_failed")

    def _append_error(self, key: TaskAssistRunKey, *, code: str, message: str) -> None:
        self._append(key, "agent_error", {"code": code, "message": message})

    def _append(
        self,
        key: TaskAssistRunKey,
        event_type: str,
        payload: dict[str, Any],
        *,
        allow_cancelled: bool = False,
    ) -> bool:
        with self._lock:
            if key in self._cancelled_runs and not allow_cancelled:
                return False
            self._sequences[key] += 1
            seq = self._sequences[key]
            event_id = f"{key.thread_id}:task_assist:{key.request_id}:{seq:06d}"
            envelope = {
                "event_id": event_id,
                "thread_id": key.thread_id,
                "request_id": key.request_id,
                "run_type": "task_assist",
                "event_type": event_type,
                "seq": seq,
                "created_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                "payload": {
                    **payload,
                    "state_version": next(self._state_versions),
                },
            }
            event = format_sse_event(event_type, envelope, event_id=event_id)
            self._events[key].append(event)
            subscribers = list(self._subscribers.get(key, []))
            if event_type in TERMINAL_EVENTS:
                if key not in self._terminal_order:
                    self._terminal_order.append(key)
                while len(self._terminal_order) > self._max_retained_terminal_runs:
                    stale = self._terminal_order.popleft()
                    if stale in self._active_runs or self._subscribers.get(stale):
                        continue
                    self._events.pop(stale, None)
                    self._sequences.pop(stale, None)
        for subscriber in subscribers:
            try:
                subscriber.loop.call_soon_threadsafe(subscriber.queue.put_nowait, event)
            except RuntimeError:
                logger.debug("task_assist_subscriber_closed")
        return True

    def _is_cancelled(self, key: TaskAssistRunKey) -> bool:
        with self._lock:
            return key in self._cancelled_runs


def _event_id(event: str) -> str | None:
    for line in event.splitlines():
        if line.startswith("id: "):
            return line[4:]
    return None


def _event_type(event: str) -> str | None:
    for line in event.splitlines():
        if line.startswith("event: "):
            return line[7:]
    return None


def _history_start(history: list[str], last_event_id: str | None) -> int:
    if not last_event_id:
        return 0
    for index, event in enumerate(history):
        if _event_id(event) == last_event_id:
            return index + 1
    return 0


_global_task_assist_runtime = TaskAssistRuntime()


def get_global_task_assist_runtime() -> TaskAssistRuntime:
    return _global_task_assist_runtime
