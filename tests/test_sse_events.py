from app.api.sse import format_sse_error_event


def test_format_sse_error_event_includes_code_message_and_state_version():
    event = format_sse_error_event(
        event_id="evt_001",
        state_version=7,
        code="TASK_TREE_VALIDATION_FAILED",
        message="task-a depends on missing-node",
    )

    assert event.startswith("id: evt_001\n")
    assert "event: error\n" in event
    assert '"state_version":7' in event
    assert '"code":"TASK_TREE_VALIDATION_FAILED"' in event
    assert event.endswith("\n\n")
