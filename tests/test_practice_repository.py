import asyncio
from datetime import date, datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

from app.models.base import Base
from app.models.practice import PhaseReview, PracticeLoop
from app.models.task import Task
from app.models.thread import AgentThread
from app.services.long_term_execution import LoopProgress
from app.services.practice_repository import PracticeLoopRepository
from app.api.schemas import OutcomeCheckpoint
from app.services.practice_repository import checkpoint_passes


def test_practice_log_has_daily_and_occurrence_uniqueness():
    table = Base.metadata.tables["practice_loop_logs"]
    unique_names = {
        constraint.name
        for constraint in table.constraints
        if constraint.__class__.__name__ == "UniqueConstraint"
    }

    assert "uq_practice_logs_loop_local_date" in unique_names
    assert "uq_practice_logs_occurrence_task" in unique_names


def _loop(*, user_id, active_task_id=None) -> PracticeLoop:
    return PracticeLoop(
        id=uuid4(),
        user_id=user_id,
        thread_id="thread-practice",
        phase_id="phase_01",
        loop_key="n3_vocab",
        status="active",
        timezone="Asia/Shanghai",
        starts_on=date(2026, 6, 29),
        duration_weeks=4,
        active_occurrence_task_id=active_task_id,
    )


class ScheduleSession:
    def __init__(self, task=None):
        self.task = task
        self.added = []
        self.flushed = 0

    async def get(self, _model, _task_id):
        return self.task

    def add(self, item):
        self.added.append(item)

    async def flush(self):
        self.flushed += 1
        for item in self.added:
            if getattr(item, "id", None) is None:
                item.id = uuid4()


class StubPracticeRepository(PracticeLoopRepository):
    def __init__(self, session, *, loop, has_log=False, completed=0, target=3):
        super().__init__(session)
        self.loop = loop
        self.has_log = has_log
        self.completed = completed
        self.target = target

    async def _lock_owned_loop(self, **_kwargs):
        return self.loop

    async def _has_log_on_date(self, _loop_id, _day):
        return self.has_log

    async def _load_loop_progress(self, **_kwargs):
        return LoopProgress(
            current_week_start=date(2026, 7, 6),
            current_week_target=self.target,
            current_week_completed=self.completed,
            total_completed=self.completed,
            required_completions=10,
            process_ready=False,
            estimated_end=date(2026, 7, 27),
            duration_elapsed=False,
        )

    async def _revision_for_week(self, _loop_id, _week):
        return SimpleNamespace(
            title="Complete one N3 vocabulary practice",
            done_criteria="Complete 20 questions and record mistakes",
        )

    async def _next_task_sort_order(self, _user_id):
        return 4


def test_first_schedule_creates_my_day_occurrence():
    user_id = uuid4()
    loop = _loop(user_id=user_id)
    session = ScheduleSession()
    repository = StubPracticeRepository(session, loop=loop)

    result = asyncio.run(
        repository.schedule_today(
            user_id=user_id,
            thread_id=loop.thread_id,
            loop_id=loop.id,
            now=datetime(2026, 7, 6, 1, tzinfo=timezone.utc),
        )
    )

    assert result.error_code is None
    assert result.task is session.added[0]
    assert result.task.is_in_my_day is True
    assert result.task.metadata_["practice_loop_id"] == str(loop.id)
    assert loop.active_occurrence_task_id == result.task.id


def test_duplicate_schedule_returns_active_occurrence():
    user_id = uuid4()
    task = Task(id=uuid4(), status="active")
    loop = _loop(user_id=user_id, active_task_id=task.id)
    repository = StubPracticeRepository(ScheduleSession(task), loop=loop)

    result = asyncio.run(
        repository.schedule_today(
            user_id=user_id,
            thread_id=loop.thread_id,
            loop_id=loop.id,
            now=datetime(2026, 7, 6, 1, tzinfo=timezone.utc),
        )
    )

    assert result.task is task
    assert result.error_code is None


def test_schedule_rejects_daily_completion_and_weekly_quota():
    user_id = uuid4()
    loop = _loop(user_id=user_id)
    daily = StubPracticeRepository(ScheduleSession(), loop=loop, has_log=True)
    weekly = StubPracticeRepository(
        ScheduleSession(),
        loop=loop,
        completed=3,
        target=3,
    )

    daily_result = asyncio.run(
        daily.schedule_today(
            user_id=user_id,
            thread_id=loop.thread_id,
            loop_id=loop.id,
            now=datetime(2026, 7, 6, 1, tzinfo=timezone.utc),
        )
    )
    weekly_result = asyncio.run(
        weekly.schedule_today(
            user_id=user_id,
            thread_id=loop.thread_id,
            loop_id=loop.id,
            now=datetime(2026, 7, 6, 1, tzinfo=timezone.utc),
        )
    )

    assert daily_result.error_code == "DAILY_COMPLETION_REACHED"
    assert weekly_result.error_code == "WEEKLY_TARGET_REACHED"


def test_schedule_hides_unowned_loop_as_not_found():
    repository = StubPracticeRepository(ScheduleSession(), loop=None)

    result = asyncio.run(
        repository.schedule_today(
            user_id=uuid4(),
            thread_id="thread-practice",
            loop_id=uuid4(),
            now=datetime(2026, 7, 6, 1, tzinfo=timezone.utc),
        )
    )

    assert result.task is None
    assert result.error_code == "NOT_FOUND"


def test_checkpoint_passes_numeric_artifact_and_self_assessment():
    numeric_gte = OutcomeCheckpoint(
        checkpoint_id="score",
        title="Reach score",
        evidence_type="numeric",
        unit="percent",
        operator="gte",
        target_value=65,
    )
    numeric_lte = numeric_gte.model_copy(
        update={"checkpoint_id": "minutes", "operator": "lte", "target_value": 30}
    )
    artifact = OutcomeCheckpoint(
        checkpoint_id="artifact",
        title="Submit artifact",
        evidence_type="artifact",
        operator="exists",
    )
    assessment = OutcomeCheckpoint(
        checkpoint_id="confidence",
        title="Rate confidence",
        evidence_type="self_assessment",
        operator="gte",
        target_value=4,
    )

    assert checkpoint_passes(numeric_gte, {"value": 65}) is True
    assert checkpoint_passes(numeric_gte, {"value": 64}) is False
    assert checkpoint_passes(numeric_lte, {"value": 30}) is True
    assert checkpoint_passes(artifact, {"value": "https://example.com/work"}) is True
    assert checkpoint_passes(artifact, {"value": "  "}) is False
    assert checkpoint_passes(assessment, {"value": 4}) is True


class ReviewSession:
    def __init__(self, values):
        self.values = list(values)
        self.commit_count = 0
        self.rollback_count = 0
        self.refreshed = []
        self.added = []

    async def execute(self, _statement):
        return ReviewScalarResult(self.values.pop(0))

    def add(self, item):
        self.added.append(item)

    async def commit(self):
        self.commit_count += 1

    async def rollback(self):
        self.rollback_count += 1

    async def refresh(self, item):
        self.refreshed.append(item)


class ReviewScalarResult:
    def __init__(self, value):
        self.value = value

    def scalar_one_or_none(self):
        return self.value

    def scalars(self):
        return self

    def all(self):
        return self.value if isinstance(self.value, list) else []


def _review_thread(*, user_id) -> AgentThread:
    return AgentThread(
        user_id=user_id,
        thread_id="thread-v2",
        intent_text="Learn N3",
        status="succeeded",
        current_node="persist_internal_tasks",
        next_nodes=[],
        interrupt_payload=None,
        latest_checkpoint_id=None,
        task_tree={
            "root": {
                "client_node_id": "root",
                "title": "Start",
                "verb": "Start",
                "estimated_minutes": 5,
                "node_type": "group",
                "children": [],
            },
            "summary": "Start",
            "assumptions": [],
            "planning_context": {
                "schema_version": 2,
                "intent_type": "long_term_growth",
                "time_horizon": "months",
                "roadmap": [
                    {
                        "phase_id": "phase_01",
                        "order": 1,
                        "title": "Start",
                        "objective": "Build baseline",
                        "status": "current",
                    },
                    {
                        "phase_id": "phase_02",
                        "order": 2,
                        "title": "Build",
                        "objective": "Build skill",
                        "status": "planned",
                    },
                    {
                        "phase_id": "phase_03",
                        "order": 3,
                        "title": "Review",
                        "objective": "Verify skill",
                        "status": "planned",
                    },
                ],
                "current_phase": {
                    "phase_id": "phase_01",
                    "title": "Start",
                    "objective": "Build baseline",
                    "completion_rule": "long_term_execution_gate",
                    "estimated_duration_weeks": 4,
                },
                "next_action_client_node_id": None,
                "practice_loops": [],
                "outcome_checkpoints": [
                    {
                        "checkpoint_id": "artifact",
                        "title": "Save artifact",
                        "evidence_type": "artifact",
                        "operator": "exists",
                    }
                ],
                "phase_gate": {
                    "process_threshold": 0.8,
                    "outcome_rule": "all_required",
                },
            },
        },
        error_code=None,
        error_message=None,
        expires_at=None,
        interrupted_at=None,
        completed_at=None,
    )


def test_proceed_requires_ready_recommendation():
    user_id = uuid4()
    review = PhaseReview(
        id=uuid4(),
        user_id=user_id,
        thread_id="thread-v2",
        phase_id="phase_01",
        status="draft",
        recommendation="partial",
        evidence={},
        statistics={},
    )
    repository = PracticeLoopRepository(
        ReviewSession([_review_thread(user_id=user_id), review])
    )

    result = asyncio.run(
        repository.finalize_review(
            user_id=user_id,
            thread_id="thread-v2",
            phase_id="phase_01",
            decision="proceed",
            payload={},
        )
    )

    assert result.error_code == "PHASE_NOT_READY"


def test_override_requires_reason():
    user_id = uuid4()
    review = PhaseReview(
        id=uuid4(),
        user_id=user_id,
        thread_id="thread-v2",
        phase_id="phase_01",
        status="draft",
        recommendation="partial",
        evidence={},
        statistics={},
    )
    repository = PracticeLoopRepository(
        ReviewSession([_review_thread(user_id=user_id), review])
    )

    result = asyncio.run(
        repository.finalize_review(
            user_id=user_id,
            thread_id="thread-v2",
            phase_id="phase_01",
            decision="override",
            payload={"override_reason": ""},
        )
    )

    assert result.error_code == "OVERRIDE_REASON_REQUIRED"


def test_extend_updates_phase_and_loop_duration_atomically():
    user_id = uuid4()
    thread = _review_thread(user_id=user_id)
    context = thread.task_tree["planning_context"]
    context["practice_loops"] = [
        {
            "loop_id": "n3_vocab",
            "title": "Practice vocabulary",
            "target_per_week": 3,
            "duration_weeks": 4,
            "done_criteria": "Complete 20 questions",
        }
    ]
    review = PhaseReview(
        id=uuid4(),
        user_id=user_id,
        thread_id="thread-v2",
        phase_id="phase_01",
        status="draft",
        recommendation="partial",
        evidence={},
        statistics={},
    )
    loop = _loop(user_id=user_id)
    session = ReviewSession([thread, review, [loop]])
    repository = PracticeLoopRepository(session)

    result = asyncio.run(
        repository.finalize_review(
            user_id=user_id,
            thread_id="thread-v2",
            phase_id="phase_01",
            payload={"decision": "extend", "extension_weeks": 2},
            now=datetime(2026, 7, 5, tzinfo=timezone.utc),
        )
    )

    updated = thread.task_tree["planning_context"]
    assert result.error_code is None
    assert result.review.status == "finalized"
    assert updated["current_phase"]["estimated_duration_weeks"] == 6
    assert updated["practice_loops"][0]["duration_weeks"] == 6
    assert loop.duration_weeks == 6
    assert session.commit_count == 1


def test_adjustment_revision_starts_next_monday():
    user_id = uuid4()
    thread = _review_thread(user_id=user_id)
    context = thread.task_tree["planning_context"]
    context["practice_loops"] = [
        {
            "loop_id": "n3_vocab",
            "title": "Practice vocabulary",
            "target_per_week": 3,
            "duration_weeks": 4,
            "done_criteria": "Complete 20 questions",
        }
    ]
    review = PhaseReview(
        id=uuid4(),
        user_id=user_id,
        thread_id="thread-v2",
        phase_id="phase_01",
        status="draft",
        recommendation="partial",
        evidence={},
        statistics={},
    )
    loop = _loop(user_id=user_id)
    current_revision = SimpleNamespace(
        revision=1,
        title="Practice vocabulary",
        target_per_week=3,
        done_criteria="Complete 20 questions",
    )
    session = ReviewSession([thread, review, [loop], current_revision])
    repository = PracticeLoopRepository(session)

    result = asyncio.run(
        repository.finalize_review(
            user_id=user_id,
            thread_id="thread-v2",
            phase_id="phase_01",
            payload={
                "decision": "adjust",
                "adjustments": [
                    {
                        "loop_id": str(loop.id),
                        "target_per_week": 2,
                    }
                ],
            },
            now=datetime(2026, 7, 8, tzinfo=timezone.utc),
        )
    )

    revision = session.added[0]
    assert result.error_code is None
    assert revision.revision == 2
    assert revision.target_per_week == 2
    assert revision.effective_week == date(2026, 7, 13)
    assert thread.task_tree["planning_context"]["practice_loops"][0][
        "target_per_week"
    ] == 2


def test_final_phase_completes_only_after_ready_review_proceeds():
    user_id = uuid4()
    thread = _review_thread(user_id=user_id)
    context = thread.task_tree["planning_context"]
    context["roadmap"][0]["status"] = "completed"
    context["roadmap"][1]["status"] = "completed"
    context["roadmap"][2]["status"] = "current"
    context["current_phase"] = {
        "phase_id": "phase_03",
        "title": "Review",
        "objective": "Verify skill",
        "completion_rule": "long_term_execution_gate",
        "estimated_duration_weeks": 4,
    }
    review = PhaseReview(
        id=uuid4(),
        user_id=user_id,
        thread_id="thread-v2",
        phase_id="phase_03",
        status="draft",
        recommendation="ready",
        evidence={},
        statistics={"one_off_ready": True},
    )
    session = ReviewSession([thread, review, []])
    repository = PracticeLoopRepository(session)

    result = asyncio.run(
        repository.finalize_review(
            user_id=user_id,
            thread_id="thread-v2",
            phase_id="phase_03",
            payload={"decision": "proceed"},
            now=datetime(2026, 7, 8, tzinfo=timezone.utc),
        )
    )

    updated = thread.task_tree["planning_context"]
    assert result.error_code is None
    assert updated["current_phase"] is None
    assert all(phase["status"] == "completed" for phase in updated["roadmap"])
