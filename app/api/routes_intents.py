from typing import Annotated
from uuid import uuid4
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, status

from app.api.dependencies import get_user_timezone
from app.api.schemas import IntentCreateRequest, IntentCreateResponse

router = APIRouter(prefix="/api/intents", tags=["intents"])


@router.post("", response_model=IntentCreateResponse, status_code=status.HTTP_202_ACCEPTED)
async def create_intent(
    payload: IntentCreateRequest,
    user_timezone: Annotated[ZoneInfo, Depends(get_user_timezone)],
) -> IntentCreateResponse:
    thread_id = f"thr_{uuid4().hex}"
    return IntentCreateResponse(
        thread_id=thread_id,
        status="running",
        events_url=f"/api/threads/{thread_id}/events",
    )
