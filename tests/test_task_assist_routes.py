from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4

from fastapi.testclient import TestClient

from app.api.auth import AuthUser, get_current_user, get_user_for_sse
from app.api.routes_task_assist import (
    get_task_assist_runtime,
    get_task_assist_service,
)
from app.api.schemas import TaskAssistApplyReceipt, TaskAssistApplyResponse, TaskResponse
from app.main import create_app
from app.services.task_assist import TaskAssistError


class FakeRepository:
    def __init__(self, run=None):
        self.run = run
        self.cancelled = False

    async def create_or_get(self, **_kwargs):
        return self.run, True

    async def get_owned(self, **_kwargs):
        return self.run

    async def expire_if_needed(self, run):
        return run

    async def fail_interrupted_if_lease_expired(self, _run):
        return False

    async def cancel(self, run):
        self.cancelled = True
        run.status = "cancelled"
        run.stage = "cancelled"
        run.proposal = None
        return run


class FakeService:
    def __init__(self, task, run=None):
        self.task = task
        self.repository = FakeRepository(run)
        self.apply_calls = []
        self.apply_error = None
        self.task_error = None

    async def load_supported_task(self, **_kwargs):
        if self.task_error is not None:
            raise self.task_error
        return self.task

    async def apply(self, **kwargs):
        self.apply_calls.append(kwargs)
        if self.apply_error is not None:
            raise self.apply_error
        task_response = TaskResponse.model_validate(self.task)
        return TaskAssistApplyResponse(
            status="applied",
            task=task_response,
            tasks=[task_response],
            apply_receipt=TaskAssistApplyReceipt(
                request_id=kwargs["request_id"],
                proposal_type="start",
                applied_at=datetime.now(timezone.utc),
                affected_task_ids=[self.task.id],
            ),
        )


class FakeRuntime:
    def __init__(self):
        self.run_calls = []
        self.cancel_calls = []
        self.restored = []

    async def run(self, **kwargs):
        self.run_calls.append(kwargs)

    async def cancel(self, **kwargs):
        self.cancel_calls.append(kwargs)

    def restore_from_snapshot(self, run):
        self.restored.append(run)

    async def stream(self, **_kwargs):
        yield (
            'id: thread-1:task_assist:req:000001\n'
            'event: done\n'
            'data: {"run_type":"task_assist","event_type":"done"}\n\n'
        )


def _task():
    task_id = uuid4()
    user_id = uuid4()
    return SimpleNamespace(
        id=task_id,
        user_id=user_id,
        thread_id="thread-1",
        parent_task_id=None,
        client_node_id="task-1",
        title="写市场分析",
        description=None,
        node_type="action",
        status="active",
        view_bucket="planned",
        is_in_my_day=True,
        estimated_minutes=30,
        sort_order=0,
        metadata_={"source": "ai"},
    )


def _run(task, request_id=None, status="running"):
    now = datetime.now(timezone.utc)
    return SimpleNamespace(
        task_id=task.id,
        thread_id=task.thread_id,
        request_id=request_id or uuid4(),
        mode="start",
        status=status,
        stage="queued" if status == "running" else status,
        proposal=None,
        error_code=None,
        error_message=None,
        created_at=now,
        updated_at=now,
        expires_at=now + timedelta(hours=24),
    )


def _client(monkeypatch, service, runtime):
    monkeypatch.setenv("EASYPLAN_TASK_ASSIST_ENABLED", "true")
    app = create_app(enable_static=False)
    user = AuthUser(
        id=service.task.user_id,
        email="owner@example.com",
        password_hash="hash",
    )
    app.dependency_overrides[get_current_user] = lambda: user
    app.dependency_overrides[get_user_for_sse] = lambda: user
    app.dependency_overrides[get_task_assist_service] = lambda: service
    app.dependency_overrides[get_task_assist_runtime] = lambda: runtime
    return TestClient(app)


def test_start_returns_202_and_runs_background_generation(monkeypatch):
    task = _task()
    request_id = uuid4()
    run = _run(task, request_id)
    service = FakeService(task, run)
    runtime = FakeRuntime()
    client = _client(monkeypatch, service, runtime)

    response = client.post(
        f"/api/tasks/{task.id}/assist",
        json={"request_id": str(request_id), "mode": "start", "user_context": None},
    )
    assert response.status_code == 202
    assert response.json()["request_id"] == str(request_id)
    assert runtime.run_calls[0]["task_id"] == task.id


def test_start_masks_missing_or_foreign_task_as_not_found(monkeypatch):
    task = _task()
    service = FakeService(task)
    service.task_error = TaskAssistError(
        code="TASK_ASSIST_TASK_NOT_FOUND",
        message="任务不存在。",
        status_code=404,
    )
    response = _client(monkeypatch, service, FakeRuntime()).post(
        f"/api/tasks/{task.id}/assist",
        json={"request_id": str(uuid4()), "mode": "start"},
    )
    assert response.status_code == 404
    assert response.json()["detail"]["error_code"] == "TASK_ASSIST_TASK_NOT_FOUND"


def test_snapshot_and_cancel_are_request_scoped(monkeypatch):
    task = _task()
    run = _run(task, status="ready")
    service = FakeService(task, run)
    runtime = FakeRuntime()
    client = _client(monkeypatch, service, runtime)

    snapshot = client.get(f"/api/tasks/{task.id}/assist/{run.request_id}")
    assert snapshot.status_code == 200
    assert snapshot.json()["status"] == "ready"

    cancelled = client.delete(f"/api/tasks/{task.id}/assist/{run.request_id}")
    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "cancelled"
    assert runtime.cancel_calls == [
        {"thread_id": task.thread_id, "request_id": run.request_id}
    ]


def test_foreign_run_returns_404(monkeypatch):
    task = _task()
    service = FakeService(task, None)
    response = _client(monkeypatch, service, FakeRuntime()).get(
        f"/api/tasks/{task.id}/assist/{uuid4()}"
    )
    assert response.status_code == 404


def test_apply_forwards_only_selected_option_and_server_identity(monkeypatch):
    task = _task()
    run = _run(task, status="ready")
    service = FakeService(task, run)
    client = _client(monkeypatch, service, FakeRuntime())
    response = client.post(
        f"/api/tasks/{task.id}/assist/{run.request_id}/apply",
        json={"selected_option_id": None},
    )
    assert response.status_code == 200
    assert response.json()["task"]["id"] == str(task.id)
    assert service.apply_calls[0] == {
        "user_id": task.user_id,
        "task_id": task.id,
        "request_id": run.request_id,
        "selected_option_id": None,
    }


def test_apply_stale_response_preserves_structured_context_stale_code(monkeypatch):
    task = _task()
    run = _run(task, status="ready")
    service = FakeService(task, run)
    service.apply_error = TaskAssistError(
        code="TASK_ASSIST_CONTEXT_STALE",
        message="任务已发生变化，请重新生成辅助建议。",
    )
    response = _client(monkeypatch, service, FakeRuntime()).post(
        f"/api/tasks/{task.id}/assist/{run.request_id}/apply",
        json={"selected_option_id": None},
    )
    assert response.status_code == 409
    assert response.json()["detail"] == {
        "error_code": "TASK_ASSIST_CONTEXT_STALE",
        "message": "任务已发生变化，请重新生成辅助建议。",
    }


def test_disabled_feature_returns_404(monkeypatch):
    monkeypatch.setenv("EASYPLAN_TASK_ASSIST_ENABLED", "false")
    task = _task()
    app = create_app(enable_static=False)
    app.dependency_overrides[get_current_user] = lambda: AuthUser(
        id=task.user_id,
        email="owner@example.com",
        password_hash="hash",
    )
    app.dependency_overrides[get_task_assist_service] = lambda: FakeService(task)
    app.dependency_overrides[get_task_assist_runtime] = lambda: FakeRuntime()
    response = TestClient(app).post(
        f"/api/tasks/{task.id}/assist",
        json={"request_id": str(uuid4()), "mode": "start"},
    )
    assert response.status_code == 404
