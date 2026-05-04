import hashlib
from dataclasses import dataclass
from typing import Any

from app.api.schemas import TaskNode, TaskTree
from app.services.mcp_adapters import (
    TodoistAdapter,
    TodoistCredentials,
    TodoistCreateTaskRequest,
    ToolCallResult,
)


SYNC_STATUS_PENDING = "pending"
SYNC_STATUS_RUNNING = "running"
SYNC_STATUS_SYNCED = "synced"
SYNC_STATUS_RETRYABLE_FAILED = "retryable_failed"
SYNC_STATUS_FAILED = "failed"


@dataclass
class SyncItemRecord:
    user_id: str
    thread_id: str
    request_id: str
    client_node_id: str
    idempotency_key: str
    status: str = SYNC_STATUS_PENDING
    external_task_id: str | None = None
    external_url: str | None = None
    error_message: str | None = None
    response_payload: dict[str, Any] | None = None


@dataclass(frozen=True)
class SyncSummary:
    status: str
    total_count: int
    success_count: int
    failure_count: int
    items: list[SyncItemRecord]


class InMemorySyncRepository:
    """Local repository mirroring sync_run_items behavior for tests and dev."""

    def __init__(self) -> None:
        self._items_by_key: dict[str, SyncItemRecord] = {}

    def get_by_idempotency_key(self, idempotency_key: str) -> SyncItemRecord | None:
        return self._items_by_key.get(idempotency_key)

    def upsert_pending(
        self,
        *,
        user_id: str,
        thread_id: str,
        request_id: str,
        client_node_id: str,
        idempotency_key: str,
    ) -> SyncItemRecord:
        existing = self._items_by_key.get(idempotency_key)
        if existing is not None:
            return existing
        record = SyncItemRecord(
            user_id=user_id,
            thread_id=thread_id,
            request_id=request_id,
            client_node_id=client_node_id,
            idempotency_key=idempotency_key,
        )
        self._items_by_key[idempotency_key] = record
        return record

    def mark_running(self, record: SyncItemRecord) -> None:
        record.status = SYNC_STATUS_RUNNING
        record.error_message = None

    def mark_synced(self, record: SyncItemRecord, result: ToolCallResult) -> None:
        record.status = SYNC_STATUS_SYNCED
        record.external_task_id = result.external_task_id
        record.external_url = result.external_url
        record.response_payload = result.response_payload
        record.error_message = None

    def mark_failed(self, record: SyncItemRecord, error: Exception) -> None:
        record.status = SYNC_STATUS_RETRYABLE_FAILED
        record.error_message = str(error)


class TaskTreeTodoistSyncService:
    """Syncs TaskTree action leaves to Todoist with per-item idempotency."""

    def __init__(
        self,
        *,
        repository: InMemorySyncRepository,
        adapter: TodoistAdapter,
        credentials: TodoistCredentials,
    ) -> None:
        self.repository = repository
        self.adapter = adapter
        self.credentials = credentials

    async def sync_task_tree(
        self,
        *,
        user_id: str,
        thread_id: str,
        request_id: str,
        task_tree: dict[str, Any] | TaskTree,
    ) -> SyncSummary:
        parsed_tree = task_tree if isinstance(task_tree, TaskTree) else TaskTree.model_validate(task_tree)
        items: list[SyncItemRecord] = []
        for node in _action_leaves(parsed_tree.root):
            idempotency_key = build_idempotency_key(
                user_id=user_id,
                thread_id=thread_id,
                client_node_id=node.client_node_id,
                provider="todoist",
                action="create_task",
            )
            record = self.repository.upsert_pending(
                user_id=user_id,
                thread_id=thread_id,
                request_id=request_id,
                client_node_id=node.client_node_id,
                idempotency_key=idempotency_key,
            )
            if record.status == SYNC_STATUS_SYNCED:
                items.append(record)
                continue

            self.repository.mark_running(record)
            try:
                result = await self.adapter.create_task(
                    TodoistCreateTaskRequest(
                        content=node.title,
                        description=node.description,
                        idempotency_key=idempotency_key,
                    ),
                    self.credentials,
                )
            except Exception as exc:
                self.repository.mark_failed(record, exc)
            else:
                self.repository.mark_synced(record, result)
            items.append(record)

        return _summarize_items(items)


def build_idempotency_key(
    *,
    user_id: str,
    thread_id: str,
    client_node_id: str,
    provider: str,
    action: str,
) -> str:
    material = f"{user_id}:{thread_id}:{client_node_id}:{provider}:{action}"
    digest = hashlib.sha256(material.encode("utf-8")).hexdigest()
    return f"{provider}:{action}:{digest}"


def _action_leaves(root: TaskNode) -> list[TaskNode]:
    leaves: list[TaskNode] = []

    def walk(node: TaskNode) -> None:
        if node.node_type == "action" and not node.children:
            leaves.append(node)
            return
        for child in node.children:
            walk(child)

    walk(root)
    return leaves


def _summarize_items(items: list[SyncItemRecord]) -> SyncSummary:
    success_count = sum(1 for item in items if item.status == SYNC_STATUS_SYNCED)
    failure_count = sum(1 for item in items if item.status in {SYNC_STATUS_RETRYABLE_FAILED, SYNC_STATUS_FAILED})
    if failure_count == 0:
        status = "succeeded"
    elif success_count > 0:
        status = "partially_succeeded"
    else:
        status = "failed"
    return SyncSummary(
        status=status,
        total_count=len(items),
        success_count=success_count,
        failure_count=failure_count,
        items=items,
    )
