import json
from typing import Any


def format_sse_event(event: str, data: dict[str, Any], event_id: str | None = None) -> str:
    lines: list[str] = []
    if event_id:
        lines.append(f"id: {event_id}")
    lines.append(f"event: {event}")
    lines.append(f"data: {json.dumps(data, ensure_ascii=False, separators=(',', ':'))}")
    return "\n".join(lines) + "\n\n"


def format_sse_error_event(
    *,
    event_id: str,
    state_version: int,
    code: str,
    message: str,
) -> str:
    return format_sse_event(
        "agent_error",
        {
            "state_version": state_version,
            "code": code,
            "message": message,
        },
        event_id=event_id,
    )
