from datetime import datetime
from enum import Enum
from uuid import UUID
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


MAX_TASK_TREE_DEPTH = 8
MAX_TASK_TREE_SIBLINGS = 20
MAX_TASK_TREE_NODES = 200
ACTION_QUALITY_FIELDS = ("done_criteria", "start_hint", "fallback_action")


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


class TaskTree(BaseModel):
    model_config = ConfigDict(extra="forbid")

    root: TaskNode
    summary: str = Field(..., max_length=500)
    assumptions: list[str] = Field(default_factory=list)

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
    status: Literal["running"]
    events_url: str


IntentType = Literal[
    "long_term_growth",
    "short_term_delivery",
    "context_checklist",
    "exploration_decision",
]
TimeHorizon = Literal["minutes", "hours", "days", "weeks", "months"]


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


class ThreadSnapshot(BaseModel):
    thread_id: str
    status: str
    state_version: int = Field(..., ge=0)
    last_event_id: str | None
    server_time: datetime
    intent_text: str
    task_tree: dict[str, Any] | None = None
    interrupt_payload: dict[str, Any] | None = None
    latest_checkpoint_id: str | None = None


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
                if field not in ACTION_QUALITY_FIELDS and hasattr(data, field)
            }
            metadata = getattr(data, "metadata_", {}) or {}

        if isinstance(metadata, dict):
            for field in ACTION_QUALITY_FIELDS:
                if payload.get(field) is None:
                    payload[field] = _metadata_string_or_none(metadata.get(field))
        return payload


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
