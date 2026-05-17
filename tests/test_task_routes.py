from dataclasses import dataclass, field
from uuid import UUID, uuid4
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.sql import Select

from app.api.auth import AuthService, AuthUser, get_auth_service, get_current_user
from app.db.session import get_db
from app.main import create_app
from app.models.task import Task
from app.models.thread import AgentThread
from app.models.user import User


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
    is_in_my_day: bool
    estimated_minutes: int | None
    sort_order: int
    metadata_: dict[str, Any] = field(default_factory=dict)


class FakeTaskRepository:
    def __init__(self) -> None:
        self.tasks: dict[UUID, FakeTask] = {}
        self.list_calls: list[dict] = []
        self.create_calls: list[dict] = []
        self.update_calls: list[dict] = []
        self.delete_calls: list[dict] = []

    async def list_tasks_for_user(self, *, user_id, view_bucket=None):
        self.list_calls.append({"user_id": user_id, "view_bucket": view_bucket})
        return [
            task
            for task in self.tasks.values()
            if task.user_id == user_id
            and (
                view_bucket is None
                or (view_bucket == "my_day" and task.is_in_my_day)
                or (view_bucket != "my_day" and task.view_bucket == view_bucket)
            )
        ]

    async def update_task_for_user(self, *, user_id, task_id, changes):
        self.update_calls.append({"user_id": user_id, "task_id": task_id, "changes": changes})
        task = self.tasks.get(task_id)
        if task is None or task.user_id != user_id:
            return None
        for key, value in changes.items():
            setattr(task, key, value)
        return task

    async def create_task_for_user(
        self,
        *,
        user_id,
        title,
        description,
        view_bucket,
        parent_task_id,
        is_in_my_day=False,
    ):
        self.create_calls.append(
            {
                "user_id": user_id,
                "title": title,
                "description": description,
                "view_bucket": view_bucket,
                "is_in_my_day": is_in_my_day,
                "parent_task_id": parent_task_id,
            }
        )
        if parent_task_id is not None:
            parent_task = self.tasks.get(parent_task_id)
            if parent_task is None or parent_task.user_id != user_id:
                return None
        task = _fake_task(
            user_id=user_id,
            view_bucket=view_bucket,
            is_in_my_day=is_in_my_day,
            title=title,
            parent_task_id=parent_task_id,
            description=description,
        )
        task.client_node_id = f"manual-{uuid4().hex}"
        self.tasks[task.id] = task
        return task

    async def delete_task_for_user(self, *, user_id, task_id):
        self.delete_calls.append({"user_id": user_id, "task_id": task_id})
        task = self.tasks.get(task_id)
        if task is None or task.user_id != user_id:
            return False
        del self.tasks[task_id]
        return True


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


def test_get_my_day_tasks_uses_virtual_mapping_in_response():
    repository = FakeTaskRepository()
    client, user = _client_with_task_repository(repository)
    my_day_task = _fake_task(
        user_id=user.id,
        view_bucket="planned",
        title="Today mapped task",
        is_in_my_day=True,
    )
    planned_only_task = _fake_task(
        user_id=user.id,
        view_bucket="planned",
        title="Later task",
        is_in_my_day=False,
    )
    repository.tasks[my_day_task.id] = my_day_task
    repository.tasks[planned_only_task.id] = planned_only_task

    response = client.get("/api/tasks?view_bucket=my_day")

    assert response.status_code == 200
    assert response.json()[0]["title"] == "Today mapped task"
    assert response.json()[0]["view_bucket"] == "planned"
    assert response.json()[0]["is_in_my_day"] is True
    assert repository.list_calls == [{"user_id": user.id, "view_bucket": "my_day"}]


def test_get_tasks_returns_action_quality_fields_from_metadata_without_mutating_metadata():
    repository = FakeTaskRepository()
    client, user = _client_with_task_repository(repository)
    task = _fake_task(
        user_id=user.id,
        view_bucket="planned",
        title="Draft outline",
        metadata_={
            "source": "planner",
            "done_criteria": "Outline includes problem, solution, and next step.",
            "start_hint": "Reuse the meeting notes.",
            "fallback_action": "Write just the three section titles.",
        },
    )
    repository.tasks[task.id] = task

    response = client.get("/api/tasks?view_bucket=planned")

    assert response.status_code == 200
    payload = response.json()[0]
    assert payload["done_criteria"] == "Outline includes problem, solution, and next step."
    assert payload["start_hint"] == "Reuse the meeting notes."
    assert payload["fallback_action"] == "Write just the three section titles."
    assert repository.tasks[task.id].metadata_["source"] == "planner"


def test_get_tasks_returns_null_action_quality_fields_for_legacy_metadata():
    repository = FakeTaskRepository()
    client, user = _client_with_task_repository(repository)
    task = _fake_task(user_id=user.id, view_bucket="planned", title="Legacy task", metadata_={"source": "manual"})
    repository.tasks[task.id] = task

    response = client.get("/api/tasks?view_bucket=planned")

    assert response.status_code == 200
    payload = response.json()[0]
    assert payload["done_criteria"] is None
    assert payload["start_hint"] is None
    assert payload["fallback_action"] is None


def test_get_tasks_returns_empty_array_for_authenticated_user_with_no_tasks():
    repository = FakeTaskRepository()
    client, user = _client_with_task_repository(repository)

    response = client.get("/api/tasks?view_bucket=planned")

    assert response.status_code == 200
    assert response.json() == []
    assert repository.list_calls == [{"user_id": user.id, "view_bucket": "planned"}]


def test_post_tasks_creates_manual_task_for_authenticated_user_with_default_bucket():
    repository = FakeTaskRepository()
    client, user = _client_with_task_repository(repository)

    response = client.post("/api/tasks", json={"title": "Buy notebooks"})

    assert response.status_code == 201
    payload = response.json()
    assert payload["user_id"] == str(user.id)
    assert payload["title"] == "Buy notebooks"
    assert payload["description"] is None
    assert payload["view_bucket"] == "planned"
    assert payload["is_in_my_day"] is False
    assert payload["node_type"] == "action"
    assert payload["status"] == "active"
    assert payload["parent_task_id"] is None
    assert repository.create_calls[0]["user_id"] == user.id
    assert repository.create_calls[0]["view_bucket"] == "planned"
    assert repository.create_calls[0]["is_in_my_day"] is False
    planned_response = client.get("/api/tasks?view_bucket=planned")
    assert [task["title"] for task in planned_response.json()] == ["Buy notebooks"]


def test_post_tasks_can_mark_task_as_in_my_day_without_physical_bucket_move():
    repository = FakeTaskRepository()
    client, user = _client_with_task_repository(repository)

    response = client.post(
        "/api/tasks",
        json={"title": "Review launch notes", "view_bucket": "planned", "is_in_my_day": True},
    )

    assert response.status_code == 201
    payload = response.json()
    assert payload["view_bucket"] == "planned"
    assert payload["is_in_my_day"] is True
    assert repository.create_calls == [
        {
            "user_id": user.id,
            "title": "Review launch notes",
            "description": None,
            "view_bucket": "planned",
            "is_in_my_day": True,
            "parent_task_id": None,
        }
    ]


def test_post_tasks_normalizes_explicit_my_day_to_virtual_mapping():
    repository = FakeTaskRepository()
    client, user = _client_with_task_repository(repository)

    response = client.post(
        "/api/tasks",
        json={"title": "Call accountant", "view_bucket": "my_day"},
    )

    assert response.status_code == 201
    payload = response.json()
    assert payload["view_bucket"] == "planned"
    assert payload["is_in_my_day"] is True
    assert repository.create_calls == [
        {
            "user_id": user.id,
            "title": "Call accountant",
            "description": None,
            "view_bucket": "planned",
            "is_in_my_day": True,
            "parent_task_id": None,
        }
    ]
    my_day_response = client.get("/api/tasks?view_bucket=my_day")
    assert [task["title"] for task in my_day_response.json()] == ["Call accountant"]
    assert all(
        not (task.view_bucket == "my_day" and task.is_in_my_day is False)
        for task in repository.tasks.values()
    )


def test_post_tasks_rejects_parent_task_from_another_tenant():
    repository = FakeTaskRepository()
    client, _user = _client_with_task_repository(repository)
    other_task = _fake_task(user_id=uuid4(), view_bucket="planned", title="Other tenant")
    repository.tasks[other_task.id] = other_task

    response = client.post(
        "/api/tasks",
        json={"title": "Nested task", "parent_task_id": str(other_task.id)},
    )

    assert response.status_code == 404
    assert response.json()["detail"] == "Parent task not found"


def test_post_tasks_creates_standalone_task_after_real_auth_lookup_without_500():
    auth_service = AuthService(jwt_secret="test-secret")
    user = AuthUser(
        id=uuid4(),
        email="user@example.com",
        password_hash="hash",
    )
    session = FakeRouteDbSession(
        User(
            id=user.id,
            email=user.email,
            password_hash=user.password_hash,
            status=user.status,
        )
    )
    app = create_app(enable_static=False)

    async def override_get_db():
        yield session

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_auth_service] = lambda: auth_service
    client = TestClient(app, raise_server_exceptions=False)

    response = client.post(
        "/api/tasks",
        headers={"Authorization": f"Bearer {auth_service.issue_access_token(user)}"},
        json={"title": "Standalone task"},
    )

    assert response.status_code == 201, response.text
    payload = response.json()
    assert payload["title"] == "Standalone task"
    assert payload["parent_task_id"] is None
    assert payload["view_bucket"] == "planned"
    assert payload["is_in_my_day"] is False
    assert session.commit_count == 1
    assert [type(item) for item in session.added] == [AgentThread, Task]


def test_patch_task_updates_only_authenticated_users_task():
    repository = FakeTaskRepository()
    client, user = _client_with_task_repository(repository)
    own_task = _fake_task(user_id=user.id, view_bucket="planned", title="Draft outline")
    other_task = _fake_task(user_id=uuid4(), view_bucket="planned", title="Other tenant")
    repository.tasks[own_task.id] = own_task
    repository.tasks[other_task.id] = other_task

    response = client.patch(
        f"/api/tasks/{own_task.id}",
        json={"title": "Draft intro", "view_bucket": "planned", "is_in_my_day": True, "estimated_minutes": 4},
    )
    forbidden_response = client.patch(
        f"/api/tasks/{other_task.id}",
        json={"title": "Should not change"},
    )

    assert response.status_code == 200
    assert response.json()["title"] == "Draft intro"
    assert response.json()["view_bucket"] == "planned"
    assert response.json()["is_in_my_day"] is True
    assert response.json()["estimated_minutes"] == 4
    assert repository.tasks[own_task.id].title == "Draft intro"
    assert repository.tasks[own_task.id].is_in_my_day is True
    assert forbidden_response.status_code == 404
    assert repository.tasks[other_task.id].title == "Other tenant"


def test_patch_task_allows_clearing_nullable_description():
    repository = FakeTaskRepository()
    client, user = _client_with_task_repository(repository)
    own_task = _fake_task(
        user_id=user.id,
        view_bucket="planned",
        title="Draft outline",
        description="Old description",
    )
    repository.tasks[own_task.id] = own_task

    response = client.patch(
        f"/api/tasks/{own_task.id}",
        json={"description": None},
    )

    assert response.status_code == 200
    assert response.json()["description"] is None
    assert repository.update_calls == [
        {
            "user_id": user.id,
            "task_id": own_task.id,
            "changes": {"description": None},
        }
    ]


@pytest.mark.parametrize("field", ["title", "status", "view_bucket", "sort_order", "is_in_my_day"])
def test_patch_task_rejects_explicit_null_for_required_columns(field: str):
    repository = FakeTaskRepository()
    client, user = _client_with_task_repository(repository)
    own_task = _fake_task(user_id=user.id, view_bucket="planned", title="Draft outline")
    repository.tasks[own_task.id] = own_task

    response = client.patch(
        f"/api/tasks/{own_task.id}",
        json={field: None},
    )

    assert response.status_code == 422
    assert field in response.text
    assert repository.update_calls == []


def test_delete_task_hard_deletes_only_authenticated_users_task():
    repository = FakeTaskRepository()
    client, user = _client_with_task_repository(repository)
    own_task = _fake_task(user_id=user.id, view_bucket="planned", title="Draft outline")
    other_task = _fake_task(user_id=uuid4(), view_bucket="planned", title="Other tenant")
    repository.tasks[own_task.id] = own_task
    repository.tasks[other_task.id] = other_task

    response = client.delete(f"/api/tasks/{own_task.id}")
    forbidden_response = client.delete(f"/api/tasks/{other_task.id}")

    assert response.status_code == 204
    assert response.content == b""
    assert own_task.id not in repository.tasks
    assert forbidden_response.status_code == 404
    assert other_task.id in repository.tasks
    assert repository.delete_calls == [
        {"user_id": user.id, "task_id": own_task.id},
        {"user_id": user.id, "task_id": other_task.id},
    ]


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


class FakeRouteDbSession:
    def __init__(self, user: User) -> None:
        self.user = user
        self.added = []
        self.refreshed = []
        self.active_transaction = False
        self.begin_count = 0
        self.commit_count = 0
        self.rollback_count = 0

    def begin(self):
        self.begin_count += 1
        if self.active_transaction:
            raise RuntimeError("A transaction is already begun on this Session.")
        self.active_transaction = True
        return FakeRouteTransaction(self)

    def add(self, item):
        if isinstance(item, (AgentThread, Task)) and item.id is None:
            item.id = uuid4()
        self.added.append(item)

    async def execute(self, statement):
        assert isinstance(statement, Select)
        self.active_transaction = True
        entity = statement.column_descriptions[0].get("entity")
        if entity is User:
            return FakeRouteScalarResult(self.user)
        return FakeRouteScalarResult(0)

    async def commit(self):
        self.commit_count += 1
        self.active_transaction = False

    async def rollback(self):
        self.rollback_count += 1
        self.active_transaction = False

    async def refresh(self, item):
        if isinstance(item, Task) and item.id is None:
            item.id = uuid4()
        self.refreshed.append(item)


class FakeRouteScalarResult:
    def __init__(self, value):
        self.value = value

    def scalar_one_or_none(self):
        return self.value

    def scalar_one(self):
        return self.value


class FakeRouteTransaction:
    def __init__(self, session: FakeRouteDbSession) -> None:
        self.session = session

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        self.session.active_transaction = False
        if exc_type is None:
            self.session.commit_count += 1
        return False


def _fake_task(
    *,
    user_id: UUID,
    view_bucket: str,
    title: str,
    parent_task_id: UUID | None = None,
    description: str | None = None,
    is_in_my_day: bool = False,
    metadata_: dict[str, Any] | None = None,
) -> FakeTask:
    return FakeTask(
        id=uuid4(),
        user_id=user_id,
        thread_id="thread-1",
        client_node_id=f"node-{uuid4().hex}",
        parent_task_id=parent_task_id,
        title=title,
        description=description,
        node_type="action",
        status="active",
        view_bucket=view_bucket,
        is_in_my_day=is_in_my_day,
        estimated_minutes=2,
        sort_order=0,
        metadata_=metadata_ or {},
    )
