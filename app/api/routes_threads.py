from datetime import datetime, timezone
from typing import Annotated, AsyncIterator

from fastapi import APIRouter, Header, Path, status
from fastapi.responses import StreamingResponse

from app.api.schemas import ConfirmationRequest, ConfirmationResponse, ThreadSnapshot

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
    yield "event: snapshot_required\ndata: {}\n\n"


@router.get("/{thread_id}/events")
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
    user_timezone: Annotated[str, Header(alias="X-User-Timezone")],
) -> ConfirmationResponse:
    return ConfirmationResponse(
        thread_id=thread_id,
        request_id=payload.request_id,
        status="accepted",
    )
