import asyncio
import json
from uuid import UUID

from app.services.mcp_adapters import (
    TodoistAdapter,
    TodoistCredentials,
    TodoistCreateTaskRequest,
)
from app.services.sync_service import InMemorySyncRepository, TaskTreeTodoistSyncService


class FakeResponse:
    def __init__(self, payload: dict):
        self.payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return self.payload


class FakeHttpClient:
    def __init__(self):
        self.posts: list[dict] = []

    async def post(self, url: str, *, data: dict, headers: dict, timeout: float):
        self.posts.append({"url": url, "data": data, "headers": headers, "timeout": timeout})
        command = json.loads(data["commands"])[0]
        return FakeResponse(
            {
                "sync_status": {command["uuid"]: "ok"},
                "temp_id_mapping": {command["temp_id"]: "todoist-task-1"},
            }
        )


def _task_tree() -> dict:
    return {
        "root": {
            "client_node_id": "root",
            "title": "Project",
            "description": None,
            "verb": "Plan",
            "estimated_minutes": 1,
            "node_type": "group",
            "depends_on": [],
            "children": [
                {
                    "client_node_id": "task-a",
                    "title": "Open inbox",
                    "description": "Start from inbox",
                    "verb": "Open",
                    "estimated_minutes": 2,
                    "node_type": "action",
                    "depends_on": [],
                    "children": [],
                },
                {
                    "client_node_id": "task-b",
                    "title": "Write outline",
                    "description": None,
                    "verb": "Write",
                    "estimated_minutes": 2,
                    "node_type": "action",
                    "depends_on": [],
                    "children": [],
                },
            ],
        },
        "summary": "Project plan",
        "assumptions": [],
    }


def test_todoist_adapter_sends_deterministic_command_uuid_from_idempotency_key():
    http_client = FakeHttpClient()
    adapter = TodoistAdapter(http_client=http_client, api_base_url="https://api.todoist.com")

    result = asyncio.run(
        adapter.create_task(
            TodoistCreateTaskRequest(
                content="Open inbox",
                description="Start from inbox",
                idempotency_key="user-1:thread-1:request-1:task-a",
            ),
            TodoistCredentials(access_token="token-1"),
        )
    )

    post = http_client.posts[0]
    command = json.loads(post["data"]["commands"])[0]
    assert post["url"] == "https://api.todoist.com/api/v1/sync"
    assert post["headers"]["Authorization"] == "Bearer token-1"
    assert command["type"] == "item_add"
    assert str(UUID(command["uuid"])) == command["uuid"]
    assert command["args"]["content"] == "Open inbox"
    assert result.external_task_id == "todoist-task-1"


class PartiallyFailingAdapter:
    def __init__(self):
        self.calls: list[str] = []

    async def create_task(self, request: TodoistCreateTaskRequest, credentials: TodoistCredentials):
        self.calls.append(request.content)
        if request.content == "Write outline" and self.calls.count("Write outline") == 1:
            raise RuntimeError("temporary failure")
        return type(
            "Result",
            (),
            {
                "external_task_id": f"ext-{request.content}",
                "external_url": f"https://todoist.example/{request.content}",
                "response_payload": {"content": request.content},
            },
        )()


def test_sync_retry_skips_successful_items_and_replays_only_failed_children():
    adapter = PartiallyFailingAdapter()
    service = TaskTreeTodoistSyncService(
        repository=InMemorySyncRepository(),
        adapter=adapter,
        credentials=TodoistCredentials(access_token="token-1"),
    )

    first = asyncio.run(
        service.sync_task_tree(
            user_id="user-1",
            thread_id="thread-1",
            request_id="request-1",
            task_tree=_task_tree(),
        )
    )
    second = asyncio.run(
        service.sync_task_tree(
            user_id="user-1",
            thread_id="thread-1",
            request_id="request-1",
            task_tree=_task_tree(),
        )
    )

    assert first.status == "partially_succeeded"
    assert second.status == "succeeded"
    assert adapter.calls == ["Open inbox", "Write outline", "Write outline"]
