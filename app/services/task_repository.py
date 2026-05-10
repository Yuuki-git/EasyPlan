from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.task import Task


class TaskRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def list_tasks_for_user(
        self,
        *,
        user_id: UUID,
        view_bucket: str | None = None,
    ) -> list[Task]:
        query = select(Task).where(Task.user_id == user_id)
        if view_bucket is not None:
            query = query.where(Task.view_bucket == view_bucket)
        query = query.order_by(Task.sort_order.asc(), Task.created_at.asc())
        result = await self.session.execute(query)
        return list(result.scalars().all())

    async def update_task_for_user(
        self,
        *,
        user_id: UUID,
        task_id: UUID,
        changes: dict[str, Any],
    ) -> Task | None:
        result = await self.session.execute(
            select(Task).where(
                Task.user_id == user_id,
                Task.id == task_id,
            )
        )
        task = result.scalar_one_or_none()
        if task is None:
            return None
        for field, value in changes.items():
            setattr(task, field, value)
        await self.session.commit()
        await self.session.refresh(task)
        return task
