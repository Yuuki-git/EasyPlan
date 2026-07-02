from typing import Annotated
from uuid import uuid4
from zoneinfo import ZoneInfo

from fastapi import APIRouter, BackgroundTasks, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import AuthUser, get_current_user
from app.api.dependencies import get_user_timezone
from app.api.schemas import IntentCreateRequest, IntentCreateResponse
from app.db.session import get_db
from app.services.agent_runtime import AgentRuntime, agent_runtime
from app.services.thread_repository import AgentThreadRepository

router = APIRouter(prefix="/api/intents", tags=["intents"])


def get_agent_runtime() -> AgentRuntime:
    return agent_runtime


def get_thread_repository(
    session: Annotated[AsyncSession, Depends(get_db)],
) -> AgentThreadRepository:
    return AgentThreadRepository(session)


@router.post("", response_model=IntentCreateResponse, status_code=status.HTTP_202_ACCEPTED)
async def create_intent(
    payload: IntentCreateRequest,
    background_tasks: BackgroundTasks,
    user_timezone: Annotated[ZoneInfo, Depends(get_user_timezone)],
    current_user: Annotated[AuthUser, Depends(get_current_user)],
    repository: Annotated[AgentThreadRepository, Depends(get_thread_repository)],
    runtime: Annotated[AgentRuntime, Depends(get_agent_runtime)],
) -> IntentCreateResponse:
    thread_id = f"thr_{uuid4().hex}"
    request_id = uuid4()
    await repository.create_thread(
        user_id=current_user.id,
        thread_id=thread_id,
        intent_text=payload.intent_text,
        selected_provider=payload.preferred_provider,
    )
    background_tasks.add_task(
        runtime.run_new_thread,
        user_id=str(current_user.id),
        thread_id=thread_id,
        request_id=str(request_id),
        intent_text=payload.intent_text,
        selected_provider=payload.preferred_provider,
        planner_provider=payload.planner_provider,
        planner_model=payload.planner_model,
    )
    return IntentCreateResponse(
        thread_id=thread_id,
        request_id=request_id,
        status="running",
        events_url=(
            f"/api/threads/{thread_id}/events"
            f"?run_type=initial&request_id={request_id}"
        ),
    )
