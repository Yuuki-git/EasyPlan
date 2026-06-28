from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any
from uuid import UUID, uuid4

from fastapi.testclient import TestClient

from app.api.schemas import TaskTree
from app.api.auth import AuthUser, get_current_user
from app.api.routes_intents import get_agent_runtime as get_intent_runtime
from app.api.routes_intents import get_thread_repository as get_intent_repository
from app.api.routes_threads import get_agent_runtime as get_thread_runtime
from app.api.routes_threads import get_thread_repository as get_thread_repository
from app.api.routes_tasks import get_task_repository as get_task_repository
from app.main import create_app
from app.services.thread_repository import ThreadStateConflictError


@dataclass
class FakeThread:
    thread_id: str
    user_id: str
    intent_text: str
    status: str = "running"
    state_version: int = 0
    last_event_id: str | None = None
    task_tree: dict | None = None
    interrupt_payload: dict | None = None
    latest_checkpoint_id: str | None = None
    lease_owner: str | None = None
    lease_expires_at: datetime | None = None


@dataclass
class FakeRouteTask:
    id: UUID
    user_id: UUID
    thread_id: str
    client_node_id: str
    parent_task_id: UUID | None
    title: str
    description: str | None
    node_type: str
    status: str
    view_bucket: str
    is_in_my_day: bool
    estimated_minutes: int | None
    sort_order: int
    ai_generated: bool
    metadata_: dict[str, Any]


class FakeThreadRepository:
    def __init__(self) -> None:
        self.threads: dict[tuple[str, str], FakeThread] = {}
        self.tasks: dict[UUID, FakeRouteTask] = {}
        self.created: list[FakeThread] = []
        self.deleted: list[dict] = []

    async def create_thread(self, *, user_id, thread_id, intent_text, selected_provider):
        thread = FakeThread(
            thread_id=thread_id,
            user_id=str(user_id),
            intent_text=intent_text,
            status="running",
        )
        self.threads[(str(user_id), thread_id)] = thread
        self.created.append(thread)
        return thread

    async def get_thread_for_user(self, *, user_id, thread_id):
        return self.threads.get((str(user_id), thread_id))

    async def mark_confirmation_accepted(self, *, thread, request_id, action=None):
        payload = thread.interrupt_payload if isinstance(thread.interrupt_payload, dict) else {}
        if payload.get("type") == "next_phase_review":
            expected_request_id = payload.get("request_id")
            if expected_request_id != request_id:
                raise ThreadStateConflictError(
                    code="REQUEST_ID_MISMATCH",
                    message="Next-phase preview request_id does not match the current pending preview",
                )
            if payload.get("status") != "awaiting_confirmation":
                raise ThreadStateConflictError(
                    code="PREVIEW_ALREADY_CONFIRMED",
                    message="This next-phase preview has already been confirmed or cancelled",
                )
            thread.interrupt_payload = {
                **payload,
                "status": "confirming",
            }
            thread.current_node = "next_phase_planner"
        elif payload.get("type") == "task_tree_review":
            next_payload = {
                **payload,
                "request_id": request_id,
            }
            if action == "refine":
                next_payload["status"] = "regenerating"
                thread.current_node = "planner"
            elif action == "edit":
                next_payload["status"] = "editing"
                thread.current_node = "validator"
            elif action == "approve":
                next_payload["status"] = "confirming"
                thread.current_node = "persist_internal_tasks"
            elif action == "reject":
                next_payload["status"] = "cancelled"
            thread.interrupt_payload = next_payload
        thread.status = "running"

    async def cancel_pending_preview(self, *, thread):
        payload = thread.interrupt_payload if isinstance(thread.interrupt_payload, dict) else {}
        if payload.get("type") != "next_phase_review" or payload.get("status") != "awaiting_confirmation":
            raise ThreadStateConflictError(
                code="NO_PENDING_PREVIEW",
                message="Thread has no pending preview to cancel",
            )
        request_id = str(payload.get("request_id") or "")
        history = dict(payload.get("history") or {})
        history[request_id] = {
            "status": "cancelled",
            "cancelled_at": datetime.now(timezone.utc).isoformat(),
        }
        thread.status = "succeeded"
        thread.current_node = "persist_internal_tasks"
        thread.lease_owner = None
        thread.lease_expires_at = None
        thread.interrupt_payload = {
            "type": "phase_generation_state",
            "request_id": request_id,
            "status": "cancelled",
            "history": history,
        }
        return thread

    async def start_next_phase_generation(self, *, user_id, thread_id, request_id, lease_seconds=300):
        thread = self.threads.get((str(user_id), thread_id))
        if thread is None:
            return None
        request_id_text = str(request_id)
        payload = thread.interrupt_payload if isinstance(thread.interrupt_payload, dict) else {}
        history = dict(payload.get("history") or {})
        if payload.get("request_id") == request_id_text and payload.get("status") in {
            "running",
            "awaiting_confirmation",
        }:
            return SimpleNamespace(
                thread=thread,
                status=payload["status"],
                should_schedule=False,
                current_phase_task_summary="",
                error_code=None,
                error_message=None,
                remaining_ai_actions=None,
            )
        if history.get(request_id_text, {}).get("status") == "confirmed":
            return SimpleNamespace(
                thread=thread,
                status=history[request_id_text]["status"],
                should_schedule=False,
                current_phase_task_summary="",
                error_code=None,
                error_message=None,
                remaining_ai_actions=None,
            )
        if history.get(request_id_text, {}).get("status") == "cancelled":
            return _phase_conflict(
                thread,
                "REQUEST_CANCELLED",
                "This next-phase request was cancelled. Generate a new request_id before trying again",
            )
        now = datetime.now(timezone.utc)
        if thread.lease_owner and thread.lease_owner != request_id_text and thread.lease_expires_at and thread.lease_expires_at > now:
            return _phase_conflict(thread, "PHASE_GENERATION_IN_PROGRESS", "Another phase request is active")

        context = (thread.task_tree or {}).get("planning_context")
        if not context:
            return _phase_conflict(thread, "PHASE_UNSUPPORTED", "Thread has no phase planning context")
        current_phase = context.get("current_phase")
        if current_phase is None:
            return _phase_conflict(thread, "GOAL_COMPLETED", "All roadmap phases are completed")
        phase_id = current_phase["phase_id"]
        phase_tasks = [
            task
            for task in self.tasks.values()
            if str(task.user_id) == str(user_id)
            and task.thread_id == thread_id
            and task.ai_generated
            and task.node_type == "action"
            and task.metadata_.get("source") == "ai"
            and task.metadata_.get("phase_id") == phase_id
        ]
        remaining = sum(task.status != "completed" for task in phase_tasks)
        if not phase_tasks:
            return _phase_conflict(thread, "PHASE_DATA_INVALID", "Current phase has no AI actions")
        if remaining:
            result = _phase_conflict(thread, "PHASE_INCOMPLETE", "Current phase is incomplete")
            result.remaining_ai_actions = remaining
            return result

        thread.status = "running"
        thread.lease_owner = request_id_text
        thread.lease_expires_at = now + timedelta(seconds=lease_seconds)
        thread.interrupt_payload = {
            "type": "phase_generation_state",
            "request_id": request_id_text,
            "status": "running",
            "history": history,
        }
        return SimpleNamespace(
            thread=thread,
            status="running",
            should_schedule=True,
            current_phase_task_summary=f"{len(phase_tasks)}/{len(phase_tasks)} AI actions completed",
            error_code=None,
            error_message=None,
            remaining_ai_actions=None,
        )

    async def delete_thread_for_user(self, *, user_id, thread_id):
        key = (str(user_id), thread_id)
        if key not in self.threads:
            return False
        del self.threads[key]
        self.tasks = {
            task_id: task
            for task_id, task in self.tasks.items()
            if not (str(task.user_id) == str(user_id) and task.thread_id == thread_id)
        }
        self.deleted.append({"user_id": user_id, "thread_id": thread_id})
        return True

    async def list_tasks_for_user(self, *, user_id, view_bucket=None):
        return [
            task
            for task in self.tasks.values()
            if str(task.user_id) == str(user_id)
            and (
                view_bucket is None
                or (view_bucket == "my_day" and task.is_in_my_day)
                or (view_bucket != "my_day" and task.view_bucket == view_bucket)
            )
        ]


class FakeRuntime:
    def __init__(self) -> None:
        self.started: list[dict] = []
        self.resumed: list[dict] = []
        self.streamed: list[dict] = []
        self.phase_runs: list[dict] = []
        self.events = ["event: reasoning\ndata: {\"state_version\":1,\"message\":\"running\"}\n\n"]

    async def run_new_thread(self, **kwargs):
        self.started.append(kwargs)

    async def resume_thread(self, **kwargs):
        self.resumed.append(kwargs)

    async def run_next_phase(self, **kwargs):
        self.phase_runs.append(kwargs)

    async def stream_thread_events(self, *, user_id, thread_id, last_event_id=None):
        self.streamed.append({"user_id": user_id, "thread_id": thread_id, "last_event_id": last_event_id})
        for event in self.events:
            yield event


def _client_with_overrides(repository: FakeThreadRepository, runtime: FakeRuntime):
    app = create_app(enable_static=False)
    user = AuthUser(
        id=uuid4(),
        email="user@example.com",
        password_hash="hash",
    )
    app.dependency_overrides[get_current_user] = lambda: user
    try:
        from app.api.auth import get_user_for_sse

        app.dependency_overrides[get_user_for_sse] = lambda: user
    except ImportError:
        pass
    app.dependency_overrides[get_intent_repository] = lambda: repository
    app.dependency_overrides[get_thread_repository] = lambda: repository
    app.dependency_overrides[get_task_repository] = lambda: repository
    app.dependency_overrides[get_intent_runtime] = lambda: runtime
    app.dependency_overrides[get_thread_runtime] = lambda: runtime
    return TestClient(app), user


def test_create_intent_persists_thread_and_starts_langgraph_background_task():
    repository = FakeThreadRepository()
    runtime = FakeRuntime()
    client, user = _client_with_overrides(repository, runtime)

    response = client.post(
        "/api/intents",
        headers={"X-User-Timezone": "Asia/Shanghai"},
        json={"intent_text": "写论文", "preferred_provider": "native"},
    )

    assert response.status_code == 202
    payload = response.json()
    assert payload["events_url"] == f"/api/threads/{payload['thread_id']}/events"
    assert repository.created[0].user_id == str(user.id)
    assert repository.created[0].intent_text == "写论文"
    assert runtime.started[0] == {
        "user_id": str(user.id),
        "thread_id": payload["thread_id"],
        "intent_text": "写论文",
        "selected_provider": "native",
        "planner_provider": None,
        "planner_model": None,
    }


def test_create_intent_forwards_requested_planner_provider_and_model():
    repository = FakeThreadRepository()
    runtime = FakeRuntime()
    client, user = _client_with_overrides(repository, runtime)

    response = client.post(
        "/api/intents",
        headers={"X-User-Timezone": "Asia/Shanghai"},
        json={
            "intent_text": "写论文",
            "preferred_provider": "native",
            "planner_provider": "deepseek",
            "planner_model": "deepseek-reasoner",
        },
    )

    assert response.status_code == 202
    assert runtime.started[0]["planner_provider"] == "deepseek"
    assert runtime.started[0]["planner_model"] == "deepseek-reasoner"


def test_create_intent_requires_authenticated_user():
    client = TestClient(create_app(enable_static=False))

    response = client.post(
        "/api/intents",
        headers={"X-User-Timezone": "Asia/Shanghai"},
        json={"intent_text": "写论文"},
    )

    assert response.status_code == 401


def test_stream_thread_events_checks_thread_ownership_and_uses_runtime_stream():
    repository = FakeThreadRepository()
    runtime = FakeRuntime()
    client, user = _client_with_overrides(repository, runtime)
    thread = FakeThread(thread_id="thread-1", user_id=str(user.id), intent_text="写论文")
    repository.threads[(str(user.id), thread.thread_id)] = thread

    response = client.get("/api/threads/thread-1/events")

    assert response.status_code == 200
    assert "event: reasoning" in response.text


def test_stream_thread_events_uses_query_last_event_id_when_header_is_missing():
    repository = FakeThreadRepository()
    runtime = FakeRuntime()
    client, user = _client_with_overrides(repository, runtime)
    thread = FakeThread(thread_id="thread-1", user_id=str(user.id), intent_text="写论文")
    repository.threads[(str(user.id), thread.thread_id)] = thread

    response = client.get("/api/threads/thread-1/events?last_event_id=evt_00000002")

    assert response.status_code == 200
    assert runtime.streamed[0]["last_event_id"] == "evt_00000002"


def test_confirm_thread_checks_thread_ownership_and_resumes_langgraph():
    repository = FakeThreadRepository()
    runtime = FakeRuntime()
    client, user = _client_with_overrides(repository, runtime)
    thread = FakeThread(thread_id="thread-1", user_id=str(user.id), intent_text="写论文")
    repository.threads[(str(user.id), thread.thread_id)] = thread

    response = client.post(
        "/api/threads/thread-1/confirm",
        headers={"X-User-Timezone": "Asia/Shanghai"},
        json={"request_id": "req_12345678", "action": "refine", "feedback": "更小一点"},
    )

    assert response.status_code == 202
    assert response.json() == {
        "thread_id": "thread-1",
        "request_id": "req_12345678",
        "status": "accepted",
    }
    assert runtime.resumed[0]["user_id"] == str(user.id)
    assert runtime.resumed[0]["thread_id"] == "thread-1"
    assert runtime.resumed[0]["decision"]["action"] == "refine"
    assert runtime.resumed[0]["decision"]["feedback"] == "更小一点"


def test_confirm_thread_requires_matching_next_phase_request_id():
    repository = FakeThreadRepository()
    runtime = FakeRuntime()
    client, user = _client_with_overrides(repository, runtime)
    thread = _phase_thread(user_id=user.id, thread_id="thread-phase")
    thread.status = "awaiting_confirmation"
    thread.interrupt_payload = {
        "type": "next_phase_review",
        "request_id": "11111111-1111-1111-1111-111111111111",
        "status": "awaiting_confirmation",
        "task_tree": {"summary": "preview"},
        "history": {},
    }
    repository.threads[(str(user.id), thread.thread_id)] = thread

    response = client.post(
        f"/api/threads/{thread.thread_id}/confirm",
        headers={"X-User-Timezone": "Asia/Shanghai"},
        json={"request_id": "99999999-9999-9999-9999-999999999999", "action": "approve"},
    )

    assert response.status_code == 409
    assert runtime.resumed == []
    assert thread.status == "awaiting_confirmation"
    assert thread.interrupt_payload["status"] == "awaiting_confirmation"


def test_confirm_thread_does_not_resume_same_next_phase_preview_twice():
    repository = FakeThreadRepository()
    runtime = FakeRuntime()
    client, user = _client_with_overrides(repository, runtime)
    thread = _phase_thread(user_id=user.id, thread_id="thread-phase")
    thread.status = "awaiting_confirmation"
    thread.interrupt_payload = {
        "type": "next_phase_review",
        "request_id": "11111111-1111-1111-1111-111111111111",
        "status": "awaiting_confirmation",
        "task_tree": {"summary": "preview"},
        "history": {},
    }
    repository.threads[(str(user.id), thread.thread_id)] = thread
    payload = {"request_id": "11111111-1111-1111-1111-111111111111", "action": "approve"}
    headers = {"X-User-Timezone": "Asia/Shanghai"}

    first = client.post(f"/api/threads/{thread.thread_id}/confirm", headers=headers, json=payload)
    second = client.post(f"/api/threads/{thread.thread_id}/confirm", headers=headers, json=payload)

    assert first.status_code == 202
    assert second.status_code == 409
    assert len(runtime.resumed) == 1


def test_cancel_next_phase_preview_returns_latest_snapshot():
    repository = FakeThreadRepository()
    runtime = FakeRuntime()
    client, user = _client_with_overrides(repository, runtime)
    thread = _phase_thread(user_id=user.id, thread_id="thread-phase")
    committed_task_tree = thread.task_tree
    thread.status = "awaiting_confirmation"
    thread.current_node = "human_review"
    thread.lease_owner = "11111111-1111-1111-1111-111111111111"
    thread.lease_expires_at = datetime.now(timezone.utc) + timedelta(minutes=5)
    thread.interrupt_payload = {
        "type": "next_phase_review",
        "request_id": "11111111-1111-1111-1111-111111111111",
        "status": "awaiting_confirmation",
        "task_tree": {"summary": "preview"},
        "history": {},
    }
    repository.threads[(str(user.id), thread.thread_id)] = thread

    response = client.delete(f"/api/threads/{thread.thread_id}/phases/next/cancel")

    assert response.status_code == 200
    body = response.json()
    assert body["thread_id"] == thread.thread_id
    assert body["status"] == "cancelled"
    assert body["interrupt_payload"]["type"] == "phase_generation_state"
    assert body["interrupt_payload"]["request_id"] == "11111111-1111-1111-1111-111111111111"
    assert body["interrupt_payload"]["status"] == "cancelled"
    assert body["interrupt_payload"]["history"]["11111111-1111-1111-1111-111111111111"]["status"] == "cancelled"
    assert "cancelled_at" in body["interrupt_payload"]["history"]["11111111-1111-1111-1111-111111111111"]
    assert body["task_tree"] == TaskTree.model_validate(committed_task_tree).model_dump(mode="json")


def test_cancel_next_phase_preview_returns_404_for_other_users_thread():
    repository = FakeThreadRepository()
    runtime = FakeRuntime()
    client, _user = _client_with_overrides(repository, runtime)
    other_user_id = uuid4()
    thread = _phase_thread(user_id=other_user_id, thread_id="other-thread")
    thread.status = "awaiting_confirmation"
    thread.interrupt_payload = {
        "type": "next_phase_review",
        "request_id": "11111111-1111-1111-1111-111111111111",
        "status": "awaiting_confirmation",
        "task_tree": {"summary": "preview"},
        "history": {},
    }
    repository.threads[(str(other_user_id), thread.thread_id)] = thread

    response = client.delete(f"/api/threads/{thread.thread_id}/phases/next/cancel")

    assert response.status_code == 404


def test_cancel_next_phase_preview_rejects_thread_without_pending_preview():
    repository = FakeThreadRepository()
    runtime = FakeRuntime()
    client, user = _client_with_overrides(repository, runtime)
    thread = _phase_thread(user_id=user.id, thread_id="thread-phase")
    committed_task_tree = thread.task_tree
    repository.threads[(str(user.id), thread.thread_id)] = thread

    response = client.delete(f"/api/threads/{thread.thread_id}/phases/next/cancel")

    assert response.status_code == 409
    assert thread.task_tree == committed_task_tree
    assert thread.interrupt_payload is None


def test_start_next_phase_rejects_cancelled_request_id_and_requires_new_request():
    repository = FakeThreadRepository()
    runtime = FakeRuntime()
    client, user = _client_with_overrides(repository, runtime)
    cancelled_request_id = "11111111-1111-1111-1111-111111111111"
    thread = _phase_thread(user_id=user.id, thread_id="thread-phase")
    task = _fake_route_task(
        user_id=user.id,
        thread_id=thread.thread_id,
        title="Completed phase action",
        status="completed",
        phase_id="phase_01",
        ai_generated=True,
    )
    repository.tasks[task.id] = task
    thread.interrupt_payload = {
        "type": "phase_generation_state",
        "request_id": cancelled_request_id,
        "status": "cancelled",
        "history": {
            cancelled_request_id: {
                "status": "cancelled",
                "cancelled_at": "2026-06-26T00:00:00+00:00",
            }
        },
    }
    repository.threads[(str(user.id), thread.thread_id)] = thread

    response = client.post(
        f"/api/threads/{thread.thread_id}/phases/next",
        headers={"X-User-Timezone": "Asia/Shanghai"},
        json={"request_id": cancelled_request_id},
    )

    assert response.status_code == 409
    assert response.json()["detail"]["error_code"] == "REQUEST_CANCELLED"
    assert runtime.phase_runs == []


def test_start_next_phase_allows_new_request_after_cancelled_request_id():
    repository = FakeThreadRepository()
    runtime = FakeRuntime()
    client, user = _client_with_overrides(repository, runtime)
    cancelled_request_id = "11111111-1111-1111-1111-111111111111"
    new_request_id = "22222222-2222-2222-2222-222222222222"
    thread = _phase_thread(user_id=user.id, thread_id="thread-phase")
    task = _fake_route_task(
        user_id=user.id,
        thread_id=thread.thread_id,
        title="Completed phase action",
        status="completed",
        phase_id="phase_01",
        ai_generated=True,
    )
    repository.tasks[task.id] = task
    thread.interrupt_payload = {
        "type": "phase_generation_state",
        "request_id": cancelled_request_id,
        "status": "cancelled",
        "history": {
            cancelled_request_id: {
                "status": "cancelled",
                "cancelled_at": "2026-06-26T00:00:00+00:00",
            }
        },
    }
    repository.threads[(str(user.id), thread.thread_id)] = thread

    response = client.post(
        f"/api/threads/{thread.thread_id}/phases/next",
        headers={"X-User-Timezone": "Asia/Shanghai"},
        json={"request_id": new_request_id},
    )

    assert response.status_code == 202
    assert response.json()["request_id"] == new_request_id
    assert len(runtime.phase_runs) == 1


def test_start_next_phase_reuses_thread_and_runtime():
    repository = FakeThreadRepository()
    runtime = FakeRuntime()
    client, user = _client_with_overrides(repository, runtime)
    thread = _phase_thread(user_id=user.id, thread_id="thread-phase")
    repository.threads[(str(user.id), thread.thread_id)] = thread
    task = _fake_route_task(
        user_id=user.id,
        thread_id=thread.thread_id,
        title="Completed phase action",
        status="completed",
        phase_id="phase_01",
        ai_generated=True,
    )
    repository.tasks[task.id] = task
    request_id = "11111111-1111-1111-1111-111111111111"

    response = client.post(
        f"/api/threads/{thread.thread_id}/phases/next",
        headers={"X-User-Timezone": "Asia/Shanghai"},
        json={"request_id": request_id},
    )

    assert response.status_code == 202
    assert response.json() == {
        "thread_id": thread.thread_id,
        "request_id": request_id,
        "status": "running",
        "events_url": f"/api/threads/{thread.thread_id}/events",
    }
    assert runtime.phase_runs[0]["thread_id"] == thread.thread_id
    assert runtime.phase_runs[0]["request_id"] == request_id


def test_start_next_phase_rejects_incomplete_phase():
    repository = FakeThreadRepository()
    runtime = FakeRuntime()
    client, user = _client_with_overrides(repository, runtime)
    thread = _phase_thread(user_id=user.id, thread_id="thread-phase")
    repository.threads[(str(user.id), thread.thread_id)] = thread
    task = _fake_route_task(
        user_id=user.id,
        thread_id=thread.thread_id,
        title="Active phase action",
        status="active",
        phase_id="phase_01",
        ai_generated=True,
    )
    repository.tasks[task.id] = task

    response = client.post(
        f"/api/threads/{thread.thread_id}/phases/next",
        headers={"X-User-Timezone": "Asia/Shanghai"},
        json={"request_id": "11111111-1111-1111-1111-111111111111"},
    )

    assert response.status_code == 409
    assert response.json()["detail"]["error_code"] == "PHASE_INCOMPLETE"
    assert response.json()["detail"]["remaining_ai_actions"] == 1
    assert runtime.phase_runs == []


def test_start_next_phase_replays_same_request_without_second_run():
    repository = FakeThreadRepository()
    runtime = FakeRuntime()
    client, user = _client_with_overrides(repository, runtime)
    thread = _phase_thread(user_id=user.id, thread_id="thread-phase")
    repository.threads[(str(user.id), thread.thread_id)] = thread
    task = _fake_route_task(
        user_id=user.id,
        thread_id=thread.thread_id,
        title="Completed phase action",
        status="completed",
        phase_id="phase_01",
        ai_generated=True,
    )
    repository.tasks[task.id] = task
    body = {"request_id": "11111111-1111-1111-1111-111111111111"}
    headers = {"X-User-Timezone": "Asia/Shanghai"}

    first = client.post(f"/api/threads/{thread.thread_id}/phases/next", headers=headers, json=body)
    second = client.post(f"/api/threads/{thread.thread_id}/phases/next", headers=headers, json=body)

    assert first.status_code == 202
    assert second.status_code == 202
    assert len(runtime.phase_runs) == 1


def test_start_next_phase_rejects_other_active_request_and_legacy_or_final_thread():
    repository = FakeThreadRepository()
    runtime = FakeRuntime()
    client, user = _client_with_overrides(repository, runtime)
    active = _phase_thread(user_id=user.id, thread_id="active")
    active.lease_owner = "22222222-2222-2222-2222-222222222222"
    active.lease_expires_at = datetime.now(timezone.utc) + timedelta(minutes=5)
    legacy = FakeThread(thread_id="legacy", user_id=str(user.id), intent_text="Legacy", task_tree=None)
    final = _phase_thread(user_id=user.id, thread_id="final")
    final.task_tree["planning_context"]["current_phase"] = None
    final.task_tree["planning_context"]["next_action_client_node_id"] = None
    for phase in final.task_tree["planning_context"]["roadmap"]:
        phase["status"] = "completed"
    for thread in (active, legacy, final):
        repository.threads[(str(user.id), thread.thread_id)] = thread

    headers = {"X-User-Timezone": "Asia/Shanghai"}
    body = {"request_id": "11111111-1111-1111-1111-111111111111"}
    active_response = client.post("/api/threads/active/phases/next", headers=headers, json=body)
    legacy_response = client.post("/api/threads/legacy/phases/next", headers=headers, json=body)
    final_response = client.post("/api/threads/final/phases/next", headers=headers, json=body)

    assert active_response.status_code == 409
    assert active_response.json()["detail"]["error_code"] == "PHASE_GENERATION_IN_PROGRESS"
    assert legacy_response.status_code == 409
    assert legacy_response.json()["detail"]["error_code"] == "PHASE_UNSUPPORTED"
    assert final_response.status_code == 409
    assert final_response.json()["detail"]["error_code"] == "GOAL_COMPLETED"


def test_start_next_phase_returns_404_for_other_users_thread():
    repository = FakeThreadRepository()
    runtime = FakeRuntime()
    client, _user = _client_with_overrides(repository, runtime)
    other_user_id = uuid4()
    thread = _phase_thread(user_id=other_user_id, thread_id="other-thread")
    repository.threads[(str(other_user_id), thread.thread_id)] = thread

    response = client.post(
        "/api/threads/other-thread/phases/next",
        headers={"X-User-Timezone": "Asia/Shanghai"},
        json={"request_id": "11111111-1111-1111-1111-111111111111"},
    )

    assert response.status_code == 404


def test_delete_thread_removes_owned_thread_and_associated_tasks_from_views():
    repository = FakeThreadRepository()
    runtime = FakeRuntime()
    client, user = _client_with_overrides(repository, runtime)
    repository.threads[(str(user.id), "thread-1")] = FakeThread(
        thread_id="thread-1",
        user_id=str(user.id),
        intent_text="写论文",
    )
    repository.threads[(str(user.id), "thread-2")] = FakeThread(
        thread_id="thread-2",
        user_id=str(user.id),
        intent_text="保留计划",
    )
    deleted_task = _fake_route_task(
        user_id=user.id,
        thread_id="thread-1",
        title="Deleted task",
        is_in_my_day=True,
    )
    kept_task = _fake_route_task(
        user_id=user.id,
        thread_id="thread-2",
        title="Kept task",
        is_in_my_day=True,
    )
    repository.tasks[deleted_task.id] = deleted_task
    repository.tasks[kept_task.id] = kept_task

    response = client.delete("/api/threads/thread-1")

    assert response.status_code == 204
    assert ("thread-1" not in [thread_id for _user_id, thread_id in repository.threads])
    planned_titles = [task["title"] for task in client.get("/api/tasks?view_bucket=planned").json()]
    my_day_titles = [task["title"] for task in client.get("/api/tasks?view_bucket=my_day").json()]
    assert planned_titles == ["Kept task"]
    assert my_day_titles == ["Kept task"]
    assert repository.deleted == [{"user_id": user.id, "thread_id": "thread-1"}]


def test_delete_thread_rejects_other_users_thread():
    repository = FakeThreadRepository()
    runtime = FakeRuntime()
    client, _user = _client_with_overrides(repository, runtime)
    other_user_id = uuid4()
    repository.threads[(str(other_user_id), "thread-1")] = FakeThread(
        thread_id="thread-1",
        user_id=str(other_user_id),
        intent_text="其他用户计划",
    )

    response = client.delete("/api/threads/thread-1")

    assert response.status_code == 404
    assert (str(other_user_id), "thread-1") in repository.threads
    assert repository.deleted == []


def test_delete_thread_returns_404_for_missing_thread():
    repository = FakeThreadRepository()
    runtime = FakeRuntime()
    client, _user = _client_with_overrides(repository, runtime)

    response = client.delete("/api/threads/missing-thread")

    assert response.status_code == 404
    assert response.json()["detail"] == "Thread not found"


def _fake_route_task(
    *,
    user_id: UUID,
    thread_id: str,
    title: str,
    is_in_my_day: bool = False,
    status: str = "active",
    phase_id: str | None = None,
    ai_generated: bool = False,
) -> FakeRouteTask:
    metadata = {"source": "ai", "phase_id": phase_id} if phase_id else {"source": "manual"}
    return FakeRouteTask(
        id=uuid4(),
        user_id=user_id,
        thread_id=thread_id,
        client_node_id=f"node_{uuid4().hex}",
        parent_task_id=None,
        title=title,
        description=None,
        node_type="action",
        status=status,
        view_bucket="planned",
        is_in_my_day=is_in_my_day,
        estimated_minutes=None,
        sort_order=0,
        ai_generated=ai_generated,
        metadata_=metadata,
    )


def _phase_thread(*, user_id: UUID, thread_id: str) -> FakeThread:
    return FakeThread(
        thread_id=thread_id,
        user_id=str(user_id),
        intent_text="Learn Japanese N3",
        status="succeeded",
        task_tree={
            "root": {
                "client_node_id": "phase_01_root",
                "title": "Phase 1",
                "verb": "Start",
                "estimated_minutes": 5,
                "node_type": "group",
                "children": [],
            },
            "summary": "Phase 1",
            "assumptions": [],
            "planning_context": {
                "schema_version": 1,
                "intent_type": "long_term_growth",
                "time_horizon": "months",
                "roadmap": [
                    {"phase_id": "phase_01", "order": 1, "title": "Phase 1", "objective": "Start", "status": "current"},
                    {"phase_id": "phase_02", "order": 2, "title": "Phase 2", "objective": "Build", "status": "planned"},
                    {"phase_id": "phase_03", "order": 3, "title": "Phase 3", "objective": "Finish", "status": "planned"},
                ],
                "current_phase": {
                    "phase_id": "phase_01",
                    "title": "Phase 1",
                    "objective": "Start",
                    "completion_rule": "all_ai_actions_completed",
                },
                "next_action_client_node_id": None,
            },
        },
    )


def _phase_conflict(thread: FakeThread, code: str, message: str):
    return SimpleNamespace(
        thread=thread,
        status="conflict",
        should_schedule=False,
        current_phase_task_summary="",
        error_code=code,
        error_message=message,
        remaining_ai_actions=None,
    )
