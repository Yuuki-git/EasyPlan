from datetime import date, datetime
from enum import Enum
from uuid import UUID
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


MAX_TASK_TREE_DEPTH = 8
MAX_TASK_TREE_SIBLINGS = 20
MAX_TASK_TREE_NODES = 200
ACTION_QUALITY_FIELDS = ("done_criteria", "start_hint", "fallback_action")
PHASE_METADATA_FIELDS = ("source", "phase_id", "phase_order")
TASK_METADATA_FIELDS = ACTION_QUALITY_FIELDS + PHASE_METADATA_FIELDS + (
    "practice_loop_id",
)

IntentType = Literal[
    "long_term_growth",
    "short_term_delivery",
    "context_checklist",
    "exploration_decision",
]
TimeHorizon = Literal["minutes", "hours", "days", "weeks", "months"]
RoadmapStatus = Literal["planned", "current", "completed"]
SseRunType = Literal["initial", "next_phase", "refine"]
SseEventType = Literal[
    "run_started",
    "intent_profile_started",
    "intent_profile_completed",
    "strategy_selected",
    "planning_started",
    "validation_started",
    "repair_started",
    "persistence_started",
    "still_running",
    "plan_ready",
    "sync_status",
    "sync_complete",
    "done",
    "agent_error",
    "snapshot_required",
]


class SseEventEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event_id: str = Field(..., min_length=1)
    thread_id: str = Field(..., min_length=1)
    request_id: str = Field(..., min_length=1)
    run_type: SseRunType
    event_type: SseEventType
    seq: int = Field(..., ge=1)
    created_at: datetime
    payload: dict[str, Any] = Field(default_factory=dict)


class TaskNode(BaseModel):
    model_config = ConfigDict(extra="forbid")

    client_node_id: str = Field(..., min_length=1)
    title: str = Field(..., min_length=1, max_length=160)
    description: str | None = Field(default=None, max_length=1000)
    verb: str = Field(..., min_length=1)
    estimated_minutes: int = Field(..., ge=0, le=43200)
    node_type: Literal["group", "action"]
    depends_on: list[str] = Field(default_factory=list)
    children: list["TaskNode"] = Field(default_factory=list, max_length=MAX_TASK_TREE_SIBLINGS)
    done_criteria: str | None = Field(default=None, max_length=1000)
    start_hint: str | None = Field(default=None, max_length=1000)
    fallback_action: str | None = Field(default=None, max_length=1000)

    @model_validator(mode="after")
    def validate_action_estimate(self) -> "TaskNode":
        if self.node_type == "action" and self.estimated_minutes < 1:
            raise ValueError("action estimated_minutes must be greater than or equal to 1")
        return self


class RoadmapPhase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    phase_id: str = Field(..., min_length=1, max_length=80)
    order: int = Field(..., ge=1, le=5)
    title: str = Field(..., min_length=1, max_length=80)
    objective: str = Field(..., min_length=1, max_length=300)
    status: RoadmapStatus


class CurrentPhase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    phase_id: str = Field(..., min_length=1, max_length=80)
    title: str = Field(..., min_length=1, max_length=80)
    objective: str = Field(..., min_length=1, max_length=300)
    completion_rule: Literal[
        "all_ai_actions_completed",
        "long_term_execution_gate",
    ] = "all_ai_actions_completed"
    estimated_duration_weeks: int | None = Field(default=None, ge=1, le=12)


EvidenceType = Literal["numeric", "artifact", "self_assessment"]
CheckpointOperator = Literal["gte", "lte", "exists"]


class PracticeLoopDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    loop_id: str = Field(..., min_length=1, max_length=80)
    title: str = Field(..., min_length=1, max_length=160)
    target_per_week: int = Field(..., ge=1, le=7)
    duration_weeks: int = Field(..., ge=1, le=12)
    done_criteria: str = Field(..., min_length=1, max_length=1000)


class OutcomeCheckpoint(BaseModel):
    model_config = ConfigDict(extra="forbid")

    checkpoint_id: str = Field(..., min_length=1, max_length=80)
    title: str = Field(..., min_length=1, max_length=160)
    evidence_type: EvidenceType
    unit: str | None = Field(default=None, max_length=40)
    operator: CheckpointOperator
    target_value: float | None = None

    @model_validator(mode="after")
    def validate_target(self) -> "OutcomeCheckpoint":
        if self.evidence_type == "artifact" and self.operator != "exists":
            raise ValueError("artifact checkpoint operator must be exists")
        if self.evidence_type != "artifact" and self.target_value is None:
            raise ValueError("numeric and self_assessment checkpoints require target_value")
        if (
            self.evidence_type == "self_assessment"
            and not 1 <= float(self.target_value) <= 5
        ):
            raise ValueError("self_assessment target_value must be between 1 and 5")
        return self


class PhaseGate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    process_threshold: Literal[0.8] = 0.8
    outcome_rule: Literal["all_required"] = "all_required"


class PlanningContext(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1, 2] = 1
    intent_type: Literal["long_term_growth", "exploration_decision"]
    time_horizon: TimeHorizon
    roadmap: list[RoadmapPhase] = Field(..., min_length=3, max_length=5)
    current_phase: CurrentPhase | None
    next_action_client_node_id: str | None = Field(default=None, max_length=160)
    practice_loops: list[PracticeLoopDefinition] = Field(
        default_factory=list,
        max_length=2,
    )
    outcome_checkpoints: list[OutcomeCheckpoint] = Field(
        default_factory=list,
        max_length=2,
    )
    phase_gate: PhaseGate | None = None

    @model_validator(mode="after")
    def validate_roadmap_state(self) -> "PlanningContext":
        orders = [phase.order for phase in self.roadmap]
        if orders != list(range(1, len(self.roadmap) + 1)):
            raise ValueError("roadmap order must be continuous from 1")

        phase_ids = [phase.phase_id for phase in self.roadmap]
        if len(set(phase_ids)) != len(phase_ids):
            raise ValueError("roadmap phase_id must be unique")

        current = [phase for phase in self.roadmap if phase.status == "current"]
        if self.current_phase is None:
            if any(phase.status != "completed" for phase in self.roadmap):
                raise ValueError("roadmap must be completed when current_phase is null")
        else:
            if len(current) != 1:
                raise ValueError("exactly one current roadmap phase is required")
            if current[0].phase_id != self.current_phase.phase_id:
                raise ValueError("current_phase must match the current roadmap phase")
            if (
                current[0].title != self.current_phase.title
                or current[0].objective != self.current_phase.objective
            ):
                raise ValueError("current_phase fields must match roadmap")

        if self.schema_version == 1:
            if self.practice_loops or self.outcome_checkpoints or self.phase_gate is not None:
                raise ValueError(
                    "schema version 1 cannot contain long-term execution fields"
                )
            if (
                self.current_phase
                and self.current_phase.completion_rule
                != "all_ai_actions_completed"
            ):
                raise ValueError("schema version 1 requires all_ai_actions_completed")
            if (
                self.current_phase
                and self.current_phase.estimated_duration_weeks is not None
            ):
                raise ValueError(
                    "schema version 1 cannot define estimated_duration_weeks"
                )
            return self

        if self.intent_type != "long_term_growth":
            raise ValueError("schema version 2 is only valid for long_term_growth")
        if (
            self.current_phase
            and self.current_phase.completion_rule != "long_term_execution_gate"
        ):
            raise ValueError("schema version 2 requires long_term_execution_gate")
        if self.current_phase and self.current_phase.estimated_duration_weeks is None:
            raise ValueError("schema version 2 requires estimated_duration_weeks")
        if not self.outcome_checkpoints:
            raise ValueError("schema version 2 requires at least one outcome checkpoint")
        if self.current_phase and any(
            loop.duration_weeks > self.current_phase.estimated_duration_weeks
            for loop in self.practice_loops
        ):
            raise ValueError(
                "practice loop duration cannot exceed current phase duration"
            )
        if len({loop.loop_id for loop in self.practice_loops}) != len(
            self.practice_loops
        ):
            raise ValueError("practice loop_id must be unique")
        if len(
            {item.checkpoint_id for item in self.outcome_checkpoints}
        ) != len(self.outcome_checkpoints):
            raise ValueError("checkpoint_id must be unique")
        return self


class TaskTree(BaseModel):
    model_config = ConfigDict(extra="forbid")

    root: TaskNode
    summary: str = Field(..., max_length=500)
    assumptions: list[str] = Field(default_factory=list)
    planning_context: PlanningContext | None = None

    @model_validator(mode="after")
    def validate_tree_limits(self) -> "TaskTree":
        max_depth, total_nodes = _measure_tree(self.root)
        if max_depth > MAX_TASK_TREE_DEPTH:
            raise ValueError(f"TaskTree maximum depth is {MAX_TASK_TREE_DEPTH}")
        if total_nodes > MAX_TASK_TREE_NODES:
            raise ValueError(f"TaskTree maximum node count is {MAX_TASK_TREE_NODES}")
        return self


def _measure_tree(root: TaskNode) -> tuple[int, int]:
    def walk(node: TaskNode, depth: int) -> tuple[int, int]:
        max_depth = depth
        total = 1
        for child in node.children:
            child_depth, child_total = walk(child, depth + 1)
            max_depth = max(max_depth, child_depth)
            total += child_total
        return max_depth, total

    return walk(root, 1)


class IntentCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    intent_text: str = Field(..., min_length=1, max_length=2000)
    preferred_provider: str = Field(default="native", max_length=64)
    planner_provider: Literal["openai", "deepseek", "xiaomi"] | None = None
    planner_model: str | None = Field(default=None, min_length=1, max_length=128)


class IntentCreateResponse(BaseModel):
    thread_id: str
    request_id: UUID
    status: Literal["running"]
    events_url: str


class IntentProfile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    intent_type: IntentType
    time_horizon: TimeHorizon
    confidence_score: float = Field(..., ge=0.0, le=1.0)


class ConfirmationAction(str, Enum):
    approve = "approve"
    edit = "edit"
    refine = "refine"
    reject = "reject"


class ConfirmationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_id: str = Field(..., min_length=8, max_length=128)
    action: ConfirmationAction
    task_tree: TaskTree | None = None
    feedback: str | None = Field(default=None, max_length=2000)
    reason: str | None = Field(default=None, max_length=1000)


class ConfirmationResponse(BaseModel):
    thread_id: str
    request_id: str
    status: str


class PhaseReviewUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    evidence: dict[str, dict[str, object]] = Field(default_factory=dict)
    difficulty: str | None = Field(default=None, max_length=2000)
    next_capacity: str | None = Field(default=None, max_length=1000)
    early_review_requested: bool = False


class PracticeLoopAdjustmentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    loop_id: UUID
    title: str | None = Field(default=None, min_length=1, max_length=160)
    target_per_week: int | None = Field(default=None, ge=1, le=7)
    done_criteria: str | None = Field(default=None, min_length=1, max_length=1000)

    @model_validator(mode="after")
    def require_adjustment(self) -> "PracticeLoopAdjustmentRequest":
        if not {"title", "target_per_week", "done_criteria"} & self.model_fields_set:
            raise ValueError("At least one practice loop field must be adjusted")
        return self


class PhaseReviewDecisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: Literal["proceed", "extend", "adjust", "override"]
    override_reason: str | None = Field(default=None, max_length=1000)
    extension_weeks: int | None = Field(default=None, ge=1, le=12)
    adjustments: list[PracticeLoopAdjustmentRequest] = Field(
        default_factory=list,
        max_length=2,
    )


class NextPhaseRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_id: UUID


class NextPhaseResponse(BaseModel):
    thread_id: str
    request_id: UUID
    status: str
    events_url: str


class ThreadSnapshot(BaseModel):
    thread_id: str
    status: str
    state_version: int = Field(..., ge=0)
    last_event_id: str | None
    server_time: datetime
    intent_text: str
    task_tree: TaskTree | None = None
    interrupt_payload: dict[str, Any] | None = None
    latest_checkpoint_id: str | None = None
    long_term_execution: "LongTermExecutionSnapshot | None" = None


class PracticeLoopProgressResponse(BaseModel):
    loop_id: UUID
    loop_key: str
    title: str
    done_criteria: str
    target_per_week: int
    current_week_completed: int
    total_completed: int
    required_completions: int
    estimated_end: date
    status: Literal["active", "paused", "completed", "superseded"]
    can_schedule_today: bool
    active_occurrence_task_id: UUID | None


class PhaseReviewResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    phase_id: str
    status: Literal["draft", "finalized"]
    recommendation: Literal["ready", "partial", "not_ready", "overridden"]
    decision: Literal["proceed", "extend", "adjust", "override"] | None
    evidence: dict[str, dict[str, object]]
    difficulty: str | None
    next_capacity: str | None
    override_reason: str | None
    statistics: dict[str, object]
    created_at: datetime
    updated_at: datetime


class LongTermExecutionSnapshot(BaseModel):
    phase_id: str
    recommendation: Literal["ready", "partial", "not_ready", "overridden"]
    review_available: bool
    one_off_ready: bool
    process_ready: bool
    outcome_ready: bool
    loops: list[PracticeLoopProgressResponse]
    active_review: PhaseReviewResponse | None
    latest_finalized_review: PhaseReviewResponse | None
    review_history: list[PhaseReviewResponse]


TaskViewBucket = Literal["planned", "my_day", "backlog"]
TaskStatus = Literal["draft", "active", "today", "completed", "archived"]
TASK_UPDATE_NON_NULL_FIELDS = ("title", "status", "view_bucket", "is_in_my_day", "sort_order")


class TaskCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(..., min_length=1, max_length=160)
    description: str | None = Field(default=None, max_length=1000)
    view_bucket: TaskViewBucket = "planned"
    is_in_my_day: bool = False
    parent_task_id: UUID | None = None
    thread_id: str | None = Field(default=None, min_length=1, max_length=128)


class TaskResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    user_id: UUID
    thread_id: str
    parent_task_id: UUID | None
    client_node_id: str
    title: str
    description: str | None
    node_type: Literal["group", "action"]
    status: str
    view_bucket: str
    is_in_my_day: bool
    estimated_minutes: int | None
    sort_order: int
    done_criteria: str | None = None
    start_hint: str | None = None
    fallback_action: str | None = None
    source: str | None = None
    phase_id: str | None = None
    phase_order: int | None = None
    practice_loop_id: UUID | None = None

    @model_validator(mode="before")
    @classmethod
    def extract_action_quality_from_metadata(cls, data: Any) -> Any:
        if isinstance(data, dict):
            payload = dict(data)
            metadata = payload.get("metadata_") or payload.get("metadata") or {}
        else:
            payload = {
                field: getattr(data, field)
                for field in cls.model_fields
                if field not in TASK_METADATA_FIELDS and hasattr(data, field)
            }
            metadata = getattr(data, "metadata_", {}) or {}

        if isinstance(metadata, dict):
            for field in ACTION_QUALITY_FIELDS:
                if payload.get(field) is None:
                    payload[field] = _metadata_string_or_none(metadata.get(field))
            for field in ("source", "phase_id"):
                if payload.get(field) is None:
                    payload[field] = _metadata_string_or_none(metadata.get(field))
            if payload.get("phase_order") is None:
                payload["phase_order"] = _metadata_int_or_none(metadata.get("phase_order"))
            if payload.get("practice_loop_id") is None:
                payload["practice_loop_id"] = _metadata_uuid_or_none(
                    metadata.get("practice_loop_id")
                )
        return payload


class NextPhaseCommitReceipt(BaseModel):
    thread_id: str
    request_id: str
    status: Literal[
        "confirmed",
        "incomplete",
        "running",
        "awaiting_confirmation",
        "confirming",
        "cancelled",
        "failed",
        "unknown",
    ]
    current_phase_id: str | None
    task_tree: TaskTree | None
    tasks: list[TaskResponse]


class TaskUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str | None = Field(
        default=None,
        min_length=1,
        max_length=160,
        description="Omit to keep unchanged. Explicit null is rejected.",
    )
    description: str | None = Field(
        default=None,
        max_length=1000,
        description="Omit to keep unchanged. Explicit null clears the description.",
    )
    status: TaskStatus | None = Field(
        default=None,
        description="Omit to keep unchanged. Explicit null is rejected.",
    )
    view_bucket: TaskViewBucket | None = Field(
        default=None,
        description="Omit to keep unchanged. Explicit null is rejected.",
    )
    is_in_my_day: bool | None = Field(
        default=None,
        description="Omit to keep unchanged. Explicit null is rejected.",
    )
    estimated_minutes: int | None = Field(
        default=None,
        ge=1,
        le=43200,
        description="Omit to keep unchanged. Explicit null clears the estimate.",
    )
    sort_order: int | None = Field(
        default=None,
        ge=0,
        description="Omit to keep unchanged. Explicit null is rejected.",
    )

    @model_validator(mode="after")
    def require_at_least_one_change(self) -> "TaskUpdateRequest":
        if not self.model_fields_set:
            raise ValueError("At least one task field must be provided")
        return self

    @model_validator(mode="after")
    def reject_null_for_required_columns(self) -> "TaskUpdateRequest":
        null_fields = [
            field
            for field in TASK_UPDATE_NON_NULL_FIELDS
            if field in self.model_fields_set and getattr(self, field) is None
        ]
        if null_fields:
            raise ValueError(f"{', '.join(null_fields)} cannot be null")
        return self


TaskNode.model_rebuild()


def _metadata_string_or_none(value: Any) -> str | None:
    return value if isinstance(value, str) else None


def _metadata_int_or_none(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _metadata_uuid_or_none(value: Any) -> UUID | None:
    if isinstance(value, UUID):
        return value
    if not isinstance(value, str):
        return None
    try:
        return UUID(value)
    except ValueError:
        return None
