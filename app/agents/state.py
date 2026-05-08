from typing import Any, Literal, TypedDict


class AgentState(TypedDict, total=False):
    user_id: str
    thread_id: str
    intent_text: str
    route: Literal["create_plan", "query_status"]
    reasoning_events: list[dict[str, Any]]
    task_tree: dict[str, Any]
    validation_status: Literal["valid", "needs_replan", "failed"]
    validation_errors: list[str]
    replan_attempts: int
    human_decision: dict[str, Any]
    refinement_feedback: str
    request_id: str
    selected_provider: str
    error: dict[str, Any]
    prompt: str
    raw_llm_response: Any


MAX_INTENT_TEXT_CHARS = 2000
MAX_REASONING_EVENTS = 20
ALLOWED_REASONING_KEYS = {"message", "code", "node"}
DISALLOWED_CHECKPOINT_KEYS = {
    "prompt",
    "raw_llm_response",
}


def prune_state(state: AgentState) -> AgentState:
    """Return a checkpoint-safe state without prompts or raw reasoning payloads."""

    pruned: AgentState = {
        key: value
        for key, value in state.items()
        if key not in DISALLOWED_CHECKPOINT_KEYS
    }

    intent_text = pruned.get("intent_text")
    if isinstance(intent_text, str) and len(intent_text) > MAX_INTENT_TEXT_CHARS:
        pruned["intent_text"] = f"{intent_text[:MAX_INTENT_TEXT_CHARS]}...[truncated]"

    reasoning_events = pruned.get("reasoning_events")
    if reasoning_events:
        summarized: list[dict[str, Any]] = []
        for event in reasoning_events[-MAX_REASONING_EVENTS:]:
            summarized.append(
                {
                    key: value
                    for key, value in event.items()
                    if key in ALLOWED_REASONING_KEYS
                }
            )
        pruned["reasoning_events"] = summarized

    return pruned
