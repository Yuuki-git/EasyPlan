import asyncio
from uuid import UUID

from app.agents.nodes import flatten_task_tree_for_persistence, persist_internal_tasks_node
from app.models.task import Task, TaskDependency


USER_ID = "11111111-1111-1111-1111-111111111111"


def valid_tree_with_hierarchy_and_dependency() -> dict:
    return {
        "root": {
            "client_node_id": "root",
            "title": "Plan paper",
            "description": None,
            "verb": "Plan",
            "estimated_minutes": 120,
            "node_type": "group",
            "depends_on": [],
            "children": [
                {
                    "client_node_id": "outline",
                    "title": "Draft outline",
                    "description": "Create the first outline",
                    "verb": "Draft",
                    "estimated_minutes": 2,
                    "node_type": "action",
                    "depends_on": [],
                    "children": [],
                },
                {
                    "client_node_id": "review",
                    "title": "Review outline",
                    "description": None,
                    "verb": "Review",
                    "estimated_minutes": 3,
                    "node_type": "action",
                    "depends_on": ["outline"],
                    "children": [],
                },
            ],
        },
        "summary": "Paper starter plan",
        "assumptions": [],
    }


def test_flatten_task_tree_preserves_client_node_parent_mapping_and_planned_bucket():
    tasks, dependencies = flatten_task_tree_for_persistence(
        valid_tree_with_hierarchy_and_dependency(),
        user_id=USER_ID,
        thread_id="thread-1",
    )

    by_client_id = {task.client_node_id: task for task in tasks}

    assert list(by_client_id) == ["root", "outline", "review"]
    assert by_client_id["root"].parent_task_id is None
    assert by_client_id["outline"].parent_task_id == by_client_id["root"].id
    assert by_client_id["review"].parent_task_id == by_client_id["root"].id
    assert by_client_id["review"].estimated_minutes == 3
    assert {task.view_bucket for task in tasks} == {"planned"}
    assert {task.status for task in tasks} == {"active"}
    assert all(task.user_id == UUID(USER_ID) for task in tasks)
    assert all(task.thread_id == "thread-1" for task in tasks)

    assert len(dependencies) == 1
    assert dependencies[0].task_id == by_client_id["review"].id
    assert dependencies[0].depends_on_task_id == by_client_id["outline"].id


def test_persist_internal_tasks_node_inserts_tasks_dependencies_and_marks_thread_succeeded(monkeypatch):
    session = FakePersistSession()

    def fake_async_session():
        return FakeSessionContext(session)

    import app.db.session as db_session

    monkeypatch.setattr(db_session, "async_session", fake_async_session, raising=False)

    result = asyncio.run(
        persist_internal_tasks_node(
            {
                "user_id": USER_ID,
                "thread_id": "thread-1",
                "task_tree": valid_tree_with_hierarchy_and_dependency(),
            }
        )
    )

    tasks = [item for item in session.added if isinstance(item, Task)]
    dependencies = [item for item in session.added if isinstance(item, TaskDependency)]

    assert result == {"task_persistence_status": "succeeded"}
    assert session.begin_count == 1
    assert len(tasks) == 3
    assert len(dependencies) == 1
    assert session.executed_updates
    compiled_params = session.executed_updates[0].compile().params
    assert "succeeded" in compiled_params.values()


class FakePersistSession:
    def __init__(self) -> None:
        self.added = []
        self.executed_updates = []
        self.begin_count = 0

    def begin(self):
        self.begin_count += 1
        return FakeTransaction()

    def add_all(self, items):
        self.added.extend(items)

    async def execute(self, statement):
        self.executed_updates.append(statement)


class FakeSessionContext:
    def __init__(self, session: FakePersistSession) -> None:
        self.session = session

    async def __aenter__(self):
        return self.session

    async def __aexit__(self, exc_type, exc, tb):
        return False


class FakeTransaction:
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False
