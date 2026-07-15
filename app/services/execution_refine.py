from __future__ import annotations

import copy
import hashlib
import json
import math
import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable, Protocol, Sequence
from uuid import UUID, uuid4

from pydantic import ValidationError
from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas import (
    AddTaskOperation,
    ExecutionDiffOperation,
    ExecutionRefineApplyReceipt,
    ExecutionRefineMode,
    ExecutionRefineProposal,
    ExecutionRefineRequest,
    ExecutionTaskChanges,
    ReorderSiblingsOperation,
    SetMyDayOperation,
    TaskNode,
    TaskTree,
    UpdateTaskOperation,
)
from app.models.execution_refine import ExecutionRefineRun
from app.models.practice import PhaseReview
from app.models.task import Task, TaskDependency
from app.models.thread import AgentThread
from app.services.action_quality import score_action_node
from app.services.llm_service import LLMStructuredOutputError
from app.services.strategy_context import validate_strategy_context
from app.services.task_repository import TaskRepository


EXECUTION_REFINE_EXPIRY = timedelta(hours=24)
EXECUTION_REFINE_TERMINAL_STATUSES = {
    "applied",
    "cancelled",
    "failed",
    "expired",
}
EXECUTION_REFINE_SAFE_INVALID_MESSAGE = "这次调整方案未能通过安全校验，请重新生成。"
EXECUTION_REFINE_INTERRUPTED_MESSAGE = "本次计划调整已中断，请重新发起。"
EXECUTION_REFINE_SAFE_FAILURE_MESSAGE = "本次计划调整暂时未完成，请稍后重试。"
INVALID_QUALITY_PLACEHOLDERS = {
    "placeholder",
    "todo",
    "tbd",
    "开始做",
    "准备开始",
    "完成任务",
    "学习完成",
    "完成即可",
    "少做一点",
    "降低难度",
}
INVALID_QUALITY_MARKERS = (
    "placeholder",
    "todo",
    "tbd",
    "占位",
    "无操作",
    "待补充",
)
MAX_EXECUTION_REFINE_REPAIRS = 2


class ExecutionRefineError(RuntimeError):
    def __init__(self, *, code: str, message: str, status_code: int = 409) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


class ExecutionRefineProposalClient(Protocol):
    async def create_execution_refine_proposal(self, *, prompt: str) -> dict[str, Any]: ...


@dataclass(frozen=True)
class ExecutionRefineValidationIssue:
    error_code: str
    message: str
    fix_suggestion: str
    operation_index: int | None = None
    task_ref: str | None = None

    def model_payload(self) -> dict[str, Any]:
        return {
            "error_code": self.error_code,
            "operation_index": self.operation_index,
            "task_ref": self.task_ref,
            "message": self.message,
            "fix_suggestion": self.fix_suggestion,
        }


@dataclass(frozen=True)
class ExecutionRefineScope:
    thread_id: str
    task_tree: TaskTree
    snapshot: dict[str, Any]
    fingerprint: str
    task_records: dict[str, dict[str, Any]]
    dependency_edges: tuple[tuple[str, str], ...]
    current_phase_id: str | None
    intent_type: str


class ExecutionRefineRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_owned(
        self,
        *,
        user_id: UUID,
        thread_id: str,
        request_id: UUID,
        for_update: bool = False,
    ) -> ExecutionRefineRun | None:
        query = select(ExecutionRefineRun).where(
            ExecutionRefineRun.user_id == user_id,
            ExecutionRefineRun.thread_id == thread_id,
            ExecutionRefineRun.request_id == request_id,
        )
        if for_update:
            query = query.with_for_update()
        result = await self.session.execute(query)
        return result.scalar_one_or_none()

    async def create_or_get(
        self,
        *,
        user_id: UUID,
        thread_id: str,
        request: ExecutionRefineRequest,
        scope: ExecutionRefineScope,
        lease_owner: str | None = None,
        now: datetime | None = None,
    ) -> tuple[ExecutionRefineRun, bool]:
        current_time = now or datetime.now(timezone.utc)
        if scope.thread_id != thread_id:
            raise ExecutionRefineError(
                code="EXECUTION_REFINE_SCOPE_FORBIDDEN",
                message="计划范围不匹配。",
                status_code=404,
            )
        thread_result = await self.session.execute(
            select(AgentThread)
            .where(
                AgentThread.user_id == user_id,
                AgentThread.thread_id == thread_id,
            )
            .with_for_update()
        )
        locked_thread = thread_result.scalar_one_or_none()
        if locked_thread is None:
            raise ExecutionRefineError(
                code="EXECUTION_REFINE_SCOPE_FORBIDDEN",
                message="计划不存在。",
                status_code=404,
            )
        if _thread_has_conflicting_plan_run(locked_thread, current_time):
            raise ExecutionRefineError(
                code="EXECUTION_REFINE_PROJECT_BUSY",
                message="当前计划正在生成或提交，请稍后再调整。",
            )

        input_context = request.model_dump(mode="json")
        existing = await self.get_owned(
            user_id=user_id,
            thread_id=thread_id,
            request_id=request.request_id,
        )
        if existing is not None:
            if existing.mode != request.mode or existing.input_context != input_context:
                raise ExecutionRefineError(
                    code="EXECUTION_REFINE_REQUEST_CONFLICT",
                    message="该 request_id 已用于不同的调整请求。",
                )
            await self.fail_interrupted_if_lease_expired(existing, now=current_time)
            return existing, False

        active_result = await self.session.execute(
            select(ExecutionRefineRun)
            .where(
                ExecutionRefineRun.user_id == user_id,
                ExecutionRefineRun.thread_id == thread_id,
                ExecutionRefineRun.status == "running",
            )
            .with_for_update()
        )
        active_run = active_result.scalar_one_or_none()
        if active_run is not None:
            if _lease_is_active(active_run.lease_expires_at, current_time):
                raise ExecutionRefineError(
                    code="EXECUTION_REFINE_ACTIVE_RUN",
                    message="该计划已有正在生成的调整方案。",
                )
            _mark_interrupted(active_run)
            await self.session.flush()

        active_count_result = await self.session.execute(
            select(func.count(ExecutionRefineRun.id)).where(
                ExecutionRefineRun.user_id == user_id,
                ExecutionRefineRun.status == "running",
                ExecutionRefineRun.lease_expires_at > current_time,
            )
        )
        active_count = int(active_count_result.scalar_one())
        max_active = max(
            1,
            int(os.getenv("EASYPLAN_EXECUTION_REFINE_MAX_ACTIVE_PER_USER", "2")),
        )
        if active_count >= max_active:
            raise ExecutionRefineError(
                code="EXECUTION_REFINE_RATE_LIMITED",
                message="正在生成的计划调整过多，请稍后再试。",
                status_code=429,
            )

        run = ExecutionRefineRun(
            id=uuid4(),
            user_id=user_id,
            thread_id=thread_id,
            request_id=request.request_id,
            mode=request.mode,
            input_context=input_context,
            scope_snapshot=scope.snapshot,
            scope_fingerprint=scope.fingerprint,
            status="running",
            stage="queued",
            proposal=None,
            apply_receipt=None,
            error_code=None,
            error_message=None,
            lease_owner=(lease_owner or f"queued:{request.request_id}")[:128],
            lease_expires_at=current_time + timedelta(seconds=_lease_seconds()),
            created_at=current_time,
            updated_at=current_time,
            expires_at=current_time + EXECUTION_REFINE_EXPIRY,
            applied_at=None,
            cancelled_at=None,
        )
        self.session.add(run)
        try:
            await self.session.commit()
        except IntegrityError:
            await self.session.rollback()
            existing = await self.get_owned(
                user_id=user_id,
                thread_id=thread_id,
                request_id=request.request_id,
            )
            if existing is not None:
                return existing, False
            active_result = await self.session.execute(
                select(ExecutionRefineRun).where(
                    ExecutionRefineRun.user_id == user_id,
                    ExecutionRefineRun.thread_id == thread_id,
                    ExecutionRefineRun.status == "running",
                )
            )
            if active_result.scalar_one_or_none() is not None:
                raise ExecutionRefineError(
                    code="EXECUTION_REFINE_ACTIVE_RUN",
                    message="该计划已有正在生成的调整方案。",
                )
            raise
        await self.session.refresh(run)
        return run, True

    async def claim_lease(
        self,
        run: ExecutionRefineRun,
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
        run: ExecutionRefineRun,
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
        run: ExecutionRefineRun,
        stage: str,
        *,
        lease_owner: str | None = None,
    ) -> bool:
        await self.session.refresh(run, with_for_update=True)
        if run.status != "running" or (
            lease_owner is not None and run.lease_owner != lease_owner
        ):
            return False
        run.stage = stage[:64]
        if lease_owner is not None:
            run.lease_expires_at = datetime.now(timezone.utc) + timedelta(
                seconds=_lease_seconds()
            )
        await self.session.commit()
        return True

    async def save_proposal(
        self,
        run: ExecutionRefineRun,
        proposal: ExecutionRefineProposal,
        *,
        lease_owner: str | None = None,
        now: datetime | None = None,
    ) -> bool:
        current_time = now or datetime.now(timezone.utc)
        await self.session.refresh(run, with_for_update=True)
        if run.status != "running" or (
            lease_owner is not None
            and (
                run.lease_owner != lease_owner
                or not _lease_is_active(run.lease_expires_at, current_time)
            )
        ):
            return False
        if proposal.mode != run.mode:
            raise ExecutionRefineError(
                code="EXECUTION_REFINE_INVALID_PROPOSAL",
                message=EXECUTION_REFINE_SAFE_INVALID_MESSAGE,
            )
        run.proposal = proposal.model_dump(mode="json", exclude_unset=True)
        run.status = "ready"
        run.stage = "ready"
        run.error_code = None
        run.error_message = None
        run.lease_owner = None
        run.lease_expires_at = None
        await self.session.commit()
        return True

    async def fail(
        self,
        run: ExecutionRefineRun,
        *,
        code: str,
        message: str,
        lease_owner: str | None = None,
    ) -> bool:
        await self.session.refresh(run, with_for_update=True)
        if run.status != "running" or (
            lease_owner is not None and run.lease_owner != lease_owner
        ):
            return False
        run.status = "failed"
        run.stage = "failed"
        run.proposal = None
        run.error_code = code[:128]
        run.error_message = _safe_failure_message(code, message)
        run.lease_owner = None
        run.lease_expires_at = None
        await self.session.commit()
        return True

    async def cancel(
        self,
        run: ExecutionRefineRun,
        *,
        now: datetime | None = None,
    ) -> ExecutionRefineRun:
        current_time = now or datetime.now(timezone.utc)
        await self.session.refresh(run, with_for_update=True)
        if run.status == "cancelled":
            return run
        if run.status not in {"running", "ready"}:
            raise ExecutionRefineError(
                code="EXECUTION_REFINE_NOT_CANCELLABLE",
                message="当前调整请求不能取消。",
            )
        run.status = "cancelled"
        run.stage = "cancelled"
        run.proposal = None
        run.cancelled_at = current_time
        run.lease_owner = None
        run.lease_expires_at = None
        await self.session.commit()
        return run

    async def fail_interrupted_if_lease_expired(
        self,
        run: ExecutionRefineRun,
        *,
        now: datetime | None = None,
    ) -> bool:
        current_time = now or datetime.now(timezone.utc)
        await self.session.refresh(run, with_for_update=True)
        if run.status != "running" or _lease_is_active(
            run.lease_expires_at,
            current_time,
        ):
            return False
        _mark_interrupted(run)
        await self.session.commit()
        return True

    async def expire_if_needed(
        self,
        run: ExecutionRefineRun,
        *,
        now: datetime | None = None,
    ) -> ExecutionRefineRun:
        current_time = now or datetime.now(timezone.utc)
        await self.session.refresh(run, with_for_update=True)
        if run.status == "ready" and run.expires_at <= current_time:
            run.status = "expired"
            run.stage = "expired"
            run.proposal = None
            run.lease_owner = None
            run.lease_expires_at = None
            await self.session.commit()
        return run

    async def save_apply_receipt(
        self,
        run: ExecutionRefineRun,
        receipt: ExecutionRefineApplyReceipt,
    ) -> dict[str, Any]:
        await self.session.refresh(run, with_for_update=True)
        if run.status == "applied" and isinstance(run.apply_receipt, dict):
            return dict(run.apply_receipt)
        if run.status != "ready":
            raise ExecutionRefineError(
                code="EXECUTION_REFINE_NOT_READY",
                message="调整方案尚未准备好。",
            )
        if (
            receipt.run_id != run.id
            or receipt.thread_id != run.thread_id
            or receipt.request_id != run.request_id
            or receipt.scope_fingerprint != run.scope_fingerprint
        ):
            raise ExecutionRefineError(
                code="EXECUTION_REFINE_APPLY_CONFLICT",
                message="应用凭证与当前调整请求不匹配。",
            )
        payload = receipt.model_dump(mode="json")
        run.apply_receipt = payload
        run.status = "applied"
        run.stage = "applied"
        run.applied_at = receipt.applied_at
        await self.session.commit()
        return payload

    async def cleanup_terminal(
        self,
        *,
        before: datetime,
        limit: int = 100,
    ) -> int:
        bounded_limit = min(max(limit, 1), 500)
        id_result = await self.session.execute(
            select(ExecutionRefineRun.id)
            .where(
                ExecutionRefineRun.status.in_(EXECUTION_REFINE_TERMINAL_STATUSES),
                ExecutionRefineRun.updated_at < before,
            )
            .order_by(ExecutionRefineRun.updated_at)
            .limit(bounded_limit)
        )
        run_ids = list(id_result.scalars().all())
        if not run_ids:
            return 0
        result = await self.session.execute(
            delete(ExecutionRefineRun).where(ExecutionRefineRun.id.in_(run_ids))
        )
        await self.session.commit()
        return int(result.rowcount or 0)


class ExecutionRefineService:
    def __init__(
        self,
        session: AsyncSession,
        *,
        proposal_client: ExecutionRefineProposalClient | None = None,
    ) -> None:
        self.session = session
        self.repository = ExecutionRefineRepository(session)
        self.proposal_client = proposal_client

    async def load_scope(
        self,
        *,
        user_id: UUID,
        thread_id: str,
        request: ExecutionRefineRequest,
    ) -> ExecutionRefineScope:
        thread_result = await self.session.execute(
            select(AgentThread).where(
                AgentThread.user_id == user_id,
                AgentThread.thread_id == thread_id,
            )
        )
        thread = thread_result.scalar_one_or_none()
        if thread is None:
            raise ExecutionRefineError(
                code="EXECUTION_REFINE_SCOPE_FORBIDDEN",
                message="计划不存在。",
                status_code=404,
            )

        tasks_result = await self.session.execute(
            select(Task).where(Task.user_id == user_id, Task.thread_id == thread_id)
        )
        tasks = list(tasks_result.scalars().all())
        task_ids = [task.id for task in tasks]
        dependencies: list[TaskDependency] = []
        if task_ids:
            dependency_result = await self.session.execute(
                select(TaskDependency).where(TaskDependency.task_id.in_(task_ids))
            )
            dependencies = list(dependency_result.scalars().all())

        review_result = await self.session.execute(
            select(PhaseReview).where(
                PhaseReview.user_id == user_id,
                PhaseReview.thread_id == thread_id,
            )
        )
        reviews = list(review_result.scalars().all())
        latest_result = await self.session.execute(
            select(ExecutionRefineRun)
            .where(
                ExecutionRefineRun.user_id == user_id,
                ExecutionRefineRun.thread_id == thread_id,
                ExecutionRefineRun.status == "applied",
            )
            .order_by(ExecutionRefineRun.applied_at.desc())
            .limit(1)
        )
        latest_applied = latest_result.scalar_one_or_none()
        return build_execution_refine_scope(
            thread=thread,
            tasks=tasks,
            dependencies=dependencies,
            phase_reviews=reviews,
            request=request,
            latest_applied_run=latest_applied,
        )

    async def generate_proposal(
        self,
        *,
        request: ExecutionRefineRequest,
        scope: ExecutionRefineScope,
        on_stage: Callable[[str, dict[str, Any]], Awaitable[None]] | None = None,
    ) -> ExecutionRefineProposal:
        if self.proposal_client is None:
            raise RuntimeError("proposal_client is required")

        issues: list[ExecutionRefineValidationIssue] = []
        repair_base_proposal: ExecutionRefineProposal | None = None
        for attempt in range(MAX_EXECUTION_REFINE_REPAIRS + 1):
            prompt = build_execution_refine_prompt(
                request=request,
                scope=scope,
                repair_issues=issues,
                repair_base_proposal=repair_base_proposal,
            )
            try:
                payload = await self.proposal_client.create_execution_refine_proposal(
                    prompt=prompt
                )
            except LLMStructuredOutputError as exc:
                issues = [
                    _issue(
                        "EXECUTION_REFINE_SCHEMA_INVALID",
                        "provider 返回的 proposal 未通过严格 JSON Schema 校验。",
                        "保持原 mode、约束和有效操作，仅重新输出符合 schema 的 JSON 对象。",
                    )
                ]
                if attempt == MAX_EXECUTION_REFINE_REPAIRS:
                    raise ExecutionRefineError(
                        code="EXECUTION_REFINE_INVALID_PROPOSAL",
                        message=EXECUTION_REFINE_SAFE_INVALID_MESSAGE,
                    ) from exc
                if on_stage is not None:
                    await on_stage(
                        "repairing",
                        {
                            "attempt": attempt + 1,
                            "issues": [issue.model_payload() for issue in issues],
                        },
                    )
                continue
            if on_stage is not None:
                await on_stage(
                    "validating",
                    {"attempt": attempt + 1},
                )
            try:
                proposal = ExecutionRefineProposal.model_validate(payload)
            except ValidationError as exc:
                issues = [
                    _issue(
                        "EXECUTION_REFINE_SCHEMA_INVALID",
                        "proposal 未通过严格 JSON Schema 校验。",
                        "只修复 JSON 字段、类型和枚举，不改变有效操作与用户约束。",
                    )
                ]
                if attempt == MAX_EXECUTION_REFINE_REPAIRS:
                    raise ExecutionRefineError(
                        code="EXECUTION_REFINE_INVALID_PROPOSAL",
                        message=EXECUTION_REFINE_SAFE_INVALID_MESSAGE,
                    ) from exc
                if on_stage is not None:
                    await on_stage(
                        "repairing",
                        {
                            "attempt": attempt + 1,
                            "issues": [issue.model_payload() for issue in issues],
                        },
                    )
                continue
            proposal = normalize_execution_refine_proposal(
                proposal=proposal,
                request=request,
                scope=scope,
                enforce_capacity_fallback=(
                    attempt == MAX_EXECUTION_REFINE_REPAIRS
                ),
            )

            issues = validate_execution_refine_proposal(
                proposal=proposal,
                request=request,
                scope=scope,
                repair_base_proposal=repair_base_proposal,
            )
            if not issues:
                return proposal
            repair_base_proposal = proposal
            if attempt == MAX_EXECUTION_REFINE_REPAIRS:
                break
            if on_stage is not None:
                await on_stage(
                    "repairing",
                    {
                        "attempt": attempt + 1,
                        "issues": [issue.model_payload() for issue in issues],
                    },
                )
        raise ExecutionRefineError(
            code="EXECUTION_REFINE_INVALID_PROPOSAL",
            message=EXECUTION_REFINE_SAFE_INVALID_MESSAGE,
        )

    async def apply(
        self,
        *,
        user_id: UUID,
        thread_id: str,
        request_id: UUID,
        expected_scope_fingerprint: str | None = None,
        now: datetime | None = None,
    ) -> ExecutionRefineApplyReceipt:
        current_time = now or datetime.now(timezone.utc)
        try:
            async with self.session.begin():
                thread_result = await self.session.execute(
                    select(AgentThread)
                    .where(
                        AgentThread.user_id == user_id,
                        AgentThread.thread_id == thread_id,
                    )
                    .with_for_update()
                )
                thread = thread_result.scalar_one_or_none()
                if thread is None:
                    raise ExecutionRefineError(
                        code="EXECUTION_REFINE_SCOPE_FORBIDDEN",
                        message="计划不存在。",
                        status_code=404,
                    )

                run_result = await self.session.execute(
                    select(ExecutionRefineRun)
                    .where(
                        ExecutionRefineRun.user_id == user_id,
                        ExecutionRefineRun.thread_id == thread_id,
                        ExecutionRefineRun.request_id == request_id,
                    )
                    .with_for_update()
                )
                run = run_result.scalar_one_or_none()
                if run is None:
                    raise ExecutionRefineError(
                        code="EXECUTION_REFINE_SCOPE_FORBIDDEN",
                        message="调整请求不存在。",
                        status_code=404,
                    )
                if run.status == "applied" and isinstance(run.apply_receipt, dict):
                    return ExecutionRefineApplyReceipt.model_validate(run.apply_receipt)
                if run.status == "ready" and run.expires_at <= current_time:
                    run.status = "expired"
                    run.stage = "expired"
                    run.proposal = None
                    raise ExecutionRefineError(
                        code="EXECUTION_REFINE_EXPIRED",
                        message="调整方案已过期，请重新生成。",
                    )
                if run.status != "ready" or not isinstance(run.proposal, dict):
                    raise ExecutionRefineError(
                        code="EXECUTION_REFINE_NOT_READY",
                        message="调整方案尚未准备好。",
                    )
                if (
                    expected_scope_fingerprint is not None
                    and expected_scope_fingerprint != run.scope_fingerprint
                ):
                    raise ExecutionRefineError(
                        code="EXECUTION_REFINE_CONTEXT_STALE",
                        message="计划已发生变化，请重新生成调整方案。",
                    )

                task_result = await self.session.execute(
                    select(Task)
                    .where(Task.user_id == user_id, Task.thread_id == thread_id)
                    .order_by(Task.id)
                    .with_for_update()
                )
                tasks = list(task_result.scalars().all())
                task_ids = [task.id for task in tasks]
                dependencies: list[TaskDependency] = []
                if task_ids:
                    dependency_result = await self.session.execute(
                        select(TaskDependency)
                        .where(TaskDependency.task_id.in_(task_ids))
                        .with_for_update()
                    )
                    dependencies = list(dependency_result.scalars().all())
                review_result = await self.session.execute(
                    select(PhaseReview).where(
                        PhaseReview.user_id == user_id,
                        PhaseReview.thread_id == thread_id,
                    )
                )
                reviews = list(review_result.scalars().all())
                request = ExecutionRefineRequest.model_validate(run.input_context)
                scope = build_execution_refine_scope(
                    thread=thread,
                    tasks=tasks,
                    dependencies=dependencies,
                    phase_reviews=reviews,
                    request=request,
                )
                if scope.fingerprint != run.scope_fingerprint:
                    raise ExecutionRefineError(
                        code="EXECUTION_REFINE_CONTEXT_STALE",
                        message="计划已发生变化，请重新生成调整方案。",
                    )
                proposal = ExecutionRefineProposal.model_validate(run.proposal)
                validation_issues = validate_execution_refine_proposal(
                    proposal=proposal,
                    request=request,
                    scope=scope,
                )
                if validation_issues:
                    raise ExecutionRefineError(
                        code="EXECUTION_REFINE_INVALID_PROPOSAL",
                        message=EXECUTION_REFINE_SAFE_INVALID_MESSAGE,
                    )

                affected_ids, created_ids = await self._apply_validated_proposal(
                    user_id=user_id,
                    thread=thread,
                    run=run,
                    proposal=proposal,
                    scope=scope,
                    tasks=tasks,
                    current_time=current_time,
                )
                await self.session.flush()
                await TaskRepository(self.session)._recalculate_thread_phase_state(
                    user_id=user_id,
                    thread_id=thread_id,
                )
                await self.session.flush()
                receipt = ExecutionRefineApplyReceipt(
                    run_id=run.id,
                    thread_id=thread_id,
                    request_id=request_id,
                    applied_at=current_time,
                    scope_fingerprint=run.scope_fingerprint,
                    affected_task_ids=sorted(affected_ids, key=str),
                    created_task_ids=sorted(created_ids, key=str),
                    focus_task_ids=proposal.focus_task_ids,
                )
                run.apply_receipt = receipt.model_dump(mode="json")
                run.status = "applied"
                run.stage = "applied"
                run.applied_at = current_time
                run.lease_owner = None
                run.lease_expires_at = None
                return receipt
        except IntegrityError as exc:
            raise ExecutionRefineError(
                code="EXECUTION_REFINE_APPLY_CONFLICT",
                message="调整方案与当前任务发生冲突，请重新生成。",
            ) from exc

    async def _apply_validated_proposal(
        self,
        *,
        user_id: UUID,
        thread: AgentThread,
        run: ExecutionRefineRun,
        proposal: ExecutionRefineProposal,
        scope: ExecutionRefineScope,
        tasks: list[Task],
        current_time: datetime,
    ) -> tuple[set[UUID], set[UUID]]:
        task_by_id = {str(task.id): task for task in tasks}
        tree = scope.task_tree.model_copy(deep=True)
        records = copy.deepcopy(scope.task_records)
        affected_ids: set[UUID] = set()
        created_ids: set[UUID] = set()
        drafts: dict[str, Task] = {}
        add_operations: list[AddTaskOperation] = []

        for operation in proposal.operations:
            if isinstance(operation, UpdateTaskOperation):
                task = task_by_id[str(operation.task_id)]
                changes = operation.changes.model_dump(
                    mode="json",
                    exclude_unset=True,
                )
                metadata = dict(task.metadata_ or {})
                tree_changes: dict[str, Any] = {}
                for field, value in changes.items():
                    if field in {"title", "description", "estimated_minutes"}:
                        setattr(task, field, value)
                    else:
                        if value is None:
                            metadata.pop(field, None)
                        else:
                            metadata[field] = value
                    tree_changes[field] = value
                task.metadata_ = metadata
                task.updated_at = current_time
                _update_tree_node(tree.root, task.client_node_id, tree_changes)
                affected_ids.add(task.id)

            elif isinstance(operation, AddTaskOperation):
                add_operations.append(operation)
                parent_id = str(operation.parent_task_id) if operation.parent_task_id else None
                record = {
                    "task_id": f"draft:{operation.draft_id}",
                    "client_node_id": execution_refine_client_node_id(
                        run.request_id,
                        operation.draft_id,
                    ),
                    "parent_task_id": parent_id,
                }
                sort_order = _insert_sort_order(
                    tasks,
                    parent_task_id=operation.parent_task_id,
                    insert_after_task_id=operation.insert_after_task_id,
                )
                _shift_siblings_for_insert(
                    tasks,
                    parent_task_id=operation.parent_task_id,
                    from_sort_order=sort_order,
                )
                task = Task(
                    id=uuid4(),
                    user_id=user_id,
                    thread_id=thread.thread_id,
                    parent_task_id=operation.parent_task_id,
                    client_node_id=record["client_node_id"],
                    title=operation.title,
                    description=operation.description,
                    node_type="action",
                    status="active",
                    view_bucket="planned",
                    is_in_my_day=False,
                    estimated_minutes=operation.estimated_minutes,
                    sort_order=sort_order,
                    ai_generated=True,
                    user_edited=False,
                    metadata_={
                        "source": "ai",
                        "phase_id": scope.current_phase_id,
                        "created_by": "execution_refine",
                        "execution_refine_request_id": str(run.request_id),
                        "done_criteria": operation.done_criteria,
                        "start_hint": operation.start_hint,
                        "fallback_action": operation.fallback_action,
                    },
                    created_at=current_time,
                    updated_at=current_time,
                )
                self.session.add(task)
                tasks.append(task)
                task_by_id[str(task.id)] = task
                drafts[operation.draft_id] = task
                records[record["task_id"]] = {
                    **record,
                    "sort_order": sort_order,
                }
                affected_ids.add(task.id)
                created_ids.add(task.id)

            elif isinstance(operation, ReorderSiblingsOperation):
                ordered = [task_by_id[str(task_id)] for task_id in operation.ordered_task_ids]
                positions = sorted(task.sort_order for task in ordered)
                for task, position in zip(ordered, positions, strict=True):
                    task.sort_order = position
                    task.updated_at = current_time
                    records[str(task.id)]["sort_order"] = position
                    affected_ids.add(task.id)
                _reorder_tree_siblings(
                    tree.root,
                    records,
                    str(operation.parent_task_id) if operation.parent_task_id else None,
                    [str(task_id) for task_id in operation.ordered_task_ids],
                )

            elif isinstance(operation, SetMyDayOperation):
                task = task_by_id[str(operation.task_id)]
                task.is_in_my_day = operation.is_in_my_day
                task.updated_at = current_time
                affected_ids.add(task.id)

        await self.session.flush()
        for operation in add_operations:
            task = drafts[operation.draft_id]
            depends_on_client_ids: list[str] = []
            for reference in operation.depends_on_refs:
                dependency_task = drafts.get(reference) or task_by_id.get(reference)
                if dependency_task is None:
                    raise ExecutionRefineError(
                        code="EXECUTION_REFINE_INVALID_PROPOSAL",
                        message=EXECUTION_REFINE_SAFE_INVALID_MESSAGE,
                    )
                self.session.add(
                    TaskDependency(
                        id=uuid4(),
                        task_id=task.id,
                        depends_on_task_id=dependency_task.id,
                        created_at=current_time,
                    )
                )
                depends_on_client_ids.append(dependency_task.client_node_id)
            node = TaskNode(
                client_node_id=task.client_node_id,
                title=task.title,
                description=task.description,
                verb=_infer_verb(task.title),
                estimated_minutes=int(task.estimated_minutes or 1),
                node_type="action",
                depends_on=depends_on_client_ids,
                children=[],
                done_criteria=task.metadata_.get("done_criteria"),
                start_hint=task.metadata_.get("start_hint"),
                fallback_action=task.metadata_.get("fallback_action"),
            )
            inserted = _insert_tree_node(
                tree.root,
                records,
                parent_id=(
                    str(operation.parent_task_id)
                    if operation.parent_task_id
                    else None
                ),
                node=node,
                insert_after_task_id=(
                    str(operation.insert_after_task_id)
                    if operation.insert_after_task_id
                    else None
                ),
            )
            if not inserted:
                raise ExecutionRefineError(
                    code="EXECUTION_REFINE_APPLY_CONFLICT",
                    message="调整方案无法映射到当前任务树。",
                )

        thread.task_tree = TaskTree.model_validate(
            tree.model_dump(mode="json")
        ).model_dump(mode="json")
        thread.updated_at = current_time
        return affected_ids, created_ids


def execution_refine_enabled() -> bool:
    return os.getenv("EASYPLAN_EXECUTION_REFINE_ENABLED", "false").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def build_execution_refine_scope(
    *,
    thread: AgentThread,
    tasks: Sequence[Task],
    dependencies: Sequence[TaskDependency],
    phase_reviews: Sequence[PhaseReview],
    request: ExecutionRefineRequest,
    latest_applied_run: ExecutionRefineRun | None = None,
) -> ExecutionRefineScope:
    if not isinstance(thread.task_tree, dict):
        raise ExecutionRefineError(
            code="EXECUTION_REFINE_SCOPE_FORBIDDEN",
            message="当前计划没有可调整的已提交任务。",
        )
    try:
        task_tree = TaskTree.model_validate(thread.task_tree)
    except ValidationError as exc:
        raise ExecutionRefineError(
            code="EXECUTION_REFINE_SCOPE_FORBIDDEN",
            message="当前计划暂时无法安全调整。",
        ) from exc

    task_by_id = {str(task.id): task for task in tasks}
    referenced_ids = {
        *(str(value) for value in request.priority_task_ids),
        *(str(value) for value in request.blocked_task_ids),
    }
    if referenced_ids - set(task_by_id):
        raise ExecutionRefineError(
            code="EXECUTION_REFINE_SCOPE_FORBIDDEN",
            message="请求包含不属于当前计划的任务。",
        )

    planning_context = task_tree.planning_context
    schema_version = planning_context.schema_version if planning_context is not None else 1
    current_phase_id = (
        planning_context.current_phase.phase_id
        if planning_context is not None and planning_context.current_phase is not None
        else None
    )
    intent_type = (
        planning_context.intent_type
        if planning_context is not None
        else _intent_type_from_strategy(task_tree)
    )

    task_records: dict[str, dict[str, Any]] = {}
    protected_summaries: list[dict[str, Any]] = []
    prompt_tasks: list[dict[str, Any]] = []
    for task in sorted(tasks, key=_task_order_key):
        record = _task_scope_record(
            task,
            schema_version=schema_version,
            current_phase_id=current_phase_id,
        )
        task_records[str(task.id)] = record
        if record["protected_reason"] is not None:
            protected_summaries.append(
                {
                    "task_id": record["task_id"],
                    "title": record["title"],
                    "status": record["status"],
                    "estimated_minutes": record["estimated_minutes"],
                    "protected_reason": record["protected_reason"],
                }
            )
        prompt_tasks.append(_prompt_task_record(record))

    edges = tuple(
        sorted(
            (
                str(dependency.task_id),
                str(dependency.depends_on_task_id),
            )
            for dependency in dependencies
            if str(dependency.task_id) in task_records
            and str(dependency.depends_on_task_id) in task_records
        )
    )
    protected_my_day_minutes = sum(
        int(record["estimated_minutes"] or 0)
        for record in task_records.values()
        if record["is_in_my_day"]
        and record["protected_reason"] == "practice_occurrence"
    )
    reviews = [
        {
            "phase_id": review.phase_id,
            "status": review.status,
            "recommendation": review.recommendation,
            "decision": review.decision,
            "evidence": _json_safe(review.evidence),
            "difficulty": review.difficulty,
            "next_capacity": review.next_capacity,
            "override_reason": review.override_reason,
            "statistics": _json_safe(review.statistics),
            "updated_at": _iso(review.updated_at),
        }
        for review in sorted(phase_reviews, key=lambda item: (item.phase_id, str(item.id)))
    ]
    latest_context = _latest_applied_context(latest_applied_run)
    task_tree_payload = task_tree.model_dump(mode="json")
    snapshot = {
        "schema_version": 1,
        "thread": {
            "thread_id": thread.thread_id,
            "intent_text": thread.intent_text[:2000],
            "summary": task_tree.summary,
            "intent_type": intent_type,
            "planning_schema_version": schema_version,
            "current_phase_id": current_phase_id,
        },
        "current_phase": (
            planning_context.current_phase.model_dump(mode="json")
            if planning_context is not None and planning_context.current_phase is not None
            else None
        ),
        "roadmap_summary": (
            [
                {
                    "phase_id": phase.phase_id,
                    "order": phase.order,
                    "title": phase.title,
                    "objective": phase.objective,
                    "status": phase.status,
                }
                for phase in planning_context.roadmap
            ]
            if planning_context is not None
            else []
        ),
        "strategy_context": (
            task_tree.strategy_context.model_dump(mode="json")
            if task_tree.strategy_context is not None
            else None
        ),
        "tasks": prompt_tasks,
        "protected_history": protected_summaries,
        "dependencies": [list(edge) for edge in edges],
        "selected_project_my_day_task_ids": sorted(
            task_id
            for task_id, record in task_records.items()
            if record["is_in_my_day"]
        ),
        "protected_my_day_minutes": protected_my_day_minutes,
        "latest_applied_execution_refine": latest_context,
        "request_context": request.model_dump(mode="json"),
    }
    fingerprint_payload = {
        "thread_id": thread.thread_id,
        "task_tree": task_tree_payload,
        "tasks": [task_records[key] for key in sorted(task_records)],
        "dependencies": [list(edge) for edge in edges],
        "phase_reviews": reviews,
        "current_phase_id": current_phase_id,
    }
    fingerprint = canonical_scope_fingerprint(fingerprint_payload)
    return ExecutionRefineScope(
        thread_id=thread.thread_id,
        task_tree=task_tree,
        snapshot=snapshot,
        fingerprint=fingerprint,
        task_records=task_records,
        dependency_edges=edges,
        current_phase_id=current_phase_id,
        intent_type=intent_type,
    )


def canonical_scope_fingerprint(payload: dict[str, Any]) -> str:
    encoded = json.dumps(
        _json_safe(payload),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _time_budget_capacity_facts(
    *,
    request: ExecutionRefineRequest,
    scope: ExecutionRefineScope,
) -> dict[str, Any] | None:
    if request.mode != "time_budget":
        return None

    available_minutes = int(request.available_minutes or 0)
    buffer_minutes = min(20, max(3, math.ceil(available_minutes * 0.15)))
    protected_minutes = int(scope.snapshot.get("protected_my_day_minutes", 0))
    counted_commitments: list[dict[str, Any]] = []
    read_only_not_counted: list[dict[str, Any]] = []
    for task_id, record in sorted(scope.task_records.items()):
        protected_reason = record.get("protected_reason")
        if protected_reason is None:
            continue
        summary = {
            "task_id": task_id,
            "title": record.get("title"),
            "estimated_minutes": int(record.get("estimated_minutes") or 0),
            "protected_reason": protected_reason,
        }
        if record.get("is_in_my_day") and protected_reason == "practice_occurrence":
            counted_commitments.append(summary)
        else:
            read_only_not_counted.append(summary)

    return {
        "available_minutes": available_minutes,
        "buffer_minutes": buffer_minutes,
        "protected_commitment_minutes": protected_minutes,
        "remaining_focus_minutes": max(
            0,
            available_minutes - buffer_minutes - protected_minutes,
        ),
        "capacity_counted_protected_commitments": counted_commitments,
        "read_only_not_capacity_counted": read_only_not_counted,
    }


def build_execution_refine_prompt(
    *,
    request: ExecutionRefineRequest,
    scope: ExecutionRefineScope,
    repair_issues: Sequence[ExecutionRefineValidationIssue] = (),
    repair_base_proposal: ExecutionRefineProposal | None = None,
) -> str:
    capacity_facts = _time_budget_capacity_facts(request=request, scope=scope)
    mode_rules = {
        "time_budget": (
            "按以下确定性公式安排容量：buffer_minutes=min(20,max(3,ceil(available_minutes*0.15)))；"
            "usable_minutes=available_minutes-buffer_minutes-protected_my_day_minutes。"
            "最多选择 5 个当前项目可变父级 Action 作为 focus，选择后的 estimated_minutes 总和必须 <= usable_minutes；"
            "estimated_focus_minutes 必须精确等于 focus_task_ids 对应任务在所有 update_task 生效后的时长总和。"
            "每个 focus task 必须 set_my_day=true，其他可变 My Day task 必须 set_my_day=false；"
            "不得把 protected_my_day_minutes、practice occurrence 或 Assist child 当作可删除容量。"
            "如果没有现有任务能直接放入容量，可以把一个允许 update 的 Action 缩小为仍然具体且可验证的动作；"
            "绝对禁止为了填满预算而增加任何现有任务的 estimated_minutes；预算是上限，不是必须耗尽的配额。"
            "如果受保护承诺已占满 usable capacity，则 focus 必须为空、estimated_focus_minutes=0，并给出 warning。"
        ),
        "progress_recovery": (
            "只重排或缩小当前执行范围；最多新增 2 个缺失 Action，不得创建新阶段。"
        ),
        "context_change": (
            "原样保留 deadline、priority_task_ids、blocked_task_ids 和 user_context 中的显式约束。"
        ),
    }
    parts = [
        "你是 EasyPlan Execution Refine 提案器。只输出一个符合 JSON Schema 的对象。",
        "只允许 operation_type=update_task/add_task/reorder_siblings/set_my_day。",
        "禁止 delete/archive/status/phase/source/parent/client_node_id/history/roadmap/review/loop/checkpoint 操作。",
        "Task Assist children、practice occurrences、completed tasks 和 historical phases 只能作为只读上下文。",
        "user_facing_reasons 只写简短可见理由，不输出思维链、提示词、token 或内部校验过程。",
        "禁止使用 placeholder、todo、tbd、占位任务、无操作、待补充、完成任务、开始做等占位标题或质量字段。",
        "只使用 scope.tasks 中 capabilities 明确允许的操作；没有权限就不要生成该操作。",
        f"模式规则：{mode_rules[request.mode]}",
        "Proposal JSON Schema: "
        + json.dumps(
            ExecutionRefineProposal.model_json_schema(),
            ensure_ascii=False,
            separators=(",", ":"),
        ),
        "Request JSON: "
        + json.dumps(request.model_dump(mode="json"), ensure_ascii=False),
        "Bounded Scope JSON: "
        + json.dumps(scope.snapshot, ensure_ascii=False, separators=(",", ":")),
    ]
    if capacity_facts is not None:
        parts.extend(
            [
                "服务端容量事实（不可修改或重新估算）：",
                json.dumps(capacity_facts, ensure_ascii=False, separators=(",", ":")),
                "remaining_focus_minutes 是本次可变候选任务的硬上限；"
                "capacity_counted_protected_commitments 已占用的时间不得再次加入 focus，"
                "read_only_not_capacity_counted 只读且不得重复计时。",
            ]
        )
    if repair_issues:
        parts.extend(
            [
                "上一次提案未通过确定性校验。只修复下列无效 operation；保留其他有效 operation、mode、"
                "用户时间、deadline、priority、blocked 和 user_context，不重写整份提案：",
                json.dumps(
                    [issue.model_payload() for issue in repair_issues],
                    ensure_ascii=False,
                    separators=(",", ":"),
                ),
            ]
        )
        if request.mode == "time_budget" and repair_base_proposal is not None:
            parts.extend(
                [
                    "time_budget repair 硬约束：只能从上一次候选中减少 focus、压缩允许 update 的候选任务、"
                    "同步移出 My Day，或重新排序现有候选。禁止 add_task，禁止引入新的 focus_task_id，"
                    "禁止增加任何任务的 estimated_minutes，禁止修改或重复计算受保护承诺。",
                    "上一次 schema-valid proposal（repair 的候选边界）：",
                    json.dumps(
                        repair_base_proposal.model_dump(
                            mode="json",
                            exclude_unset=True,
                        ),
                        ensure_ascii=False,
                        separators=(",", ":"),
                    ),
                ]
            )
    return "\n".join(parts)


def _validate_time_budget_repair_scope(
    *,
    proposal: ExecutionRefineProposal,
    repair_base_proposal: ExecutionRefineProposal,
    scope: ExecutionRefineScope,
    issues: list[ExecutionRefineValidationIssue],
) -> None:
    base_focus_ids = {str(task_id) for task_id in repair_base_proposal.focus_task_ids}
    base_candidate_ids = set(base_focus_ids)
    base_estimates = {
        task_id: int(record.get("estimated_minutes") or 0)
        for task_id, record in scope.task_records.items()
    }
    for operation in repair_base_proposal.operations:
        if not isinstance(operation, UpdateTaskOperation):
            continue
        task_id = str(operation.task_id)
        base_candidate_ids.add(task_id)
        changes = operation.changes.model_dump(mode="json", exclude_unset=True)
        if isinstance(changes.get("estimated_minutes"), int):
            base_estimates[task_id] = int(changes["estimated_minutes"])

    new_focus_ids = {
        str(task_id) for task_id in proposal.focus_task_ids
    } - base_focus_ids
    if new_focus_ids:
        task_ref = sorted(new_focus_ids)[0]
        issues.append(
            _issue(
                "EXECUTION_REFINE_REPAIR_FOCUS_EXPANDED",
                f"time_budget repair 引入了新的 focus task {task_ref}。",
                "只能从上一次 focus 中移除候选，不能新增候选任务。",
                task_ref=task_ref,
            )
        )

    for index, operation in enumerate(proposal.operations):
        if isinstance(operation, AddTaskOperation):
            issues.append(
                _operation_issue(
                    "EXECUTION_REFINE_REPAIR_ADD_FORBIDDEN",
                    index,
                    operation.draft_id,
                    "time_budget repair 不允许新增任务。",
                    "删除 add_task；只减少、压缩或重新排序上一次候选任务。",
                )
            )
            continue
        if isinstance(operation, UpdateTaskOperation):
            task_id = str(operation.task_id)
            if task_id not in base_candidate_ids:
                issues.append(
                    _operation_issue(
                        "EXECUTION_REFINE_REPAIR_TARGET_EXPANDED",
                        index,
                        task_id,
                        "time_budget repair 修改了上一次候选范围之外的任务。",
                        "只压缩上一次 focus 或 update_task 已引用的候选任务。",
                    )
                )
                continue
            changes = operation.changes.model_dump(mode="json", exclude_unset=True)
            repaired_minutes = changes.get("estimated_minutes")
            previous_minutes = base_estimates.get(task_id)
            if (
                isinstance(repaired_minutes, int)
                and previous_minutes is not None
                and repaired_minutes > previous_minutes
            ):
                issues.append(
                    _operation_issue(
                        "EXECUTION_REFINE_REPAIR_CAPACITY_EXPANDED",
                        index,
                        task_id,
                        f"repair 将候选估时从 {previous_minutes} 增加到 {repaired_minutes} 分钟。",
                        "保持或降低该候选估时；repair 不能扩大容量。",
                    )
                )
        elif (
            isinstance(operation, SetMyDayOperation)
            and operation.is_in_my_day
            and str(operation.task_id) not in base_focus_ids
        ):
            issues.append(
                _operation_issue(
                    "EXECUTION_REFINE_REPAIR_MY_DAY_EXPANDED",
                    index,
                    str(operation.task_id),
                    "time_budget repair 将新的候选加入了 My Day。",
                    "只能保留上一次 focus 的 My Day 映射，或将超额候选移出 My Day。",
                )
            )


def validate_execution_refine_proposal(
    *,
    proposal: ExecutionRefineProposal,
    request: ExecutionRefineRequest,
    scope: ExecutionRefineScope,
    repair_base_proposal: ExecutionRefineProposal | None = None,
) -> list[ExecutionRefineValidationIssue]:
    issues: list[ExecutionRefineValidationIssue] = []
    if proposal.mode != request.mode:
        issues.append(
            _issue(
                "EXECUTION_REFINE_MODE_MISMATCH",
                f"proposal mode={proposal.mode} 与 request mode={request.mode} 不一致。",
                "保持 request.mode 不变，只修正 proposal.mode。",
            )
        )
        return issues

    add_operations = [
        operation
        for operation in proposal.operations
        if isinstance(operation, AddTaskOperation)
    ]
    if (
        request.mode == "time_budget"
        and repair_base_proposal is not None
    ):
        _validate_time_budget_repair_scope(
            proposal=proposal,
            repair_base_proposal=repair_base_proposal,
            scope=scope,
            issues=issues,
        )
    max_additions = 2 if request.mode == "progress_recovery" else 3
    if len(add_operations) > max_additions:
        issues.append(
            _issue(
                "EXECUTION_REFINE_ADD_LIMIT",
                f"当前模式最多新增 {max_additions} 个任务。",
                "只删除超额的 add_task operation，不改写其他操作。",
            )
        )

    draft_ids = [operation.draft_id for operation in add_operations]
    if len(draft_ids) != len(set(draft_ids)):
        issues.append(
            _issue(
                "EXECUTION_REFINE_DRAFT_ID_DUPLICATE",
                "add_task draft_id 必须唯一。",
                "只重命名重复 draft_id，并同步 depends_on_refs。",
            )
        )

    records = copy.deepcopy(scope.task_records)
    tree = scope.task_tree.model_copy(deep=True)
    changed_nodes: list[TaskNode] = []
    seen_targets: set[tuple[str, str]] = set()
    draft_records: dict[str, dict[str, Any]] = {}

    for index, operation in enumerate(proposal.operations):
        if isinstance(operation, UpdateTaskOperation):
            target = str(operation.task_id)
            if not _require_capability(
                records,
                target,
                "update_task",
                index,
                issues,
            ):
                continue
            if ("update_task", target) in seen_targets:
                issues.append(
                    _operation_issue(
                        "EXECUTION_REFINE_DUPLICATE_TARGET",
                        index,
                        target,
                        "同一任务不能出现多个 update_task。",
                        "合并为一个 update_task changes 对象。",
                    )
                )
                continue
            seen_targets.add(("update_task", target))
            record = records[target]
            changes = operation.changes.model_dump(
                exclude_unset=True,
                mode="json",
            )
            if (
                request.mode == "time_budget"
                and isinstance(changes.get("estimated_minutes"), int)
                and int(changes["estimated_minutes"])
                > int(record.get("estimated_minutes") or 0)
            ):
                issues.append(
                    _operation_issue(
                        "EXECUTION_REFINE_CAPACITY_INFLATION",
                        index,
                        target,
                        "time_budget 模式不能为了填满预算而增加任务估时。",
                        "保留原估时或缩小任务范围；预算是上限，不要求耗尽。",
                    )
                )
            if not _changes_modify_record(record, changes):
                issues.append(
                    _operation_issue(
                        "EXECUTION_REFINE_NOOP_UPDATE",
                        index,
                        target,
                        "update_task 没有改变任何值。",
                        "删除该操作，或只保留确实变化的允许字段。",
                    )
                )
                continue
            record.update(changes)
            node = _update_tree_node(tree.root, record["client_node_id"], changes)
            if node is None:
                issues.append(
                    _operation_issue(
                        "EXECUTION_REFINE_TREE_REFERENCE_INVALID",
                        index,
                        target,
                        "可更新 AI 任务未在 committed TaskTree 中找到。",
                        "删除该操作并重新读取当前 scope。",
                    )
                )
            else:
                changed_nodes.append(node)

        elif isinstance(operation, AddTaskOperation):
            parent_id = str(operation.parent_task_id) if operation.parent_task_id else None
            if parent_id is not None and not _is_insertion_parent(records.get(parent_id)):
                issues.append(
                    _operation_issue(
                        "EXECUTION_REFINE_PARENT_FORBIDDEN",
                        index,
                        parent_id,
                        "add_task parent 不是当前执行阶段的可插入容器。",
                        "改用 scope 中 capabilities.add_child=true 的 Group，或添加为 root task。",
                    )
                )
                continue
            if operation.insert_after_task_id is not None:
                after_id = str(operation.insert_after_task_id)
                after = records.get(after_id)
                if after is None or after["parent_task_id"] != parent_id:
                    issues.append(
                        _operation_issue(
                            "EXECUTION_REFINE_INSERT_POSITION_INVALID",
                            index,
                            after_id,
                            "insert_after_task_id 不属于同一父级。",
                            "移除 insert_after_task_id，或引用同一父级的当前任务。",
                        )
                    )
                    continue
            synthetic_id = f"draft:{operation.draft_id}"
            client_node_id = execution_refine_client_node_id(
                request.request_id,
                operation.draft_id,
            )
            if any(
                item["client_node_id"] == client_node_id
                for item in records.values()
            ):
                issues.append(
                    _operation_issue(
                        "EXECUTION_REFINE_CLIENT_ID_CONFLICT",
                        index,
                        operation.draft_id,
                        "服务端生成的 client_node_id 与现有任务冲突。",
                        "更换 draft_id 后只重试该 add_task operation。",
                    )
                )
                continue
            record = {
                "task_id": synthetic_id,
                "client_node_id": client_node_id,
                "parent_task_id": parent_id,
                "title": operation.title,
                "description": operation.description,
                "estimated_minutes": operation.estimated_minutes,
                "done_criteria": operation.done_criteria,
                "start_hint": operation.start_hint,
                "fallback_action": operation.fallback_action,
                "node_type": "action",
                "status": "active",
                "sort_order": _next_sort_order(records, parent_id),
                "source": "ai",
                "phase_id": scope.current_phase_id,
                "is_in_my_day": False,
                "protected_reason": None,
                "capabilities": {
                    "update_task": True,
                    "reorder": True,
                    "set_my_day": True,
                    "add_child": False,
                },
                "metadata": {
                    "source": "ai",
                    "created_by": "execution_refine",
                    "execution_refine_request_id": str(request.request_id),
                    "phase_id": scope.current_phase_id,
                },
                "updated_at": None,
            }
            records[synthetic_id] = record
            draft_records[operation.draft_id] = record
            node = TaskNode(
                client_node_id=client_node_id,
                title=operation.title,
                description=operation.description,
                verb=_infer_verb(operation.title),
                estimated_minutes=operation.estimated_minutes,
                node_type="action",
                depends_on=[],
                children=[],
                done_criteria=operation.done_criteria,
                start_hint=operation.start_hint,
                fallback_action=operation.fallback_action,
            )
            if not _insert_tree_node(
                tree.root,
                records,
                parent_id=parent_id,
                node=node,
                insert_after_task_id=(
                    str(operation.insert_after_task_id)
                    if operation.insert_after_task_id
                    else None
                ),
            ):
                issues.append(
                    _operation_issue(
                        "EXECUTION_REFINE_TREE_REFERENCE_INVALID",
                        index,
                        parent_id,
                        "add_task 无法映射到 committed TaskTree 父级。",
                        "改用 scope 中可插入的父级，或添加为 root task。",
                    )
                )
            else:
                changed_nodes.append(node)

        elif isinstance(operation, ReorderSiblingsOperation):
            parent_id = str(operation.parent_task_id) if operation.parent_task_id else None
            key = ("reorder_siblings", parent_id or "root")
            if key in seen_targets:
                issues.append(
                    _operation_issue(
                        "EXECUTION_REFINE_DUPLICATE_TARGET",
                        index,
                        parent_id,
                        "同一父级只能重排一次。",
                        "合并为一个 reorder_siblings operation。",
                    )
                )
                continue
            seen_targets.add(key)
            ordered_ids = [str(task_id) for task_id in operation.ordered_task_ids]
            expected = {
                task_id
                for task_id, record in records.items()
                if record["parent_task_id"] == parent_id
                and record["capabilities"]["reorder"]
                and not task_id.startswith("draft:")
            }
            if set(ordered_ids) != expected:
                issues.append(
                    _operation_issue(
                        "EXECUTION_REFINE_SIBLING_SET_INVALID",
                        index,
                        parent_id,
                        "ordered_task_ids 必须完整列出该父级的可变 sibling 集合。",
                        "使用 scope 中同一 parent 的全部 capabilities.reorder=true 任务，且不增不漏。",
                    )
                )
                continue
            for task_id in ordered_ids:
                if not _require_capability(records, task_id, "reorder", index, issues):
                    continue
            _apply_record_reorder(records, parent_id, ordered_ids)
            _reorder_tree_siblings(tree.root, records, parent_id, ordered_ids)

        elif isinstance(operation, SetMyDayOperation):
            target = str(operation.task_id)
            if not _require_capability(
                records,
                target,
                "set_my_day",
                index,
                issues,
            ):
                continue
            if ("set_my_day", target) in seen_targets:
                issues.append(
                    _operation_issue(
                        "EXECUTION_REFINE_DUPLICATE_TARGET",
                        index,
                        target,
                        "同一任务不能出现多个 set_my_day。",
                        "只保留最终需要的 My Day 状态。",
                    )
                )
                continue
            seen_targets.add(("set_my_day", target))
            if records[target]["is_in_my_day"] == operation.is_in_my_day:
                issues.append(
                    _operation_issue(
                        "EXECUTION_REFINE_NOOP_MY_DAY",
                        index,
                        target,
                        "set_my_day 与当前状态相同。",
                        "删除该无效操作。",
                    )
                )
                continue
            records[target]["is_in_my_day"] = operation.is_in_my_day

    _validate_add_dependencies(
        proposal=proposal,
        scope=scope,
        tree=tree,
        records=records,
        draft_records=draft_records,
        issues=issues,
    )
    _validate_changed_action_quality(changed_nodes, issues)
    _validate_tree_and_strategy(tree, scope, issues)
    _validate_request_constraints(proposal, request, scope, records, issues)
    _validate_capacity(proposal, request, scope, records, issues)
    return _dedupe_issues(issues)


def normalize_execution_refine_proposal(
    *,
    proposal: ExecutionRefineProposal,
    request: ExecutionRefineRequest,
    scope: ExecutionRefineScope,
    enforce_capacity_fallback: bool = False,
) -> ExecutionRefineProposal:
    """Canonicalize server-derived fields without inventing task content."""
    records = copy.deepcopy(scope.task_records)
    substantive_operations: list[ExecutionDiffOperation] = []
    deferred_my_day: list[SetMyDayOperation] = []
    focus_task_ids = list(proposal.focus_task_ids)

    for operation in proposal.operations:
        if isinstance(operation, UpdateTaskOperation):
            record = records.get(str(operation.task_id))
            changes = operation.changes.model_dump(mode="json", exclude_unset=True)
            effective = (
                {
                    field: value
                    for field, value in changes.items()
                    if record.get(field) != value
                }
                if record is not None
                else changes
            )
            if not effective:
                continue
            if record is not None:
                record.update(effective)
            substantive_operations.append(
                operation.model_copy(
                    update={"changes": ExecutionTaskChanges.model_validate(effective)}
                )
            )
        elif isinstance(operation, SetMyDayOperation):
            deferred_my_day.append(operation)
        else:
            substantive_operations.append(operation)

    if request.mode == "time_budget":
        available = int(request.available_minutes or 0)
        buffer_minutes = min(20, max(3, math.ceil(available * 0.15)))
    else:
        buffer_minutes = 0
    protected_minutes = int(scope.snapshot.get("protected_my_day_minutes", 0))
    warnings = list(proposal.warnings)
    if request.mode == "time_budget" and enforce_capacity_fallback:
        usable_minutes = max(0, available - buffer_minutes - protected_minutes)
        selected: list[UUID] = []
        selected_minutes = 0
        for task_id in focus_task_ids:
            record = records.get(str(task_id))
            minutes = int(record.get("estimated_minutes") or 0) if record else 0
            if record is not None and selected_minutes + minutes <= usable_minutes:
                selected.append(task_id)
                selected_minutes += minutes
        if len(selected) != len(focus_task_ids):
            warnings.append(
                "部分候选任务超出本次可用容量，服务端已从 focus 中安全移除。"
            )
        focus_task_ids = selected
    focus_ids = {str(task_id) for task_id in focus_task_ids}

    my_day_positions: dict[str, int] = {}
    for operation in deferred_my_day:
        task_id = str(operation.task_id)
        desired = (
            task_id in focus_ids
            if request.mode == "time_budget" and enforce_capacity_fallback
            else (True if task_id in focus_ids else operation.is_in_my_day)
        )
        record = records.get(task_id)
        if record is not None and record.get("is_in_my_day") == desired:
            continue
        normalized = operation.model_copy(update={"is_in_my_day": desired})
        if task_id in my_day_positions:
            substantive_operations[my_day_positions[task_id]] = normalized
        else:
            my_day_positions[task_id] = len(substantive_operations)
            substantive_operations.append(normalized)
        if record is not None:
            record["is_in_my_day"] = desired

    for task_id in focus_ids:
        record = records.get(task_id)
        if (
            record is None
            or record.get("is_in_my_day") is True
            or not record.get("capabilities", {}).get("set_my_day", False)
            or len(substantive_operations) >= 12
        ):
            continue
        operation = SetMyDayOperation(
            operation_type="set_my_day",
            task_id=UUID(task_id),
            is_in_my_day=True,
            reason="该任务属于本次 focus，服务端同步加入 My Day。",
        )
        my_day_positions[task_id] = len(substantive_operations)
        substantive_operations.append(operation)
        record["is_in_my_day"] = True

    if request.mode == "time_budget" and enforce_capacity_fallback:
        for task_id, record in records.items():
            if (
                task_id in focus_ids
                or record.get("is_in_my_day") is not True
                or not record.get("capabilities", {}).get("set_my_day", False)
                or len(substantive_operations) >= 12
            ):
                continue
            substantive_operations.append(
                SetMyDayOperation(
                    operation_type="set_my_day",
                    task_id=UUID(task_id),
                    is_in_my_day=False,
                    reason="该任务不在最终 focus 中，服务端同步移出 My Day。",
                )
            )
            record["is_in_my_day"] = False

    if not substantive_operations:
        substantive_operations = list(proposal.operations)

    focus_minutes = sum(
        int(records[task_id].get("estimated_minutes") or 0)
        for task_id in focus_ids
        if task_id in records
    )
    if (
        request.mode == "time_budget"
        and protected_minutes > int(request.available_minutes or 0) - buffer_minutes
        and not warnings
    ):
        warnings.append("受保护的今日承诺已占满本次可用时间，无法增加新的 focus。")

    return proposal.model_copy(
        update={
            "operations": substantive_operations,
            "focus_task_ids": focus_task_ids,
            "estimated_focus_minutes": focus_minutes,
            "buffer_minutes": buffer_minutes,
            "warnings": warnings,
        }
    )


def execution_refine_client_node_id(request_id: UUID, draft_id: str) -> str:
    digest = hashlib.sha256(f"{request_id}:{draft_id}".encode("utf-8")).hexdigest()[:32]
    return f"execution_refine_{digest}"


def _task_scope_record(
    task: Task,
    *,
    schema_version: int,
    current_phase_id: str | None,
) -> dict[str, Any]:
    metadata = dict(task.metadata_) if isinstance(task.metadata_, dict) else {}
    source = metadata.get("source")
    if not isinstance(source, str):
        source = "ai" if task.ai_generated else "manual"
    phase_id = metadata.get("phase_id") if isinstance(metadata.get("phase_id"), str) else None
    protected_reason: str | None = None
    if task.status in {"completed", "archived"}:
        protected_reason = "completed_or_archived"
    elif source == "task_assist":
        protected_reason = "task_assist_child"
    elif metadata.get("practice_loop_id") is not None or source == "practice":
        protected_reason = "practice_occurrence"
    elif metadata.get("read_only_preview") is True:
        protected_reason = "read_only_preview"
    elif schema_version == 2 and phase_id != current_phase_id:
        protected_reason = "historical_or_future_phase"

    is_manual = source == "manual" or task.ai_generated is False
    assist_anchor = metadata.get("assist_rollup") is True
    if protected_reason is None and assist_anchor:
        protected_reason = "assist_rollup_anchor"
    active = task.status in {"draft", "active", "today"}
    update_allowed = (
        protected_reason is None
        and active
        and task.node_type == "action"
        and not is_manual
        and not assist_anchor
    )
    anchor_or_unprotected = protected_reason in {None, "assist_rollup_anchor"}
    reorder_allowed = anchor_or_unprotected and active and (
        task.node_type == "action" or is_manual or assist_anchor
    )
    my_day_allowed = anchor_or_unprotected and active
    add_child_allowed = (
        protected_reason is None
        and active
        and task.node_type == "group"
        and not is_manual
        and not assist_anchor
    )
    return {
        "task_id": str(task.id),
        "client_node_id": task.client_node_id,
        "parent_task_id": str(task.parent_task_id) if task.parent_task_id else None,
        "title": task.title,
        "description": task.description,
        "estimated_minutes": task.estimated_minutes,
        "done_criteria": metadata.get("done_criteria"),
        "start_hint": metadata.get("start_hint"),
        "fallback_action": metadata.get("fallback_action"),
        "node_type": task.node_type,
        "status": task.status,
        "sort_order": task.sort_order,
        "source": source,
        "phase_id": phase_id,
        "is_in_my_day": bool(task.is_in_my_day),
        "protected_reason": protected_reason,
        "capabilities": {
            "update_task": update_allowed,
            "reorder": reorder_allowed,
            "set_my_day": my_day_allowed,
            "add_child": add_child_allowed,
        },
        "metadata": _json_safe(metadata),
        "updated_at": _iso(task.updated_at),
    }


def _prompt_task_record(record: dict[str, Any]) -> dict[str, Any]:
    base = {
        "task_id": record["task_id"],
        "parent_task_id": record["parent_task_id"],
        "title": record["title"],
        "node_type": record["node_type"],
        "status": record["status"],
        "estimated_minutes": record["estimated_minutes"],
        "sort_order": record["sort_order"],
        "is_in_my_day": record["is_in_my_day"],
        "source": record["source"],
        "phase_id": record["phase_id"],
        "protected_reason": record["protected_reason"],
        "capabilities": record["capabilities"],
    }
    if record["protected_reason"] is None:
        base.update(
            {
                "description": record["description"],
                "done_criteria": record["done_criteria"],
                "start_hint": record["start_hint"],
                "fallback_action": record["fallback_action"],
            }
        )
    return base


def _latest_applied_context(run: ExecutionRefineRun | None) -> dict[str, Any] | None:
    if run is None:
        return None
    proposal = run.proposal if isinstance(run.proposal, dict) else {}
    return {
        "request_id": str(run.request_id),
        "mode": run.mode,
        "summary": proposal.get("summary"),
        "input_context": _json_safe(run.input_context),
        "applied_at": _iso(run.applied_at),
    }


def _validate_add_dependencies(
    *,
    proposal: ExecutionRefineProposal,
    scope: ExecutionRefineScope,
    tree: TaskTree,
    records: dict[str, dict[str, Any]],
    draft_records: dict[str, dict[str, Any]],
    issues: list[ExecutionRefineValidationIssue],
) -> None:
    edges: dict[str, set[str]] = {key: set() for key in records}
    for task_id, dependency_id in scope.dependency_edges:
        if task_id in edges and dependency_id in records:
            edges[task_id].add(dependency_id)
    for index, operation in enumerate(proposal.operations):
        if not isinstance(operation, AddTaskOperation):
            continue
        record = draft_records.get(operation.draft_id)
        if record is None:
            continue
        target = record["task_id"]
        seen_refs: set[str] = set()
        for reference in operation.depends_on_refs:
            if reference in seen_refs:
                issues.append(
                    _operation_issue(
                        "EXECUTION_REFINE_DEPENDENCY_INVALID",
                        index,
                        reference,
                        "depends_on_refs 不能重复。",
                        "删除重复依赖。",
                    )
                )
                continue
            seen_refs.add(reference)
            dependency_key = (
                draft_records[reference]["task_id"]
                if reference in draft_records
                else reference
            )
            dependency = records.get(dependency_key)
            if (
                dependency is None
                or dependency["node_type"] != "action"
                or dependency["source"] == "task_assist"
                or dependency_key == target
            ):
                issues.append(
                    _operation_issue(
                        "EXECUTION_REFINE_DEPENDENCY_INVALID",
                        index,
                        reference,
                        "depends_on_refs 必须引用当前 scope 的 Action task_id 或 proposal draft_id。",
                        "删除非法引用，只保留同一项目中的有效 Action 依赖。",
                    )
                )
                continue
            edges[target].add(dependency_key)
        tree_node = next(
            (
                node
                for node in _iter_tree_nodes(tree.root)
                if node.client_node_id == record["client_node_id"]
            ),
            None,
        )
        if tree_node is not None:
            tree_node.depends_on = [
                records[
                    draft_records[reference]["task_id"]
                    if reference in draft_records
                    else reference
                ]["client_node_id"]
                for reference in operation.depends_on_refs
                if (
                    draft_records[reference]["task_id"]
                    if reference in draft_records
                    else reference
                )
                in records
            ]
    if _has_cycle(edges):
        issues.append(
            _issue(
                "EXECUTION_REFINE_DEPENDENCY_CYCLE",
                "proposal 与现有依赖合并后形成循环。",
                "只移除造成循环的新增依赖，不改变其他有效操作。",
            )
        )


def _validate_changed_action_quality(
    nodes: Sequence[TaskNode],
    issues: list[ExecutionRefineValidationIssue],
) -> None:
    for node in nodes:
        quality = score_action_node(node)
        placeholder_title = _is_invalid_quality_text(node.title)
        invalid_fields = [
            field
            for field in ("done_criteria", "start_hint", "fallback_action")
            if isinstance(getattr(node, field), str)
            and _is_invalid_quality_text(getattr(node, field))
        ]
        missing_long_done_criteria = (
            node.estimated_minutes >= 20 and not node.done_criteria
        )
        if (
            quality.score < 70
            or quality.has_abstract_violation
            or placeholder_title
            or invalid_fields
            or missing_long_done_criteria
        ):
            reasons = list(quality.reasons) + [f"invalid_{field}" for field in invalid_fields]
            if placeholder_title:
                reasons.append("placeholder_title")
            if missing_long_done_criteria:
                reasons.append("missing_done_criteria_for_long_action")
            issues.append(
                _issue(
                    "EXECUTION_REFINE_ACTION_QUALITY",
                    f"任务《{node.title}》可执行性不足：score={quality.score}, issues={reasons}。",
                    "只改写该 Action 为明确动词、明确对象、合理时长和可核验 done_criteria。",
                    task_ref=node.client_node_id,
                )
            )


def _is_invalid_quality_text(value: str) -> bool:
    normalized = value.strip().lower()
    return normalized in INVALID_QUALITY_PLACEHOLDERS or any(
        marker in normalized for marker in INVALID_QUALITY_MARKERS
    )


def _validate_tree_and_strategy(
    tree: TaskTree,
    scope: ExecutionRefineScope,
    issues: list[ExecutionRefineValidationIssue],
) -> None:
    try:
        validated = TaskTree.model_validate(tree.model_dump(mode="json"))
    except ValidationError:
        issues.append(
            _issue(
                "EXECUTION_REFINE_RESULT_TREE_INVALID",
                "应用 proposal 后的内存 TaskTree 不合法。",
                "只修复导致树结构、引用或深度错误的 operation。",
            )
        )
        return
    original = scope.task_tree
    if (
        validated.planning_context != original.planning_context
        or validated.strategy_context != original.strategy_context
    ):
        issues.append(
            _issue(
                "EXECUTION_REFINE_HISTORY_MUTATION",
                "proposal 改变了 planning_context 或 strategy_context。",
                "恢复 Roadmap、阶段、review、loop、checkpoint 和策略上下文原值。",
            )
        )

    strategy_tree = validated.model_copy(deep=True)
    baseline_tree = original.model_copy(deep=True)
    if (
        strategy_tree.strategy_context is not None
        and strategy_tree.strategy_context.strategy_type == "delivery"
    ):
        planned_minutes = sum(
            node.estimated_minutes
            for node in _iter_tree_nodes(strategy_tree.root)
            if node.node_type == "action"
        )
        strategy_tree.strategy_context.time_plan.planned_minutes = planned_minutes
    baseline_errors = validate_strategy_context(
        baseline_tree,
        intent_type=scope.intent_type,
        intent_text=scope.snapshot["thread"].get("intent_text"),
        enabled=True,
    )
    baseline_error_keys = {
        (error.code, error.offender)
        for error in baseline_errors
    }
    strategy_errors = validate_strategy_context(
        strategy_tree,
        intent_type=scope.intent_type,
        intent_text=scope.snapshot["thread"].get("intent_text"),
        enabled=True,
    )
    for error in strategy_errors:
        if (error.code, error.offender) in baseline_error_keys:
            continue
        issues.append(
            _issue(
                f"EXECUTION_REFINE_{error.code}",
                error.message,
                error.fix_suggestion,
                task_ref=error.offender,
            )
        )


def _validate_request_constraints(
    proposal: ExecutionRefineProposal,
    request: ExecutionRefineRequest,
    scope: ExecutionRefineScope,
    records: dict[str, dict[str, Any]],
    issues: list[ExecutionRefineValidationIssue],
) -> None:
    focus = {str(task_id) for task_id in proposal.focus_task_ids}
    for task_id in request.blocked_task_ids:
        value = str(task_id)
        if value in focus or records[value]["is_in_my_day"]:
            issues.append(
                _issue(
                    "EXECUTION_REFINE_BLOCKED_CONSTRAINT_LOST",
                    f"blocked task {value} 仍被放入即时执行范围。",
                    "将该任务移出 focus 和 My Day，同时保留其项目记录。",
                    task_ref=value,
                )
            )
    combined_text = " ".join(
        [proposal.summary, *proposal.preserved_constraints, *proposal.user_facing_reasons]
    )
    if request.new_deadline is not None:
        deadline_token = request.new_deadline.date().isoformat()
        if deadline_token not in combined_text:
            issues.append(
                _issue(
                    "EXECUTION_REFINE_DEADLINE_CONSTRAINT_LOST",
                    "proposal 没有原样保留新的截止日期。",
                    f"在 preserved_constraints 中明确写入 {deadline_token}，不改动日期。",
                )
            )
    for task_id in request.priority_task_ids:
        value = str(task_id)
        touched = value in focus or any(
            _operation_targets_task(operation, value)
            for operation in proposal.operations
        )
        if not touched:
            issues.append(
                _issue(
                    "EXECUTION_REFINE_PRIORITY_CONSTRAINT_LOST",
                    f"priority task {value} 没有出现在 focus 或调整操作中。",
                    "保留该 priority_task_id，并通过 focus、排序或 My Day 操作体现优先级。",
                    task_ref=value,
                )
            )


def _validate_capacity(
    proposal: ExecutionRefineProposal,
    request: ExecutionRefineRequest,
    scope: ExecutionRefineScope,
    records: dict[str, dict[str, Any]],
    issues: list[ExecutionRefineValidationIssue],
) -> None:
    focus_ids = [str(task_id) for task_id in proposal.focus_task_ids]
    focus_minutes = 0
    for task_id in focus_ids:
        record = records.get(task_id)
        if (
            record is None
            or record["source"] in {"task_assist", "practice"}
            or record["node_type"] != "action"
            or record["protected_reason"] not in {None, "assist_rollup_anchor"}
        ):
            issues.append(
                _issue(
                    "EXECUTION_REFINE_FOCUS_REFERENCE_INVALID",
                    f"focus_task_ids 包含不可直接聚焦的任务 {task_id}。",
                    "只引用当前项目中可直接执行的父级 Action。",
                    task_ref=task_id,
                )
            )
            continue
        if not record["is_in_my_day"]:
            issues.append(
                _issue(
                    "EXECUTION_REFINE_FOCUS_NOT_IN_MY_DAY",
                    f"focus task {task_id} 未处于 My Day。",
                    "添加 set_my_day=true，或从 focus_task_ids 移除该任务。",
                    task_ref=task_id,
                )
            )
        focus_minutes += int(record["estimated_minutes"] or 0)

    if proposal.estimated_focus_minutes != focus_minutes:
        issues.append(
            _issue(
                "EXECUTION_REFINE_FOCUS_MINUTES_MISMATCH",
                f"estimated_focus_minutes={proposal.estimated_focus_minutes}，服务端重算为 {focus_minutes}。",
                f"将 estimated_focus_minutes 改为 {focus_minutes}，不要修改显式时间约束。",
            )
        )

    if request.mode != "time_budget":
        if proposal.buffer_minutes != 0:
            issues.append(
                _issue(
                    "EXECUTION_REFINE_BUFFER_INVALID",
                    "非 time_budget 模式的 buffer_minutes 必须为 0。",
                    "只将 buffer_minutes 改为 0。",
                )
            )
        return

    available = int(request.available_minutes or 0)
    expected_buffer = min(20, max(3, math.ceil(available * 0.15)))
    if proposal.buffer_minutes != expected_buffer:
        issues.append(
            _issue(
                "EXECUTION_REFINE_BUFFER_INVALID",
                f"buffer_minutes 必须由服务端公式计算为 {expected_buffer}。",
                f"将 buffer_minutes 改为 {expected_buffer}。",
            )
        )
    protected_minutes = int(scope.snapshot.get("protected_my_day_minutes", 0))
    usable = available - expected_buffer
    unselected_my_day = [
        task_id
        for task_id, record in records.items()
        if record["is_in_my_day"]
        and record["capabilities"]["set_my_day"]
        and record["node_type"] == "action"
        and task_id not in set(focus_ids)
        and not task_id.startswith("draft:")
    ]
    if unselected_my_day:
        issues.append(
            _issue(
                "EXECUTION_REFINE_MY_DAY_FOCUS_MISMATCH",
                "time_budget 模式仍有可变任务留在 My Day，但未列入 focus。",
                "对非 focus 任务添加 set_my_day=false；不要改动其他项目或受保护承诺。",
                task_ref=unselected_my_day[0],
            )
        )
    if protected_minutes > usable:
        if focus_ids or focus_minutes:
            issues.append(
                _issue(
                    "EXECUTION_REFINE_CAPACITY_UNAVAILABLE",
                    "受保护的 My Day 承诺已超过本次可用容量，不能伪造新的 focus set。",
                    "清空 focus_task_ids，estimated_focus_minutes 设为 0，并添加容量不足 warning。",
                )
            )
        if not proposal.warnings:
            issues.append(
                _issue(
                    "EXECUTION_REFINE_CAPACITY_WARNING_REQUIRED",
                    "容量不足时必须给出用户可见 warning。",
                    "添加一条说明受保护承诺已占满容量的 warning。",
                )
            )
    elif focus_minutes + protected_minutes > usable:
        issues.append(
            _issue(
                "EXECUTION_REFINE_CAPACITY_EXCEEDED",
                f"focus {focus_minutes} + protected {protected_minutes} 超过可执行容量 {usable}。",
                "缩短明确允许 update 的任务，或减少 focus；不得减少 buffer 或修改受保护任务。",
            )
        )


def _require_capability(
    records: dict[str, dict[str, Any]],
    task_id: str,
    capability: str,
    operation_index: int,
    issues: list[ExecutionRefineValidationIssue],
) -> bool:
    record = records.get(task_id)
    if record is None:
        issues.append(
            _operation_issue(
                "EXECUTION_REFINE_REFERENCE_INVALID",
                operation_index,
                task_id,
                "operation 引用了当前项目 scope 外的任务。",
                "删除该 operation，或只引用 bounded scope 中的 task_id。",
            )
        )
        return False
    if not record["capabilities"].get(capability, False):
        issues.append(
            _operation_issue(
                "EXECUTION_REFINE_MUTATION_FORBIDDEN",
                operation_index,
                task_id,
                f"任务不允许 {capability}；protected_reason={record['protected_reason']}。",
                "删除该 operation；completed/history/Assist/practice 数据必须保持不变。",
            )
        )
        return False
    return True


def _changes_modify_record(record: dict[str, Any], changes: dict[str, Any]) -> bool:
    return any(record.get(field) != value for field, value in changes.items())


def _is_insertion_parent(record: dict[str, Any] | None) -> bool:
    return bool(record and record["capabilities"].get("add_child"))


def _next_sort_order(records: dict[str, dict[str, Any]], parent_id: str | None) -> int:
    values = [
        int(record["sort_order"])
        for record in records.values()
        if record["parent_task_id"] == parent_id
    ]
    return max(values, default=-1) + 1


def _update_tree_node(
    root: TaskNode,
    client_node_id: str,
    changes: dict[str, Any],
) -> TaskNode | None:
    for node in _iter_tree_nodes(root):
        if node.client_node_id != client_node_id:
            continue
        for field, value in changes.items():
            setattr(node, field, value)
        return node
    return None


def _insert_tree_node(
    root: TaskNode,
    records: dict[str, dict[str, Any]],
    *,
    parent_id: str | None,
    node: TaskNode,
    insert_after_task_id: str | None,
) -> bool:
    if parent_id is None:
        children = root.children
    else:
        parent_record = records.get(parent_id)
        if parent_record is None:
            return False
        parent_node = next(
            (
                item
                for item in _iter_tree_nodes(root)
                if item.client_node_id == parent_record["client_node_id"]
            ),
            None,
        )
        if parent_node is None:
            return False
        children = parent_node.children
    if insert_after_task_id is None:
        children.append(node)
        return True
    after_record = records.get(insert_after_task_id)
    if after_record is None:
        return False
    for index, child in enumerate(children):
        if child.client_node_id == after_record["client_node_id"]:
            children.insert(index + 1, node)
            return True
    return False


def _apply_record_reorder(
    records: dict[str, dict[str, Any]],
    parent_id: str | None,
    ordered_ids: list[str],
) -> None:
    mutable_positions = sorted(
        int(record["sort_order"])
        for record in records.values()
        if record["parent_task_id"] == parent_id
        and record["capabilities"]["reorder"]
        and not str(record["task_id"]).startswith("draft:")
    )
    for task_id, sort_order in zip(ordered_ids, mutable_positions, strict=True):
        records[task_id]["sort_order"] = sort_order


def _reorder_tree_siblings(
    root: TaskNode,
    records: dict[str, dict[str, Any]],
    parent_id: str | None,
    ordered_ids: list[str],
) -> None:
    if parent_id is None:
        children = root.children
    else:
        parent = records.get(parent_id)
        if parent is None:
            return
        node = next(
            (
                item
                for item in _iter_tree_nodes(root)
                if item.client_node_id == parent["client_node_id"]
            ),
            None,
        )
        if node is None:
            return
        children = node.children
    desired = [records[task_id]["client_node_id"] for task_id in ordered_ids]
    mutable_nodes = [child for child in children if child.client_node_id in desired]
    by_id = {child.client_node_id: child for child in mutable_nodes}
    iterator = iter([by_id[value] for value in desired if value in by_id])
    for index, child in enumerate(children):
        if child.client_node_id in by_id:
            children[index] = next(iterator)


def _operation_targets_task(operation: Any, task_id: str) -> bool:
    if isinstance(operation, (UpdateTaskOperation, SetMyDayOperation)):
        return str(operation.task_id) == task_id
    if isinstance(operation, ReorderSiblingsOperation):
        return any(str(value) == task_id for value in operation.ordered_task_ids)
    if isinstance(operation, AddTaskOperation):
        return (
            str(operation.parent_task_id) == task_id
            if operation.parent_task_id is not None
            else False
        )
    return False


def _insert_sort_order(
    tasks: Sequence[Task],
    *,
    parent_task_id: UUID | None,
    insert_after_task_id: UUID | None,
) -> int:
    siblings = [task for task in tasks if task.parent_task_id == parent_task_id]
    if insert_after_task_id is not None:
        anchor = next(
            (task for task in siblings if task.id == insert_after_task_id),
            None,
        )
        if anchor is None:
            raise ExecutionRefineError(
                code="EXECUTION_REFINE_APPLY_CONFLICT",
                message="任务插入位置已失效，请重新生成。",
            )
        return anchor.sort_order + 1
    return max((task.sort_order for task in siblings), default=-1) + 1


def _shift_siblings_for_insert(
    tasks: Sequence[Task],
    *,
    parent_task_id: UUID | None,
    from_sort_order: int,
) -> None:
    for task in tasks:
        if (
            task.parent_task_id == parent_task_id
            and task.sort_order >= from_sort_order
        ):
            task.sort_order += 1


def _infer_verb(title: str) -> str:
    normalized = title.strip()
    for length in (4, 3, 2, 1):
        candidate = normalized[:length]
        if candidate in {
            "打开",
            "完成",
            "列出",
            "写出",
            "保存",
            "记录",
            "整理",
            "对比",
            "创建",
            "提交",
            "标出",
            "联系",
            "搜索",
            "阅读",
        }:
            return candidate
    return normalized[:2] or "执行"


def _has_cycle(edges: dict[str, set[str]]) -> bool:
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(node: str) -> bool:
        if node in visiting:
            return True
        if node in visited:
            return False
        visiting.add(node)
        if any(visit(value) for value in edges.get(node, set())):
            return True
        visiting.remove(node)
        visited.add(node)
        return False

    return any(visit(node) for node in edges)


def _iter_tree_nodes(root: TaskNode):
    yield root
    for child in root.children:
        yield from _iter_tree_nodes(child)


def _task_order_key(task: Task) -> tuple[str, int, str]:
    return (str(task.parent_task_id or ""), int(task.sort_order), str(task.id))


def _intent_type_from_strategy(task_tree: TaskTree) -> str:
    context = task_tree.strategy_context
    if context is None:
        return "general"
    return (
        "short_term_delivery"
        if context.strategy_type == "delivery"
        else "exploration_decision"
    )


def _json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(item) for item in value]
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, datetime):
        return _iso(value)
    return value


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    normalized = (
        value.replace(tzinfo=timezone.utc)
        if value.tzinfo is None
        else value.astimezone(timezone.utc)
    )
    return normalized.isoformat()


def _lease_seconds() -> int:
    return max(15, int(os.getenv("EASYPLAN_EXECUTION_REFINE_LEASE_SECONDS", "45")))


def _thread_has_conflicting_plan_run(thread: AgentThread, now: datetime) -> bool:
    payload = thread.interrupt_payload if isinstance(thread.interrupt_payload, dict) else {}
    payload_type = payload.get("type")
    payload_status = payload.get("status")
    if payload_type == "phase_generation_state" and payload_status == "running":
        return True
    if payload_type == "next_phase_review" and payload_status in {
        "awaiting_confirmation",
        "confirming",
    }:
        return True
    if thread.status != "running":
        return False
    if thread.lease_expires_at is None:
        return thread.current_node not in {None, "completed", "human_review"}
    return _lease_is_active(thread.lease_expires_at, now)


def _lease_is_active(expires_at: datetime | None, now: datetime) -> bool:
    if expires_at is None:
        return False
    normalized = (
        expires_at.replace(tzinfo=timezone.utc)
        if expires_at.tzinfo is None
        else expires_at.astimezone(timezone.utc)
    )
    return normalized > now


def _mark_interrupted(run: ExecutionRefineRun) -> None:
    run.status = "failed"
    run.stage = "failed"
    run.proposal = None
    run.error_code = "EXECUTION_REFINE_INTERRUPTED"
    run.error_message = EXECUTION_REFINE_INTERRUPTED_MESSAGE
    run.lease_owner = None
    run.lease_expires_at = None


def _safe_failure_message(code: str, _message: str) -> str:
    if code == "EXECUTION_REFINE_INTERRUPTED":
        return EXECUTION_REFINE_INTERRUPTED_MESSAGE
    if code == "EXECUTION_REFINE_INVALID_PROPOSAL":
        return EXECUTION_REFINE_SAFE_INVALID_MESSAGE
    return EXECUTION_REFINE_SAFE_FAILURE_MESSAGE


def _issue(
    code: str,
    message: str,
    fix: str,
    *,
    operation_index: int | None = None,
    task_ref: str | None = None,
) -> ExecutionRefineValidationIssue:
    return ExecutionRefineValidationIssue(
        error_code=code,
        message=message,
        fix_suggestion=fix,
        operation_index=operation_index,
        task_ref=task_ref,
    )


def _operation_issue(
    code: str,
    operation_index: int,
    task_ref: str | None,
    message: str,
    fix: str,
) -> ExecutionRefineValidationIssue:
    return _issue(
        code,
        message,
        fix,
        operation_index=operation_index,
        task_ref=task_ref,
    )


def _dedupe_issues(
    issues: Sequence[ExecutionRefineValidationIssue],
) -> list[ExecutionRefineValidationIssue]:
    seen: set[tuple[Any, ...]] = set()
    result: list[ExecutionRefineValidationIssue] = []
    for issue in issues:
        key = (
            issue.error_code,
            issue.operation_index,
            issue.task_ref,
            issue.message,
        )
        if key in seen:
            continue
        seen.add(key)
        result.append(issue)
    return result
