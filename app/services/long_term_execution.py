from dataclasses import dataclass
from datetime import date, datetime, timedelta
from math import ceil
from typing import Iterable, Mapping, Sequence
from zoneinfo import ZoneInfo


@dataclass(frozen=True)
class LoopProgress:
    current_week_start: date
    current_week_target: int
    current_week_completed: int
    total_completed: int
    required_completions: int
    process_ready: bool
    estimated_end: date
    duration_elapsed: bool


@dataclass(frozen=True)
class PhaseReadiness:
    one_off_ready: bool
    process_ready: bool
    outcome_ready: bool
    recommendation: str
    review_available: bool


def local_date(instant: datetime, timezone_name: str) -> date:
    return instant.astimezone(ZoneInfo(timezone_name)).date()


def week_start(day: date) -> date:
    return day - timedelta(days=day.weekday())


def local_week_start(instant: datetime, timezone_name: str) -> date:
    return week_start(local_date(instant, timezone_name))


def next_week_start(instant: datetime, timezone_name: str) -> date:
    return local_week_start(instant, timezone_name) + timedelta(weeks=1)


def target_for_week(
    revisions: Sequence[Mapping[str, object]],
    week: date,
) -> int:
    eligible = [
        revision
        for revision in revisions
        if revision["effective_week"] <= week
    ]
    if not eligible:
        raise ValueError("practice loop requires a revision effective by this week")
    selected = max(eligible, key=lambda item: item["effective_week"])
    return int(selected["target_per_week"])


def calculate_loop_progress(
    *,
    starts_on: date,
    duration_weeks: int,
    revisions: Sequence[Mapping[str, object]],
    completion_dates: Iterable[date],
    today: date,
) -> LoopProgress:
    first_week = week_start(starts_on)
    planned_weeks = [
        first_week + timedelta(weeks=index)
        for index in range(duration_weeks)
    ]
    planned_target = sum(
        target_for_week(revisions, week)
        for week in planned_weeks
    )
    required = ceil(planned_target * 0.8)
    current_week = week_start(today)
    unique_dates = set(completion_dates)
    current_completed = sum(
        week_start(day) == current_week
        for day in unique_dates
    )
    estimated_end = first_week + timedelta(weeks=duration_weeks)
    total_completed = len(unique_dates)
    return LoopProgress(
        current_week_start=current_week,
        current_week_target=target_for_week(revisions, current_week),
        current_week_completed=current_completed,
        total_completed=total_completed,
        required_completions=required,
        process_ready=total_completed >= required,
        estimated_end=estimated_end,
        duration_elapsed=today >= estimated_end,
    )


def calculate_readiness(
    *,
    one_off_ready: bool,
    loop_progress: Sequence[LoopProgress],
    outcome_results: Sequence[bool],
    phase_estimated_end: date,
    today: date,
    early_review_requested: bool = False,
) -> PhaseReadiness:
    process_ready = all(progress.process_ready for progress in loop_progress)
    outcome_ready = bool(outcome_results) and all(outcome_results)
    conditions = (one_off_ready, process_ready, outcome_ready)
    recommendation = (
        "ready"
        if all(conditions)
        else "partial"
        if any(conditions)
        else "not_ready"
    )
    duration_elapsed = today >= phase_estimated_end
    return PhaseReadiness(
        one_off_ready=one_off_ready,
        process_ready=process_ready,
        outcome_ready=outcome_ready,
        recommendation=recommendation,
        review_available=(
            all(conditions)
            or duration_elapsed
            or early_review_requested
        ),
    )
