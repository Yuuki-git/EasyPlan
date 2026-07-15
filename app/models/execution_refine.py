from datetime import datetime
from uuid import UUID

from sqlalchemy import DateTime, ForeignKey, Index, String, Text, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import JSONB, UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base
from app.models.types import JsonDict, utc_created_at, utc_updated_at, uuid_pk


class ExecutionRefineRun(Base):
    __tablename__ = "execution_refine_runs"
    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "thread_id",
            "request_id",
            name="uq_execution_refine_runs_user_thread_request",
        ),
        Index(
            "uq_execution_refine_runs_active_thread",
            "user_id",
            "thread_id",
            unique=True,
            postgresql_where=text("status = 'running'"),
        ),
    )

    id: Mapped[UUID] = uuid_pk()
    user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    thread_id: Mapped[str] = mapped_column(
        String(128),
        ForeignKey("agent_threads.thread_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    request_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    mode: Mapped[str] = mapped_column(String(32), nullable=False)
    input_context: Mapped[JsonDict] = mapped_column(JSONB, nullable=False, default=dict)
    scope_snapshot: Mapped[JsonDict] = mapped_column(JSONB, nullable=False, default=dict)
    scope_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="running")
    stage: Mapped[str | None] = mapped_column(String(64))
    proposal: Mapped[JsonDict | None] = mapped_column(JSONB)
    apply_receipt: Mapped[JsonDict | None] = mapped_column(JSONB)
    error_code: Mapped[str | None] = mapped_column(String(128))
    error_message: Mapped[str | None] = mapped_column(Text)
    lease_owner: Mapped[str | None] = mapped_column(String(128))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = utc_created_at()
    updated_at: Mapped[datetime] = utc_updated_at()
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    applied_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
