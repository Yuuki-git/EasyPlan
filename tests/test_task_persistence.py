import asyncio
from copy import deepcopy
from uuid import UUID

import pytest
from sqlalchemy.sql import Select, Update

from app.agents.nodes import flatten_task_tree_for_persistence, persist_internal_tasks_node
from app.models.task import Task, TaskDependency
from app.models.thread import AgentThread


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


def phase_tree_with_hierarchy_and_dependency() -> dict:
    tree = valid_tree_with_hierarchy_and_dependency()
    tree["planning_context"] = {
        "schema_version": 1,
        "intent_type": "long_term_growth",
        "time_horizon": "months",
        "roadmap": [
            {
                "phase_id": "phase_01",
                "order": 1,
                "title": "起步",
                "objective": "完成论文启动",
                "status": "current",
            },
            {
                "phase_id": "phase_02",
                "order": 2,
                "title": "撰写",
                "objective": "完成论文初稿",
                "status": "planned",
            },
            {
                "phase_id": "phase_03",
                "order": 3,
                "title": "修改",
                "objective": "完成论文定稿",
                "status": "planned",
            },
        ],
        "current_phase": {
            "phase_id": "phase_01",
            "title": "起步",
            "objective": "完成论文启动",
            "completion_rule": "all_ai_actions_completed",
        },
        "next_action_client_node_id": "outline",
    }
    return tree


def next_phase_tree() -> dict:
    tree = deepcopy(phase_tree_with_hierarchy_and_dependency())
    tree["root"]["client_node_id"] = "phase_02_root"
    tree["root"]["children"][0]["client_node_id"] = "phase_02_outline"
    tree["root"]["children"][1]["client_node_id"] = "phase_02_review"
    tree["root"]["children"][1]["depends_on"] = ["phase_02_outline"]
    tree["planning_context"]["roadmap"][0]["status"] = "completed"
    tree["planning_context"]["roadmap"][1]["status"] = "current"
    tree["planning_context"]["current_phase"] = {
        "phase_id": "phase_02",
        "title": "撰写",
        "objective": "完成论文初稿",
        "completion_rule": "all_ai_actions_completed",
    }
    tree["planning_context"]["next_action_client_node_id"] = "model-supplied-id"
    return tree


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


def test_flatten_task_tree_persists_action_quality_fields_to_metadata():
    tree = valid_tree_with_hierarchy_and_dependency()
    tree["root"]["children"][0].update(
        {
            "done_criteria": "Outline has three sections.",
            "start_hint": "Start from the existing notes.",
            "fallback_action": "Write only the section headings.",
        }
    )

    tasks, _dependencies = flatten_task_tree_for_persistence(
        tree,
        user_id=USER_ID,
        thread_id="thread-1",
    )

    outline = {task.client_node_id: task for task in tasks}["outline"]
    review = {task.client_node_id: task for task in tasks}["review"]
    assert outline.metadata_ == {
        "source": "ai",
        "done_criteria": "Outline has three sections.",
        "start_hint": "Start from the existing notes.",
        "fallback_action": "Write only the section headings.",
    }
    assert review.metadata_ == {"source": "ai"}


def test_flatten_phase_tree_adds_phase_metadata_to_every_ai_node():
    tasks, _ = flatten_task_tree_for_persistence(
        phase_tree_with_hierarchy_and_dependency(),
        user_id=USER_ID,
        thread_id="thread-1",
    )

    assert all(task.metadata_["source"] == "ai" for task in tasks)
    assert all(task.metadata_["phase_id"] == "phase_01" for task in tasks)
    assert all(task.metadata_["phase_order"] == 1 for task in tasks)


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

    assert result == {"task_persistence_status": "succeeded"}
    assert session.begin_count == 1
    assert session.add_all_calls == 0
    assert session.task_rows_inserted == 3
    assert session.dependency_rows_inserted == 1
    assert len(session.conflict_safe_inserts) == 3
    assert all("ON CONFLICT" in statement for statement in session.conflict_safe_inserts)
    assert any("thread_id, client_node_id" in statement for statement in session.conflict_safe_inserts)
    assert any("task_id, depends_on_task_id" in statement for statement in session.conflict_safe_inserts)
    assert session.executed_updates
    compiled_params = session.executed_updates[0].compile().params
    assert "succeeded" in compiled_params.values()


def test_persist_internal_tasks_node_does_not_use_add_all(monkeypatch):
    session = FakePersistSession(raise_on_add_all=True)

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

    assert result == {"task_persistence_status": "succeeded"}
    assert session.add_all_calls == 0


def test_persist_internal_tasks_node_can_retry_without_duplicate_rows(monkeypatch):
    session = FakePersistSession()

    def fake_async_session():
        return FakeSessionContext(session)

    import app.db.session as db_session

    monkeypatch.setattr(db_session, "async_session", fake_async_session, raising=False)
    state = {
        "user_id": USER_ID,
        "thread_id": "thread-1",
        "task_tree": valid_tree_with_hierarchy_and_dependency(),
    }

    first_result = asyncio.run(persist_internal_tasks_node(state))
    second_result = asyncio.run(persist_internal_tasks_node(state))

    assert first_result == {"task_persistence_status": "succeeded"}
    assert second_result == {"task_persistence_status": "succeeded"}
    assert session.add_all_calls == 0
    assert session.task_rows_inserted == 3
    assert session.dependency_rows_inserted == 1


def test_persist_next_phase_updates_same_thread_and_server_derived_next_action(monkeypatch):
    request_id = "11111111-1111-1111-1111-111111111111"
    committed_tree = phase_tree_with_hierarchy_and_dependency()
    thread = AgentThread(
        user_id=UUID(USER_ID),
        thread_id="thread-1",
        intent_text="Write a paper",
        status="awaiting_confirmation",
        current_node="human_review",
        next_nodes=[],
        interrupt_payload={
            "type": "next_phase_review",
            "request_id": request_id,
            "status": "awaiting_confirmation",
            "task_tree": next_phase_tree(),
            "history": {},
        },
        latest_checkpoint_id=None,
        task_tree=committed_tree,
        error_code=None,
        error_message=None,
        expires_at=None,
        interrupted_at=None,
        completed_at=None,
    )
    session = FakePersistSession(thread=thread)

    def fake_async_session():
        return FakeSessionContext(session)

    import app.db.session as db_session

    monkeypatch.setattr(db_session, "async_session", fake_async_session, raising=False)

    result = asyncio.run(
        persist_internal_tasks_node(
            {
                "user_id": USER_ID,
                "thread_id": "thread-1",
                "task_tree": next_phase_tree(),
                "planning_mode": "next_phase",
                "phase_request_id": request_id,
            }
        )
    )

    assert result == {"task_persistence_status": "succeeded"}
    update_values = list(session.executed_updates[-1].compile().params.values())
    persisted_tree = next(
        value
        for value in update_values
        if isinstance(value, dict) and "planning_context" in value
    )
    envelope = next(
        value
        for value in update_values
        if isinstance(value, dict) and value.get("type") == "phase_generation_state"
    )
    assert persisted_tree["planning_context"]["next_action_client_node_id"] == "phase_02_outline"
    assert envelope["history"][request_id]["status"] == "confirmed"
    assert "task_tree" not in envelope
    assert session.task_rows_inserted == 3


class FakePersistSession:
    def __init__(
        self,
        *,
        raise_on_add_all: bool = False,
        thread: AgentThread | None = None,
    ) -> None:
        self.raise_on_add_all = raise_on_add_all
        self.thread = thread
        self.add_all_calls = 0
        self.executed_updates = []
        self.conflict_safe_inserts = []
        self.persisted_tasks: dict[str, Task] = {}
        self.persisted_dependency_pairs: set[tuple[UUID, UUID]] = set()
        self.task_rows_inserted = 0
        self.dependency_rows_inserted = 0
        self.begin_count = 0

    def begin(self):
        self.begin_count += 1
        return FakeTransaction()

    def add_all(self, items):
        self.add_all_calls += 1
        if self.raise_on_add_all:
            raise AssertionError("persist_internal_tasks_node must use conflict-safe upserts")

    async def execute(self, statement):
        if isinstance(statement, Select):
            entity = statement.column_descriptions[0].get("entity")
            if entity is AgentThread:
                return FakeScalarResult(self.thread)
            return FakeScalarResult(list(self.persisted_tasks.values()))
        if isinstance(statement, Update):
            self.executed_updates.append(statement)
            return None
        sql = _compile_postgresql(statement)
        if "ON CONFLICT" in sql:
            self.conflict_safe_inserts.append(sql)
        rows = list(getattr(statement, "_multi_values", [()])[0])
        if "INSERT INTO tasks " in sql:
            for row in rows:
                row = dict(row)
                row["metadata_"] = row.pop("metadata")
                task = Task(**row)
                if task.client_node_id not in self.persisted_tasks:
                    self.persisted_tasks[task.client_node_id] = task
                    self.task_rows_inserted += 1
        elif "INSERT INTO task_dependencies " in sql:
            for row in rows:
                pair = (row["task_id"], row["depends_on_task_id"])
                if pair not in self.persisted_dependency_pairs:
                    self.persisted_dependency_pairs.add(pair)
                    self.dependency_rows_inserted += 1
        return None


class FakeScalarResult:
    def __init__(self, rows):
        self.rows = rows

    def scalars(self):
        return self

    def scalar_one_or_none(self):
        return self.rows

    def all(self):
        return self.rows


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


def _compile_postgresql(statement) -> str:
    from sqlalchemy.dialects import postgresql

    try:
        return str(statement.compile(dialect=postgresql.dialect()))
    except Exception as exc:
        pytest.fail(f"statement did not compile for PostgreSQL: {exc}")
