from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from langgraph.checkpoint.memory import InMemorySaver
from sqlalchemy import select

from app.models.checkpoint import LangGraphCheckpoint


@dataclass(frozen=True)
class TenantCheckpointRecord:
    user_id: str
    thread_id: str
    checkpoint_ns: str
    checkpoint_id: str
    checkpoint: dict[str, Any]
    metadata: dict[str, Any] = field(default_factory=dict)
    parent_checkpoint_id: str | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


class InMemoryTenantCheckpointStore:
    """Small tenant-aware checkpoint store used by tests and local adapters."""

    def __init__(self) -> None:
        self._records: dict[tuple[str, str, str, str], TenantCheckpointRecord] = {}

    def put(self, record: TenantCheckpointRecord) -> None:
        key = (
            record.user_id,
            record.thread_id,
            record.checkpoint_ns,
            record.checkpoint_id,
        )
        self._records[key] = record

    def get(
        self,
        user_id: str,
        thread_id: str,
        checkpoint_ns: str,
        checkpoint_id: str,
    ) -> TenantCheckpointRecord | None:
        return self._records.get((user_id, thread_id, checkpoint_ns, checkpoint_id))

    def list_for_thread(
        self,
        user_id: str,
        thread_id: str,
        checkpoint_ns: str = "",
    ) -> list[TenantCheckpointRecord]:
        records = [
            record
            for key, record in self._records.items()
            if key[0] == user_id and key[1] == thread_id and key[2] == checkpoint_ns
        ]
        return sorted(records, key=lambda record: record.created_at)


def build_tenant_checkpoint_restore_query(
    *,
    user_id: UUID,
    thread_id: str,
    checkpoint_ns: str = "",
    checkpoint_id: str | None = None,
):
    statement = select(LangGraphCheckpoint).where(
        LangGraphCheckpoint.user_id == user_id,
        LangGraphCheckpoint.thread_id == thread_id,
        LangGraphCheckpoint.checkpoint_ns == checkpoint_ns,
    )
    if checkpoint_id is not None:
        statement = statement.where(LangGraphCheckpoint.checkpoint_id == checkpoint_id)
    return statement.order_by(LangGraphCheckpoint.created_at.desc()).limit(1)


class TenantAwareMemorySaver(InMemorySaver):
    """LangGraph checkpointer that namespaces checkpoints by user_id + thread_id."""

    @staticmethod
    def _tenant_config(config: dict[str, Any]) -> dict[str, Any]:
        configurable = dict(config.get("configurable") or {})
        user_id = configurable.get("user_id")
        thread_id = configurable.get("thread_id")
        if not user_id or not thread_id:
            raise ValueError("LangGraph checkpoint config requires user_id and thread_id")

        tenant_config = dict(config)
        tenant_config["configurable"] = {
            **configurable,
            "thread_id": f"{user_id}:{thread_id}",
            "easyplan_user_id": user_id,
            "easyplan_thread_id": thread_id,
        }
        return tenant_config

    def get_tuple(self, config: dict[str, Any]):
        return super().get_tuple(self._tenant_config(config))

    async def aget_tuple(self, config: dict[str, Any]):
        return await super().aget_tuple(self._tenant_config(config))

    def put(
        self,
        config: dict[str, Any],
        checkpoint: dict[str, Any],
        metadata: dict[str, Any],
        new_versions: dict[str, Any],
    ) -> dict[str, Any]:
        tenant_config = self._tenant_config(config)
        saved_config = super().put(tenant_config, checkpoint, metadata, new_versions)
        return self._restore_public_config(saved_config)

    async def aput(
        self,
        config: dict[str, Any],
        checkpoint: dict[str, Any],
        metadata: dict[str, Any],
        new_versions: dict[str, Any],
    ) -> dict[str, Any]:
        tenant_config = self._tenant_config(config)
        saved_config = await super().aput(tenant_config, checkpoint, metadata, new_versions)
        return self._restore_public_config(saved_config)

    def put_writes(
        self,
        config: dict[str, Any],
        writes: list[tuple[str, Any]],
        task_id: str,
        task_path: str = "",
    ) -> None:
        super().put_writes(self._tenant_config(config), writes, task_id, task_path)

    async def aput_writes(
        self,
        config: dict[str, Any],
        writes: list[tuple[str, Any]],
        task_id: str,
        task_path: str = "",
    ) -> None:
        await super().aput_writes(self._tenant_config(config), writes, task_id, task_path)

    def list(self, config: dict[str, Any] | None, **kwargs: Any):
        if config is None:
            raise ValueError("LangGraph checkpoint list requires user_id and thread_id")
        return super().list(self._tenant_config(config), **kwargs)

    async def alist(self, config: dict[str, Any] | None, **kwargs: Any):
        if config is None:
            raise ValueError("LangGraph checkpoint list requires user_id and thread_id")
        async for checkpoint in super().alist(self._tenant_config(config), **kwargs):
            yield checkpoint

    @staticmethod
    def _restore_public_config(config: dict[str, Any]) -> dict[str, Any]:
        configurable = dict(config.get("configurable") or {})
        user_id = configurable.get("easyplan_user_id")
        thread_id = configurable.get("easyplan_thread_id")
        if user_id and thread_id:
            configurable["user_id"] = user_id
            configurable["thread_id"] = thread_id
        restored = dict(config)
        restored["configurable"] = configurable
        return restored
