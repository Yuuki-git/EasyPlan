import asyncio
from uuid import uuid4

import pytest
from sqlalchemy.dialects import postgresql

from app.api.auth import (
    AuthService,
    DuplicateEmailError,
    InMemoryUserRepository,
    build_thread_ownership_query,
)
from app.services.checkpoint_service import build_tenant_checkpoint_restore_query
from app.services.oauth_service import (
    CredentialCipher,
    InMemoryIntegrationRepository,
    InMemoryOAuthStateRepository,
    OAuthStateError,
    TodoistOAuthService,
)


def test_auth_service_registers_user_and_issues_hs256_jwt():
    repository = InMemoryUserRepository()
    auth_service = AuthService(user_repository=repository, jwt_secret="unit-test-secret")

    user = auth_service.register_user(
        email="USER@example.com",
        password="correct horse battery staple",
        display_name="User",
    )
    token = auth_service.issue_access_token(user)
    claims = auth_service.decode_access_token(token)

    assert user.email == "user@example.com"
    assert repository.get_by_email("user@example.com").password_hash != "correct horse battery staple"
    assert claims["sub"] == str(user.id)
    assert claims["email"] == "user@example.com"


def test_auth_service_rejects_duplicate_registration():
    repository = InMemoryUserRepository()
    auth_service = AuthService(user_repository=repository, jwt_secret="unit-test-secret")

    auth_service.register_user("user@example.com", "password-1")

    with pytest.raises(DuplicateEmailError):
        auth_service.register_user("USER@example.com", "password-2")


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
