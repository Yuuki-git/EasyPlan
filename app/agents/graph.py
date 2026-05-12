from typing import Any

from langchain_core.runnables import RunnableConfig
from langgraph.config import var_child_runnable_config
from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from app.agents.nodes import (
    IntentProfilerClient,
    PlannerClient,
    RuleBasedIntentProfilerClient,
    RuleBasedPlannerClient,
    failed_validation_node,
    intent_profiler_node_factory,
    persist_internal_tasks_node,
    planner_node_factory,
    route_after_validation,
    task_tree_validator_node,
)
from app.agents.state import AgentState
from app.services.checkpoint_service import TenantAwareMemorySaver


def create_graph_config(user_id: str, thread_id: str) -> dict[str, dict[str, str]]:
    return {"configurable": {"user_id": user_id, "thread_id": thread_id}}


async def human_review_node(state: AgentState, config: RunnableConfig) -> AgentState:
    # Python 3.10 does not reliably propagate LangGraph runnable config into async interrupt nodes.
    config_token = var_child_runnable_config.set(config)
    try:
        decision = interrupt(
            {
                "type": "task_tree_review",
                "user_id": state["user_id"],
                "thread_id": state["thread_id"],
                "task_tree": state.get("task_tree"),
                "allowed_actions": ["approve", "edit", "refine", "reject"],
            }
        )
    finally:
        var_child_runnable_config.reset(config_token)

    action = decision.get("action") if isinstance(decision, dict) else None
    if action == "refine":
        return {
            "human_decision": decision,
            "refinement_feedback": decision.get("feedback", ""),
            "validation_status": "needs_replan",
        }
    if action == "edit":
        return {
            "human_decision": decision,
            "task_tree": decision.get("task_tree"),
            "validation_status": "needs_replan",
        }
    if action == "reject":
        return {
            "human_decision": decision,
            "error": {"code": "PLAN_REJECTED", "message": decision.get("reason", "rejected")},
        }
    return {
        "human_decision": decision,
        "request_id": decision.get("request_id") if isinstance(decision, dict) else None,
    }


def route_after_human_review(state: AgentState) -> str:
    action = state.get("human_decision", {}).get("action")
    if action == "refine":
        return "planner"
    if action == "edit":
        return "validator"
    if action == "reject":
        return "end"
    if action == "approve":
        return "persist_tasks"
    return "end"


def build_task_graph(
    *,
    planner: PlannerClient | None = None,
    intent_profiler: IntentProfilerClient | None = None,
    checkpointer: TenantAwareMemorySaver | None = None,
):
    planner_client = planner or RuleBasedPlannerClient()
    intent_profiler_client = intent_profiler or (
        planner_client
        if hasattr(planner_client, "profile_intent")
        else RuleBasedIntentProfilerClient()
    )
    checkpoint_saver = checkpointer or TenantAwareMemorySaver()

    graph = StateGraph(AgentState)
    graph.add_node("intent_profiler", intent_profiler_node_factory(intent_profiler_client))
    graph.add_node("planner", planner_node_factory(planner_client))
    graph.add_node("validator", task_tree_validator_node)
    graph.add_node("human_review", human_review_node)
    graph.add_node("persist_tasks", persist_internal_tasks_node)
    graph.add_node("failed_validation", failed_validation_node)

    graph.add_edge(START, "intent_profiler")
    graph.add_edge("intent_profiler", "planner")
    graph.add_edge("planner", "validator")
    graph.add_conditional_edges(
        "validator",
        route_after_validation,
        {
            "human_review": "human_review",
            "planner": "planner",
            "failed": "failed_validation",
        },
    )
    graph.add_conditional_edges(
        "human_review",
        route_after_human_review,
        {
            "planner": "planner",
            "validator": "validator",
            "persist_tasks": "persist_tasks",
            "end": END,
        },
    )
    graph.add_edge("persist_tasks", END)
    graph.add_edge("failed_validation", END)

    return graph.compile(checkpointer=checkpoint_saver)


def resume_with_human_input(action: str, **payload: Any):
    from langgraph.types import Command

    return Command(resume={"action": action, **payload})
