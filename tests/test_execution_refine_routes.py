from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4

from fastapi.testclient import TestClient

from app.api.auth import AuthUser, get_current_user, get_user_for_sse
from app.api.routes_execution_refine import (
    get_execution_refine_runtime,
    get_execution_refine_service,
)
from app.api.schemas import ExecutionRefineApplyReceipt
from app.main import create_app
from app.services.execution_refine import ExecutionRefineError


class FakeRepository:
    def __init__(self, run=None):
        self.run = run

    async def create_or_get(self, **_kwargs):
        return self.run, True

    async def get_owned(self, **_kwargs):
        return self.run

    async def fail_interrupted_if_lease_expired(self, _run):
        return False

    async def expire_if_needed(self, run):
        return run

    async def cancel(self, run):
        if run.status not in {"running", "ready", "cancelled"}:
            raise ExecutionRefineError(
                code="EXECUTION_REFINE_NOT_CANCELLABLE",
                message="当前请求不能取消。",
            )
        run.status = "cancelled"
        run.stage = "cancelled"
        run.proposal = None
        return run


class FakeService:
    def __init__(self, run):
        self.repository = FakeRepository(run)
        self.apply_calls = []
        self.scope_error = None

    async def load_scope(self, **_kwargs):
        if self.scope_error is not None:
            raise self.scope_error
        return SimpleNamespace(thread_id=self.repository.run.thread_id, fingerprint="a" * 64)

    async def apply(self, **kwargs):
        self.apply_calls.append(kwargs)
        run = self.repository.run
        return ExecutionRefineApplyReceipt(
            run_id=run.id,
            thread_id=run.thread_id,
            request_id=run.request_id,
            applied_at=datetime.now(timezone.utc),
            scope_fingerprint=run.scope_fingerprint,
            affected_task_ids=[],
            created_task_ids=[],
            focus_task_ids=[],
        )


class FakeRuntime:
    lease_owner = "test-runtime"

    def __init__(self):
        self.run_calls = []
        self.cancel_calls = []
        self.restore_calls = []
        self.stream_calls = []

    async def run(self, **kwargs):
        self.run_calls.append(kwargs)

    async def cancel(self, **kwargs):
        self.cancel_calls.append(kwargs)

    def restore_from_snapshot(self, run):
        self.restore_calls.append(run)

    async def stream(self, **kwargs):
        self.stream_calls.append(kwargs)
        yield (
            f"id: {kwargs['thread_id']}:execution_refine:{kwargs['request_id']}:000001\n"
            "event: done\n"
            "data: {\"run_type\":\"execution_refine\",\"event_type\":\"done\"}\n\n"
        )


def _run(*, user_id=None, request_id=None, status="running"):
    now = datetime.now(timezone.utc)
    return SimpleNamespace(
        id=uuid4(),
        user_id=user_id or uuid4(),
        thread_id="thread-1",
        request_id=request_id or uuid4(),
        mode="context_change",
        status=status,
        stage="queued" if status == "running" else status,
        scope_fingerprint="a" * 64,
        proposal=None,
        apply_receipt=None,
        error_code=None,
        error_message=None,
        created_at=now,
        updated_at=now,
        expires_at=now + timedelta(hours=24),
    )


def _client(monkeypatch, service, runtime):
    monkeypatch.setenv("EASYPLAN_EXECUTION_REFINE_ENABLED", "true")
    app = create_app(enable_static=False)
    user = AuthUser(
        id=service.repository.run.user_id,
        email="owner@example.com",
        password_hash="hash",
    )
    app.dependency_overrides[get_current_user] = lambda: user
    app.dependency_overrides[get_user_for_sse] = lambda: user
    app.dependency_overrides[get_execution_refine_service] = lambda: service
    app.dependency_overrides[get_execution_refine_runtime] = lambda: runtime
    return TestClient(app)


def _payload(request_id):
    return {
        "request_id": str(request_id),
        "mode": "context_change",
        "user_context": "今天优先完成演示稿",
    }


def test_start_returns_202_and_runs_isolated_background_job(monkeypatch):
    run = _run()
    runtime = FakeRuntime()
    response = _client(monkeypatch, FakeService(run), runtime).post(
        f"/api/threads/{run.thread_id}/refine-diffs",
        json=_payload(run.request_id),
    )
    assert response.status_code == 202
    assert response.json()["request_id"] == str(run.request_id)
    assert runtime.run_calls == [
        {
            "user_id": run.user_id,
            "thread_id": run.thread_id,
            "request_id": run.request_id,
        }
    ]


def test_snapshot_cancel_and_apply_are_request_scoped(monkeypatch):
    run = _run(status="ready")
    runtime = FakeRuntime()
    service = FakeService(run)
    client = _client(monkeypatch, service, runtime)

    snapshot = client.get(
        f"/api/threads/{run.thread_id}/refine-diffs/{run.request_id}"
    )
    assert snapshot.status_code == 200
    assert snapshot.json()["status"] == "ready"

    apply_response = client.post(
        f"/api/threads/{run.thread_id}/refine-diffs/{run.request_id}/apply",
        json={"expected_scope_fingerprint": run.scope_fingerprint},
    )
    assert apply_response.status_code == 200
    assert service.apply_calls[0]["user_id"] == run.user_id
    assert service.apply_calls[0]["thread_id"] == run.thread_id

    cancelled = client.delete(
        f"/api/threads/{run.thread_id}/refine-diffs/{run.request_id}"
    )
    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "cancelled"
    assert runtime.cancel_calls == [
        {"thread_id": run.thread_id, "request_id": run.request_id}
    ]


def test_sse_uses_query_cursor_fallback_and_authenticated_identity(monkeypatch):
    run = _run(status="ready")
    runtime = FakeRuntime()
    response = _client(monkeypatch, FakeService(run), runtime).get(
        f"/api/threads/{run.thread_id}/refine-diffs/{run.request_id}/events",
        params={"last_event_id": "cursor-from-query"},
    )
    assert response.status_code == 200
    assert runtime.stream_calls[0]["last_event_id"] == "cursor-from-query"
    assert runtime.stream_calls[0]["user_id"] == run.user_id
    assert runtime.restore_calls == [run]


def test_wrong_thread_request_or_tenant_is_hidden_as_404(monkeypatch):
    run = _run()
    service = FakeService(run)
    service.repository.run = None
    app = create_app(enable_static=False)
    user = AuthUser(id=uuid4(), email="other@example.com", password_hash="hash")
    app.dependency_overrides[get_current_user] = lambda: user
    app.dependency_overrides[get_user_for_sse] = lambda: user
    app.dependency_overrides[get_execution_refine_service] = lambda: service
    app.dependency_overrides[get_execution_refine_runtime] = FakeRuntime
    client = TestClient(app)
    monkeypatch.setenv("EASYPLAN_EXECUTION_REFINE_ENABLED", "true")

    assert client.get(
        f"/api/threads/foreign/refine-diffs/{uuid4()}"
    ).status_code == 404
    assert client.delete(
        f"/api/threads/foreign/refine-diffs/{uuid4()}"
    ).status_code == 404


def test_disabled_feature_returns_404(monkeypatch):
    monkeypatch.setenv("EASYPLAN_EXECUTION_REFINE_ENABLED", "false")
    run = _run()
    app = create_app(enable_static=False)
    app.dependency_overrides[get_current_user] = lambda: AuthUser(
        id=run.user_id,
        email="owner@example.com",
        password_hash="hash",
    )
    app.dependency_overrides[get_execution_refine_service] = lambda: FakeService(run)
    app.dependency_overrides[get_execution_refine_runtime] = FakeRuntime
    response = TestClient(app).post(
        f"/api/threads/{run.thread_id}/refine-diffs",
        json=_payload(run.request_id),
    )
    assert response.status_code == 404
