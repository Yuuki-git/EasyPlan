from tests.test_agent_routes_integration import FakeRuntime, FakeThread, FakeThreadRepository, _client_with_overrides


def test_intent_rejects_invalid_iana_timezone_header():
    client, _ = _client_with_overrides(FakeThreadRepository(), FakeRuntime())

    response = client.post(
        "/api/intents",
        headers={"X-User-Timezone": "Mars/Olympus"},
        json={"intent_text": "写论文", "preferred_provider": "todoist"},
    )

    assert response.status_code == 422
    assert "Invalid IANA timezone" in response.text


def test_intent_accepts_valid_iana_timezone_header():
    client, _ = _client_with_overrides(FakeThreadRepository(), FakeRuntime())

    response = client.post(
        "/api/intents",
        headers={"X-User-Timezone": "Asia/Shanghai"},
        json={"intent_text": "写论文", "preferred_provider": "todoist"},
    )

    assert response.status_code == 202


def test_confirm_rejects_invalid_iana_timezone_header():
    repository = FakeThreadRepository()
    runtime = FakeRuntime()
    client, user = _client_with_overrides(repository, runtime)
    repository.threads[(str(user.id), "thread_1")] = FakeThread(
        thread_id="thread_1",
        user_id=str(user.id),
        intent_text="写论文",
    )

    response = client.post(
        "/api/threads/thread_1/confirm",
        headers={"X-User-Timezone": "Not/AZone"},
        json={"request_id": "req_12345678", "action": "reject", "reason": "no"},
    )

    assert response.status_code == 422
