from datetime import datetime
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


MAX_TASK_TREE_DEPTH = 8
MAX_TASK_TREE_SIBLINGS = 20
MAX_TASK_TREE_NODES = 200


class TaskNode(BaseModel):
    model_config = ConfigDict(extra="forbid")

    client_node_id: str = Field(..., min_length=1)
    title: str = Field(..., min_length=1, max_length=160)
    description: str | None = Field(default=None, max_length=1000)
    verb: str = Field(..., min_length=1)
    estimated_minutes: int = Field(..., ge=1, lt=5)
    node_type: Literal["group", "action"]
    depends_on: list[str] = Field(default_factory=list)
    children: list["TaskNode"] = Field(default_factory=list, max_length=MAX_TASK_TREE_SIBLINGS)


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
    preferred_provider: str = Field(default="todoist", max_length=64)
    planner_provider: Literal["openai", "deepseek", "xiaomi"] = "openai"
    planner_model: str | None = Field(default=None, min_length=1, max_length=128)


class IntentCreateResponse(BaseModel):
    thread_id: str
    status: Literal["running"]
    events_url: str


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


class OAuthStartResponse(BaseModel):
    provider: str
    authorization_url: str
    state: str
    expires_at: datetime


class IntegrationStatus(BaseModel):
    provider: str
    display_name: str
    status: str
    is_integrated: bool
    external_account_id: str | None = None


TaskNode.model_rebuild()
