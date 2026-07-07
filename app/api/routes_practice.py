from datetime import datetime, timezone
from typing import Annotated
from uuid import UUID
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Path, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import AuthUser, get_current_user
from app.api.dependencies import get_user_timezone
from app.api.schemas import (
    PhaseReviewDecisionRequest,
    PhaseReviewResponse,
    PhaseReviewUpdateRequest,
    TaskResponse,
)
from app.db.session import get_db
from app.services.phase_planning import long_term_execution_enabled
from app.services.practice_repository import PracticeLoopRepository


router = APIRouter(prefix="/api/threads", tags=["practice"])


def get_practice_repository(
    session: Annotated[AsyncSession, Depends(get_db)],
) -> PracticeLoopRepository:
    return PracticeLoopRepository(session)


def _require_long_term_execution() -> None:
    if not long_term_execution_enabled():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Not found",
        )


def _raise_mutation_error(
    *,
    error_code: str | None,
    error_message: str | None,
    invalid_status: int = status.HTTP_409_CONFLICT,
) -> None:
    if error_code == "NOT_FOUND":
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Not found",
        )
    if error_code is not None:
        raise HTTPException(
            status_code=invalid_status,
            detail={
                "error_code": error_code,
                "message": error_message or "Unable to update long-term execution",
            },
        )


@router.post(
    "/{thread_id}/practice-loops/{loop_id}/schedule-today",
    response_model=TaskResponse,
)
async def schedule_practice_today(
    thread_id: Annotated[str, Path(min_length=1, max_length=128)],
    loop_id: UUID,
    user_timezone: Annotated[ZoneInfo, Depends(get_user_timezone)],
    current_user: Annotated[AuthUser, Depends(get_current_user)],
    repository: Annotated[
        PracticeLoopRepository,
        Depends(get_practice_repository),
    ],
) -> TaskResponse:
    _require_long_term_execution()
    try:
        result = await repository.schedule_today(
            user_id=current_user.id,
            thread_id=thread_id,
            loop_id=loop_id,
            now=datetime.now(timezone.utc),
        )
        _raise_mutation_error(
            error_code=result.error_code,
            error_message=result.error_message,
        )
        await repository.session.commit()
        if result.task is None:
            raise RuntimeError("practice scheduling returned no task")
        await repository.session.refresh(result.task)
        return TaskResponse.model_validate(result.task)
    except HTTPException:
        await repository.session.rollback()
        raise
    except Exception:
        await repository.session.rollback()
        raise


@router.put(
    "/{thread_id}/phases/{phase_id}/review",
    response_model=PhaseReviewResponse,
)
async def update_phase_review(
    thread_id: Annotated[str, Path(min_length=1, max_length=128)],
    phase_id: Annotated[str, Path(min_length=1, max_length=80)],
    payload: PhaseReviewUpdateRequest,
    user_timezone: Annotated[ZoneInfo, Depends(get_user_timezone)],
    current_user: Annotated[AuthUser, Depends(get_current_user)],
    repository: Annotated[
        PracticeLoopRepository,
        Depends(get_practice_repository),
    ],
) -> PhaseReviewResponse:
    _require_long_term_execution()
    result = await repository.update_phase_review(
        user_id=current_user.id,
        thread_id=thread_id,
        phase_id=phase_id,
        payload=payload,
        now=datetime.now(timezone.utc),
    )
    _raise_mutation_error(
        error_code=result.error_code,
        error_message=result.error_message,
        invalid_status=(
            status.HTTP_422_UNPROCESSABLE_CONTENT
            if result.error_code == "INVALID_CHECKPOINT_EVIDENCE"
            else status.HTTP_409_CONFLICT
        ),
    )
    if result.review is None:
        raise RuntimeError("phase review update returned no review")
    return PhaseReviewResponse.model_validate(result.review)


@router.post(
    "/{thread_id}/phases/{phase_id}/review/decision",
    response_model=PhaseReviewResponse,
)
async def finalize_phase_review(
    thread_id: Annotated[str, Path(min_length=1, max_length=128)],
    phase_id: Annotated[str, Path(min_length=1, max_length=80)],
    payload: PhaseReviewDecisionRequest,
    user_timezone: Annotated[ZoneInfo, Depends(get_user_timezone)],
    current_user: Annotated[AuthUser, Depends(get_current_user)],
    repository: Annotated[
        PracticeLoopRepository,
        Depends(get_practice_repository),
    ],
) -> PhaseReviewResponse:
    _require_long_term_execution()
    result = await repository.finalize_review(
        user_id=current_user.id,
        thread_id=thread_id,
        phase_id=phase_id,
        payload=payload,
        now=datetime.now(timezone.utc),
    )
    _raise_mutation_error(
        error_code=result.error_code,
        error_message=result.error_message,
    )
    if result.review is None:
        raise RuntimeError("phase review decision returned no review")
    return PhaseReviewResponse.model_validate(result.review)
