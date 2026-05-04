from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


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
