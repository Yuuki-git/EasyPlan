from datetime import datetime
from uuid import UUID

from sqlalchemy import ARRAY, ForeignKeyConstraint, Integer, PrimaryKeyConstraint, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base
from app.models.types import JsonDict, utc_created_at, uuid_pk


class AgentCheckpoint(Base):
    __tablename__ = "agent_checkpoints"
    __table_args__ = (
        ForeignKeyConstraint(
            ["user_id", "thread_id"],
            ["agent_threads.user_id", "agent_threads.thread_id"],
            ondelete="CASCADE",
            name="fk_agent_checkpoints_thread_tenant",
        ),
        UniqueConstraint(
            "user_id",
            "thread_id",
            "checkpoint_ns",
            "checkpoint_id",
            name="uq_agent_checkpoints_tenant_checkpoint",
        ),
    )

    id: Mapped[UUID] = uuid_pk()
    user_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    thread_id: Mapped[str] = mapped_column(String(128), nullable=False)
    checkpoint_id: Mapped[str] = mapped_column(String(128), nullable=False)
    checkpoint_ns: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    parent_checkpoint_id: Mapped[str | None] = mapped_column(String(128))
    node_name: Mapped[str | None] = mapped_column(String(128))
    graph_status: Mapped[str] = mapped_column(String(64), nullable=False)
    state_summary: Mapped[JsonDict] = mapped_column(JSONB, nullable=False, default=dict)
    next_nodes: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False, default=list)
    interrupt_payload: Mapped[JsonDict | None] = mapped_column(JSONB)
    metadata_: Mapped[JsonDict] = mapped_column("metadata", JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = utc_created_at()


class LangGraphCheckpoint(Base):
    __tablename__ = "langgraph_checkpoints"
    __table_args__ = (
        PrimaryKeyConstraint(
            "user_id",
            "thread_id",
            "checkpoint_ns",
            "checkpoint_id",
            name="pk_langgraph_checkpoints_tenant",
        ),
        ForeignKeyConstraint(
            ["user_id", "thread_id"],
            ["agent_threads.user_id", "agent_threads.thread_id"],
            ondelete="CASCADE",
            name="fk_langgraph_checkpoints_thread_tenant",
        ),
    )

    user_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    thread_id: Mapped[str] = mapped_column(String(128), nullable=False)
    checkpoint_ns: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    checkpoint_id: Mapped[str] = mapped_column(String(128), nullable=False)
    parent_checkpoint_id: Mapped[str | None] = mapped_column(String(128))
    checkpoint: Mapped[JsonDict] = mapped_column(JSONB, nullable=False)
    metadata_: Mapped[JsonDict] = mapped_column("metadata", JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = utc_created_at()


class LangGraphCheckpointWrite(Base):
    __tablename__ = "langgraph_checkpoint_writes"
    __table_args__ = (
        PrimaryKeyConstraint(
            "user_id",
            "thread_id",
            "checkpoint_ns",
            "checkpoint_id",
            "task_id",
            "idx",
            name="pk_langgraph_checkpoint_writes_tenant",
        ),
        ForeignKeyConstraint(
            ["user_id", "thread_id", "checkpoint_ns", "checkpoint_id"],
            [
                "langgraph_checkpoints.user_id",
                "langgraph_checkpoints.thread_id",
                "langgraph_checkpoints.checkpoint_ns",
                "langgraph_checkpoints.checkpoint_id",
            ],
            ondelete="CASCADE",
            name="fk_langgraph_checkpoint_writes_checkpoint",
        ),
    )

    user_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    thread_id: Mapped[str] = mapped_column(String(128), nullable=False)
    checkpoint_ns: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    checkpoint_id: Mapped[str] = mapped_column(String(128), nullable=False)
    task_id: Mapped[str] = mapped_column(String(128), nullable=False)
    idx: Mapped[int] = mapped_column(Integer, nullable=False)
    channel: Mapped[str] = mapped_column(String(128), nullable=False)
    value: Mapped[JsonDict] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = utc_created_at()
