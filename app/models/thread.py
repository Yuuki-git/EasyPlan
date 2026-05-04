from datetime import datetime
from uuid import UUID

from sqlalchemy import ARRAY, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base
from app.models.types import JsonDict, utc_created_at, utc_updated_at, uuid_pk


class AgentThread(Base):
    __tablename__ = "agent_threads"
    __table_args__ = (
        UniqueConstraint("user_id", "thread_id", name="uq_agent_threads_user_thread"),
    )

    id: Mapped[UUID] = uuid_pk()
    thread_id: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    intent_text: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(64), nullable=False)
    current_node: Mapped[str | None] = mapped_column(String(128))
    next_nodes: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False, default=list)
    lease_owner: Mapped[str | None] = mapped_column(String(128))
    lease_expires_at: Mapped[datetime | None]
    interrupt_payload: Mapped[JsonDict | None] = mapped_column(JSONB)
    latest_checkpoint_id: Mapped[str | None] = mapped_column(String(128))
    task_tree: Mapped[JsonDict | None] = mapped_column(JSONB)
    error_code: Mapped[str | None] = mapped_column(String(128))
    error_message: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = utc_created_at()
    updated_at: Mapped[datetime] = utc_updated_at()
    interrupted_at: Mapped[datetime | None]
    completed_at: Mapped[datetime | None]
    expires_at: Mapped[datetime | None]
