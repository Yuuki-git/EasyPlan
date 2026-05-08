import asyncio
from uuid import uuid4

import pytest
from sqlalchemy.dialects import postgresql

from app.api.auth import (
    AuthService,
    DatabaseUserRepository,
    DuplicateEmailError,
    build_thread_ownership_query,
)
from app.models.user import User
from app.services.checkpoint_service import build_tenant_checkpoint_restore_query


class FakeScalarResult:
    def __init__(self, value):
        self.value = value

    def scalar_one_or_none(self):
        return self.value


class FakeUserSession:
    def __init__(self) -> None:
        self.users_by_email = {}
        self.users_by_id = {}
        self.added = []
        self.commits = 0
        self.rollbacks = 0

    def add(self, model):
        self.added.append(model)

    async def commit(self):
        self.commits += 1
        for user in self.added:
            self.users_by_email[user.email] = user
            self.users_by_id[user.id] = user

    async def rollback(self):
        self.rollbacks += 1

    async def refresh(self, model):
        return None

    async def execute(self, statement):
        params = statement.compile().params
        for value in params.values():
            if isinstance(value, str) and "@" in value:
                return FakeScalarResult(self.users_by_email.get(value))
            return FakeScalarResult(self.users_by_id.get(value))
        return FakeScalarResult(None)


def test_auth_service_registers_user_in_database_repository_and_issues_hs256_jwt():
    session = FakeUserSession()
    repository = DatabaseUserRepository(session)
    auth_service = AuthService(jwt_secret="unit-test-secret")

    user = asyncio.run(
        auth_service.register_user(
            repository=repository,
            email="USER@example.com",
            password="correct horse battery staple",
            display_name="User",
        )
    )
    token = auth_service.issue_access_token(user)
    claims = auth_service.decode_access_token(token)

    assert user.email == "user@example.com"
    assert isinstance(session.added[0], User)
    assert session.users_by_email["user@example.com"].password_hash != "correct horse battery staple"
    assert claims["sub"] == str(user.id)
    assert claims["email"] == "user@example.com"


def test_auth_service_rejects_duplicate_registration():
    session = FakeUserSession()
    repository = DatabaseUserRepository(session)
    auth_service = AuthService(jwt_secret="unit-test-secret")

    asyncio.run(auth_service.register_user(repository, "user@example.com", "password-1"))

    with pytest.raises(DuplicateEmailError):
        asyncio.run(auth_service.register_user(repository, "USER@example.com", "password-2"))


def test_tenant_restore_queries_always_filter_by_user_id_and_thread_id():
    user_id = uuid4()
    thread_id = "thread-1"

    thread_query = build_thread_ownership_query(user_id=user_id, thread_id=thread_id)
    checkpoint_query = build_tenant_checkpoint_restore_query(user_id=user_id, thread_id=thread_id)

    compiled_thread = str(
        thread_query.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True})
    )
    compiled_checkpoint = str(
        checkpoint_query.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True})
    )

    assert "agent_threads.user_id" in compiled_thread
    assert "agent_threads.thread_id" in compiled_thread
    assert "langgraph_checkpoints.user_id" in compiled_checkpoint
    assert "langgraph_checkpoints.thread_id" in compiled_checkpoint
