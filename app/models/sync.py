from datetime import datetime
from uuid import UUID

from sqlalchemy import ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base
from app.models.types import JsonDict, utc_created_at, utc_updated_at, uuid_pk


class SyncRun(Base):
    __tablename__ = "sync_runs"
    __table_args__ = (
        UniqueConstraint("user_id", "request_id", name="uq_sync_runs_user_request"),
    )

    id: Mapped[UUID] = uuid_pk()
    thread_id: Mapped[str] = mapped_column(
        String(128),
        ForeignKey("agent_threads.thread_id", ondelete="CASCADE"),
        nullable=False,
    )
    user_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    integration_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    request_id: Mapped[str] = mapped_column(String(128), nullable=False)
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(64), nullable=False, default="running")
    total_count: Mapped[int] = mapped_column(nullable=False, default=0)
    success_count: Mapped[int] = mapped_column(nullable=False, default=0)
    failure_count: Mapped[int] = mapped_column(nullable=False, default=0)
    started_at: Mapped[datetime] = utc_created_at()
    completed_at: Mapped[datetime | None]
    error_message: Mapped[str | None] = mapped_column(Text)


class SyncRunItem(Base):
    __tablename__ = "sync_run_items"
    __table_args__ = (
        UniqueConstraint("sync_run_id", "task_id", name="uq_sync_run_items_run_task"),
        UniqueConstraint("idempotency_key", name="uq_sync_run_items_idempotency_key"),
    )

    id: Mapped[UUID] = uuid_pk()
    sync_run_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("sync_runs.id", ondelete="CASCADE"),
        nullable=False,
    )
    task_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("tasks.id", ondelete="CASCADE"),
        nullable=False,
    )
    mcp_tool_name: Mapped[str] = mapped_column(String(128), nullable=False)
    request_payload: Mapped[JsonDict] = mapped_column(JSONB, nullable=False)
    response_payload: Mapped[JsonDict | None] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(String(64), nullable=False, default="pending")
    external_task_id: Mapped[str | None] = mapped_column(String(256))
    external_url: Mapped[str | None] = mapped_column(Text)
    idempotency_key: Mapped[str] = mapped_column(String(512), nullable=False)
    error_message: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = utc_created_at()
    updated_at: Mapped[datetime] = utc_updated_at()


class ConfirmationRequest(Base):
    __tablename__ = "confirmation_requests"
    __table_args__ = (
        UniqueConstraint("user_id", "request_id", name="uq_confirmation_requests_user_request"),
    )

    id: Mapped[UUID] = uuid_pk()
    thread_id: Mapped[str] = mapped_column(
        String(128),
        ForeignKey("agent_threads.thread_id", ondelete="CASCADE"),
        nullable=False,
    )
    user_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    request_id: Mapped[str] = mapped_column(String(128), nullable=False)
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    payload_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[str] = mapped_column(String(64), nullable=False, default="accepted")
    sync_run_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("sync_runs.id"))
    response_payload: Mapped[JsonDict | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = utc_created_at()
    updated_at: Mapped[datetime] = utc_updated_at()
