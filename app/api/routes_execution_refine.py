from __future__ import annotations

from typing import Annotated, AsyncIterator
from uuid import UUID

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    Header,
    HTTPException,
    Path,
    Query,
    status,
)
from fastapi.responses import StreamingResponse

from app.api.auth import AuthUser, get_current_user, get_user_for_sse
from app.api.schemas import (
    ExecutionRefineApplyReceipt,
    ExecutionRefineApplyRequest,
    ExecutionRefineRequest,
    ExecutionRefineRunSnapshot,
    ExecutionRefineStartResponse,
    SseEventEnvelope,
)
from app.db.session import async_session
from app.services.execution_refine import (
    ExecutionRefineError,
    ExecutionRefineRepository,
    ExecutionRefineService,
    execution_refine_enabled,
)
from app.services.execution_refine_runtime import (
    ExecutionRefineRuntime,
    get_global_execution_refine_runtime,
)


router = APIRouter(prefix="/api/threads", tags=["execution-refine"])


async def get_execution_refine_service() -> AsyncIterator[ExecutionRefineService]:
    async with async_session() as session:
        yield ExecutionRefineService(session)


def get_execution_refine_runtime() -> ExecutionRefineRuntime:
    return get_global_execution_refine_runtime()


@router.post(
    "/{thread_id}/refine-diffs",
    response_model=ExecutionRefineStartResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def start_execution_refine(
    thread_id: Annotated[str, Path(min_length=1, max_length=128)],
    payload: ExecutionRefineRequest,
    background_tasks: BackgroundTasks,
    current_user: Annotated[AuthUser, Depends(get_current_user)],
    service: Annotated[ExecutionRefineService, Depends(get_execution_refine_service)],
    runtime: Annotated[ExecutionRefineRuntime, Depends(get_execution_refine_runtime)],
) -> ExecutionRefineStartResponse:
    _require_enabled()
    try:
        scope = await service.load_scope(
            user_id=current_user.id,
            thread_id=thread_id,
            request=payload,
        )
        run, created = await service.repository.create_or_get(
            user_id=current_user.id,
            thread_id=thread_id,
            request=payload,
            scope=scope,
            lease_owner=runtime.lease_owner,
        )
    except ExecutionRefineError as exc:
        raise _http_error(exc) from exc
    if created:
        background_tasks.add_task(
            runtime.run,
            user_id=current_user.id,
            thread_id=thread_id,
            request_id=payload.request_id,
        )
    return ExecutionRefineStartResponse(
        run_id=run.id,
        thread_id=thread_id,
        request_id=payload.request_id,
        mode=payload.mode,
        status=run.status,
        events_url=(
            f"/api/threads/{thread_id}/refine-diffs/"
            f"{payload.request_id}/events"
        ),
    )


@router.get(
    "/{thread_id}/refine-diffs/{request_id}",
    response_model=ExecutionRefineRunSnapshot,
)
async def get_execution_refine_snapshot(
    thread_id: Annotated[str, Path(min_length=1, max_length=128)],
    request_id: Annotated[UUID, Path()],
    current_user: Annotated[AuthUser, Depends(get_current_user)],
    service: Annotated[ExecutionRefineService, Depends(get_execution_refine_service)],
) -> ExecutionRefineRunSnapshot:
    _require_enabled()
    run = await service.repository.get_owned(
        user_id=current_user.id,
        thread_id=thread_id,
        request_id=request_id,
    )
    if run is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    await service.repository.fail_interrupted_if_lease_expired(run)
    run = await service.repository.expire_if_needed(run)
    return _snapshot(run)


@router.get(
    "/{thread_id}/refine-diffs/{request_id}/events",
    description=(
        "Execution-refine-only SSE stream. Allowed events are run_started, "
        "execution_context_ready, refine_generation_started, "
        "refine_validation_started, repair_started, still_running, diff_ready, "
        "snapshot_required, done, and agent_error."
    ),
    responses={
        200: {
            "model": SseEventEnvelope,
            "description": "Execution Refine SSE envelopes.",
            "content": {"text/event-stream": {}},
        }
    },
)
async def stream_execution_refine_events(
    thread_id: Annotated[str, Path(min_length=1, max_length=128)],
    request_id: Annotated[UUID, Path()],
    current_user: Annotated[AuthUser, Depends(get_user_for_sse)],
    service: Annotated[ExecutionRefineService, Depends(get_execution_refine_service)],
    runtime: Annotated[ExecutionRefineRuntime, Depends(get_execution_refine_runtime)],
    last_event_id_header: Annotated[str | None, Header(alias="Last-Event-ID")] = None,
    last_event_id_query: Annotated[str | None, Query(alias="last_event_id")] = None,
) -> StreamingResponse:
    _require_enabled()
    run = await service.repository.get_owned(
        user_id=current_user.id,
        thread_id=thread_id,
        request_id=request_id,
    )
    if run is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    await service.repository.fail_interrupted_if_lease_expired(run)
    run = await service.repository.expire_if_needed(run)
    runtime.restore_from_snapshot(run)
    return StreamingResponse(
        runtime.stream(
            thread_id=thread_id,
            request_id=request_id,
            last_event_id=last_event_id_header or last_event_id_query,
            user_id=current_user.id,
        ),
        media_type="text/event-stream",
    )


@router.delete(
    "/{thread_id}/refine-diffs/{request_id}",
    response_model=ExecutionRefineRunSnapshot,
)
async def cancel_execution_refine(
    thread_id: Annotated[str, Path(min_length=1, max_length=128)],
    request_id: Annotated[UUID, Path()],
    current_user: Annotated[AuthUser, Depends(get_current_user)],
    service: Annotated[ExecutionRefineService, Depends(get_execution_refine_service)],
    runtime: Annotated[ExecutionRefineRuntime, Depends(get_execution_refine_runtime)],
) -> ExecutionRefineRunSnapshot:
    _require_enabled()
    run = await service.repository.get_owned(
        user_id=current_user.id,
        thread_id=thread_id,
        request_id=request_id,
    )
    if run is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    try:
        run = await service.repository.cancel(run)
    except ExecutionRefineError as exc:
        raise _http_error(exc) from exc
    await runtime.cancel(thread_id=thread_id, request_id=request_id)
    return _snapshot(run)


@router.post(
    "/{thread_id}/refine-diffs/{request_id}/apply",
    response_model=ExecutionRefineApplyReceipt,
)
async def apply_execution_refine(
    thread_id: Annotated[str, Path(min_length=1, max_length=128)],
    request_id: Annotated[UUID, Path()],
    payload: ExecutionRefineApplyRequest,
    current_user: Annotated[AuthUser, Depends(get_current_user)],
    service: Annotated[ExecutionRefineService, Depends(get_execution_refine_service)],
) -> ExecutionRefineApplyReceipt:
    _require_enabled()
    try:
        return await service.apply(
            user_id=current_user.id,
            thread_id=thread_id,
            request_id=request_id,
            expected_scope_fingerprint=payload.expected_scope_fingerprint,
        )
    except ExecutionRefineError as exc:
        raise _http_error(exc) from exc


def _snapshot(run: object) -> ExecutionRefineRunSnapshot:
    return ExecutionRefineRunSnapshot.model_validate(
        {
            "run_id": getattr(run, "id"),
            "thread_id": getattr(run, "thread_id"),
            "request_id": getattr(run, "request_id"),
            "mode": getattr(run, "mode"),
            "status": getattr(run, "status"),
            "stage": getattr(run, "stage"),
            "scope_fingerprint": getattr(run, "scope_fingerprint"),
            "proposal": getattr(run, "proposal"),
            "apply_receipt": getattr(run, "apply_receipt"),
            "error_code": getattr(run, "error_code"),
            "error_message": getattr(run, "error_message"),
            "created_at": getattr(run, "created_at"),
            "updated_at": getattr(run, "updated_at"),
            "expires_at": getattr(run, "expires_at"),
        }
    )


def _require_enabled() -> None:
    if not execution_refine_enabled():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")


def _http_error(error: ExecutionRefineError) -> HTTPException:
    return HTTPException(
        status_code=error.status_code,
        detail={"error_code": error.code, "message": error.message},
    )
