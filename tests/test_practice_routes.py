from datetime import datetime, timezone
from uuid import uuid4

from fastapi.testclient import TestClient

from app.api.auth import AuthUser, get_current_user
from app.api.routes_practice import get_practice_repository
from app.main import create_app
from app.models.task import Task
from app.models.practice import PhaseReview
from app.services.practice_repository import (
    PhaseReviewMutationResult,
    ScheduleOccurrenceResult,
)


class FakePracticeSession:
    def __init__(self):
        self.commit_count = 0
        self.rollback_count = 0

    async def commit(self):
        self.commit_count += 1

    async def rollback(self):
        self.rollback_count += 1

    async def refresh(self, _item):
        return None


class FakePracticeRepository:
    def __init__(self, result, *, review_result=None):
        self.result = result
        self.review_result = review_result
        self.session = FakePracticeSession()
        self.calls = []

    async def schedule_today(self, **kwargs):
        self.calls.append(kwargs)
        return self.result

    async def update_phase_review(self, **kwargs):
        self.calls.append(kwargs)
        return self.review_result

    async def finalize_review(self, **kwargs):
        self.calls.append(kwargs)
        return self.review_result


def _task(*, user_id, thread_id: str, loop_id) -> Task:
    return Task(
        id=uuid4(),
        user_id=user_id,
        thread_id=thread_id,
        parent_task_id=None,
        client_node_id=f"practice_{uuid4().hex}",
        title="Practice vocabulary",
        description=None,
        node_type="action",
        status="active",
        view_bucket="planned",
        is_in_my_day=True,
        estimated_minutes=None,
        sort_order=0,
        ai_generated=True,
        user_edited=False,
        metadata_={
            "source": "practice_loop",
            "practice_loop_id": str(loop_id),
        },
    )


def _client(repository):
    app = create_app(enable_static=False)
    user = AuthUser(
        id=uuid4(),
        email="practice@example.com",
        password_hash="hash",
    )
    app.dependency_overrides[get_current_user] = lambda: user
    app.dependency_overrides[get_practice_repository] = lambda: repository
    return TestClient(app), user


def test_schedule_today_returns_idempotent_occurrence():
    loop_id = uuid4()
    provisional_user = uuid4()
    repository = FakePracticeRepository(
        ScheduleOccurrenceResult(
            task=_task(
                user_id=provisional_user,
                thread_id="thread-v2",
                loop_id=loop_id,
            )
        )
    )
    client, user = _client(repository)
    repository.result.task.user_id = user.id

    response = client.post(
        f"/api/threads/thread-v2/practice-loops/{loop_id}/schedule-today",
        headers={"X-User-Timezone": "Asia/Shanghai"},
    )

    assert response.status_code == 200
    assert response.json()["practice_loop_id"] == str(loop_id)
    assert repository.calls[0]["user_id"] == user.id
    assert repository.session.commit_count == 1


def test_schedule_today_maps_ownership_and_quota_errors():
    loop_id = uuid4()
    not_found = FakePracticeRepository(
        ScheduleOccurrenceResult(task=None, error_code="NOT_FOUND")
    )
    not_found_client, _ = _client(not_found)
    not_found_response = not_found_client.post(
        f"/api/threads/thread-v2/practice-loops/{loop_id}/schedule-today",
        headers={"X-User-Timezone": "Asia/Shanghai"},
    )

    conflict = FakePracticeRepository(
        ScheduleOccurrenceResult(
            task=None,
            error_code="WEEKLY_TARGET_REACHED",
            error_message="This week's target is already complete",
        )
    )
    conflict_client, _ = _client(conflict)
    conflict_response = conflict_client.post(
        f"/api/threads/thread-v2/practice-loops/{loop_id}/schedule-today",
        headers={"X-User-Timezone": "Asia/Shanghai"},
    )

    assert not_found_response.status_code == 404
    assert conflict_response.status_code == 409
    assert conflict_response.json()["detail"]["error_code"] == "WEEKLY_TARGET_REACHED"


def test_practice_mutation_routes_can_be_disabled(monkeypatch):
    monkeypatch.setenv("EASYPLAN_LONG_TERM_EXECUTION_ENABLED", "false")
    repository = FakePracticeRepository(
        ScheduleOccurrenceResult(task=None, error_code="NOT_FOUND")
    )
    client, _ = _client(repository)

    response = client.post(
        f"/api/threads/thread-v2/practice-loops/{uuid4()}/schedule-today",
        headers={"X-User-Timezone": "UTC"},
    )

    assert response.status_code == 404
    assert repository.calls == []


def _review(*, user_id) -> PhaseReview:
    now = datetime(2026, 7, 5, tzinfo=timezone.utc)
    return PhaseReview(
        id=uuid4(),
        user_id=user_id,
        thread_id="thread-v2",
        phase_id="phase_01",
        status="draft",
        recommendation="ready",
        decision=None,
        evidence={"artifact": {"value": "https://example.com/work"}},
        difficulty=None,
        next_capacity=None,
        override_reason=None,
        statistics={"review_available": True},
        created_at=now,
        updated_at=now,
    )


def test_review_routes_return_review_and_map_invalid_evidence():
    placeholder_user = uuid4()
    successful = FakePracticeRepository(
        ScheduleOccurrenceResult(task=None),
        review_result=PhaseReviewMutationResult(
            review=_review(user_id=placeholder_user)
        ),
    )
    client, user = _client(successful)
    successful.review_result.review.user_id = user.id

    update_response = client.put(
        "/api/threads/thread-v2/phases/phase_01/review",
        headers={"X-User-Timezone": "Asia/Shanghai"},
        json={
            "evidence": {
                "artifact": {
                    "evidence_type": "artifact",
                    "value": "https://example.com/work",
                }
            }
        },
    )
    decision_response = client.post(
        "/api/threads/thread-v2/phases/phase_01/review/decision",
        headers={"X-User-Timezone": "Asia/Shanghai"},
        json={"decision": "proceed"},
    )

    assert update_response.status_code == 200
    assert decision_response.status_code == 200
    assert successful.calls[0]["user_id"] == user.id

    invalid = FakePracticeRepository(
        ScheduleOccurrenceResult(task=None),
        review_result=PhaseReviewMutationResult(
            review=None,
            error_code="INVALID_CHECKPOINT_EVIDENCE",
            error_message="Evidence type does not match checkpoint",
        ),
    )
    invalid_client, _ = _client(invalid)
    invalid_response = invalid_client.put(
        "/api/threads/thread-v2/phases/phase_01/review",
        headers={"X-User-Timezone": "UTC"},
        json={"evidence": {"unknown": {"value": 1}}},
    )

    assert invalid_response.status_code == 422
    assert (
        invalid_response.json()["detail"]["error_code"]
        == "INVALID_CHECKPOINT_EVIDENCE"
    )
