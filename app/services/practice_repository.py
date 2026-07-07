from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas import (
    OutcomeCheckpoint,
    PhaseReviewDecisionRequest,
    PhaseReviewUpdateRequest,
    PracticeLoopDefinition,
    TaskTree,
)
from app.models.practice import (
    PhaseReview,
    PracticeLoop,
    PracticeLoopLog,
    PracticeLoopRevision,
)
from app.models.task import Task
from app.models.thread import AgentThread
from app.services.long_term_execution import (
    LoopProgress,
    calculate_loop_progress,
    calculate_readiness,
    local_date,
    local_week_start,
    next_week_start,
)
from app.services.phase_planning import calculate_phase_progress, complete_final_phase


@dataclass(frozen=True)
class ScheduleOccurrenceResult:
    task: Task | None
    error_code: str | None = None
    error_message: str | None = None


@dataclass(frozen=True)
class PhaseReviewMutationResult:
    review: "PhaseReview | None"
    error_code: str | None = None
    error_message: str | None = None


class PracticeLoopConflictError(RuntimeError):
    def __init__(self, *, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def checkpoint_passes(
    checkpoint: OutcomeCheckpoint,
    evidence: dict[str, object],
) -> bool:
    if checkpoint.evidence_type == "artifact":
        value = str(evidence.get("value") or "").strip()
        return bool(value)
    try:
        value = float(evidence["value"])
        target = float(checkpoint.target_value)
    except (KeyError, TypeError, ValueError):
        return False
    if checkpoint.operator == "gte":
        return value >= target
    if checkpoint.operator == "lte":
        return value <= target
    return False


class PracticeLoopRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def persist_definitions_from_tree(
        self,
        *,
        user_id: UUID,
        thread_id: str,
        task_tree: TaskTree,
        timezone_name: str,
        now: datetime,
    ) -> None:
        context = task_tree.planning_context
        if (
            context is None
            or context.schema_version != 2
            or context.current_phase is None
        ):
            return

        effective_week = local_week_start(now, timezone_name)
        for definition in context.practice_loops:
            loop = await self._get_or_create_loop(
                user_id=user_id,
                thread_id=thread_id,
                phase_id=context.current_phase.phase_id,
                definition=definition,
                timezone_name=timezone_name,
                starts_on=effective_week,
            )
            await self._create_initial_revision_if_missing(
                loop=loop,
                definition=definition,
                effective_week=effective_week,
            )

    async def schedule_today(
        self,
        *,
        user_id: UUID,
        thread_id: str,
        loop_id: UUID,
        now: datetime,
    ) -> ScheduleOccurrenceResult:
        loop = await self._lock_owned_loop(
            user_id=user_id,
            thread_id=thread_id,
            loop_id=loop_id,
        )
        if loop is None:
            return ScheduleOccurrenceResult(task=None, error_code="NOT_FOUND")
        if loop.active_occurrence_task_id is not None:
            task = await self.session.get(Task, loop.active_occurrence_task_id)
            if task is not None and task.status != "completed":
                return ScheduleOccurrenceResult(task=task)

        today = local_date(now, loop.timezone)
        if await self._has_log_on_date(loop.id, today):
            return ScheduleOccurrenceResult(
                task=None,
                error_code="DAILY_COMPLETION_REACHED",
                error_message=(
                    "This practice loop already has a counted completion today"
                ),
            )
        progress = await self._load_loop_progress(loop=loop, now=now)
        if progress.current_week_completed >= progress.current_week_target:
            return ScheduleOccurrenceResult(
                task=None,
                error_code="WEEKLY_TARGET_REACHED",
                error_message="This week's target is already complete",
            )

        revision = await self._revision_for_week(
            loop.id,
            progress.current_week_start,
        )
        task = Task(
            user_id=user_id,
            thread_id=thread_id,
            parent_task_id=None,
            client_node_id=f"practice_{loop.id.hex}_{uuid4().hex}",
            title=revision.title,
            description=None,
            node_type="action",
            status="active",
            view_bucket="planned",
            is_in_my_day=True,
            estimated_minutes=None,
            sort_order=await self._next_task_sort_order(user_id),
            ai_generated=True,
            user_edited=False,
            metadata_={
                "source": "practice_loop",
                "practice_loop_id": str(loop.id),
                "phase_id": loop.phase_id,
                "done_criteria": revision.done_criteria,
            },
        )
        self.session.add(task)
        await self.session.flush()
        loop.active_occurrence_task_id = task.id
        return ScheduleOccurrenceResult(task=task)

    async def update_phase_review(
        self,
        *,
        user_id: UUID,
        thread_id: str,
        phase_id: str,
        payload: PhaseReviewUpdateRequest,
        now: datetime,
    ) -> PhaseReviewMutationResult:
        try:
            thread, tree = await self._lock_v2_phase_thread(
                user_id=user_id,
                thread_id=thread_id,
                phase_id=phase_id,
            )
            if thread is None or tree is None:
                return PhaseReviewMutationResult(
                    review=None,
                    error_code="NOT_FOUND",
                    error_message="Long-term phase was not found",
                )
            evidence_error = _validate_checkpoint_evidence(
                tree=tree,
                evidence=payload.evidence,
            )
            if evidence_error is not None:
                return PhaseReviewMutationResult(
                    review=None,
                    error_code="INVALID_CHECKPOINT_EVIDENCE",
                    error_message=evidence_error,
                )

            statistics, recommendation = await self._calculate_phase_statistics(
                user_id=user_id,
                thread=thread,
                tree=tree,
                evidence=payload.evidence,
                now=now,
                early_review_requested=payload.early_review_requested,
            )
            result = await self.session.execute(
                select(PhaseReview)
                .where(
                    PhaseReview.user_id == user_id,
                    PhaseReview.thread_id == thread_id,
                    PhaseReview.phase_id == phase_id,
                    PhaseReview.status == "draft",
                )
                .with_for_update()
            )
            review = result.scalar_one_or_none()
            if review is None:
                review = PhaseReview(
                    id=uuid4(),
                    user_id=user_id,
                    thread_id=thread_id,
                    phase_id=phase_id,
                    status="draft",
                    recommendation=recommendation,
                    decision=None,
                    evidence=payload.evidence,
                    difficulty=payload.difficulty,
                    next_capacity=payload.next_capacity,
                    override_reason=None,
                    statistics=statistics,
                )
                self.session.add(review)
            else:
                review.recommendation = recommendation
                review.evidence = payload.evidence
                review.difficulty = payload.difficulty
                review.next_capacity = payload.next_capacity
                review.statistics = statistics
            await self.session.commit()
            await self.session.refresh(review)
            return PhaseReviewMutationResult(review=review)
        except Exception:
            await self.session.rollback()
            raise

    async def get_execution_snapshot(
        self,
        *,
        user_id: UUID,
        thread: AgentThread,
        now: datetime,
    ) -> dict[str, object] | None:
        if thread.user_id != user_id or not thread.task_tree:
            return None
        try:
            tree = TaskTree.model_validate(thread.task_tree)
        except Exception:
            return None
        context = tree.planning_context
        if (
            context is None
            or context.schema_version != 2
            or context.current_phase is None
        ):
            return None

        phase_id = context.current_phase.phase_id
        loops = await self._load_owned_phase_loops(
            user_id=user_id,
            thread_id=thread.thread_id,
            phase_id=phase_id,
        )
        loop_payloads: list[dict[str, object]] = []
        for loop in loops:
            progress = await self._load_loop_progress(loop=loop, now=now)
            revision = await self._revision_for_week(
                loop.id,
                progress.current_week_start,
            )
            has_today_log = await self._has_log_on_date(
                loop.id,
                local_date(now, loop.timezone),
            )
            loop_payloads.append(
                {
                    "loop_id": loop.id,
                    "loop_key": loop.loop_key,
                    "title": revision.title,
                    "done_criteria": revision.done_criteria,
                    "target_per_week": progress.current_week_target,
                    "current_week_completed": progress.current_week_completed,
                    "total_completed": progress.total_completed,
                    "required_completions": progress.required_completions,
                    "estimated_end": progress.estimated_end,
                    "status": loop.status,
                    "can_schedule_today": (
                        loop.status == "active"
                        and loop.active_occurrence_task_id is None
                        and not has_today_log
                        and progress.current_week_completed
                        < progress.current_week_target
                    ),
                    "active_occurrence_task_id": loop.active_occurrence_task_id,
                }
            )

        review_result = await self.session.execute(
            select(PhaseReview)
            .where(
                PhaseReview.user_id == user_id,
                PhaseReview.thread_id == thread.thread_id,
                PhaseReview.phase_id == phase_id,
            )
            .order_by(PhaseReview.created_at.desc())
        )
        reviews = list(review_result.scalars().all())
        active_review = next(
            (review for review in reviews if review.status == "draft"),
            None,
        )
        finalized = [
            review
            for review in reviews
            if review.status == "finalized"
        ]
        latest_finalized = finalized[0] if finalized else None

        if active_review is not None:
            statistics = dict(active_review.statistics or {})
            recommendation = active_review.recommendation
        else:
            statistics, recommendation = await self._calculate_phase_statistics(
                user_id=user_id,
                thread=thread,
                tree=tree,
                evidence={},
                now=now,
                early_review_requested=False,
            )
        return {
            "phase_id": phase_id,
            "recommendation": recommendation,
            "review_available": bool(statistics.get("review_available")),
            "one_off_ready": bool(statistics.get("one_off_ready")),
            "process_ready": bool(statistics.get("process_ready")),
            "outcome_ready": bool(statistics.get("outcome_ready")),
            "loops": loop_payloads,
            "active_review": active_review,
            "latest_finalized_review": latest_finalized,
            "review_history": finalized,
        }

    async def finalize_review(
        self,
        *,
        user_id: UUID,
        thread_id: str,
        phase_id: str,
        decision: str | None = None,
        payload: PhaseReviewDecisionRequest | dict[str, object] | None = None,
        now: datetime | None = None,
    ) -> PhaseReviewMutationResult:
        now = now or datetime.now().astimezone()
        decision_payload = _coerce_review_decision(
            decision=decision,
            payload=payload,
        )
        try:
            thread, tree = await self._lock_v2_phase_thread(
                user_id=user_id,
                thread_id=thread_id,
                phase_id=phase_id,
            )
            if thread is None or tree is None:
                return PhaseReviewMutationResult(
                    review=None,
                    error_code="NOT_FOUND",
                    error_message="Long-term phase was not found",
                )
            review_result = await self.session.execute(
                select(PhaseReview)
                .where(
                    PhaseReview.user_id == user_id,
                    PhaseReview.thread_id == thread_id,
                    PhaseReview.phase_id == phase_id,
                    PhaseReview.status == "draft",
                )
                .with_for_update()
            )
            review = review_result.scalar_one_or_none()
            if review is None:
                return PhaseReviewMutationResult(
                    review=None,
                    error_code="PHASE_REVIEW_NOT_FOUND",
                    error_message="Create the phase review before finalizing it",
                )

            if (
                decision_payload.decision == "proceed"
                and review.recommendation != "ready"
            ):
                return PhaseReviewMutationResult(
                    review=review,
                    error_code="PHASE_NOT_READY",
                    error_message="The phase is not ready to proceed",
                )
            if (
                decision_payload.decision == "override"
                and not str(decision_payload.override_reason or "").strip()
            ):
                return PhaseReviewMutationResult(
                    review=review,
                    error_code="OVERRIDE_REASON_REQUIRED",
                    error_message="Override requires a non-empty reason",
                )

            loops = await self._load_owned_phase_loops(
                user_id=user_id,
                thread_id=thread_id,
                phase_id=phase_id,
                lock=True,
            )
            context = tree.planning_context
            if context is None or context.current_phase is None:
                return PhaseReviewMutationResult(
                    review=review,
                    error_code="PHASE_STATE_INVALID",
                    error_message="Current phase context is missing",
                )

            if decision_payload.decision == "extend":
                extension = decision_payload.extension_weeks
                if extension is None:
                    return PhaseReviewMutationResult(
                        review=review,
                        error_code="EXTENSION_WEEKS_REQUIRED",
                        error_message="Extension weeks are required",
                    )
                current_duration = context.current_phase.estimated_duration_weeks or 0
                if current_duration + extension > 12:
                    return PhaseReviewMutationResult(
                        review=review,
                        error_code="PHASE_DURATION_LIMIT",
                        error_message="Extended phase duration cannot exceed 12 weeks",
                    )
                context.current_phase.estimated_duration_weeks += extension
                for definition in context.practice_loops:
                    if definition.duration_weeks + extension > 12:
                        return PhaseReviewMutationResult(
                            review=review,
                            error_code="PHASE_DURATION_LIMIT",
                            error_message=(
                                "Extended practice loop duration cannot exceed "
                                "12 weeks"
                            ),
                        )
                    definition.duration_weeks += extension
                for loop in loops:
                    loop.duration_weeks += extension

            if decision_payload.decision == "adjust":
                if not decision_payload.adjustments:
                    return PhaseReviewMutationResult(
                        review=review,
                        error_code="ADJUSTMENTS_REQUIRED",
                        error_message="At least one practice loop adjustment is required",
                    )
                loops_by_id = {loop.id: loop for loop in loops}
                if any(
                    adjustment.loop_id not in loops_by_id
                    for adjustment in decision_payload.adjustments
                ):
                    return PhaseReviewMutationResult(
                        review=review,
                        error_code="PRACTICE_LOOP_NOT_FOUND",
                        error_message="Practice loop was not found",
                    )
                for adjustment in decision_payload.adjustments:
                    loop = loops_by_id[adjustment.loop_id]
                    revision = await self._create_adjusted_revision(
                        loop=loop,
                        adjustment=adjustment,
                        effective_week=next_week_start(now, loop.timezone),
                    )
                    definition = next(
                        item
                        for item in context.practice_loops
                        if item.loop_id == loop.loop_key
                    )
                    definition.title = revision.title
                    definition.target_per_week = revision.target_per_week
                    definition.done_criteria = revision.done_criteria

            if decision_payload.decision in {"proceed", "override"}:
                current_index = next(
                    index
                    for index, phase in enumerate(context.roadmap)
                    if phase.phase_id == phase_id
                )
                if current_index == len(context.roadmap) - 1:
                    tree.planning_context = complete_final_phase(context)

            thread.task_tree = tree.model_dump(mode="json")
            review.status = "finalized"
            review.decision = decision_payload.decision
            review.override_reason = (
                str(decision_payload.override_reason).strip()
                if decision_payload.override_reason
                else None
            )
            review.statistics = dict(review.statistics or {})
            await self.session.commit()
            await self.session.refresh(review)
            return PhaseReviewMutationResult(review=review)
        except Exception:
            await self.session.rollback()
            raise

    async def record_completion(
        self,
        *,
        user_id: UUID,
        task: Task,
        loop_id: UUID,
        now: datetime,
    ) -> None:
        loop = await self._lock_owned_loop(
            user_id=user_id,
            thread_id=task.thread_id,
            loop_id=loop_id,
        )
        if loop is None:
            raise PracticeLoopConflictError(
                code="PRACTICE_LOOP_NOT_FOUND",
                message="Practice loop was not found",
            )
        completed_local_date = local_date(now, loop.timezone)
        result = await self.session.execute(
            insert(PracticeLoopLog)
            .values(
                id=uuid4(),
                user_id=user_id,
                loop_id=loop.id,
                occurrence_task_id=task.id,
                completed_at=now,
                local_date=completed_local_date,
                note={},
            )
            .on_conflict_do_nothing()
            .returning(PracticeLoopLog.id)
        )
        if result.scalar_one_or_none() is None:
            raise PracticeLoopConflictError(
                code="DAILY_COMPLETION_REACHED",
                message="This practice loop already has a counted completion today",
            )
        if loop.active_occurrence_task_id == task.id:
            loop.active_occurrence_task_id = None

    async def clear_active_occurrence(
        self,
        *,
        user_id: UUID,
        loop_id: UUID,
        task_id: UUID,
    ) -> None:
        result = await self.session.execute(
            select(PracticeLoop)
            .where(
                PracticeLoop.user_id == user_id,
                PracticeLoop.id == loop_id,
            )
            .with_for_update()
        )
        loop = result.scalar_one_or_none()
        if loop is not None and loop.active_occurrence_task_id == task_id:
            loop.active_occurrence_task_id = None

    async def _lock_v2_phase_thread(
        self,
        *,
        user_id: UUID,
        thread_id: str,
        phase_id: str,
    ) -> tuple[AgentThread | None, TaskTree | None]:
        result = await self.session.execute(
            select(AgentThread)
            .where(
                AgentThread.user_id == user_id,
                AgentThread.thread_id == thread_id,
            )
            .with_for_update()
        )
        thread = result.scalar_one_or_none()
        if thread is None or not thread.task_tree:
            return None, None
        try:
            tree = TaskTree.model_validate(thread.task_tree)
        except Exception:
            return None, None
        context = tree.planning_context
        if (
            context is None
            or context.schema_version != 2
            or context.current_phase is None
            or context.current_phase.phase_id != phase_id
        ):
            return None, None
        return thread, tree

    async def _calculate_phase_statistics(
        self,
        *,
        user_id: UUID,
        thread: AgentThread,
        tree: TaskTree,
        evidence: dict[str, dict[str, object]],
        now: datetime,
        early_review_requested: bool,
    ) -> tuple[dict[str, object], str]:
        context = tree.planning_context
        if context is None or context.current_phase is None:
            raise RuntimeError("phase context is missing")
        phase_id = context.current_phase.phase_id
        task_result = await self.session.execute(
            select(Task).where(
                Task.user_id == user_id,
                Task.thread_id == thread.thread_id,
            )
        )
        tasks = list(task_result.scalars().all())
        one_off_progress = calculate_phase_progress(tasks, phase_id)
        one_off_ready = (
            one_off_progress.is_complete
            or one_off_progress.total_ai_actions == 0
        )

        loops = await self._load_owned_phase_loops(
            user_id=user_id,
            thread_id=thread.thread_id,
            phase_id=phase_id,
        )
        loop_progress = [
            await self._load_loop_progress(loop=loop, now=now)
            for loop in loops
        ]
        outcome_results = [
            checkpoint_passes(
                checkpoint,
                evidence.get(checkpoint.checkpoint_id, {}),
            )
            for checkpoint in context.outcome_checkpoints
        ]
        timezone_name = loops[0].timezone if loops else "UTC"
        starts_on = (
            min(loop.starts_on for loop in loops)
            if loops
            else local_date(thread.updated_at or now, timezone_name)
        )
        duration_weeks = context.current_phase.estimated_duration_weeks or 1
        phase_estimated_end = starts_on + timedelta(weeks=duration_weeks)
        readiness = calculate_readiness(
            one_off_ready=one_off_ready,
            loop_progress=loop_progress,
            outcome_results=outcome_results,
            phase_estimated_end=phase_estimated_end,
            today=local_date(now, timezone_name),
            early_review_requested=early_review_requested,
        )
        statistics: dict[str, object] = {
            "one_off_total": one_off_progress.total_ai_actions,
            "one_off_completed": one_off_progress.completed_ai_actions,
            "one_off_ready": readiness.one_off_ready,
            "process_ready": readiness.process_ready,
            "outcome_ready": readiness.outcome_ready,
            "review_available": readiness.review_available,
            "phase_estimated_end": phase_estimated_end.isoformat(),
            "loop_progress": [
                _loop_progress_json(item)
                for item in loop_progress
            ],
            "outcome_results": {
                checkpoint.checkpoint_id: passed
                for checkpoint, passed in zip(
                    context.outcome_checkpoints,
                    outcome_results,
                )
            },
        }
        return statistics, readiness.recommendation

    async def _load_owned_phase_loops(
        self,
        *,
        user_id: UUID,
        thread_id: str,
        phase_id: str,
        lock: bool = False,
    ) -> list[PracticeLoop]:
        query = select(PracticeLoop).where(
            PracticeLoop.user_id == user_id,
            PracticeLoop.thread_id == thread_id,
            PracticeLoop.phase_id == phase_id,
        )
        if lock:
            query = query.with_for_update()
        result = await self.session.execute(query)
        return list(result.scalars().all())

    async def _create_adjusted_revision(
        self,
        *,
        loop: PracticeLoop,
        adjustment,
        effective_week: date,
    ) -> PracticeLoopRevision:
        result = await self.session.execute(
            select(PracticeLoopRevision)
            .where(PracticeLoopRevision.loop_id == loop.id)
            .order_by(PracticeLoopRevision.revision.desc())
            .limit(1)
            .with_for_update()
        )
        current = result.scalar_one_or_none()
        if current is None:
            raise RuntimeError("practice loop has no revision")
        revision = PracticeLoopRevision(
            id=uuid4(),
            loop_id=loop.id,
            revision=current.revision + 1,
            effective_week=effective_week,
            title=adjustment.title or current.title,
            target_per_week=(
                adjustment.target_per_week
                if adjustment.target_per_week is not None
                else current.target_per_week
            ),
            done_criteria=adjustment.done_criteria or current.done_criteria,
        )
        self.session.add(revision)
        return revision

    async def _lock_owned_loop(
        self,
        *,
        user_id: UUID,
        thread_id: str,
        loop_id: UUID,
    ) -> PracticeLoop | None:
        result = await self.session.execute(
            select(PracticeLoop)
            .where(
                PracticeLoop.user_id == user_id,
                PracticeLoop.thread_id == thread_id,
                PracticeLoop.id == loop_id,
                PracticeLoop.status == "active",
            )
            .with_for_update()
        )
        return result.scalar_one_or_none()

    async def _has_log_on_date(self, loop_id: UUID, day: date) -> bool:
        result = await self.session.execute(
            select(PracticeLoopLog.id).where(
                PracticeLoopLog.loop_id == loop_id,
                PracticeLoopLog.local_date == day,
            )
        )
        return result.scalar_one_or_none() is not None

    async def _load_loop_progress(
        self,
        *,
        loop: PracticeLoop,
        now: datetime,
    ) -> LoopProgress:
        revision_result = await self.session.execute(
            select(PracticeLoopRevision)
            .where(PracticeLoopRevision.loop_id == loop.id)
            .order_by(PracticeLoopRevision.effective_week.asc())
        )
        revisions = [
            {
                "effective_week": revision.effective_week,
                "target_per_week": revision.target_per_week,
            }
            for revision in revision_result.scalars().all()
        ]
        log_result = await self.session.execute(
            select(PracticeLoopLog.local_date).where(
                PracticeLoopLog.loop_id == loop.id
            )
        )
        completion_dates = list(log_result.scalars().all())
        return calculate_loop_progress(
            starts_on=loop.starts_on,
            duration_weeks=loop.duration_weeks,
            revisions=revisions,
            completion_dates=completion_dates,
            today=local_date(now, loop.timezone),
        )

    async def _revision_for_week(
        self,
        loop_id: UUID,
        week: date,
    ) -> PracticeLoopRevision:
        result = await self.session.execute(
            select(PracticeLoopRevision)
            .where(
                PracticeLoopRevision.loop_id == loop_id,
                PracticeLoopRevision.effective_week <= week,
            )
            .order_by(PracticeLoopRevision.effective_week.desc())
            .limit(1)
        )
        revision = result.scalar_one_or_none()
        if revision is None:
            raise RuntimeError("practice loop has no effective revision")
        return revision

    async def _next_task_sort_order(self, user_id: UUID) -> int:
        result = await self.session.execute(
            select(func.coalesce(func.max(Task.sort_order), -1) + 1).where(
                Task.user_id == user_id
            )
        )
        return int(result.scalar_one())

    async def _get_or_create_loop(
        self,
        *,
        user_id: UUID,
        thread_id: str,
        phase_id: str,
        definition: PracticeLoopDefinition,
        timezone_name: str,
        starts_on,
    ) -> PracticeLoop:
        loop_id = uuid4()
        result = await self.session.execute(
            insert(PracticeLoop)
            .values(
                id=loop_id,
                user_id=user_id,
                thread_id=thread_id,
                phase_id=phase_id,
                loop_key=definition.loop_id,
                status="active",
                timezone=timezone_name,
                starts_on=starts_on,
                duration_weeks=definition.duration_weeks,
            )
            .on_conflict_do_nothing(
                index_elements=["thread_id", "phase_id", "loop_key"],
            )
            .returning(PracticeLoop)
        )
        loop = result.scalar_one_or_none()
        if loop is not None:
            return loop

        existing = await self.session.execute(
            select(PracticeLoop).where(
                PracticeLoop.user_id == user_id,
                PracticeLoop.thread_id == thread_id,
                PracticeLoop.phase_id == phase_id,
                PracticeLoop.loop_key == definition.loop_id,
            )
        )
        loop = existing.scalar_one_or_none()
        if loop is None:
            raise RuntimeError("practice loop conflict did not resolve to an owned row")
        return loop

    async def _create_initial_revision_if_missing(
        self,
        *,
        loop: PracticeLoop,
        definition: PracticeLoopDefinition,
        effective_week,
    ) -> None:
        await self.session.execute(
            insert(PracticeLoopRevision)
            .values(
                id=uuid4(),
                loop_id=loop.id,
                revision=1,
                effective_week=effective_week,
                title=definition.title,
                target_per_week=definition.target_per_week,
                done_criteria=definition.done_criteria,
            )
            .on_conflict_do_nothing(
                index_elements=["loop_id", "effective_week"],
            )
        )


def _validate_checkpoint_evidence(
    *,
    tree: TaskTree,
    evidence: dict[str, dict[str, object]],
) -> str | None:
    context = tree.planning_context
    if context is None:
        return "Planning context is missing"
    checkpoints = {
        checkpoint.checkpoint_id: checkpoint
        for checkpoint in context.outcome_checkpoints
    }
    unknown_ids = sorted(set(evidence) - set(checkpoints))
    if unknown_ids:
        return f"Unknown checkpoint ID: {unknown_ids[0]}"
    for checkpoint_id, item in evidence.items():
        supplied_type = item.get("evidence_type")
        if (
            supplied_type is not None
            and supplied_type != checkpoints[checkpoint_id].evidence_type
        ):
            return f"Evidence type does not match checkpoint {checkpoint_id}"
    return None


def _coerce_review_decision(
    *,
    decision: str | None,
    payload: PhaseReviewDecisionRequest | dict[str, object] | None,
) -> PhaseReviewDecisionRequest:
    if isinstance(payload, PhaseReviewDecisionRequest):
        if decision is None or payload.decision == decision:
            return payload
        return payload.model_copy(update={"decision": decision})
    values = dict(payload or {})
    if decision is not None:
        values["decision"] = decision
    return PhaseReviewDecisionRequest.model_validate(values)


def _loop_progress_json(progress: LoopProgress) -> dict[str, object]:
    payload = asdict(progress)
    payload["current_week_start"] = progress.current_week_start.isoformat()
    payload["estimated_end"] = progress.estimated_end.isoformat()
    return payload
