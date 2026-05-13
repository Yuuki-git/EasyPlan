from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.task import Task
from app.models.thread import AgentThread


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

    async def create_task_for_user(
        self,
        *,
        user_id: UUID,
        title: str,
        description: str | None,
        view_bucket: str,
        parent_task_id: UUID | None,
    ) -> Task | None:
        try:
            parent_task: Task | None = None
            if parent_task_id is not None:
                result = await self.session.execute(
                    select(Task).where(
                        Task.user_id == user_id,
                        Task.id == parent_task_id,
                    )
                )
                parent_task = result.scalar_one_or_none()
                if parent_task is None:
                    await self.session.rollback()
                    return None

            thread_id = parent_task.thread_id if parent_task is not None else f"manual_{uuid4().hex}"
            if parent_task is None:
                self.session.add(
                    AgentThread(
                        user_id=user_id,
                        thread_id=thread_id,
                        intent_text=title,
                        status="completed",
                        current_node="manual_task",
                        next_nodes=[],
                        interrupt_payload=None,
                        latest_checkpoint_id=None,
                        task_tree=None,
                        error_code=None,
                        error_message=None,
                        expires_at=None,
                        interrupted_at=None,
                        completed_at=None,
                    )
                )

            task = Task(
                user_id=user_id,
                thread_id=thread_id,
                parent_task_id=parent_task_id,
                client_node_id=f"manual_{uuid4().hex}",
                title=title,
                description=description,
                node_type="action",
                status="active",
                view_bucket=view_bucket,
                estimated_minutes=None,
                sort_order=await self._next_sort_order(
                    user_id=user_id,
                    view_bucket=view_bucket,
                    parent_task_id=parent_task_id,
                ),
                ai_generated=False,
                user_edited=True,
                metadata_={"source": "manual"},
            )
            self.session.add(task)
            await self.session.commit()
        except Exception:
            await self.session.rollback()
            raise

        await self.session.refresh(task)
        return task

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

    async def delete_task_for_user(
        self,
        *,
        user_id: UUID,
        task_id: UUID,
    ) -> bool:
        try:
            result = await self.session.execute(
                delete(Task).where(
                    Task.user_id == user_id,
                    Task.id == task_id,
                )
            )
            await self.session.commit()
        except Exception:
            await self.session.rollback()
            raise
        return result.rowcount > 0

    async def _next_sort_order(
        self,
        *,
        user_id: UUID,
        view_bucket: str,
        parent_task_id: UUID | None,
    ) -> int:
        query = select(func.coalesce(func.max(Task.sort_order), -1) + 1).where(
            Task.user_id == user_id,
            Task.view_bucket == view_bucket,
        )
        if parent_task_id is None:
            query = query.where(Task.parent_task_id.is_(None))
        else:
            query = query.where(Task.parent_task_id == parent_task_id)
        result = await self.session.execute(query)
        return int(result.scalar_one())
