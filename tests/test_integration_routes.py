from uuid import uuid4

from fastapi.testclient import TestClient

from app.api.auth import AuthUser, get_current_user
from app.main import create_app


def _client_with_user() -> TestClient:
    app = create_app(enable_static=False)
    app.dependency_overrides[get_current_user] = lambda: AuthUser(
        id=uuid4(),
        email="user@example.com",
        password_hash="hash",
    )
    return TestClient(app)


def test_microsoft_todo_oauth_start_is_supported():
    client = _client_with_user()

    response = client.get("/api/integrations/microsoft_todo/oauth/start")

    assert response.status_code == 200
    payload = response.json()
    assert payload["provider"] == "microsoft_todo"
    assert "login.microsoftonline.com" in payload["authorization_url"]
    assert "Tasks.ReadWrite" in payload["authorization_url"]


def test_microsoft_todo_tools_endpoint_returns_create_task_schema():
    client = TestClient(create_app(enable_static=False))

    response = client.get("/api/integrations/microsoft_todo/tools")

    assert response.status_code == 200
    tool = response.json()["tools"][0]
    assert tool["name"] == "microsoft_todo.create_task"
    assert "title" in tool["input_schema"]["properties"]
    assert "idempotency_key" in tool["input_schema"]["required"]
