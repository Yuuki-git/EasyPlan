import json
import uuid
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


TODOIST_SYNC_PATH = "/api/v1/sync"
TODOIST_TASK_NAMESPACE = uuid.UUID("8ddca813-6d8b-4f33-9d80-05f22f6d4f23")


class TodoistAdapterError(RuntimeError):
    """Raised when Todoist rejects or fails a tool call."""


class TodoistCredentials(BaseModel):
    model_config = ConfigDict(extra="forbid")

    access_token: str = Field(..., min_length=1)
    token_type: str = Field(default="Bearer", min_length=1)


class TodoistCreateTaskRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    content: str = Field(..., min_length=1, max_length=500)
    description: str | None = Field(default=None, max_length=4000)
    project_id: str | None = None
    section_id: str | None = None
    parent_id: str | None = None
    labels: list[str] = Field(default_factory=list)
    priority: int | None = Field(default=None, ge=1, le=4)
    idempotency_key: str = Field(..., min_length=1, max_length=512)


@dataclass(frozen=True)
class McpToolDefinition:
    name: str
    title: str
    description: str
    input_schema: dict[str, Any]


@dataclass(frozen=True)
class ToolCallResult:
    external_task_id: str | None
    external_url: str | None
    response_payload: dict[str, Any] = field(default_factory=dict)


class TodoistAdapter:
    """Built-in MCP-compatible Todoist adapter exposed as a create_task tool."""

    provider = "todoist"
    create_task_tool_name = "todoist.create_task"

    def __init__(
        self,
        *,
        http_client: Any | None = None,
        api_base_url: str = "https://api.todoist.com",
        timeout_seconds: float = 15.0,
    ) -> None:
        self._http_client = http_client
        self.api_base_url = api_base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds

    def list_tools(self) -> list[McpToolDefinition]:
        return [
            McpToolDefinition(
                name=self.create_task_tool_name,
                title="Create Todoist task",
                description="Create a Todoist task with a required EasyPlan idempotency key.",
                input_schema=TodoistCreateTaskRequest.model_json_schema(),
            )
        ]

    async def call_tool(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        credentials: TodoistCredentials,
    ) -> ToolCallResult:
        if tool_name != self.create_task_tool_name:
            raise TodoistAdapterError(f"Unsupported Todoist tool: {tool_name}")
        return await self.create_task(TodoistCreateTaskRequest.model_validate(arguments), credentials)

    async def create_task(
        self,
        request: TodoistCreateTaskRequest,
        credentials: TodoistCredentials,
    ) -> ToolCallResult:
        command_uuid = _stable_uuid(f"{request.idempotency_key}:command")
        temp_id = _stable_uuid(f"{request.idempotency_key}:temp")
        command = {
            "type": "item_add",
            "uuid": command_uuid,
            "temp_id": temp_id,
            "args": _todoist_task_args(request),
        }
        payload = {"commands": json.dumps([command], separators=(",", ":"))}
        response = await self._post(
            TODOIST_SYNC_PATH,
            data=payload,
            headers={
                "Authorization": f"{credentials.token_type} {credentials.access_token}",
            },
        )
        response.raise_for_status()
        response_payload = response.json()
        _raise_for_sync_error(response_payload, command_uuid)
        external_task_id = _extract_external_task_id(response_payload, temp_id)
        return ToolCallResult(
            external_task_id=external_task_id,
            external_url=_todoist_task_url(external_task_id),
            response_payload=_summarize_todoist_response(response_payload, command_uuid, temp_id),
        )

    async def _post(self, path: str, *, data: dict[str, Any], headers: dict[str, str]):
        url = f"{self.api_base_url}{path}"
        if self._http_client is not None:
            return await self._http_client.post(
                url,
                data=data,
                headers=headers,
                timeout=self.timeout_seconds,
            )

        import httpx

        async with httpx.AsyncClient() as client:
            return await client.post(
                url,
                data=data,
                headers=headers,
                timeout=self.timeout_seconds,
            )


def _stable_uuid(material: str) -> str:
    return str(uuid.uuid5(TODOIST_TASK_NAMESPACE, material))


def _todoist_task_args(request: TodoistCreateTaskRequest) -> dict[str, Any]:
    args: dict[str, Any] = {"content": request.content}
    optional_fields = {
        "description": request.description,
        "project_id": request.project_id,
        "section_id": request.section_id,
        "parent_id": request.parent_id,
        "labels": request.labels or None,
        "priority": request.priority,
    }
    for key, value in optional_fields.items():
        if value is not None:
            args[key] = value
    return args


def _raise_for_sync_error(response_payload: dict[str, Any], command_uuid: str) -> None:
    status = response_payload.get("sync_status", {}).get(command_uuid)
    if status in (None, "ok"):
        return
    if isinstance(status, dict):
        message = status.get("error") or status.get("message") or json.dumps(status, ensure_ascii=False)
    else:
        message = str(status)
    raise TodoistAdapterError(message)


def _extract_external_task_id(response_payload: dict[str, Any], temp_id: str) -> str | None:
    mapping = response_payload.get("temp_id_mapping") or {}
    external_id = mapping.get(temp_id)
    return str(external_id) if external_id is not None else None


def _todoist_task_url(external_task_id: str | None) -> str | None:
    if not external_task_id:
        return None
    return f"https://app.todoist.com/app/task/{external_task_id}"


def _summarize_todoist_response(
    response_payload: dict[str, Any],
    command_uuid: str,
    temp_id: str,
) -> dict[str, Any]:
    return {
        "sync_status": {command_uuid: response_payload.get("sync_status", {}).get(command_uuid)},
        "external_task_id": _extract_external_task_id(response_payload, temp_id),
    }
