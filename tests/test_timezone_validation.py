from fastapi.testclient import TestClient

from app.main import create_app


def test_intent_rejects_invalid_iana_timezone_header():
    client = TestClient(create_app())

    response = client.post(
        "/api/intents",
        headers={"X-User-Timezone": "Mars/Olympus"},
        json={"intent_text": "写论文", "preferred_provider": "todoist"},
    )

    assert response.status_code == 422
    assert "Invalid IANA timezone" in response.text


def test_intent_accepts_valid_iana_timezone_header():
    client = TestClient(create_app())

    response = client.post(
        "/api/intents",
        headers={"X-User-Timezone": "Asia/Shanghai"},
        json={"intent_text": "写论文", "preferred_provider": "todoist"},
    )

    assert response.status_code == 202


def test_confirm_rejects_invalid_iana_timezone_header():
    client = TestClient(create_app())

    response = client.post(
        "/api/threads/thread_1/confirm",
        headers={"X-User-Timezone": "Not/AZone"},
        json={"request_id": "req_12345678", "action": "reject", "reason": "no"},
    )

    assert response.status_code == 422
