from datetime import datetime, timezone
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import and_, delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas import TaskTree
from app.models.task import Task, TaskDependency
from app.models.thread import AgentThread
from app.services.phase_planning import (
    calculate_phase_progress,
    choose_next_action,
    complete_final_phase,
    is_ai_phase_action,
)
from app.services.practice_repository import (
    PracticeLoopConflictError,
    PracticeLoopRepository,
)


class TaskRollupConflictError(ValueError):
    def __init__(self, *, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


TASK_ASSIST_CHILD_MY_DAY_FORBIDDEN = "TASK_ASSIST_CHILD_MY_DAY_FORBIDDEN"


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
        if view_bucket == "my_day":
            source = Task.metadata_["source"].astext
            explicit_my_day_task = and_(
                Task.is_in_my_day.is_(True),
                or_(source.is_(None), source != "task_assist"),
            )
            my_day_parent_ids = select(Task.id).where(
                Task.user_id == user_id,
                explicit_my_day_task,
            )
            query = query.where(
                or_(
                    explicit_my_day_task,
                    and_(
                        Task.parent_task_id.in_(my_day_parent_ids),
                        source == "task_assist",
                    ),
                )
            )
        elif view_bucket is not None:
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
        thread_id: str | None = None,
        is_in_my_day: bool = False,
    ) -> Task | None:
        view_bucket, is_in_my_day = _normalize_create_bucket(
            view_bucket=view_bucket,
            is_in_my_day=is_in_my_day,
        )
        try:
            parent_task: Task | None = None
            should_create_manual_thread = False
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
                thread_id = parent_task.thread_id
            elif thread_id is not None:
                result = await self.session.execute(
                    select(AgentThread).where(
                        AgentThread.user_id == user_id,
                        AgentThread.thread_id == thread_id,
                    )
                )
                thread = result.scalar_one_or_none()
                if thread is None:
                    await self.session.rollback()
                    return None
            else:
                thread_id = f"manual_{uuid4().hex}"
                should_create_manual_thread = True

            if should_create_manual_thread:
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
                is_in_my_day=is_in_my_day,
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
        try:
            result = await self.session.execute(
                select(Task)
                .where(
                    Task.user_id == user_id,
                    Task.id == task_id,
                )
                .with_for_update()
            )
            task = result.scalar_one_or_none()
            if task is None:
                return None

            previous_status = task.status
            if _metadata(task).get("source") == "task_assist" and (
                changes.get("is_in_my_day") is True
                or changes.get("view_bucket") == "my_day"
            ):
                raise TaskRollupConflictError(
                    code=TASK_ASSIST_CHILD_MY_DAY_FORBIDDEN,
                    message=(
                        "Assisted subtasks inherit My Day visibility from their parent "
                        "and cannot be added independently."
                    ),
                )
            if (
                changes.get("status") == "completed"
                and _metadata(task).get("assist_rollup") is True
            ):
                assist_children = await self._load_assist_children(
                    user_id=user_id,
                    parent_task_id=task.id,
                )
                if any(child.status != "completed" for child in assist_children):
                    raise TaskRollupConflictError(
                        code="TASK_ASSIST_CHILDREN_INCOMPLETE",
                        message="Complete all assisted subtasks before completing the parent task",
                    )
            practice_loop_id = _practice_loop_id(task)
            if (
                practice_loop_id is not None
                and previous_status == "completed"
                and changes.get("status") != "completed"
                and "status" in changes
            ):
                raise PracticeLoopConflictError(
                    code="PRACTICE_COMPLETION_IMMUTABLE",
                    message="A counted practice completion cannot be reopened",
                )
            for field, value in changes.items():
                setattr(task, field, value)

            rollup_parent_changed = False
            if (
                "status" in changes
                and task.status != previous_status
                and _metadata(task).get("source") == "task_assist"
                and task.parent_task_id is not None
            ):
                rollup_parent_changed = await self._recalculate_assist_parent(
                    user_id=user_id,
                    parent_task_id=task.parent_task_id,
                )

            if (
                practice_loop_id is not None
                and previous_status != "completed"
                and task.status == "completed"
            ):
                await PracticeLoopRepository(self.session).record_completion(
                    user_id=user_id,
                    task=task,
                    loop_id=practice_loop_id,
                    now=datetime.now(timezone.utc),
                )

            phase_id = _phase_id_for_ai_action(task)
            if (
                phase_id is not None
                and "status" in changes
                and task.status != previous_status
            ):
                await self._recalculate_thread_phase_state(
                    user_id=user_id,
                    thread_id=task.thread_id,
                )
            elif rollup_parent_changed:
                await self._recalculate_thread_phase_state(
                    user_id=user_id,
                    thread_id=task.thread_id,
                )
            await self.session.commit()
        except Exception:
            await self.session.rollback()
            raise

        await self.session.refresh(task)
        return task

    async def delete_task_for_user(
        self,
        *,
        user_id: UUID,
        task_id: UUID,
    ) -> bool:
        try:
            task_result = await self.session.execute(
                select(Task)
                .where(
                    Task.user_id == user_id,
                    Task.id == task_id,
                )
                .with_for_update()
            )
            task = task_result.scalar_one_or_none()
            if task is None:
                return False

            practice_loop_id = _practice_loop_id(task)
            if practice_loop_id is not None and task.status != "completed":
                await PracticeLoopRepository(
                    self.session
                ).clear_active_occurrence(
                    user_id=user_id,
                    loop_id=practice_loop_id,
                    task_id=task.id,
                )
            phase_id = _phase_id_for_ai_action(task)
            assist_parent_id = (
                task.parent_task_id
                if _metadata(task).get("source") == "task_assist"
                else None
            )
            result = await self.session.execute(
                delete(Task).where(
                    Task.user_id == user_id,
                    Task.id == task_id,
                )
            )
            if result.rowcount <= 0:
                return False
            rollup_parent_changed = False
            if assist_parent_id is not None:
                await self.session.flush()
                rollup_parent_changed = await self._recalculate_assist_parent(
                    user_id=user_id,
                    parent_task_id=assist_parent_id,
                )
            if phase_id is not None or rollup_parent_changed:
                await self._recalculate_thread_phase_state(
                    user_id=user_id,
                    thread_id=task.thread_id,
                )
            await self.session.commit()
        except Exception:
            await self.session.rollback()
            raise
        return True

    async def _load_assist_children(
        self,
        *,
        user_id: UUID,
        parent_task_id: UUID,
    ) -> list[Task]:
        result = await self.session.execute(
            select(Task).where(
                Task.user_id == user_id,
                Task.parent_task_id == parent_task_id,
            )
        )
        return [
            child
            for child in result.scalars().all()
            if _metadata(child).get("source") == "task_assist"
        ]

    async def _recalculate_assist_parent(
        self,
        *,
        user_id: UUID,
        parent_task_id: UUID,
    ) -> bool:
        parent_result = await self.session.execute(
            select(Task)
            .where(Task.user_id == user_id, Task.id == parent_task_id)
            .with_for_update()
        )
        parent = parent_result.scalar_one_or_none()
        if parent is None:
            return False
        metadata = dict(_metadata(parent))
        if metadata.get("assist_rollup") is not True:
            return False
        children = await self._load_assist_children(
            user_id=user_id,
            parent_task_id=parent_task_id,
        )
        previous_status = parent.status
        if not children:
            metadata.pop("assist_rollup", None)
            parent.metadata_ = metadata
            if parent.status == "completed":
                parent.status = "active"
        else:
            parent.status = (
                "completed"
                if all(child.status == "completed" for child in children)
                else "active"
            )
        return parent.status != previous_status

    async def _recalculate_thread_phase_state(
        self,
        *,
        user_id: UUID,
        thread_id: str,
    ) -> None:
        thread_result = await self.session.execute(
            select(AgentThread)
            .where(
                AgentThread.user_id == user_id,
                AgentThread.thread_id == thread_id,
            )
            .with_for_update()
        )
        thread = thread_result.scalar_one_or_none()
        if thread is None or not thread.task_tree:
            return

        tree = TaskTree.model_validate(thread.task_tree)
        context = tree.planning_context
        if context is None or context.current_phase is None:
            return

        tasks = await self._load_thread_tasks(user_id=user_id, thread_id=thread_id)
        dependencies = await self._load_dependencies(tasks)
        current_phase_id = context.current_phase.phase_id
        progress = calculate_phase_progress(tasks, current_phase_id)
        next_task = None
        if not progress.is_complete:
            next_task = choose_next_action(tasks, dependencies, current_phase_id)
        context.next_action_client_node_id = (
            next_task.client_node_id if next_task is not None else None
        )

        current_index = next(
            index
            for index, phase in enumerate(context.roadmap)
            if phase.phase_id == current_phase_id
        )
        if (
            context.schema_version == 1
            and progress.is_complete
            and current_index == len(context.roadmap) - 1
        ):
            tree.planning_context = complete_final_phase(context)
        thread.task_tree = tree.model_dump(mode="json")

    async def _load_thread_tasks(self, *, user_id: UUID, thread_id: str) -> list[Task]:
        result = await self.session.execute(
            select(Task).where(
                Task.user_id == user_id,
                Task.thread_id == thread_id,
            )
        )
        return list(result.scalars().all())

    async def _load_dependencies(
        self,
        tasks: list[Task],
    ) -> dict[UUID, set[UUID]]:
        task_ids = [task.id for task in tasks]
        if not task_ids:
            return {}
        result = await self.session.execute(
            select(TaskDependency).where(TaskDependency.task_id.in_(task_ids))
        )
        dependencies: dict[UUID, set[UUID]] = {}
        for dependency in result.scalars().all():
            dependencies.setdefault(dependency.task_id, set()).add(
                dependency.depends_on_task_id
            )
        return dependencies

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


def _normalize_create_bucket(*, view_bucket: str, is_in_my_day: bool) -> tuple[str, bool]:
    if view_bucket == "my_day":
        return "planned", True
    return view_bucket, is_in_my_day


def _metadata(task: Task) -> dict[str, Any]:
    return task.metadata_ if isinstance(task.metadata_, dict) else {}


def _phase_id_for_ai_action(task: Task) -> str | None:
    metadata = _metadata(task)
    phase_id = metadata.get("phase_id")
    if not isinstance(phase_id, str):
        return None
    return phase_id if is_ai_phase_action(task, phase_id) else None


def _practice_loop_id(task: Task) -> UUID | None:
    metadata = _metadata(task)
    value = metadata.get("practice_loop_id")
    if not isinstance(value, str):
        return None
    try:
        return UUID(value)
    except ValueError:
        return None
