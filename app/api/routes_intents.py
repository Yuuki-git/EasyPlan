from typing import Annotated
from uuid import uuid4

from fastapi import APIRouter, Header, status

from app.api.schemas import IntentCreateRequest, IntentCreateResponse

router = APIRouter(prefix="/api/intents", tags=["intents"])


@router.post("", response_model=IntentCreateResponse, status_code=status.HTTP_202_ACCEPTED)
async def create_intent(
    payload: IntentCreateRequest,
    user_timezone: Annotated[str, Header(alias="X-User-Timezone")],
) -> IntentCreateResponse:
    thread_id = f"thr_{uuid4().hex}"
    return IntentCreateResponse(
        thread_id=thread_id,
        status="running",
        events_url=f"/api/threads/{thread_id}/events",
    )
