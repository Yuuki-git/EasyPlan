import asyncio
from uuid import uuid4

import pytest
from sqlalchemy.sql import Select
from sqlalchemy.sql.dml import Delete

from app.models.task import Task, TaskDependency
from app.models.thread import AgentThread
from app.services.task_repository import TaskRepository
from app.services.practice_repository import PracticeLoopConflictError


def test_create_task_for_user_commits_manual_thread_and_task_together():
    session = FakeTaskSession()
    repository = TaskRepository(session)
    user_id = uuid4()

    task = asyncio.run(
        repository.create_task_for_user(
            user_id=user_id,
            title="Buy notebooks",
            description=None,
            view_bucket="my_day",
            is_in_my_day=True,
            parent_task_id=None,
        )
    )

    assert session.begin_count == 0
    assert session.commit_count == 1
    assert session.rollback_count == 0
    assert [type(item) for item in session.added] == [AgentThread, Task]
    assert task.view_bucket == "planned"
    assert task.is_in_my_day is True
    assert task.thread_id.startswith("manual_")
    assert task in session.refreshed


def test_create_task_for_user_adds_root_task_to_existing_thread_without_manual_thread():
    user_id = uuid4()
    thread = _agent_thread(user_id=user_id, thread_id="thread-plan-1")
    session = FakeTaskSession(scalar_results=[thread, 0])
    repository = TaskRepository(session)

    task = asyncio.run(
        repository.create_task_for_user(
            user_id=user_id,
            title="Add root task",
            description=None,
            view_bucket="planned",
            is_in_my_day=False,
            parent_task_id=None,
            thread_id="thread-plan-1",
        )
    )

    assert task is not None
    assert task.thread_id == "thread-plan-1"
    assert task.parent_task_id is None
    assert [type(item) for item in session.added] == [Task]
    assert session.commit_count == 1
    assert len(session.select_statements) == 2


def test_create_task_for_user_inherits_parent_thread_before_payload_thread():
    user_id = uuid4()
    parent_id = uuid4()
    parent_task = _task(user_id=user_id, task_id=parent_id, thread_id="thread-parent")
    session = FakeTaskSession(scalar_results=[parent_task, 0])
    repository = TaskRepository(session)

    task = asyncio.run(
        repository.create_task_for_user(
            user_id=user_id,
            title="Child task",
            description=None,
            view_bucket="planned",
            is_in_my_day=False,
            parent_task_id=parent_id,
            thread_id="thread-ignored",
        )
    )

    assert task is not None
    assert task.thread_id == "thread-parent"
    assert task.parent_task_id == parent_id
    assert [type(item) for item in session.added] == [Task]
    assert session.commit_count == 1


def test_create_task_for_user_rejects_thread_not_owned_by_user():
    session = FakeTaskSession(scalar_results=[None])
    repository = TaskRepository(session)

    task = asyncio.run(
        repository.create_task_for_user(
            user_id=uuid4(),
            title="Bad context",
            description=None,
            view_bucket="planned",
            is_in_my_day=False,
            parent_task_id=None,
            thread_id="other-thread",
        )
    )

    assert task is None
    assert session.added == []
    assert session.commit_count == 0
    assert session.rollback_count == 1


def test_create_task_for_user_rolls_back_manual_thread_when_task_add_fails():
    session = FakeTaskSession(raise_on_task_add=True)
    repository = TaskRepository(session)

    with pytest.raises(RuntimeError, match="task insert failed"):
        asyncio.run(
            repository.create_task_for_user(
                user_id=uuid4(),
                title="Buy notebooks",
                description=None,
                view_bucket="my_day",
                is_in_my_day=False,
                parent_task_id=None,
            )
        )

    assert session.begin_count == 0
    assert session.commit_count == 0
    assert session.rollback_count == 1


def test_list_tasks_for_user_maps_my_day_to_virtual_flag():
    session = FakeTaskSession()
    repository = TaskRepository(session)
    user_id = uuid4()

    asyncio.run(
        repository.list_tasks_for_user(
            user_id=user_id,
            view_bucket="my_day",
        )
    )

    sql = str(session.select_statements[-1])
    assert "tasks.is_in_my_day IS true" in sql
    assert "tasks.view_bucket =" not in sql


def test_list_tasks_for_user_keeps_planned_as_physical_bucket():
    session = FakeTaskSession()
    repository = TaskRepository(session)
    user_id = uuid4()

    asyncio.run(
        repository.list_tasks_for_user(
            user_id=user_id,
            view_bucket="planned",
        )
    )

    sql = str(session.select_statements[-1])
    assert "tasks.view_bucket" in sql
    assert "planned" in session.select_statements[-1].compile().params.values()


def test_delete_task_for_user_uses_user_scoped_hard_delete():
    user_id = uuid4()
    task_id = uuid4()
    task = _task(user_id=user_id, task_id=task_id, thread_id="thread-delete")
    session = FakeTaskSession(delete_rowcount=1, scalar_results=[task])
    repository = TaskRepository(session)

    deleted = asyncio.run(
        repository.delete_task_for_user(
            user_id=user_id,
            task_id=task_id,
        )
    )

    assert deleted is True
    assert session.commit_count == 1
    assert session.rollback_count == 0
    assert len(session.delete_statements) == 1
    params = session.delete_statements[0].compile().params
    assert user_id in params.values()
    assert task_id in params.values()


def test_delete_task_for_user_returns_false_when_no_user_scoped_row_matches():
    session = FakeTaskSession(delete_rowcount=0, scalar_results=[None])
    repository = TaskRepository(session)

    deleted = asyncio.run(
        repository.delete_task_for_user(
            user_id=uuid4(),
            task_id=uuid4(),
        )
    )

    assert deleted is False
    assert session.commit_count == 0
    assert session.rollback_count == 0


def test_update_phase_action_recalculates_next_action_and_commits_once():
    user_id = uuid4()
    first = _phase_task(
        user_id=user_id,
        thread_id="thread-phase",
        client_node_id="phase_01_action_01",
        sort_order=0,
    )
    second = _phase_task(
        user_id=user_id,
        thread_id="thread-phase",
        client_node_id="phase_01_action_02",
        sort_order=1,
    )
    dependency = TaskDependency(
        id=uuid4(),
        task_id=second.id,
        depends_on_task_id=first.id,
    )
    thread = _phase_thread(user_id=user_id, thread_id="thread-phase", final_phase=False)
    session = FakeTaskSession(
        scalar_results=[first, thread, [first, second], [dependency]],
    )
    repository = TaskRepository(session)

    updated = asyncio.run(
        repository.update_task_for_user(
            user_id=user_id,
            task_id=first.id,
            changes={"status": "completed"},
        )
    )

    assert updated is first
    assert first.status == "completed"
    assert thread.task_tree["planning_context"]["next_action_client_node_id"] == second.client_node_id
    assert session.commit_count == 1
    assert session.rollback_count == 0


def test_patch_final_ai_action_completes_final_roadmap_phase():
    user_id = uuid4()
    task = _phase_task(
        user_id=user_id,
        thread_id="thread-final",
        client_node_id="phase_03_action_01",
        phase_id="phase_03",
        phase_order=3,
    )
    thread = _phase_thread(user_id=user_id, thread_id="thread-final", final_phase=True)
    session = FakeTaskSession(scalar_results=[task, thread, [task], []])
    repository = TaskRepository(session)

    updated = asyncio.run(
        repository.update_task_for_user(
            user_id=user_id,
            task_id=task.id,
            changes={"status": "completed"},
        )
    )

    context = thread.task_tree["planning_context"]
    assert updated is task
    assert context["current_phase"] is None
    assert context["next_action_client_node_id"] is None
    assert all(phase["status"] == "completed" for phase in context["roadmap"])
    assert session.commit_count == 1


def test_patch_final_ai_action_does_not_auto_complete_schema_v2_goal():
    user_id = uuid4()
    task = _phase_task(
        user_id=user_id,
        thread_id="thread-final-v2",
        client_node_id="phase_03_action_01",
        phase_id="phase_03",
        phase_order=3,
    )
    thread = _phase_thread(
        user_id=user_id,
        thread_id="thread-final-v2",
        final_phase=True,
    )
    _make_thread_schema_v2(thread)
    session = FakeTaskSession(scalar_results=[task, thread, [task], []])
    repository = TaskRepository(session)

    asyncio.run(
        repository.update_task_for_user(
            user_id=user_id,
            task_id=task.id,
            changes={"status": "completed"},
        )
    )

    context = thread.task_tree["planning_context"]
    assert context["current_phase"]["phase_id"] == "phase_03"
    assert context["roadmap"][2]["status"] == "current"


def test_manual_task_status_update_does_not_recalculate_phase_state():
    user_id = uuid4()
    task = _task(user_id=user_id, task_id=uuid4(), thread_id="thread-manual")
    session = FakeTaskSession(scalar_results=[task])
    repository = TaskRepository(session)

    asyncio.run(
        repository.update_task_for_user(
            user_id=user_id,
            task_id=task.id,
            changes={"status": "completed"},
        )
    )

    assert len(session.select_statements) == 1
    assert session.commit_count == 1


def test_completing_practice_occurrence_records_completion(monkeypatch):
    user_id = uuid4()
    loop_id = uuid4()
    task = _task(user_id=user_id, task_id=uuid4(), thread_id="thread-practice")
    task.metadata_ = {"source": "practice_loop", "practice_loop_id": str(loop_id)}
    calls = []

    async def record_completion(_repository, **kwargs):
        calls.append(kwargs)

    from app.services.practice_repository import PracticeLoopRepository

    monkeypatch.setattr(
        PracticeLoopRepository,
        "record_completion",
        record_completion,
    )
    session = FakeTaskSession(scalar_results=[task])
    repository = TaskRepository(session)

    updated = asyncio.run(
        repository.update_task_for_user(
            user_id=user_id,
            task_id=task.id,
            changes={"status": "completed"},
        )
    )

    assert updated.status == "completed"
    assert calls[0]["loop_id"] == loop_id
    assert calls[0]["task"] is task
    assert session.commit_count == 1


def test_completed_practice_occurrence_cannot_be_reopened():
    user_id = uuid4()
    task = _task(user_id=user_id, task_id=uuid4(), thread_id="thread-practice")
    task.status = "completed"
    task.metadata_ = {
        "source": "practice_loop",
        "practice_loop_id": str(uuid4()),
    }
    session = FakeTaskSession(scalar_results=[task])
    repository = TaskRepository(session)

    with pytest.raises(
        PracticeLoopConflictError,
        match="cannot be reopened",
    ):
        asyncio.run(
            repository.update_task_for_user(
                user_id=user_id,
                task_id=task.id,
                changes={"status": "active"},
            )
        )

    assert session.commit_count == 0
    assert session.rollback_count == 1


def test_deleting_uncompleted_practice_occurrence_clears_pointer(monkeypatch):
    user_id = uuid4()
    loop_id = uuid4()
    task = _task(user_id=user_id, task_id=uuid4(), thread_id="thread-practice")
    task.metadata_ = {"source": "practice_loop", "practice_loop_id": str(loop_id)}
    calls = []

    async def clear_active(_repository, **kwargs):
        calls.append(kwargs)

    from app.services.practice_repository import PracticeLoopRepository

    monkeypatch.setattr(
        PracticeLoopRepository,
        "clear_active_occurrence",
        clear_active,
    )
    session = FakeTaskSession(delete_rowcount=1, scalar_results=[task])
    repository = TaskRepository(session)

    deleted = asyncio.run(
        repository.delete_task_for_user(user_id=user_id, task_id=task.id)
    )

    assert deleted is True
    assert calls == [{"user_id": user_id, "loop_id": loop_id, "task_id": task.id}]


def test_delete_phase_action_recalculates_next_action_before_commit():
    user_id = uuid4()
    deleted_task = _phase_task(
        user_id=user_id,
        thread_id="thread-delete-phase",
        client_node_id="phase_01_action_01",
        sort_order=0,
    )
    remaining = _phase_task(
        user_id=user_id,
        thread_id="thread-delete-phase",
        client_node_id="phase_01_action_02",
        sort_order=1,
    )
    thread = _phase_thread(user_id=user_id, thread_id="thread-delete-phase", final_phase=False)
    session = FakeTaskSession(
        delete_rowcount=1,
        scalar_results=[deleted_task, thread, [remaining], []],
    )
    repository = TaskRepository(session)

    deleted = asyncio.run(
        repository.delete_task_for_user(user_id=user_id, task_id=deleted_task.id)
    )

    assert deleted is True
    assert thread.task_tree["planning_context"]["next_action_client_node_id"] == remaining.client_node_id
    assert session.commit_count == 1


def _agent_thread(*, user_id, thread_id: str) -> AgentThread:
    return AgentThread(
        user_id=user_id,
        thread_id=thread_id,
        intent_text="Existing plan",
        status="completed",
        current_node="human_review",
        next_nodes=[],
        interrupt_payload=None,
        latest_checkpoint_id=None,
        task_tree=None,
        error_code=None,
        error_message=None,
        expires_at=None,
        interrupted_at=None,
        completed_at=None,
    )


def _task(*, user_id, task_id, thread_id: str) -> Task:
    return Task(
        id=task_id,
        user_id=user_id,
        thread_id=thread_id,
        parent_task_id=None,
        client_node_id=f"node_{uuid4().hex}",
        title="Parent",
        description=None,
        node_type="action",
        status="active",
        view_bucket="planned",
        is_in_my_day=False,
        estimated_minutes=None,
        sort_order=0,
        ai_generated=False,
        user_edited=True,
        metadata_={},
    )


def _phase_task(
    *,
    user_id,
    thread_id: str,
    client_node_id: str,
    phase_id: str = "phase_01",
    phase_order: int = 1,
    sort_order: int = 0,
) -> Task:
    return Task(
        id=uuid4(),
        user_id=user_id,
        thread_id=thread_id,
        parent_task_id=None,
        client_node_id=client_node_id,
        title=client_node_id,
        description=None,
        node_type="action",
        status="active",
        view_bucket="planned",
        is_in_my_day=False,
        estimated_minutes=5,
        sort_order=sort_order,
        ai_generated=True,
        user_edited=False,
        metadata_={"source": "ai", "phase_id": phase_id, "phase_order": phase_order},
    )


def _phase_thread(*, user_id, thread_id: str, final_phase: bool) -> AgentThread:
    statuses = ["completed", "completed", "current"] if final_phase else ["current", "planned", "planned"]
    current_order = 3 if final_phase else 1
    return AgentThread(
        user_id=user_id,
        thread_id=thread_id,
        intent_text="Long-term goal",
        status="succeeded",
        current_node="persist_internal_tasks",
        next_nodes=[],
        interrupt_payload=None,
        latest_checkpoint_id=None,
        task_tree={
            "root": {
                "client_node_id": f"phase_{current_order:02d}_root",
                "title": "Current phase",
                "verb": "推进",
                "estimated_minutes": 5,
                "node_type": "group",
                "children": [],
            },
            "summary": "Current phase",
            "assumptions": [],
            "planning_context": {
                "schema_version": 1,
                "intent_type": "long_term_growth",
                "time_horizon": "months",
                "roadmap": [
                    {
                        "phase_id": f"phase_{index:02d}",
                        "order": index,
                        "title": f"Phase {index}",
                        "objective": f"Objective {index}",
                        "status": statuses[index - 1],
                    }
                    for index in range(1, 4)
                ],
                "current_phase": {
                    "phase_id": f"phase_{current_order:02d}",
                    "title": f"Phase {current_order}",
                    "objective": f"Objective {current_order}",
                    "completion_rule": "all_ai_actions_completed",
                },
                "next_action_client_node_id": f"phase_{current_order:02d}_action_01",
            },
        },
        error_code=None,
        error_message=None,
        expires_at=None,
        interrupted_at=None,
        completed_at=None,
    )


def _make_thread_schema_v2(thread: AgentThread) -> None:
    context = thread.task_tree["planning_context"]
    context["schema_version"] = 2
    context["current_phase"]["completion_rule"] = "long_term_execution_gate"
    context["current_phase"]["estimated_duration_weeks"] = 4
    context["practice_loops"] = []
    context["outcome_checkpoints"] = [
        {
            "checkpoint_id": "artifact",
            "title": "Submit one artifact",
            "evidence_type": "artifact",
            "operator": "exists",
        }
    ]
    context["phase_gate"] = {
        "process_threshold": 0.8,
        "outcome_rule": "all_required",
    }


class FakeTaskSession:
    def __init__(
        self,
        *,
        raise_on_task_add: bool = False,
        delete_rowcount: int = 0,
        scalar_results: list | None = None,
    ) -> None:
        self.raise_on_task_add = raise_on_task_add
        self.delete_rowcount = delete_rowcount
        self.scalar_results = list(scalar_results or [])
        self.added = []
        self.delete_statements = []
        self.select_statements = []
        self.refreshed = []
        self.begin_count = 0
        self.commit_count = 0
        self.rollback_count = 0
        self.transaction_exit_exc_type = None

    def begin(self):
        self.begin_count += 1
        return FakeTransaction(self)

    def add(self, item):
        if self.raise_on_task_add and isinstance(item, Task):
            raise RuntimeError("task insert failed")
        self.added.append(item)

    async def execute(self, statement):
        if isinstance(statement, Delete):
            self.delete_statements.append(statement)
            return FakeDeleteResult(self.delete_rowcount)
        assert isinstance(statement, Select)
        self.select_statements.append(statement)
        if self.scalar_results:
            return FakeScalarResult(self.scalar_results.pop(0))
        return FakeScalarResult(0)

    async def commit(self):
        self.commit_count += 1

    async def rollback(self):
        self.rollback_count += 1

    async def refresh(self, item):
        self.refreshed.append(item)


class FakeScalarResult:
    def __init__(self, value):
        self.value = value

    def scalar_one_or_none(self):
        return self.value

    def scalar_one(self):
        return self.value

    def scalars(self):
        return self

    def all(self):
        if isinstance(self.value, list):
            return self.value
        return []


class FakeDeleteResult:
    def __init__(self, rowcount: int) -> None:
        self.rowcount = rowcount


class FakeTransaction:
    def __init__(self, session: FakeTaskSession) -> None:
        self.session = session

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        self.session.transaction_exit_exc_type = exc_type
        return False
