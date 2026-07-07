from datetime import date, datetime
from uuid import UUID

from sqlalchemy import (
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base
from app.models.types import JsonDict, utc_created_at, utc_updated_at, uuid_pk


class PracticeLoop(Base):
    __tablename__ = "practice_loops"
    __table_args__ = (
        UniqueConstraint(
            "thread_id",
            "phase_id",
            "loop_key",
            name="uq_practice_loops_thread_phase_key",
        ),
    )

    id: Mapped[UUID] = uuid_pk()
    user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    thread_id: Mapped[str] = mapped_column(
        String(128),
        ForeignKey("agent_threads.thread_id", ondelete="CASCADE"),
        nullable=False,
    )
    phase_id: Mapped[str] = mapped_column(String(80), nullable=False)
    loop_key: Mapped[str] = mapped_column(String(80), nullable=False)
    status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default="active",
    )
    timezone: Mapped[str] = mapped_column(String(80), nullable=False)
    starts_on: Mapped[date] = mapped_column(Date, nullable=False)
    duration_weeks: Mapped[int] = mapped_column(Integer, nullable=False)
    active_occurrence_task_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("tasks.id", ondelete="SET NULL"),
    )
    created_at: Mapped[datetime] = utc_created_at()
    updated_at: Mapped[datetime] = utc_updated_at()


class PracticeLoopRevision(Base):
    __tablename__ = "practice_loop_revisions"
    __table_args__ = (
        UniqueConstraint(
            "loop_id",
            "revision",
            name="uq_practice_revisions_number",
        ),
        UniqueConstraint(
            "loop_id",
            "effective_week",
            name="uq_practice_revisions_week",
        ),
    )

    id: Mapped[UUID] = uuid_pk()
    loop_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("practice_loops.id", ondelete="CASCADE"),
        nullable=False,
    )
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    effective_week: Mapped[date] = mapped_column(Date, nullable=False)
    title: Mapped[str] = mapped_column(String(160), nullable=False)
    target_per_week: Mapped[int] = mapped_column(Integer, nullable=False)
    done_criteria: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = utc_created_at()


class PracticeLoopLog(Base):
    __tablename__ = "practice_loop_logs"
    __table_args__ = (
        UniqueConstraint(
            "loop_id",
            "local_date",
            name="uq_practice_logs_loop_local_date",
        ),
        UniqueConstraint(
            "occurrence_task_id",
            name="uq_practice_logs_occurrence_task",
        ),
    )

    id: Mapped[UUID] = uuid_pk()
    user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    loop_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("practice_loops.id", ondelete="CASCADE"),
        nullable=False,
    )
    occurrence_task_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("tasks.id", ondelete="SET NULL"),
        nullable=True,
    )
    completed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    local_date: Mapped[date] = mapped_column(Date, nullable=False)
    note: Mapped[JsonDict] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = utc_created_at()


class PhaseReview(Base):
    __tablename__ = "phase_reviews"
    __table_args__ = (
        Index(
            "uq_phase_reviews_active_draft",
            "thread_id",
            "phase_id",
            unique=True,
            postgresql_where=text("status = 'draft'"),
        ),
    )

    id: Mapped[UUID] = uuid_pk()
    user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    thread_id: Mapped[str] = mapped_column(
        String(128),
        ForeignKey("agent_threads.thread_id", ondelete="CASCADE"),
        nullable=False,
    )
    phase_id: Mapped[str] = mapped_column(String(80), nullable=False)
    status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default="draft",
    )
    recommendation: Mapped[str] = mapped_column(String(32), nullable=False)
    decision: Mapped[str | None] = mapped_column(String(32))
    evidence: Mapped[JsonDict] = mapped_column(JSONB, nullable=False, default=dict)
    difficulty: Mapped[str | None] = mapped_column(Text)
    next_capacity: Mapped[str | None] = mapped_column(Text)
    override_reason: Mapped[str | None] = mapped_column(Text)
    statistics: Mapped[JsonDict] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = utc_created_at()
    updated_at: Mapped[datetime] = utc_updated_at()
