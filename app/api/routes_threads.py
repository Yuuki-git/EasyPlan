from datetime import datetime, timezone
from typing import Annotated, Literal
from zoneinfo import ZoneInfo

from fastapi import APIRouter, BackgroundTasks, Depends, Header, HTTPException, Path, Query, status
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import AuthUser, get_current_user, get_user_for_sse
from app.api.dependencies import get_user_timezone
from app.api.schemas import (
    ConfirmationRequest,
    ConfirmationResponse,
    NextPhaseCommitReceipt,
    NextPhaseRequest,
    NextPhaseResponse,
    ThreadSnapshot,
)
from app.db.session import get_db
from app.services.agent_runtime import AgentRuntime, agent_runtime
from app.services.phase_planning import phase_planning_enabled
from app.services.practice_repository import PracticeLoopRepository
from app.services.thread_repository import (
    AgentThreadRepository,
    ThreadStateConflictError,
    thread_to_snapshot_payload,
)

router = APIRouter(prefix="/api/threads", tags=["threads"])


def get_agent_runtime() -> AgentRuntime:
    return agent_runtime


def get_thread_repository(
    session: Annotated[AsyncSession, Depends(get_db)],
) -> AgentThreadRepository:
    return AgentThreadRepository(session)


def _thread_conflict_http_exception(error: ThreadStateConflictError) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail={
            "error_code": error.code,
            "message": error.message,
        },
    )


@router.get("/{thread_id}", response_model=ThreadSnapshot)
async def get_thread_snapshot(
    thread_id: Annotated[str, Path(min_length=1)],
    current_user: Annotated[AuthUser, Depends(get_current_user)],
    repository: Annotated[AgentThreadRepository, Depends(get_thread_repository)],
) -> ThreadSnapshot:
    thread = await repository.get_thread_for_user(user_id=current_user.id, thread_id=thread_id)
    if thread is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Thread not found")
    execution_snapshot = await PracticeLoopRepository(
        repository.session
    ).get_execution_snapshot(
        user_id=current_user.id,
        thread=thread,
        now=datetime.now(timezone.utc),
    )
    return ThreadSnapshot(
        **thread_to_snapshot_payload(thread),
        long_term_execution=execution_snapshot,
    )


@router.get(
    "/{thread_id}/phases/next/commit",
    response_model=NextPhaseCommitReceipt,
)
async def get_next_phase_commit_receipt(
    thread_id: Annotated[str, Path(min_length=1)],
    request_id: Annotated[str, Query(min_length=8, max_length=128)],
    current_user: Annotated[AuthUser, Depends(get_current_user)],
    repository: Annotated[AgentThreadRepository, Depends(get_thread_repository)],
) -> NextPhaseCommitReceipt:
    receipt = await repository.get_next_phase_commit_receipt(
        user_id=current_user.id,
        thread_id=thread_id,
        request_id=request_id,
    )
    if receipt is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Thread not found")
    return NextPhaseCommitReceipt(
        thread_id=receipt.thread_id,
        request_id=receipt.request_id,
        status=receipt.status,
        current_phase_id=receipt.current_phase_id,
        task_tree=receipt.task_tree,
        tasks=receipt.tasks,
    )


@router.get(
    "/{thread_id}/events",
    description=(
        "Server-Sent Events stream. Events include run_started, "
        "intent_profile_started, intent_profile_completed, strategy_selected, "
        "planning_started, validation_started, repair_started, "
        "persistence_started, still_running, plan_ready, sync_status, "
        "sync_complete, done, snapshot_required, and agent_error. "
        "Every event data payload is a run-scoped envelope with event_id, "
        "thread_id, request_id, run_type, event_type, seq, created_at, and payload. "
        "Every stream requires the matching request_id. "
        "The agent_error event payload contains code and a user-safe message."
    ),
)
async def stream_thread_events(
    thread_id: Annotated[str, Path(min_length=1)],
    current_user: Annotated[AuthUser, Depends(get_user_for_sse)],
    repository: Annotated[AgentThreadRepository, Depends(get_thread_repository)],
    runtime: Annotated[AgentRuntime, Depends(get_agent_runtime)],
    request_id: Annotated[str, Query(min_length=1, max_length=128)],
    last_event_id_header: Annotated[str | None, Header(alias="Last-Event-ID")] = None,
    last_event_id_query: Annotated[str | None, Query(alias="last_event_id")] = None,
    run_type: Annotated[Literal["initial", "next_phase", "refine"], Query()] = "initial",
) -> StreamingResponse:
    thread = await repository.get_thread_for_user(user_id=current_user.id, thread_id=thread_id)
    if thread is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Thread not found")
    return StreamingResponse(
        runtime.stream_thread_events(
            user_id=str(current_user.id),
            thread_id=thread_id,
            last_event_id=last_event_id_header or last_event_id_query,
            run_type=run_type,
            request_id=request_id,
        ),
        media_type="text/event-stream",
    )


@router.post(
    "/{thread_id}/confirm",
    response_model=ConfirmationResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def confirm_thread(
    thread_id: Annotated[str, Path(min_length=1)],
    payload: ConfirmationRequest,
    background_tasks: BackgroundTasks,
    user_timezone: Annotated[ZoneInfo, Depends(get_user_timezone)],
    current_user: Annotated[AuthUser, Depends(get_current_user)],
    repository: Annotated[AgentThreadRepository, Depends(get_thread_repository)],
    runtime: Annotated[AgentRuntime, Depends(get_agent_runtime)],
) -> ConfirmationResponse:
    thread = await repository.get_thread_for_user(user_id=current_user.id, thread_id=thread_id)
    if thread is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Thread not found")
    pending_payload = thread.interrupt_payload if isinstance(thread.interrupt_payload, dict) else {}
    is_next_phase_payload = pending_payload.get("type") == "next_phase_review" or (
        pending_payload.get("type") == "phase_generation_state"
        and str(pending_payload.get("request_id") or "") == payload.request_id
    )
    run_type: Literal["initial", "next_phase", "refine"]
    if is_next_phase_payload:
        run_type = "next_phase"
    elif payload.action.value == "refine":
        run_type = "refine"
    else:
        run_type = "initial"
    next_phase_task_tree = (
        pending_payload.get("task_tree")
        if pending_payload.get("type") == "next_phase_review"
        and payload.action.value == "approve"
        else None
    )
    if (
        pending_payload.get("type") == "next_phase_review"
        and payload.action.value == "approve"
        and not isinstance(next_phase_task_tree, dict)
    ):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "error_code": "NEXT_PHASE_PREVIEW_MISSING",
                "message": "Next-phase preview is missing and cannot be committed",
            },
        )
    try:
        should_schedule = await repository.mark_confirmation_accepted(
            thread=thread,
            request_id=payload.request_id,
            action=payload.action.value,
        )
    except ThreadStateConflictError as error:
        raise _thread_conflict_http_exception(error) from error
    if not should_schedule:
        return ConfirmationResponse(
            thread_id=thread_id,
            request_id=payload.request_id,
            status="accepted",
        )
    if run_type == "next_phase" and next_phase_task_tree is not None:
        background_tasks.add_task(
            runtime.commit_next_phase,
            user_id=str(current_user.id),
            thread_id=thread_id,
            request_id=payload.request_id,
            task_tree=next_phase_task_tree,
            user_timezone=user_timezone.key,
        )
    else:
        background_tasks.add_task(
            runtime.resume_thread,
            user_id=str(current_user.id),
            thread_id=thread_id,
            decision=payload.model_dump(mode="json", exclude_none=True),
            run_type=run_type,
            request_id=payload.request_id,
            user_timezone=user_timezone.key,
        )
    return ConfirmationResponse(
        thread_id=thread_id,
        request_id=payload.request_id,
        status="accepted",
    )


@router.delete(
    "/{thread_id}/phases/next/cancel",
    response_model=ThreadSnapshot,
)
async def cancel_next_phase_preview(
    thread_id: Annotated[str, Path(min_length=1)],
    current_user: Annotated[AuthUser, Depends(get_current_user)],
    repository: Annotated[AgentThreadRepository, Depends(get_thread_repository)],
    runtime: Annotated[AgentRuntime, Depends(get_agent_runtime)],
    request_id: Annotated[str, Query(min_length=8, max_length=128)],
) -> ThreadSnapshot:
    try:
        thread = await repository.cancel_next_phase_request(
            user_id=current_user.id,
            thread_id=thread_id,
            request_id=request_id,
        )
    except ThreadStateConflictError as error:
        raise _thread_conflict_http_exception(error) from error
    if thread is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Thread not found")
    runtime.cancel_run(
        thread_id=thread_id,
        run_type="next_phase",
        request_id=request_id,
    )
    return ThreadSnapshot(**thread_to_snapshot_payload(thread))


@router.post(
    "/{thread_id}/phases/next",
    response_model=NextPhaseResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def start_next_phase(
    thread_id: Annotated[str, Path(min_length=1)],
    payload: NextPhaseRequest,
    background_tasks: BackgroundTasks,
    user_timezone: Annotated[ZoneInfo, Depends(get_user_timezone)],
    current_user: Annotated[AuthUser, Depends(get_current_user)],
    repository: Annotated[AgentThreadRepository, Depends(get_thread_repository)],
    runtime: Annotated[AgentRuntime, Depends(get_agent_runtime)],
) -> NextPhaseResponse:
    if not phase_planning_enabled():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")

    result = await repository.start_next_phase_generation(
        user_id=current_user.id,
        thread_id=thread_id,
        request_id=payload.request_id,
    )
    if result is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Thread not found")
    if result.error_code:
        detail: dict[str, object] = {
            "error_code": result.error_code,
            "message": result.error_message or "Unable to start next phase",
        }
        if result.remaining_ai_actions is not None:
            detail["remaining_ai_actions"] = result.remaining_ai_actions
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=detail)

    request_id = str(payload.request_id)
    if result.should_schedule:
        background_tasks.add_task(
            runtime.run_next_phase,
            user_id=str(current_user.id),
            thread_id=thread_id,
            request_id=request_id,
            intent_text=result.thread.intent_text,
            committed_task_tree=result.thread.task_tree,
            current_phase_task_summary=result.current_phase_task_summary,
            user_timezone=user_timezone.key,
        )
    return NextPhaseResponse(
        thread_id=thread_id,
        request_id=payload.request_id,
        status=result.status,
        events_url=(
            f"/api/threads/{thread_id}/events"
            f"?run_type=next_phase&request_id={request_id}"
        ),
    )


@router.delete("/{thread_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_thread(
    thread_id: Annotated[str, Path(min_length=1)],
    current_user: Annotated[AuthUser, Depends(get_current_user)],
    repository: Annotated[AgentThreadRepository, Depends(get_thread_repository)],
) -> None:
    deleted = await repository.delete_thread_for_user(user_id=current_user.id, thread_id=thread_id)
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Thread not found")
    return None
