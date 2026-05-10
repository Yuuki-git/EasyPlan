from dataclasses import dataclass
from uuid import UUID, uuid4

from fastapi.testclient import TestClient

from app.api.auth import AuthUser, get_current_user
from app.main import create_app


@dataclass
class FakeTask:
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
    estimated_minutes: int | None
    sort_order: int


class FakeTaskRepository:
    def __init__(self) -> None:
        self.tasks: dict[UUID, FakeTask] = {}
        self.list_calls: list[dict] = []
        self.update_calls: list[dict] = []

    async def list_tasks_for_user(self, *, user_id, view_bucket=None):
        self.list_calls.append({"user_id": user_id, "view_bucket": view_bucket})
        return [
            task
            for task in self.tasks.values()
            if task.user_id == user_id and (view_bucket is None or task.view_bucket == view_bucket)
        ]

    async def update_task_for_user(self, *, user_id, task_id, changes):
        self.update_calls.append({"user_id": user_id, "task_id": task_id, "changes": changes})
        task = self.tasks.get(task_id)
        if task is None or task.user_id != user_id:
            return None
        for key, value in changes.items():
            setattr(task, key, value)
        return task


def test_get_tasks_filters_by_authenticated_user_and_view_bucket():
    repository = FakeTaskRepository()
    client, user = _client_with_task_repository(repository)
    own_task = _fake_task(user_id=user.id, view_bucket="planned", title="Draft outline")
    repository.tasks[own_task.id] = own_task
    repository.tasks[uuid4()] = _fake_task(user_id=uuid4(), view_bucket="planned", title="Other tenant")
    repository.tasks[uuid4()] = _fake_task(user_id=user.id, view_bucket="my_day", title="Today task")

    response = client.get("/api/tasks?view_bucket=planned")

    assert response.status_code == 200
    assert [task["title"] for task in response.json()] == ["Draft outline"]
    assert repository.list_calls == [{"user_id": user.id, "view_bucket": "planned"}]


def test_patch_task_updates_only_authenticated_users_task():
    repository = FakeTaskRepository()
    client, user = _client_with_task_repository(repository)
    own_task = _fake_task(user_id=user.id, view_bucket="planned", title="Draft outline")
    other_task = _fake_task(user_id=uuid4(), view_bucket="planned", title="Other tenant")
    repository.tasks[own_task.id] = own_task
    repository.tasks[other_task.id] = other_task

    response = client.patch(
        f"/api/tasks/{own_task.id}",
        json={"title": "Draft intro", "view_bucket": "my_day", "estimated_minutes": 4},
    )
    forbidden_response = client.patch(
        f"/api/tasks/{other_task.id}",
        json={"title": "Should not change"},
    )

    assert response.status_code == 200
    assert response.json()["title"] == "Draft intro"
    assert response.json()["view_bucket"] == "my_day"
    assert response.json()["estimated_minutes"] == 4
    assert repository.tasks[own_task.id].title == "Draft intro"
    assert forbidden_response.status_code == 404
    assert repository.tasks[other_task.id].title == "Other tenant"


def _client_with_task_repository(repository: FakeTaskRepository):
    from app.api.routes_tasks import get_task_repository

    app = create_app(enable_static=False)
    user = AuthUser(
        id=uuid4(),
        email="user@example.com",
        password_hash="hash",
    )
    app.dependency_overrides[get_current_user] = lambda: user
    app.dependency_overrides[get_task_repository] = lambda: repository
    return TestClient(app), user


def _fake_task(*, user_id: UUID, view_bucket: str, title: str) -> FakeTask:
    return FakeTask(
        id=uuid4(),
        user_id=user_id,
        thread_id="thread-1",
        client_node_id=f"node-{uuid4().hex}",
        parent_task_id=None,
        title=title,
        description=None,
        node_type="action",
        status="active",
        view_bucket=view_bucket,
        estimated_minutes=2,
        sort_order=0,
    )
