from datetime import datetime, timezone
from typing import Annotated, AsyncIterator
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Header, Path, status
from fastapi.responses import StreamingResponse

from app.api.dependencies import get_user_timezone
from app.api.schemas import ConfirmationRequest, ConfirmationResponse, ThreadSnapshot
from app.api.sse import format_sse_event

router = APIRouter(prefix="/api/threads", tags=["threads"])


@router.get("/{thread_id}", response_model=ThreadSnapshot)
async def get_thread_snapshot(
    thread_id: Annotated[str, Path(min_length=1)],
) -> ThreadSnapshot:
    return ThreadSnapshot(
        thread_id=thread_id,
        status="awaiting_confirmation",
        state_version=0,
        last_event_id=None,
        server_time=datetime.now(timezone.utc),
        intent_text="",
        task_tree=None,
        interrupt_payload=None,
        latest_checkpoint_id=None,
    )


async def _empty_event_stream() -> AsyncIterator[str]:
    yield format_sse_event("snapshot_required", {})


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
    last_event_id: Annotated[str | None, Header(alias="Last-Event-ID")] = None,
) -> StreamingResponse:
    return StreamingResponse(_empty_event_stream(), media_type="text/event-stream")


@router.post(
    "/{thread_id}/confirm",
    response_model=ConfirmationResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def confirm_thread(
    thread_id: Annotated[str, Path(min_length=1)],
    payload: ConfirmationRequest,
    user_timezone: Annotated[ZoneInfo, Depends(get_user_timezone)],
) -> ConfirmationResponse:
    return ConfirmationResponse(
        thread_id=thread_id,
        request_id=payload.request_id,
        status="accepted",
    )
