import json
import hashlib
import uuid
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


TODOIST_SYNC_PATH = "/api/v1/sync"
TODOIST_TASK_NAMESPACE = uuid.UUID("8ddca813-6d8b-4f33-9d80-05f22f6d4f23")
MICROSOFT_GRAPH_BASE_URL = "https://graph.microsoft.com/v1.0"


class TodoistAdapterError(RuntimeError):
    """Raised when Todoist rejects or fails a tool call."""


class TodoistCredentials(BaseModel):
    model_config = ConfigDict(extra="forbid")

    access_token: str = Field(..., min_length=1)
    token_type: str = Field(default="Bearer", min_length=1)


class MicrosoftToDoCredentials(BaseModel):
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


class MicrosoftToDoCreateTaskRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(..., min_length=1, max_length=500)
    description: str | None = Field(default=None, max_length=4000)
    task_list_id: str | None = Field(default=None, max_length=256)
    categories: list[str] = Field(default_factory=list, max_length=20)
    importance: str | None = Field(default=None, pattern="^(low|normal|high)$")
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


class MicrosoftToDoAdapter:
    """Built-in MCP-compatible Microsoft To Do adapter exposed as a create_task tool."""

    provider = "microsoft_todo"
    create_task_tool_name = "microsoft_todo.create_task"

    def __init__(
        self,
        *,
        http_client: Any | None = None,
        graph_base_url: str = MICROSOFT_GRAPH_BASE_URL,
        timeout_seconds: float = 15.0,
    ) -> None:
        self._http_client = http_client
        self.graph_base_url = graph_base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds

    def list_tools(self) -> list[McpToolDefinition]:
        return [
            McpToolDefinition(
                name=self.create_task_tool_name,
                title="Create Microsoft To Do task",
                description="Create a Microsoft To Do task with a required EasyPlan idempotency key.",
                input_schema=MicrosoftToDoCreateTaskRequest.model_json_schema(),
            )
        ]

    async def call_tool(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        credentials: MicrosoftToDoCredentials,
    ) -> ToolCallResult:
        if tool_name != self.create_task_tool_name:
            raise TodoistAdapterError(f"Unsupported Microsoft To Do tool: {tool_name}")
        return await self.create_task(MicrosoftToDoCreateTaskRequest.model_validate(arguments), credentials)

    async def create_task(
        self,
        request: MicrosoftToDoCreateTaskRequest,
        credentials: MicrosoftToDoCredentials,
    ) -> ToolCallResult:
        task_list_id = request.task_list_id or await self._resolve_default_task_list_id(credentials)
        idempotency_category = self.idempotency_category(request.idempotency_key)
        existing_task = await self._find_existing_task_by_category(
            task_list_id,
            idempotency_category,
            credentials,
        )
        if existing_task is not None:
            return ToolCallResult(
                external_task_id=str(existing_task.get("id")),
                external_url=existing_task.get("webLink"),
                response_payload={
                    "idempotency_hit": True,
                    "idempotency_category": idempotency_category,
                },
            )

        response = await self._post(
            f"/me/todo/lists/{task_list_id}/tasks",
            json=_microsoft_todo_task_payload(request, idempotency_category),
            headers=_microsoft_graph_headers(credentials),
        )
        response.raise_for_status()
        response_payload = response.json()
        external_task_id = response_payload.get("id")
        return ToolCallResult(
            external_task_id=str(external_task_id) if external_task_id is not None else None,
            external_url=response_payload.get("webLink"),
            response_payload={
                "idempotency_hit": False,
                "idempotency_category": idempotency_category,
                "categories": response_payload.get("categories", []),
            },
        )

    @staticmethod
    def idempotency_category(idempotency_key: str) -> str:
        digest = hashlib.sha256(idempotency_key.encode("utf-8")).hexdigest()[:32]
        return f"EasyPlan:{digest}"

    async def _resolve_default_task_list_id(self, credentials: MicrosoftToDoCredentials) -> str:
        response = await self._get("/me/todo/lists", headers=_microsoft_graph_headers(credentials))
        response.raise_for_status()
        lists = response.json().get("value", [])
        default_list = next(
            (item for item in lists if item.get("wellknownListName") == "defaultList"),
            lists[0] if lists else None,
        )
        if default_list is None or not default_list.get("id"):
            raise TodoistAdapterError("Microsoft To Do default task list not found")
        return str(default_list["id"])

    async def _find_existing_task_by_category(
        self,
        task_list_id: str,
        idempotency_category: str,
        credentials: MicrosoftToDoCredentials,
    ) -> dict[str, Any] | None:
        response = await self._get(
            f"/me/todo/lists/{task_list_id}/tasks",
            headers=_microsoft_graph_headers(credentials),
            params={"$top": 100},
        )
        response.raise_for_status()
        for task in response.json().get("value", []):
            if idempotency_category in (task.get("categories") or []):
                return task
        return None

    async def _get(
        self,
        path: str,
        *,
        headers: dict[str, str],
        params: dict[str, Any] | None = None,
    ):
        url = f"{self.graph_base_url}{path}"
        if self._http_client is not None:
            return await self._http_client.get(
                url,
                headers=headers,
                params=params,
                timeout=self.timeout_seconds,
            )

        import httpx

        async with httpx.AsyncClient() as client:
            return await client.get(
                url,
                headers=headers,
                params=params,
                timeout=self.timeout_seconds,
            )

    async def _post(self, path: str, *, json: dict[str, Any], headers: dict[str, str]):
        url = f"{self.graph_base_url}{path}"
        if self._http_client is not None:
            return await self._http_client.post(
                url,
                json=json,
                headers=headers,
                timeout=self.timeout_seconds,
            )

        import httpx

        async with httpx.AsyncClient() as client:
            return await client.post(
                url,
                json=json,
                headers=headers,
                timeout=self.timeout_seconds,
            )


def _microsoft_graph_headers(credentials: MicrosoftToDoCredentials) -> dict[str, str]:
    return {
        "Authorization": f"{credentials.token_type} {credentials.access_token}",
        "Content-Type": "application/json",
    }


def _microsoft_todo_task_payload(
    request: MicrosoftToDoCreateTaskRequest,
    idempotency_category: str,
) -> dict[str, Any]:
    categories = _dedupe_categories(["EasyPlan", idempotency_category, *request.categories])
    payload: dict[str, Any] = {
        "title": request.title,
        "categories": categories,
        "body": {
            "contentType": "text",
            "content": _microsoft_todo_body(request.description, request.idempotency_key),
        },
    }
    if request.importance:
        payload["importance"] = request.importance
    return payload


def _microsoft_todo_body(description: str | None, idempotency_key: str) -> str:
    prefix = description or ""
    separator = "\n\n" if prefix else ""
    return f"{prefix}{separator}EasyPlan idempotency_key: {idempotency_key}"


def _dedupe_categories(categories: list[str]) -> list[str]:
    seen: set[str] = set()
    deduped: list[str] = []
    for category in categories:
        if category and category not in seen:
            seen.add(category)
            deduped.append(category)
    return deduped


def get_builtin_adapter(provider: str):
    normalized_provider = provider.strip().lower()
    if normalized_provider == "todoist":
        return TodoistAdapter()
    if normalized_provider in {"microsoft_todo", "microsoft"}:
        return MicrosoftToDoAdapter()
    raise TodoistAdapterError(f"Unsupported built-in adapter provider: {provider}")


def list_builtin_tools(provider: str) -> list[dict[str, Any]]:
    adapter = get_builtin_adapter(provider)
    return [
        {
            "name": tool.name,
            "title": tool.title,
            "description": tool.description,
            "input_schema": tool.input_schema,
        }
        for tool in adapter.list_tools()
    ]
