from __future__ import annotations

import os
from typing import Annotated, AsyncIterator
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, Header, HTTPException, Path, Query, status
from fastapi.responses import StreamingResponse

from app.api.auth import AuthUser, get_current_user, get_user_for_sse
from app.api.schemas import (
    TaskAssistApplyRequest,
    TaskAssistApplyResponse,
    TaskAssistRequest,
    TaskAssistRunSnapshot,
    TaskAssistStartResponse,
    SseEventEnvelope,
)
from app.db.session import async_session
from app.services.task_assist import TaskAssistError, TaskAssistRepository, TaskAssistService
from app.services.task_assist_runtime import (
    TaskAssistRuntime,
    get_global_task_assist_runtime,
)


router = APIRouter(prefix="/api/tasks", tags=["task-assist"])


async def get_task_assist_service() -> AsyncIterator[TaskAssistService]:
    async with async_session() as session:
        yield TaskAssistService(session)


def get_task_assist_runtime() -> TaskAssistRuntime:
    return get_global_task_assist_runtime()


@router.post(
    "/{task_id}/assist",
    response_model=TaskAssistStartResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def start_task_assist(
    task_id: Annotated[UUID, Path()],
    payload: TaskAssistRequest,
    background_tasks: BackgroundTasks,
    current_user: Annotated[AuthUser, Depends(get_current_user)],
    service: Annotated[TaskAssistService, Depends(get_task_assist_service)],
    runtime: Annotated[TaskAssistRuntime, Depends(get_task_assist_runtime)],
) -> TaskAssistStartResponse:
    _require_enabled()
    try:
        task = await service.load_supported_task(user_id=current_user.id, task_id=task_id)
        run, created = await service.repository.create_or_get(
            user_id=current_user.id,
            task=task,
            request_id=payload.request_id,
            mode=payload.mode,
            user_context=payload.user_context,
            lease_owner=getattr(runtime, "lease_owner", f"request:{payload.request_id}"),
        )
    except TaskAssistError as exc:
        raise _http_error(exc) from exc
    if created:
        background_tasks.add_task(
            runtime.run,
            user_id=current_user.id,
            task_id=task.id,
            thread_id=task.thread_id,
            request_id=payload.request_id,
        )
    return TaskAssistStartResponse(
        task_id=task.id,
        thread_id=task.thread_id,
        request_id=payload.request_id,
        mode=payload.mode,
        status=run.status,
        events_url=f"/api/tasks/{task.id}/assist/{payload.request_id}/events",
    )


@router.get(
    "/{task_id}/assist/{request_id}",
    response_model=TaskAssistRunSnapshot,
)
async def get_task_assist_snapshot(
    task_id: Annotated[UUID, Path()],
    request_id: Annotated[UUID, Path()],
    current_user: Annotated[AuthUser, Depends(get_current_user)],
    service: Annotated[TaskAssistService, Depends(get_task_assist_service)],
) -> TaskAssistRunSnapshot:
    _require_enabled()
    run = await service.repository.get_owned(
        user_id=current_user.id,
        task_id=task_id,
        request_id=request_id,
    )
    if run is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Task assist run not found")
    await service.repository.fail_interrupted_if_lease_expired(run)
    run = await service.repository.expire_if_needed(run)
    return _snapshot(run)


@router.get(
    "/{task_id}/assist/{request_id}/events",
    description=(
        "Task-assist-only SSE stream. The run_type is task_assist and allowed events are "
        "run_started, task_context_ready, assist_generation_started, "
        "assist_validation_started, still_running, assist_ready, done, and agent_error."
    ),
    responses={
        200: {
            "model": SseEventEnvelope,
            "description": "Task-assist SSE envelopes (one model per emitted event).",
            "content": {"text/event-stream": {}},
        }
    },
)
async def stream_task_assist_events(
    task_id: Annotated[UUID, Path()],
    request_id: Annotated[UUID, Path()],
    current_user: Annotated[AuthUser, Depends(get_user_for_sse)],
    service: Annotated[TaskAssistService, Depends(get_task_assist_service)],
    runtime: Annotated[TaskAssistRuntime, Depends(get_task_assist_runtime)],
    last_event_id_header: Annotated[str | None, Header(alias="Last-Event-ID")] = None,
    last_event_id_query: Annotated[str | None, Query(alias="last_event_id")] = None,
) -> StreamingResponse:
    _require_enabled()
    run = await service.repository.get_owned(
        user_id=current_user.id,
        task_id=task_id,
        request_id=request_id,
    )
    if run is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Task assist run not found")
    await service.repository.fail_interrupted_if_lease_expired(run)
    run = await service.repository.expire_if_needed(run)
    runtime.restore_from_snapshot(run)
    return StreamingResponse(
        runtime.stream(
            thread_id=run.thread_id,
            request_id=request_id,
            last_event_id=last_event_id_header or last_event_id_query,
            user_id=current_user.id,
            task_id=task_id,
        ),
        media_type="text/event-stream",
    )


@router.delete(
    "/{task_id}/assist/{request_id}",
    response_model=TaskAssistRunSnapshot,
)
async def cancel_task_assist(
    task_id: Annotated[UUID, Path()],
    request_id: Annotated[UUID, Path()],
    current_user: Annotated[AuthUser, Depends(get_current_user)],
    service: Annotated[TaskAssistService, Depends(get_task_assist_service)],
    runtime: Annotated[TaskAssistRuntime, Depends(get_task_assist_runtime)],
) -> TaskAssistRunSnapshot:
    _require_enabled()
    run = await service.repository.get_owned(
        user_id=current_user.id,
        task_id=task_id,
        request_id=request_id,
    )
    if run is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Task assist run not found")
    try:
        run = await service.repository.cancel(run)
    except TaskAssistError as exc:
        raise _http_error(exc) from exc
    await runtime.cancel(thread_id=run.thread_id, request_id=request_id)
    return _snapshot(run)


@router.post(
    "/{task_id}/assist/{request_id}/apply",
    response_model=TaskAssistApplyResponse,
)
async def apply_task_assist(
    task_id: Annotated[UUID, Path()],
    request_id: Annotated[UUID, Path()],
    payload: TaskAssistApplyRequest,
    current_user: Annotated[AuthUser, Depends(get_current_user)],
    service: Annotated[TaskAssistService, Depends(get_task_assist_service)],
) -> TaskAssistApplyResponse:
    _require_enabled()
    try:
        return await service.apply(
            user_id=current_user.id,
            task_id=task_id,
            request_id=request_id,
            selected_option_id=payload.selected_option_id,
        )
    except TaskAssistError as exc:
        raise _http_error(exc) from exc


def _snapshot(run) -> TaskAssistRunSnapshot:
    return TaskAssistRunSnapshot.model_validate(
        {
            "task_id": run.task_id,
            "thread_id": run.thread_id,
            "request_id": run.request_id,
            "mode": run.mode,
            "status": run.status,
            "stage": run.stage,
            "proposal": run.proposal,
            "error_code": run.error_code,
            "error_message": run.error_message,
            "created_at": run.created_at,
            "updated_at": run.updated_at,
            "expires_at": run.expires_at,
        }
    )


def _require_enabled() -> None:
    if os.getenv("EASYPLAN_TASK_ASSIST_ENABLED", "false").strip().lower() not in {
        "1",
        "true",
        "yes",
        "on",
    }:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")


def _http_error(error: TaskAssistError) -> HTTPException:
    return HTTPException(
        status_code=error.status_code,
        detail={"error_code": error.code, "message": error.message},
    )
