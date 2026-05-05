import asyncio

from app.services.mcp_adapters import (
    MicrosoftToDoAdapter,
    MicrosoftToDoCreateTaskRequest,
    MicrosoftToDoCredentials,
)


class FakeResponse:
    def __init__(self, payload: dict):
        self.payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return self.payload


class FakeGraphHttpClient:
    def __init__(self, *, existing_tasks: list[dict] | None = None):
        self.existing_tasks = existing_tasks or []
        self.gets: list[dict] = []
        self.posts: list[dict] = []

    async def get(self, url: str, *, headers: dict, params: dict | None = None, timeout: float):
        self.gets.append({"url": url, "headers": headers, "params": params, "timeout": timeout})
        if url.endswith("/me/todo/lists"):
            return FakeResponse({"value": [{"id": "default-list-id", "wellknownListName": "defaultList"}]})
        if url.endswith("/me/todo/lists/default-list-id/tasks"):
            return FakeResponse({"value": self.existing_tasks})
        raise AssertionError(f"Unexpected GET {url}")

    async def post(self, url: str, *, json: dict, headers: dict, timeout: float):
        self.posts.append({"url": url, "json": json, "headers": headers, "timeout": timeout})
        return FakeResponse(
            {
                "id": "graph-task-1",
                "webLink": "https://to-do.office.com/tasks/graph-task-1",
                "title": json["title"],
                "categories": json["categories"],
            }
        )


def test_microsoft_todo_adapter_exposes_create_task_tool_schema():
    adapter = MicrosoftToDoAdapter(http_client=FakeGraphHttpClient())

    tool = adapter.list_tools()[0]

    assert tool.name == "microsoft_todo.create_task"
    assert tool.input_schema["properties"]["title"]["minLength"] == 1
    assert "description" in tool.input_schema["properties"]
    assert "idempotency_key" in tool.input_schema["required"]


def test_microsoft_todo_adapter_creates_graph_task_with_idempotency_category():
    http_client = FakeGraphHttpClient()
    adapter = MicrosoftToDoAdapter(http_client=http_client, graph_base_url="https://graph.microsoft.com/v1.0")

    result = asyncio.run(
        adapter.create_task(
            MicrosoftToDoCreateTaskRequest(
                title="Open inbox",
                description="Start from inbox",
                idempotency_key="user-1:thread-1:task-a:microsoft_todo:create_task",
            ),
            MicrosoftToDoCredentials(access_token="graph-token"),
        )
    )

    post = http_client.posts[0]
    assert post["url"] == "https://graph.microsoft.com/v1.0/me/todo/lists/default-list-id/tasks"
    assert post["headers"]["Authorization"] == "Bearer graph-token"
    assert post["json"]["title"] == "Open inbox"
    assert post["json"]["body"] == {
        "contentType": "text",
        "content": "Start from inbox\n\nEasyPlan idempotency_key: user-1:thread-1:task-a:microsoft_todo:create_task",
    }
    assert "EasyPlan" in post["json"]["categories"]
    assert any(category.startswith("EasyPlan:") for category in post["json"]["categories"])
    assert result.external_task_id == "graph-task-1"
    assert result.response_payload["idempotency_hit"] is False


def test_microsoft_todo_adapter_skips_create_when_idempotency_category_already_exists():
    existing_category = MicrosoftToDoAdapter.idempotency_category("idem-1")
    http_client = FakeGraphHttpClient(
        existing_tasks=[
            {
                "id": "existing-task-1",
                "webLink": "https://to-do.office.com/tasks/existing-task-1",
                "title": "Open inbox",
                "categories": ["EasyPlan", existing_category],
            }
        ]
    )
    adapter = MicrosoftToDoAdapter(http_client=http_client)

    result = asyncio.run(
        adapter.create_task(
            MicrosoftToDoCreateTaskRequest(
                title="Open inbox",
                description=None,
                idempotency_key="idem-1",
            ),
            MicrosoftToDoCredentials(access_token="graph-token"),
        )
    )

    assert http_client.posts == []
    assert result.external_task_id == "existing-task-1"
    assert result.response_payload["idempotency_hit"] is True
