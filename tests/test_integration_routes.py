from uuid import uuid4

from fastapi.testclient import TestClient

from app.main import create_app


def _register_token(client: TestClient) -> str:
    response = client.post(
        "/api/auth/register",
        json={
            "email": f"user-{uuid4().hex}@example.com",
            "password": "correct horse battery staple",
        },
    )
    assert response.status_code == 201
    return response.json()["access_token"]


def test_microsoft_todo_oauth_start_is_supported():
    client = TestClient(create_app(enable_static=False))
    token = _register_token(client)

    response = client.get(
        "/api/integrations/microsoft_todo/oauth/start",
        headers={"Authorization": f"Bearer {token}"},
    )

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
