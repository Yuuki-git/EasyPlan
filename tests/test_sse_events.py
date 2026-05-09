from app.api.sse import format_sse_error_event


def test_format_sse_error_event_uses_agent_error_event_name():
    event = format_sse_error_event(
        event_id="evt_001",
        state_version=7,
        code="TASK_TREE_VALIDATION_FAILED",
        message="task-a depends on missing-node",
    )

    assert event.startswith("id: evt_001\n")
    assert "event: agent_error\n" in event
    assert "event: error\n" not in event
    assert '"state_version":7' in event
    assert '"code":"TASK_TREE_VALIDATION_FAILED"' in event
    assert event.endswith("\n\n")
