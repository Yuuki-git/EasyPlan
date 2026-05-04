from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, Request, status

from app.api.auth import AuthUser, get_current_user
from app.api.schemas import IntegrationStatus, OAuthStartResponse
from app.services.oauth_service import OAuthStateError, build_default_todoist_oauth_service

router = APIRouter(prefix="/api/integrations", tags=["integrations"])
_todoist_oauth_service = build_default_todoist_oauth_service()


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
    request: Request,
    current_user: Annotated[AuthUser, Depends(get_current_user)],
) -> OAuthStartResponse:
    if provider != "todoist":
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Unsupported provider")
    callback_url = str(request.url_for("oauth_callback", provider=provider))
    authorization = _todoist_oauth_service.start_authorization(
        user_id=current_user.id,
        redirect_uri=callback_url,
    )
    return OAuthStartResponse(
        provider=authorization.provider,
        authorization_url=authorization.authorization_url,
        state=authorization.state,
        expires_at=authorization.expires_at,
    )


@router.get("/{provider}/oauth/callback")
async def oauth_callback(
    provider: Annotated[str, Path(min_length=1)],
    code: str,
    state: str,
) -> dict[str, str]:
    try:
        await _todoist_oauth_service.complete_callback(
            user_id=None,
            provider=provider,
            code=code,
            state=state,
        )
    except OAuthStateError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return {"provider": provider, "status": "connected"}
