from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Path, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import AuthUser, get_current_user
from app.api.schemas import TaskResponse, TaskUpdateRequest, TaskViewBucket
from app.db.session import get_db
from app.services.task_repository import TaskRepository


router = APIRouter(prefix="/api/tasks", tags=["tasks"])


def get_task_repository(
    session: Annotated[AsyncSession, Depends(get_db)],
) -> TaskRepository:
    return TaskRepository(session)


@router.get("", response_model=list[TaskResponse])
async def list_tasks(
    current_user: Annotated[AuthUser, Depends(get_current_user)],
    repository: Annotated[TaskRepository, Depends(get_task_repository)],
    view_bucket: Annotated[TaskViewBucket | None, Query()] = None,
) -> list[TaskResponse]:
    return await repository.list_tasks_for_user(
        user_id=current_user.id,
        view_bucket=view_bucket,
    )


@router.patch("/{task_id}", response_model=TaskResponse)
async def update_task(
    task_id: Annotated[UUID, Path()],
    payload: TaskUpdateRequest,
    current_user: Annotated[AuthUser, Depends(get_current_user)],
    repository: Annotated[TaskRepository, Depends(get_task_repository)],
) -> TaskResponse:
    task = await repository.update_task_for_user(
        user_id=current_user.id,
        task_id=task_id,
        changes=payload.model_dump(exclude_none=True),
    )
    if task is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Task not found")
    return task
