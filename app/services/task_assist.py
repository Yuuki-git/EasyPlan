from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Protocol
from uuid import UUID, uuid4

from pydantic import TypeAdapter, ValidationError
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas import (
    AssistTaskDraft,
    DecomposeAssistProposal,
    StartAssistProposal,
    TaskAssistApplyReceipt,
    TaskAssistApplyResponse,
    TaskAssistMode,
    TaskAssistProposal,
    TaskResponse,
    UnstickAssistProposal,
)
from app.models.task import Task, TaskDependency
from app.models.task_assist import TaskAssistRun
from app.models.thread import AgentThread
from app.services.action_quality import ABSTRACT_TASK_TERMS


TASK_ASSIST_PROPOSAL_ADAPTER = TypeAdapter(TaskAssistProposal)
TASK_ASSIST_EXPIRY = timedelta(hours=24)
TASK_ASSIST_SAFE_PROVIDER_MESSAGE = "AI 辅助暂时没有生成可用建议，请稍后重试。"
TASK_ASSIST_SAFE_INVALID_MESSAGE = "这次建议还不够具体，请重新生成后再试。"
TASK_ASSIST_INTERRUPTED_MESSAGE = "本次辅助生成已中断，请使用相同模式重新发起。"
TASK_ASSIST_TERMINAL_STATUSES = {"applied", "cancelled", "failed", "expired"}
INVALID_PLACEHOLDERS = {
    "开始做",
    "准备开始",
    "完成任务",
    "学习完成",
    "完成即可",
    "少做一点",
    "降低难度",
}


class TaskAssistError(RuntimeError):
    def __init__(self, *, code: str, message: str, status_code: int = 409) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


class TaskAssistProposalClient(Protocol):
    async def create_task_assist_proposal(
        self,
        *,
        mode: TaskAssistMode,
        prompt: str,
    ) -> dict[str, Any]: ...


@dataclass(frozen=True)
class TaskAssistContext:
    task: dict[str, Any]
    ancestors: list[dict[str, Any]]
    project: dict[str, Any]
    existing_children: list[dict[str, Any]]
    user_context: str | None

    def model_payload(self) -> dict[str, Any]:
        return {
            "task": self.task,
            "ancestors": self.ancestors,
            "project": self.project,
            "existing_children": self.existing_children,
            "user_context": self.user_context,
        }


class TaskAssistRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_owned(
        self,
        *,
        user_id: UUID,
        task_id: UUID,
        request_id: UUID,
        for_update: bool = False,
    ) -> TaskAssistRun | None:
        query = select(TaskAssistRun).where(
            TaskAssistRun.user_id == user_id,
            TaskAssistRun.task_id == task_id,
            TaskAssistRun.request_id == request_id,
        )
        if for_update:
            query = query.with_for_update()
        result = await self.session.execute(query)
        return result.scalar_one_or_none()

    async def create_or_get(
        self,
        *,
        user_id: UUID,
        task: Task,
        request_id: UUID,
        mode: TaskAssistMode,
        user_context: str | None,
        lease_owner: str | None = None,
        now: datetime | None = None,
    ) -> tuple[TaskAssistRun, bool]:
        current_time = now or datetime.now(timezone.utc)
        locked_task_result = await self.session.execute(
            select(Task)
            .where(Task.user_id == user_id, Task.id == task.id)
            .with_for_update()
        )
        locked_task = locked_task_result.scalar_one_or_none()
        if locked_task is None:
            raise TaskAssistError(
                code="TASK_ASSIST_TASK_NOT_FOUND",
                message="任务不存在。",
                status_code=404,
            )
        existing = await self.get_owned(
            user_id=user_id,
            task_id=task.id,
            request_id=request_id,
        )
        if existing is not None:
            if existing.mode != mode or existing.user_context != user_context:
                raise TaskAssistError(
                    code="TASK_ASSIST_REQUEST_CONFLICT",
                    message="该 request_id 已用于不同的辅助请求。",
                )
            await self.fail_interrupted_if_lease_expired(existing, now=current_time)
            return existing, False

        active_result = await self.session.execute(
            select(TaskAssistRun)
            .where(
                TaskAssistRun.user_id == user_id,
                TaskAssistRun.task_id == task.id,
                TaskAssistRun.status == "running",
            )
            .with_for_update()
        )
        active_run = active_result.scalar_one_or_none()
        if active_run is not None:
            if _lease_is_active(active_run.lease_expires_at, current_time):
                raise TaskAssistError(
                    code="TASK_ASSIST_ACTIVE_RUN",
                    message="该任务已有正在生成的辅助建议。",
                )
            _mark_interrupted(active_run)
            await self.session.flush()

        active_count_result = await self.session.execute(
            select(func.count(TaskAssistRun.id)).where(
                TaskAssistRun.user_id == user_id,
                TaskAssistRun.status == "running",
                TaskAssistRun.lease_expires_at > current_time,
            )
        )
        active_count = int(active_count_result.scalar_one())
        max_active = max(1, int(os.getenv("EASYPLAN_TASK_ASSIST_MAX_ACTIVE_PER_USER", "2")))
        if active_count >= max_active:
            raise TaskAssistError(
                code="TASK_ASSIST_RATE_LIMITED",
                message="正在生成的任务辅助过多，请稍后再试。",
                status_code=429,
            )

        owner = (lease_owner or f"queued:{request_id}")[:128]
        run = TaskAssistRun(
            id=uuid4(),
            user_id=user_id,
            task_id=task.id,
            thread_id=task.thread_id,
            request_id=request_id,
            mode=mode,
            user_context=user_context,
            status="running",
            stage="queued",
            lease_owner=owner,
            lease_expires_at=current_time + timedelta(seconds=_lease_seconds()),
            target_task_updated_at=locked_task.updated_at,
            proposal=None,
            apply_receipt=None,
            error_code=None,
            error_message=None,
            expires_at=current_time + TASK_ASSIST_EXPIRY,
            applied_at=None,
        )
        self.session.add(run)
        try:
            await self.session.commit()
        except IntegrityError:
            await self.session.rollback()
            existing = await self.get_owned(
                user_id=user_id,
                task_id=task.id,
                request_id=request_id,
            )
            if existing is None:
                active_result = await self.session.execute(
                    select(TaskAssistRun).where(
                        TaskAssistRun.user_id == user_id,
                        TaskAssistRun.task_id == task.id,
                        TaskAssistRun.status == "running",
                    )
                )
                if active_result.scalar_one_or_none() is not None:
                    raise TaskAssistError(
                        code="TASK_ASSIST_ACTIVE_RUN",
                        message="该任务已有正在生成的辅助建议。",
                    )
                raise
            return existing, False
        await self.session.refresh(run)
        return run, True

    async def claim_lease(
        self,
        run: TaskAssistRun,
        *,
        lease_owner: str,
        now: datetime | None = None,
    ) -> bool:
        current_time = now or datetime.now(timezone.utc)
        await self.session.refresh(run, with_for_update=True)
        if run.status != "running":
            return False
        if (
            _lease_is_active(run.lease_expires_at, current_time)
            and run.lease_owner not in {None, lease_owner}
        ):
            return False
        run.lease_owner = lease_owner[:128]
        run.lease_expires_at = current_time + timedelta(seconds=_lease_seconds())
        await self.session.commit()
        return True

    async def renew_lease(
        self,
        run: TaskAssistRun,
        *,
        lease_owner: str,
        now: datetime | None = None,
    ) -> bool:
        current_time = now or datetime.now(timezone.utc)
        await self.session.refresh(run, with_for_update=True)
        if run.status != "running" or run.lease_owner != lease_owner:
            return False
        run.lease_expires_at = current_time + timedelta(seconds=_lease_seconds())
        await self.session.commit()
        return True

    async def mark_stage(
        self,
        run: TaskAssistRun,
        stage: str,
        *,
        lease_owner: str | None = None,
    ) -> None:
        if run.status != "running":
            return
        if lease_owner is not None and run.lease_owner != lease_owner:
            return
        run.stage = stage
        if lease_owner is not None:
            run.lease_expires_at = datetime.now(timezone.utc) + timedelta(
                seconds=_lease_seconds()
            )
        await self.session.commit()

    async def save_proposal(
        self,
        run: TaskAssistRun,
        proposal: TaskAssistProposal,
        *,
        lease_owner: str | None = None,
    ) -> bool:
        await self.session.refresh(run, with_for_update=True)
        if run.status != "running" or (
            lease_owner is not None
            and (
                run.lease_owner != lease_owner
                or not _lease_is_active(run.lease_expires_at, datetime.now(timezone.utc))
            )
        ):
            return False
        run.proposal = proposal.model_dump(mode="json")
        run.status = "ready"
        run.stage = "ready"
        run.error_code = None
        run.error_message = None
        run.lease_owner = None
        run.lease_expires_at = None
        await self.session.commit()
        return True

    async def cancel(self, run: TaskAssistRun) -> TaskAssistRun:
        await self.session.refresh(run, with_for_update=True)
        if run.status == "cancelled":
            return run
        if run.status not in {"running", "ready"}:
            raise TaskAssistError(
                code="TASK_ASSIST_NOT_CANCELLABLE",
                message="当前辅助请求不能取消。",
            )
        run.status = "cancelled"
        run.stage = "cancelled"
        run.proposal = None
        run.lease_owner = None
        run.lease_expires_at = None
        await self.session.commit()
        return run

    async def fail(
        self,
        run: TaskAssistRun,
        *,
        code: str,
        message: str,
        lease_owner: str | None = None,
    ) -> None:
        await self.session.refresh(run, with_for_update=True)
        if run.status != "running" or (
            lease_owner is not None and run.lease_owner != lease_owner
        ):
            return
        run.status = "failed"
        run.stage = "failed"
        run.proposal = None
        run.error_code = code[:128]
        run.error_message = message[:500]
        run.lease_owner = None
        run.lease_expires_at = None
        await self.session.commit()

    async def fail_interrupted_if_lease_expired(
        self,
        run: TaskAssistRun,
        *,
        now: datetime | None = None,
    ) -> bool:
        current_time = now or datetime.now(timezone.utc)
        await self.session.refresh(run, with_for_update=True)
        if run.status != "running" or _lease_is_active(
            run.lease_expires_at, current_time
        ):
            return False
        _mark_interrupted(run)
        await self.session.commit()
        return True

    async def expire_if_needed(
        self,
        run: TaskAssistRun,
        *,
        now: datetime | None = None,
    ) -> TaskAssistRun:
        current_time = now or datetime.now(timezone.utc)
        if run.status == "ready" and run.expires_at <= current_time:
            run.status = "expired"
            run.stage = "expired"
            run.proposal = None
            run.lease_owner = None
            run.lease_expires_at = None
            await self.session.commit()
        return run


class TaskAssistService:
    def __init__(
        self,
        session: AsyncSession,
        *,
        proposal_client: TaskAssistProposalClient | None = None,
    ) -> None:
        self.session = session
        self.repository = TaskAssistRepository(session)
        self.proposal_client = proposal_client

    async def load_supported_task(self, *, user_id: UUID, task_id: UUID) -> Task:
        result = await self.session.execute(
            select(Task).where(Task.user_id == user_id, Task.id == task_id)
        )
        task = result.scalar_one_or_none()
        if task is None:
            raise TaskAssistError(
                code="TASK_ASSIST_TASK_NOT_FOUND",
                message="任务不存在。",
                status_code=404,
            )
        metadata = _metadata(task)
        if (
            task.node_type != "action"
            or task.status not in {"active", "today"}
            or metadata.get("practice_loop_id") is not None
        ):
            raise TaskAssistError(
                code="TASK_ASSIST_UNSUPPORTED_TASK",
                message="该任务当前不支持 AI 辅助。",
            )
        return task

    async def build_context(
        self,
        *,
        user_id: UUID,
        task: Task,
        mode: TaskAssistMode,
        user_context: str | None,
    ) -> TaskAssistContext:
        metadata = _metadata(task)
        if mode == "decompose" and metadata.get("assist_rollup") is True:
            raise TaskAssistError(
                code="TASK_ASSIST_UNSUPPORTED_TASK",
                message="该任务已经拆分，不能重复拆分。",
            )

        ancestors: list[dict[str, Any]] = []
        parent_id = task.parent_task_id
        for _ in range(2):
            if parent_id is None:
                break
            result = await self.session.execute(
                select(Task).where(Task.user_id == user_id, Task.id == parent_id)
            )
            parent = result.scalar_one_or_none()
            if parent is None:
                break
            ancestors.append({"title": parent.title, "description": parent.description})
            parent_id = parent.parent_task_id

        thread_result = await self.session.execute(
            select(AgentThread).where(
                AgentThread.user_id == user_id,
                AgentThread.thread_id == task.thread_id,
            )
        )
        thread = thread_result.scalar_one_or_none()
        task_tree = thread.task_tree if thread is not None and isinstance(thread.task_tree, dict) else {}
        planning_context = task_tree.get("planning_context") if isinstance(task_tree, dict) else None
        strategy_context = task_tree.get("strategy_context") if isinstance(task_tree, dict) else None
        current_phase = (
            planning_context.get("current_phase")
            if isinstance(planning_context, dict)
            else None
        )
        project = {
            "intent_text": thread.intent_text if thread is not None else None,
            "summary": task_tree.get("summary") if isinstance(task_tree, dict) else None,
            "strategy_summary": _safe_strategy_summary(strategy_context),
            "current_phase_objective": (
                current_phase.get("objective") if isinstance(current_phase, dict) else None
            ),
            "next_action_client_node_id": (
                planning_context.get("next_action_client_node_id")
                if isinstance(planning_context, dict)
                else None
            ),
        }

        child_result = await self.session.execute(
            select(Task).where(
                Task.user_id == user_id,
                Task.parent_task_id == task.id,
            )
        )
        children = list(child_result.scalars().all())
        return TaskAssistContext(
            task={
                "title": task.title,
                "description": task.description,
                "estimated_minutes": task.estimated_minutes,
                "done_criteria": metadata.get("done_criteria"),
                "start_hint": metadata.get("start_hint"),
                "fallback_action": metadata.get("fallback_action"),
                "status": task.status,
            },
            ancestors=ancestors,
            project=project,
            existing_children=[
                {
                    "title": child.title,
                    "description": child.description,
                    "estimated_minutes": child.estimated_minutes,
                    "status": child.status,
                }
                for child in children
            ],
            user_context=user_context,
        )

    async def generate_proposal(
        self,
        *,
        user_id: UUID,
        run: TaskAssistRun,
    ) -> TaskAssistProposal:
        if self.proposal_client is None:
            raise RuntimeError("proposal_client is required")
        task = await self.load_supported_task(user_id=user_id, task_id=run.task_id)
        context = await self.build_context(
            user_id=user_id,
            task=task,
            mode=run.mode,
            user_context=run.user_context,
        )
        prompt = build_task_assist_prompt(mode=run.mode, context=context)
        errors: list[str] = []
        for attempt in range(2):
            repair_prompt = prompt
            if errors:
                repair_prompt += (
                    "\n\n上一次 proposal 未通过校验。只修复以下问题，不扩展任务范围：\n- "
                    + "\n- ".join(errors)
                )
            payload = await self.proposal_client.create_task_assist_proposal(
                mode=run.mode,
                prompt=repair_prompt,
            )
            try:
                proposal = TASK_ASSIST_PROPOSAL_ADAPTER.validate_python(payload)
            except ValidationError as exc:
                errors = ["proposal schema 不合法"]
                if attempt == 1:
                    raise TaskAssistError(
                        code="TASK_ASSIST_INVALID_PROPOSAL",
                        message=TASK_ASSIST_SAFE_INVALID_MESSAGE,
                    ) from exc
                continue
            errors = validate_task_assist_proposal(
                mode=run.mode,
                proposal=proposal,
                parent_estimated_minutes=task.estimated_minutes,
            )
            if not errors:
                return proposal
        raise TaskAssistError(
            code="TASK_ASSIST_INVALID_PROPOSAL",
            message=TASK_ASSIST_SAFE_INVALID_MESSAGE,
        )

    async def apply(
        self,
        *,
        user_id: UUID,
        task_id: UUID,
        request_id: UUID,
        selected_option_id: str | None,
        now: datetime | None = None,
    ) -> TaskAssistApplyResponse:
        current_time = now or datetime.now(timezone.utc)
        async with self.session.begin():
            run = await self.repository.get_owned(
                user_id=user_id,
                task_id=task_id,
                request_id=request_id,
                for_update=True,
            )
            if run is None:
                raise TaskAssistError(
                    code="TASK_ASSIST_RUN_NOT_FOUND",
                    message="辅助请求不存在。",
                    status_code=404,
                )
            if run.status == "applied" and isinstance(run.apply_receipt, dict):
                return TaskAssistApplyResponse.model_validate(run.apply_receipt)
            if run.status != "ready" or run.proposal is None:
                raise TaskAssistError(
                    code="TASK_ASSIST_NOT_READY",
                    message="辅助建议尚未准备好或已失效。",
                )
            if run.expires_at <= current_time:
                run.status = "expired"
                run.stage = "expired"
                run.proposal = None
                raise TaskAssistError(
                    code="TASK_ASSIST_EXPIRED",
                    message="辅助建议已过期，请重新生成。",
                )

            task_result = await self.session.execute(
                select(Task)
                .where(Task.user_id == user_id, Task.id == task_id)
                .with_for_update()
            )
            task = task_result.scalar_one_or_none()
            if task is None:
                raise TaskAssistError(
                    code="TASK_ASSIST_TASK_NOT_FOUND",
                    message="任务不存在。",
                    status_code=404,
                )
            if task.updated_at != run.target_task_updated_at:
                raise TaskAssistError(
                    code="TASK_ASSIST_CONTEXT_STALE",
                    message="任务已发生变化，请重新生成辅助建议。",
                )

            proposal = TASK_ASSIST_PROPOSAL_ADAPTER.validate_python(run.proposal)
            affected: list[Task] = [task]
            metadata = dict(_metadata(task))
            if isinstance(proposal, StartAssistProposal):
                if selected_option_id is not None:
                    raise TaskAssistError(
                        code="TASK_ASSIST_INVALID_APPLY",
                        message="开始建议不接受选项参数。",
                    )
                metadata["start_hint"] = proposal.starter_step.start_hint or proposal.starter_step.title
                task.metadata_ = metadata
                task.user_edited = True
            elif isinstance(proposal, UnstickAssistProposal):
                if selected_option_id is None:
                    raise TaskAssistError(
                        code="TASK_ASSIST_OPTION_REQUIRED",
                        message="请选择一个恢复选项。",
                    )
                selected = next(
                    (option for option in proposal.options if option.option_id == selected_option_id),
                    None,
                )
                if selected is None:
                    raise TaskAssistError(
                        code="TASK_ASSIST_OPTION_INVALID",
                        message="所选恢复选项不存在。",
                    )
                metadata["fallback_action"] = selected.action
                task.metadata_ = metadata
                task.user_edited = True
            else:
                if selected_option_id is not None:
                    raise TaskAssistError(
                        code="TASK_ASSIST_INVALID_APPLY",
                        message="拆分建议不接受选项参数。",
                    )
                if metadata.get("assist_rollup") is True:
                    raise TaskAssistError(
                        code="TASK_ASSIST_UNSUPPORTED_TASK",
                        message="该任务已经拆分，不能重复拆分。",
                    )
                affected.extend(
                    await self._insert_decompose_children(
                        user_id=user_id,
                        task=task,
                        request_id=request_id,
                        proposal=proposal,
                    )
                )
                metadata["assist_rollup"] = True
                task.metadata_ = metadata

            await self.session.flush()
            receipt = TaskAssistApplyReceipt(
                request_id=request_id,
                proposal_type=proposal.proposal_type,
                applied_at=current_time,
                affected_task_ids=[item.id for item in affected],
            )
            response = TaskAssistApplyResponse(
                status="applied",
                task=TaskResponse.model_validate(task),
                tasks=[TaskResponse.model_validate(item) for item in affected],
                apply_receipt=receipt,
            )
            run.status = "applied"
            run.stage = "applied"
            run.applied_at = current_time
            run.lease_owner = None
            run.lease_expires_at = None
            run.apply_receipt = response.model_dump(mode="json")
        return response

    async def _insert_decompose_children(
        self,
        *,
        user_id: UUID,
        task: Task,
        request_id: UUID,
        proposal: DecomposeAssistProposal,
    ) -> list[Task]:
        order_result = await self.session.execute(
            select(func.coalesce(func.max(Task.sort_order), -1) + 1).where(
                Task.user_id == user_id,
                Task.parent_task_id == task.id,
            )
        )
        start_order = int(order_result.scalar_one())
        children: list[Task] = []
        by_draft_id: dict[str, Task] = {}
        for index, draft in enumerate(proposal.subtasks):
            child = Task(
                id=uuid4(),
                user_id=user_id,
                thread_id=task.thread_id,
                parent_task_id=task.id,
                client_node_id=_assist_client_node_id(request_id, draft.draft_id),
                title=draft.title,
                description=draft.description,
                node_type="action",
                status="active",
                view_bucket=task.view_bucket,
                is_in_my_day=False,
                estimated_minutes=draft.estimated_minutes,
                sort_order=start_order + index,
                ai_generated=True,
                user_edited=False,
                metadata_={
                    "source": "task_assist",
                    "assist_request_id": str(request_id),
                    "assist_parent_rollup": True,
                    "done_criteria": draft.done_criteria,
                    "start_hint": draft.start_hint,
                    "fallback_action": draft.fallback_action,
                },
            )
            children.append(child)
            by_draft_id[draft.draft_id] = child
        self.session.add_all(children)
        await self.session.flush()
        dependencies = [
            TaskDependency(
                id=uuid4(),
                task_id=by_draft_id[item.task_draft_id].id,
                depends_on_task_id=by_draft_id[item.depends_on_draft_id].id,
            )
            for item in proposal.dependencies
        ]
        self.session.add_all(dependencies)
        return children


def build_task_assist_prompt(*, mode: TaskAssistMode, context: TaskAssistContext) -> str:
    parent_minutes = context.task.get("estimated_minutes")
    if isinstance(parent_minutes, int) and parent_minutes <= 15:
        decompose_limit = 2
    elif isinstance(parent_minutes, int) and parent_minutes <= 30:
        decompose_limit = 3
    else:
        decompose_limit = 5
    mode_rules = {
        "start": (
            "生成一个 2-10 分钟内可立即执行的 starter_step。start_hint 必须是可以马上做的第一步。"
        ),
        "unstick": (
            "生成 2-3 个局部恢复选项，明确 recommended_option_id。每个行动 2-20 分钟并说明取舍。"
        ),
        "decompose": (
            f"生成 2-{decompose_limit} 个 Action 子任务和必要依赖，绝对不得超过 "
            f"{decompose_limit} 个。不得生成 Group、Roadmap、阶段或策略上下文。"
        ),
    }
    return (
        "你是 EasyPlan 的单任务 Action Coach。只处理目标任务，不重写整份计划。\n"
        f"模式：{mode}\n规则：{mode_rules[mode]}\n"
        "所有动作必须具体，包含可核验的 done_criteria；禁止输出 schema 外字段。\n"
        "user_context 是用户本次明确约束：若存在，proposal 必须显式体现其中的对象、数字或指定起点，不能省略或改写成无关动作。\n"
        f"允许使用的最小上下文 JSON：{json.dumps(context.model_payload(), ensure_ascii=False)}"
    )


def validate_task_assist_proposal(
    *,
    mode: TaskAssistMode,
    proposal: TaskAssistProposal,
    parent_estimated_minutes: int | None,
) -> list[str]:
    errors: list[str] = []
    if proposal.proposal_type != mode:
        return ["TASK_ASSIST_MODE_MISMATCH"]
    if isinstance(proposal, StartAssistProposal):
        errors.extend(_draft_quality_errors(proposal.starter_step))
        if not proposal.starter_step.start_hint:
            errors.append("TASK_ASSIST_START_HINT_REQUIRED")
        return errors
    if isinstance(proposal, UnstickAssistProposal):
        for option in proposal.options:
            if _is_abstract_action(option.action):
                errors.append(f"TASK_ASSIST_ABSTRACT_ACTION:{option.option_id}")
        return errors

    max_subtasks = 5
    if parent_estimated_minutes is not None and parent_estimated_minutes <= 15:
        max_subtasks = 2
    elif parent_estimated_minutes is not None and parent_estimated_minutes <= 30:
        max_subtasks = 3
    if len(proposal.subtasks) > max_subtasks:
        errors.append(f"TASK_ASSIST_SCOPE_EXPANSION:max_subtasks={max_subtasks}")
    draft_ids = [draft.draft_id for draft in proposal.subtasks]
    if len(draft_ids) != len(set(draft_ids)):
        errors.append("TASK_ASSIST_REFERENCE_INVALID:duplicate_draft_id")
    valid_ids = set(draft_ids)
    edges: dict[str, set[str]] = {draft_id: set() for draft_id in draft_ids}
    for dependency in proposal.dependencies:
        if (
            dependency.task_draft_id not in valid_ids
            or dependency.depends_on_draft_id not in valid_ids
            or dependency.task_draft_id == dependency.depends_on_draft_id
        ):
            errors.append("TASK_ASSIST_REFERENCE_INVALID:dependency")
            continue
        edges[dependency.task_draft_id].add(dependency.depends_on_draft_id)
    if _has_dependency_cycle(edges):
        errors.append("TASK_ASSIST_DEPENDENCY_CYCLE")
    for draft in proposal.subtasks:
        errors.extend(_draft_quality_errors(draft))
    return errors


def _draft_quality_errors(draft: AssistTaskDraft) -> list[str]:
    errors: list[str] = []
    if _is_abstract_action(draft.title):
        errors.append(f"TASK_ASSIST_ABSTRACT_ACTION:{draft.draft_id}")
    if draft.done_criteria.strip() in INVALID_PLACEHOLDERS:
        errors.append(f"TASK_ASSIST_INVALID_DONE_CRITERIA:{draft.draft_id}")
    if draft.start_hint and draft.start_hint.strip() in INVALID_PLACEHOLDERS:
        errors.append(f"TASK_ASSIST_INVALID_START_HINT:{draft.draft_id}")
    if draft.fallback_action and draft.fallback_action.strip() in INVALID_PLACEHOLDERS:
        errors.append(f"TASK_ASSIST_INVALID_FALLBACK:{draft.draft_id}")
    return errors


def _is_abstract_action(text: str) -> bool:
    normalized = text.strip()
    if normalized in ABSTRACT_TASK_TERMS or normalized in INVALID_PLACEHOLDERS:
        return True
    return any(normalized == f"{term}一下" for term in ABSTRACT_TASK_TERMS)


def _has_dependency_cycle(edges: dict[str, set[str]]) -> bool:
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(node: str) -> bool:
        if node in visiting:
            return True
        if node in visited:
            return False
        visiting.add(node)
        if any(visit(dependency) for dependency in edges.get(node, set())):
            return True
        visiting.remove(node)
        visited.add(node)
        return False

    return any(visit(node) for node in edges)


def _assist_client_node_id(request_id: UUID, draft_id: str) -> str:
    digest = hashlib.sha256(f"{request_id}:{draft_id}".encode("utf-8")).hexdigest()[:32]
    return f"assist_{digest}"


def _metadata(task: Task) -> dict[str, Any]:
    return task.metadata_ if isinstance(task.metadata_, dict) else {}


def _lease_seconds() -> int:
    return max(15, int(os.getenv("EASYPLAN_TASK_ASSIST_LEASE_SECONDS", "30")))


def _lease_is_active(expires_at: datetime | None, now: datetime) -> bool:
    if expires_at is None:
        return False
    normalized = (
        expires_at.replace(tzinfo=timezone.utc)
        if expires_at.tzinfo is None
        else expires_at.astimezone(timezone.utc)
    )
    return normalized > now


def _mark_interrupted(run: TaskAssistRun) -> None:
    run.status = "failed"
    run.stage = "failed"
    run.proposal = None
    run.error_code = "TASK_ASSIST_INTERRUPTED"
    run.error_message = TASK_ASSIST_INTERRUPTED_MESSAGE
    run.lease_owner = None
    run.lease_expires_at = None


def _safe_strategy_summary(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    allowed = {
        "strategy_type",
        "goal",
        "definition_of_done",
        "current_judgment",
        "decision_question",
    }
    return {key: value[key] for key in allowed if key in value}
