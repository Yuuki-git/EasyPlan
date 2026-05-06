from dataclasses import dataclass
from uuid import uuid4

from fastapi.testclient import TestClient

from app.api.auth import AuthUser, get_current_user
from app.api.routes_intents import get_agent_runtime as get_intent_runtime
from app.api.routes_intents import get_thread_repository as get_intent_repository
from app.api.routes_threads import get_agent_runtime as get_thread_runtime
from app.api.routes_threads import get_thread_repository as get_thread_repository
from app.main import create_app


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


class FakeThreadRepository:
    def __init__(self) -> None:
        self.threads: dict[tuple[str, str], FakeThread] = {}
        self.created: list[FakeThread] = []

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

    async def mark_confirmation_accepted(self, *, thread, request_id):
        thread.status = "running"


class FakeRuntime:
    def __init__(self) -> None:
        self.started: list[dict] = []
        self.resumed: list[dict] = []
        self.events = ["event: reasoning\ndata: {\"state_version\":1,\"message\":\"running\"}\n\n"]

    async def run_new_thread(self, **kwargs):
        self.started.append(kwargs)

    async def resume_thread(self, **kwargs):
        self.resumed.append(kwargs)

    async def stream_thread_events(self, *, user_id, thread_id, last_event_id=None):
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
    app.dependency_overrides[get_intent_repository] = lambda: repository
    app.dependency_overrides[get_thread_repository] = lambda: repository
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
        json={"intent_text": "写论文", "preferred_provider": "todoist"},
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
        "selected_provider": "todoist",
    }


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
