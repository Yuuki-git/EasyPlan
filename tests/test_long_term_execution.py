from datetime import date, datetime, timezone

from app.services.long_term_execution import (
    LoopProgress,
    calculate_loop_progress,
    calculate_readiness,
    local_week_start,
)


def test_local_week_start_uses_loop_timezone():
    instant = datetime(2026, 7, 5, 16, 30, tzinfo=timezone.utc)

    assert local_week_start(instant, "Asia/Shanghai") == date(2026, 7, 6)
    assert local_week_start(instant, "America/Los_Angeles") == date(2026, 6, 29)


def test_loop_progress_does_not_carry_missing_quota():
    progress = calculate_loop_progress(
        starts_on=date(2026, 6, 29),
        duration_weeks=2,
        revisions=[
            {
                "effective_week": date(2026, 6, 29),
                "target_per_week": 3,
            }
        ],
        completion_dates=[
            date(2026, 6, 29),
            date(2026, 7, 1),
            date(2026, 7, 6),
        ],
        today=date(2026, 7, 7),
    )

    assert progress.current_week_completed == 1
    assert progress.current_week_target == 3
    assert progress.total_completed == 3
    assert progress.required_completions == 5


def test_loop_progress_applies_revision_from_its_effective_week():
    progress = calculate_loop_progress(
        starts_on=date(2026, 6, 29),
        duration_weeks=2,
        revisions=[
            {"effective_week": date(2026, 6, 29), "target_per_week": 3},
            {"effective_week": date(2026, 7, 6), "target_per_week": 5},
        ],
        completion_dates=[],
        today=date(2026, 7, 7),
    )

    assert progress.current_week_target == 5
    assert progress.required_completions == 7


def _ready_loop(*, ready: bool = True, elapsed: bool = False) -> LoopProgress:
    return LoopProgress(
        current_week_start=date(2026, 6, 29),
        current_week_target=3,
        current_week_completed=3 if ready else 1,
        total_completed=10 if ready else 2,
        required_completions=10,
        process_ready=ready,
        estimated_end=date(2026, 7, 27),
        duration_elapsed=elapsed,
    )


def test_readiness_requires_one_off_process_and_outcome():
    result = calculate_readiness(
        one_off_ready=True,
        loop_progress=[_ready_loop()],
        outcome_results=[True],
        phase_estimated_end=date(2026, 7, 27),
        today=date(2026, 7, 20),
    )

    assert result.recommendation == "ready"
    assert result.review_available is True


def test_readiness_supports_milestone_only_phase():
    result = calculate_readiness(
        one_off_ready=True,
        loop_progress=[],
        outcome_results=[True],
        phase_estimated_end=date(2026, 7, 27),
        today=date(2026, 7, 20),
    )

    assert result.process_ready is True
    assert result.recommendation == "ready"


def test_partial_readiness_is_not_reviewable_before_duration():
    result = calculate_readiness(
        one_off_ready=True,
        loop_progress=[_ready_loop(ready=False)],
        outcome_results=[True],
        phase_estimated_end=date(2026, 7, 27),
        today=date(2026, 7, 20),
    )

    assert result.recommendation == "partial"
    assert result.review_available is False


def test_not_ready_becomes_reviewable_when_duration_elapsed():
    result = calculate_readiness(
        one_off_ready=False,
        loop_progress=[_ready_loop(ready=False, elapsed=True)],
        outcome_results=[False],
        phase_estimated_end=date(2026, 7, 20),
        today=date(2026, 7, 20),
    )

    assert result.recommendation == "not_ready"
    assert result.review_available is True


def test_explicit_early_review_makes_partial_phase_reviewable():
    result = calculate_readiness(
        one_off_ready=True,
        loop_progress=[_ready_loop(ready=False)],
        outcome_results=[False],
        phase_estimated_end=date(2026, 7, 27),
        today=date(2026, 7, 20),
        early_review_requested=True,
    )

    assert result.recommendation == "partial"
    assert result.review_available is True
