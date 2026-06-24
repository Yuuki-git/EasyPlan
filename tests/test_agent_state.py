from app.agents.state import AgentState, prune_state


def test_prune_state_removes_prompt_raw_response_and_long_reasoning():
    state: AgentState = {
        "user_id": "user_1",
        "thread_id": "thread_1",
        "intent_text": "写论文",
        "prompt": "system prompt should not be checkpointed",
        "raw_llm_response": "raw response should not be checkpointed",
        "reasoning_events": [
            {"message": f"step {index}", "raw": "x" * 200}
            for index in range(25)
        ],
        "task_tree": {"root": {"title": "写论文"}},
    }

    pruned = prune_state(state)

    assert "prompt" not in pruned
    assert "raw_llm_response" not in pruned
    assert len(pruned["reasoning_events"]) == 20
    assert all(set(event) <= {"message", "code", "node"} for event in pruned["reasoning_events"])
    assert pruned["task_tree"] == {"root": {"title": "写论文"}}


def test_prune_state_summarizes_overlong_intent_text():
    state: AgentState = {
        "user_id": "user_1",
        "thread_id": "thread_1",
        "intent_text": "a" * 3000,
    }

    pruned = prune_state(state)

    assert len(pruned["intent_text"]) < 2100
    assert pruned["intent_text"].endswith("...[truncated]")


def test_prune_state_preserves_intent_profile():
    state: AgentState = {
        "user_id": "user_1",
        "thread_id": "thread_1",
        "intent_profile": {
            "intent_type": "short_term_delivery",
            "time_horizon": "hours",
            "confidence_score": 0.86,
        },
    }

    pruned = prune_state(state)

    assert pruned["intent_profile"] == state["intent_profile"]


def test_prune_state_preserves_phase_planning_context_without_prompt_payloads():
    state: AgentState = {
        "planning_mode": "next_phase",
        "phase_request_id": "11111111-1111-1111-1111-111111111111",
        "committed_task_tree": {"summary": "Committed tree"},
        "current_phase_task_summary": "2/2 AI actions completed",
        "prompt": "must not persist",
    }

    pruned = prune_state(state)

    assert pruned["planning_mode"] == "next_phase"
    assert pruned["phase_request_id"] == state["phase_request_id"]
    assert pruned["committed_task_tree"] == state["committed_task_tree"]
    assert pruned["current_phase_task_summary"] == state["current_phase_task_summary"]
    assert "prompt" not in pruned
