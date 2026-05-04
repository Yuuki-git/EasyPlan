import asyncio
from types import SimpleNamespace

import pytest

from app.api.schemas import TaskTree
from app.services.llm_service import (
    LLMStructuredOutputError,
    ListReasoningSink,
    OpenAIPlannerClient,
)


def _valid_task_tree() -> dict:
    return {
        "root": {
            "client_node_id": "root",
            "title": "Plan launch",
            "description": None,
            "verb": "Plan",
            "estimated_minutes": 1,
            "node_type": "group",
            "depends_on": [],
            "children": [
                {
                    "client_node_id": "task-1",
                    "title": "Open notes",
                    "description": None,
                    "verb": "Open",
                    "estimated_minutes": 2,
                    "node_type": "action",
                    "depends_on": [],
                    "children": [],
                }
            ],
        },
        "summary": "Start with notes",
        "assumptions": [],
    }


class FakeResponses:
    def __init__(self, output_parsed):
        self.output_parsed = output_parsed
        self.calls: list[dict] = []

    async def parse(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(output_parsed=self.output_parsed)


class FakeOpenAIClient:
    def __init__(self, output_parsed):
        self.responses = FakeResponses(output_parsed)


def test_openai_planner_uses_task_tree_structured_output_and_emits_safe_events():
    fake_openai = FakeOpenAIClient(TaskTree.model_validate(_valid_task_tree()))
    planner = OpenAIPlannerClient(client=fake_openai, model="gpt-4o-2024-08-06")
    reasoning_sink = ListReasoningSink()

    result = asyncio.run(planner.create_plan("Create a launch plan", reasoning_sink=reasoning_sink))

    parse_call = fake_openai.responses.calls[0]
    assert parse_call["text_format"] is TaskTree
    assert parse_call["model"] == "gpt-4o-2024-08-06"
    assert result["root"]["children"][0]["client_node_id"] == "task-1"
    assert [event["code"] for event in reasoning_sink.events] == [
        "LLM_PLANNING_STARTED",
        "LLM_SCHEMA_LOCKED",
        "LLM_PLAN_PARSED",
    ]
    assert all("raw" not in event for event in reasoning_sink.events)


def test_openai_planner_rejects_missing_structured_output():
    fake_openai = FakeOpenAIClient(None)
    planner = OpenAIPlannerClient(client=fake_openai, model="gpt-4o-2024-08-06")

    with pytest.raises(LLMStructuredOutputError):
        asyncio.run(planner.create_plan("Create a launch plan"))
