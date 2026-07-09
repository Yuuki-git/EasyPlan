from fastapi.testclient import TestClient

from app.api.auth import AuthService, get_auth_service
from app.api.routes_threads import get_agent_runtime as get_thread_runtime
from app.api.routes_threads import get_thread_repository as get_thread_repository
from app.db.session import get_db
from app.main import create_app
from tests.test_agent_routes_integration import FakeRuntime, FakeThread, FakeThreadRepository
from tests.test_auth_checkpoint import FakeUserSession


def _client_with_database_auth(session: FakeUserSession, auth_service: AuthService) -> TestClient:
    app = create_app(enable_static=False)

    async def override_get_db():
        yield session

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_auth_service] = lambda: auth_service
    return TestClient(app)


def test_register_and_token_routes_use_database_user_repository():
    session = FakeUserSession()
    auth_service = AuthService(jwt_secret="unit-test-secret")
    client = _client_with_database_auth(session, auth_service)

    register_response = client.post(
        "/api/auth/register",
        json={
            "email": "USER@example.com",
            "password": "correct horse battery staple",
            "display_name": "User",
        },
    )

    assert register_response.status_code == 201
    assert "user@example.com" in session.users_by_email
    assert session.users_by_email["user@example.com"].password_hash != "correct horse battery staple"
    claims = auth_service.decode_access_token(register_response.json()["access_token"])
    assert claims["sub"] in {str(user_id) for user_id in session.users_by_id}

    token_response = client.post(
        "/api/auth/token",
        json={
            "email": "user@example.com",
            "password": "correct horse battery staple",
        },
    )

    assert token_response.status_code == 200
    assert token_response.json()["token_type"] == "bearer"


def test_sse_thread_events_accept_token_query_for_eventsource_clients():
    session = FakeUserSession()
    auth_service = AuthService(jwt_secret="unit-test-secret")
    app = create_app(enable_static=False)
    thread_repository = FakeThreadRepository()
    runtime = FakeRuntime()

    async def override_get_db():
        yield session

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_auth_service] = lambda: auth_service
    app.dependency_overrides[get_thread_repository] = lambda: thread_repository
    app.dependency_overrides[get_thread_runtime] = lambda: runtime
    client = TestClient(app)

    register_response = client.post(
        "/api/auth/register",
        json={
            "email": "eventsource@example.com",
            "password": "correct horse battery staple",
        },
    )
    token = register_response.json()["access_token"]
    user_id = auth_service.decode_access_token(token)["sub"]
    thread_repository.threads[(user_id, "thread-1")] = FakeThread(
        thread_id="thread-1",
        user_id=user_id,
        intent_text="写论文",
    )

    request_id = "11111111-1111-1111-1111-111111111111"
    response = client.get(
        f"/api/threads/thread-1/events?token={token}"
        f"&run_type=initial&request_id={request_id}"
    )

    assert response.status_code == 200
    assert "event: run_started" in response.text
    assert runtime.streamed[0]["request_id"] == request_id


def test_regular_thread_snapshot_rejects_token_query_to_avoid_url_token_leakage():
    session = FakeUserSession()
    auth_service = AuthService(jwt_secret="unit-test-secret")
    app = create_app(enable_static=False)
    thread_repository = FakeThreadRepository()

    async def override_get_db():
        yield session

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_auth_service] = lambda: auth_service
    app.dependency_overrides[get_thread_repository] = lambda: thread_repository
    client = TestClient(app)

    register_response = client.post(
        "/api/auth/register",
        json={
            "email": "snapshot@example.com",
            "password": "correct horse battery staple",
        },
    )
    token = register_response.json()["access_token"]
    user_id = auth_service.decode_access_token(token)["sub"]
    thread_repository.threads[(user_id, "thread-1")] = FakeThread(
        thread_id="thread-1",
        user_id=user_id,
        intent_text="写论文",
    )

    response = client.get(f"/api/threads/thread-1?token={token}")

    assert response.status_code == 401
