import asyncio
from uuid import uuid4

import pytest
from sqlalchemy.sql import Select
from sqlalchemy.sql.dml import Delete

from app.models.task import Task
from app.models.thread import AgentThread
from app.services.task_repository import TaskRepository


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
            parent_task_id=None,
        )
    )

    assert session.begin_count == 0
    assert session.commit_count == 1
    assert session.rollback_count == 0
    assert [type(item) for item in session.added] == [AgentThread, Task]
    assert task in session.refreshed


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
                parent_task_id=None,
            )
        )

    assert session.begin_count == 0
    assert session.commit_count == 0
    assert session.rollback_count == 1


def test_delete_task_for_user_uses_user_scoped_hard_delete():
    session = FakeTaskSession(delete_rowcount=1)
    repository = TaskRepository(session)
    user_id = uuid4()
    task_id = uuid4()

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
    session = FakeTaskSession(delete_rowcount=0)
    repository = TaskRepository(session)

    deleted = asyncio.run(
        repository.delete_task_for_user(
            user_id=uuid4(),
            task_id=uuid4(),
        )
    )

    assert deleted is False
    assert session.commit_count == 1
    assert session.rollback_count == 0


class FakeTaskSession:
    def __init__(self, *, raise_on_task_add: bool = False, delete_rowcount: int = 0) -> None:
        self.raise_on_task_add = raise_on_task_add
        self.delete_rowcount = delete_rowcount
        self.added = []
        self.delete_statements = []
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
        return None

    def scalar_one(self):
        return self.value


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
