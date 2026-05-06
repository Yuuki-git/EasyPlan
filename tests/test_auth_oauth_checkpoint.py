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
from app.services.oauth_service import (
    CredentialCipher,
    InMemoryIntegrationRepository,
    InMemoryOAuthStateRepository,
    MicrosoftOAuthService,
    OAuthStateError,
    TodoistOAuthService,
)


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


class FakeTodoistTokenClient:
    async def exchange_code(self, *, code: str, redirect_uri: str):
        assert code == "oauth-code"
        return {
            "access_token": "todoist-access-token",
            "refresh_token": "todoist-refresh-token",
            "token_type": "Bearer",
            "expires_in": 3600,
            "scope": "task:add",
        }


def test_todoist_oauth_callback_encrypts_credentials_and_consumes_state_once():
    user_id = uuid4()
    cipher = CredentialCipher(secret="unit-test-oauth-secret")
    integration_repository = InMemoryIntegrationRepository()
    service = TodoistOAuthService(
        state_repository=InMemoryOAuthStateRepository(),
        integration_repository=integration_repository,
        token_client=FakeTodoistTokenClient(),
        cipher=cipher,
        client_id="todoist-client-id",
        client_secret="todoist-client-secret",
    )

    start = service.start_authorization(
        user_id=user_id,
        redirect_uri="https://easyplan.example/api/integrations/todoist/oauth/callback",
    )
    integration = asyncio.run(
        service.complete_callback(
            user_id=user_id,
            provider="todoist",
            code="oauth-code",
            state=start.state,
        )
    )

    assert b"todoist-access-token" not in integration.encrypted_credentials
    assert integration_repository.get(user_id, "todoist") == integration
    assert cipher.decrypt_json(integration.encrypted_credentials)["access_token"] == "todoist-access-token"
    with pytest.raises(OAuthStateError):
        asyncio.run(
            service.complete_callback(
                user_id=user_id,
                provider="todoist",
                code="oauth-code",
                state=start.state,
            )
        )


class FakeMicrosoftTokenClient:
    async def exchange_code(self, *, code: str, redirect_uri: str):
        assert code == "microsoft-code"
        return {
            "access_token": "graph-access-token",
            "refresh_token": "graph-refresh-token",
            "token_type": "Bearer",
            "expires_in": 3600,
            "scope": "offline_access User.Read Tasks.ReadWrite",
        }


def test_microsoft_oauth_callback_encrypts_graph_credentials_and_consumes_state_once():
    user_id = uuid4()
    cipher = CredentialCipher(secret="unit-test-microsoft-secret")
    integration_repository = InMemoryIntegrationRepository()
    service = MicrosoftOAuthService(
        state_repository=InMemoryOAuthStateRepository(),
        integration_repository=integration_repository,
        token_client=FakeMicrosoftTokenClient(),
        cipher=cipher,
        client_id="microsoft-client-id",
        client_secret="microsoft-client-secret",
    )

    start = service.start_authorization(
        user_id=user_id,
        redirect_uri="https://easyplan.example/api/integrations/microsoft_todo/oauth/callback",
    )
    integration = asyncio.run(
        service.complete_callback(
            user_id=user_id,
            provider="microsoft_todo",
            code="microsoft-code",
            state=start.state,
        )
    )

    assert "login.microsoftonline.com" in start.authorization_url
    assert "Tasks.ReadWrite" in start.authorization_url
    assert b"graph-access-token" not in integration.encrypted_credentials
    assert integration.provider == "microsoft_todo"
    assert integration.display_name == "Microsoft To Do"
    assert integration_repository.get(user_id, "microsoft_todo") == integration
    decrypted = cipher.decrypt_json(integration.encrypted_credentials)
    assert decrypted["access_token"] == "graph-access-token"
    assert "Tasks.ReadWrite" in integration.scopes
    with pytest.raises(OAuthStateError):
        asyncio.run(
            service.complete_callback(
                user_id=user_id,
                provider="microsoft_todo",
                code="microsoft-code",
                state=start.state,
            )
        )
