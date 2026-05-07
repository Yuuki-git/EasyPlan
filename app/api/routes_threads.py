from typing import Annotated
from zoneinfo import ZoneInfo

from fastapi import APIRouter, BackgroundTasks, Depends, Header, HTTPException, Path, Query, status
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import AuthUser, get_current_user, get_user_for_sse
from app.api.dependencies import get_user_timezone
from app.api.schemas import ConfirmationRequest, ConfirmationResponse, ThreadSnapshot
from app.db.session import get_db
from app.services.agent_runtime import AgentRuntime, agent_runtime
from app.services.thread_repository import AgentThreadRepository, thread_to_snapshot_payload

router = APIRouter(prefix="/api/threads", tags=["threads"])


def get_agent_runtime() -> AgentRuntime:
    return agent_runtime


def get_thread_repository(
    session: Annotated[AsyncSession, Depends(get_db)],
) -> AgentThreadRepository:
    return AgentThreadRepository(session)


@router.get("/{thread_id}", response_model=ThreadSnapshot)
async def get_thread_snapshot(
    thread_id: Annotated[str, Path(min_length=1)],
    current_user: Annotated[AuthUser, Depends(get_current_user)],
    repository: Annotated[AgentThreadRepository, Depends(get_thread_repository)],
) -> ThreadSnapshot:
    thread = await repository.get_thread_for_user(user_id=current_user.id, thread_id=thread_id)
    if thread is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Thread not found")
    return ThreadSnapshot(**thread_to_snapshot_payload(thread))


@router.get(
    "/{thread_id}/events",
    description=(
        "Server-Sent Events stream. Events include reasoning, checkpoint, "
        "plan_ready, sync_progress, done, snapshot_required, and error. "
        "The error event payload contains state_version, code, and message."
    ),
)
async def stream_thread_events(
    thread_id: Annotated[str, Path(min_length=1)],
    current_user: Annotated[AuthUser, Depends(get_user_for_sse)],
    repository: Annotated[AgentThreadRepository, Depends(get_thread_repository)],
    runtime: Annotated[AgentRuntime, Depends(get_agent_runtime)],
    last_event_id_header: Annotated[str | None, Header(alias="Last-Event-ID")] = None,
    last_event_id_query: Annotated[str | None, Query(alias="last_event_id")] = None,
) -> StreamingResponse:
    thread = await repository.get_thread_for_user(user_id=current_user.id, thread_id=thread_id)
    if thread is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Thread not found")
    return StreamingResponse(
        runtime.stream_thread_events(
            user_id=str(current_user.id),
            thread_id=thread_id,
            last_event_id=last_event_id_header or last_event_id_query,
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
    await repository.mark_confirmation_accepted(thread=thread, request_id=payload.request_id)
    background_tasks.add_task(
        runtime.resume_thread,
        user_id=str(current_user.id),
        thread_id=thread_id,
        decision=payload.model_dump(mode="json", exclude_none=True),
    )
    return ConfirmationResponse(
        thread_id=thread_id,
        request_id=payload.request_id,
        status="accepted",
    )
