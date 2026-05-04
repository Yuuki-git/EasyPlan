from datetime import datetime, timedelta, timezone
from typing import Annotated
from uuid import uuid4

from fastapi import APIRouter, Path

from app.api.schemas import IntegrationStatus, OAuthStartResponse

router = APIRouter(prefix="/api/integrations", tags=["integrations"])


@router.get("", response_model=list[IntegrationStatus])
async def list_integrations() -> list[IntegrationStatus]:
    return []


@router.get("/{provider}/tools")
async def list_integration_tools(
    provider: Annotated[str, Path(min_length=1)],
) -> dict[str, list[dict]]:
    return {"tools": []}


@router.post("/{provider}/refresh-tools")
async def refresh_integration_tools(
    provider: Annotated[str, Path(min_length=1)],
) -> dict[str, str]:
    return {"provider": provider, "status": "refresh_queued"}


@router.get("/{provider}/oauth/start", response_model=OAuthStartResponse)
async def start_oauth(
    provider: Annotated[str, Path(min_length=1)],
) -> OAuthStartResponse:
    state = f"oauth_{uuid4().hex}"
    return OAuthStartResponse(
        provider=provider,
        authorization_url=f"https://auth.example.com/{provider}?state={state}",
        state=state,
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=10),
    )


@router.get("/{provider}/oauth/callback")
async def oauth_callback(
    provider: Annotated[str, Path(min_length=1)],
    code: str,
    state: str,
) -> dict[str, str]:
    return {"provider": provider, "status": "connected"}
