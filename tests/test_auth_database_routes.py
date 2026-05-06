from fastapi.testclient import TestClient

from app.api.auth import AuthService, get_auth_service
from app.db.session import get_db
from app.main import create_app
from tests.test_auth_oauth_checkpoint import FakeUserSession


def test_register_and_token_routes_use_database_user_repository():
    app = create_app(enable_static=False)
    session = FakeUserSession()
    auth_service = AuthService(jwt_secret="unit-test-secret")

    async def override_get_db():
        yield session

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_auth_service] = lambda: auth_service
    client = TestClient(app)

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
