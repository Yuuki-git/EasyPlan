from typing import Any

from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from app.agents.nodes import (
    PlannerClient,
    RuleBasedPlannerClient,
    failed_validation_node,
    planner_node_factory,
    route_after_validation,
    task_tree_validator_node,
)
from app.agents.state import AgentState
from app.services.checkpoint_service import TenantAwareMemorySaver


def create_graph_config(user_id: str, thread_id: str) -> dict[str, dict[str, str]]:
    return {"configurable": {"user_id": user_id, "thread_id": thread_id}}


def human_review_node(state: AgentState) -> AgentState:
    decision = interrupt(
        {
            "type": "task_tree_review",
            "user_id": state["user_id"],
            "thread_id": state["thread_id"],
            "task_tree": state.get("task_tree"),
            "allowed_actions": ["approve", "edit", "refine", "reject"],
        }
    )

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
    return "end"


def build_task_graph(
    *,
    planner: PlannerClient | None = None,
    checkpointer: TenantAwareMemorySaver | None = None,
):
    planner_client = planner or RuleBasedPlannerClient()
    checkpoint_saver = checkpointer or TenantAwareMemorySaver()

    graph = StateGraph(AgentState)
    graph.add_node("planner", planner_node_factory(planner_client))
    graph.add_node("validator", task_tree_validator_node)
    graph.add_node("human_review", human_review_node)
    graph.add_node("failed_validation", failed_validation_node)

    graph.add_edge(START, "planner")
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
            "end": END,
        },
    )
    graph.add_edge("failed_validation", END)

    return graph.compile(checkpointer=checkpoint_saver)


def resume_with_human_input(action: str, **payload: Any):
    from langgraph.types import Command

    return Command(resume={"action": action, **payload})
