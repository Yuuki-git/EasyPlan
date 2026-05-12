import asyncio
import json
from types import SimpleNamespace

import pytest

from app.api.schemas import IntentProfile, TaskTree
from app.services.llm_service import (
    DeepSeekPlannerClient,
    LLMStructuredOutputError,
    ListReasoningSink,
    ListUsageSink,
    OpenAIPlannerClient,
    XiaomiMiMoPlannerClient,
    create_planner_client,
)


EXPECTED_USER_VISIBLE_REASONING_MESSAGES = [
    "正在分析您的核心目标...",
    "正在将目标拆解为可执行的微行动...",
    "正在为您评估每项任务的时间与依赖关系...",
    "计划生成完毕，请查阅。",
]


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
    def __init__(self, output_parsed, usage=None):
        self.output_parsed = output_parsed
        self.usage = usage
        self.calls: list[dict] = []

    async def parse(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(output_parsed=self.output_parsed, usage=self.usage)


class FakeOpenAIClient:
    def __init__(self, output_parsed, usage=None):
        self.responses = FakeResponses(output_parsed, usage=usage)


def test_openai_planner_uses_task_tree_structured_output_and_emits_safe_events():
    fake_openai = FakeOpenAIClient(
        TaskTree.model_validate(_valid_task_tree()),
        usage=SimpleNamespace(input_tokens=101, output_tokens=57, total_tokens=158),
    )
    planner = OpenAIPlannerClient(client=fake_openai, model="gpt-4o-2024-08-06")
    reasoning_sink = ListReasoningSink()
    usage_sink = ListUsageSink()

    result = asyncio.run(
        planner.create_plan(
            "Create a launch plan",
            reasoning_sink=reasoning_sink,
            usage_sink=usage_sink,
        )
    )

    parse_call = fake_openai.responses.calls[0]
    assert parse_call["text_format"] is TaskTree
    assert parse_call["model"] == "gpt-4o-2024-08-06"
    assert "CRITICAL: You MUST respond in the EXACT same language" in parse_call["input"][0]["content"]
    assert "If the user writes in Chinese" in parse_call["input"][0]["content"]
    assert result["root"]["children"][0]["client_node_id"] == "task-1"
    assert [event["code"] for event in reasoning_sink.events] == [
        "LLM_PLANNING_STARTED",
        "LLM_SCHEMA_LOCKED",
        "LLM_PLAN_PARSED",
        "LLM_USAGE_RECORDED",
    ]
    assert [event["message"] for event in reasoning_sink.events] == EXPECTED_USER_VISIBLE_REASONING_MESSAGES
    assert all("JSON" not in event["message"] for event in reasoning_sink.events)
    assert all("schema" not in event["message"].lower() for event in reasoning_sink.events)
    assert all("token" not in event["message"].lower() for event in reasoning_sink.events)
    assert all("raw" not in event for event in reasoning_sink.events)
    assert usage_sink.records[0].provider == "openai"
    assert usage_sink.records[0].model == "gpt-4o-2024-08-06"
    assert usage_sink.records[0].input_tokens == 101
    assert usage_sink.records[0].output_tokens == 57
    assert usage_sink.records[0].total_tokens == 158


def test_openai_planner_rejects_missing_structured_output():
    fake_openai = FakeOpenAIClient(None)
    planner = OpenAIPlannerClient(client=fake_openai, model="gpt-4o-2024-08-06")

    with pytest.raises(LLMStructuredOutputError):
        asyncio.run(planner.create_plan("Create a launch plan"))


def test_openai_profiles_intent_with_structured_output_and_usage():
    fake_openai = FakeOpenAIClient(
        IntentProfile(
            intent_type="short_term_delivery",
            time_horizon="hours",
            confidence_score=0.92,
        ),
        usage=SimpleNamespace(input_tokens=17, output_tokens=9, total_tokens=26),
    )
    planner = OpenAIPlannerClient(client=fake_openai, model="gpt-4o-2024-08-06")
    usage_sink = ListUsageSink()

    result = asyncio.run(
        planner.profile_intent(
            "Finish the business plan by 4pm",
            usage_sink=usage_sink,
        )
    )

    parse_call = fake_openai.responses.calls[0]
    assert parse_call["text_format"] is IntentProfile
    assert parse_call["model"] == "gpt-4o-2024-08-06"
    assert result == {
        "intent_type": "short_term_delivery",
        "time_horizon": "hours",
        "confidence_score": 0.92,
    }
    assert usage_sink.records[0].operation == "planner.profile_intent"
    assert usage_sink.records[0].total_tokens == 26


class FakeChatCompletions:
    def __init__(self, content: str):
        self.content = content
        self.calls: list[dict] = []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=self.content))],
            usage=SimpleNamespace(prompt_tokens=31, completion_tokens=43, total_tokens=74),
        )


class FakeChatClient:
    def __init__(self, content: str):
        self.chat = SimpleNamespace(completions=FakeChatCompletions(content))


def test_deepseek_planner_uses_json_mode_and_pydantic_validation():
    fake_deepseek = FakeChatClient(json.dumps(_valid_task_tree()))
    planner = DeepSeekPlannerClient(client=fake_deepseek, model="deepseek-chat")
    reasoning_sink = ListReasoningSink()
    usage_sink = ListUsageSink()

    result = asyncio.run(
        planner.create_plan(
            "Create a launch plan",
            reasoning_sink=reasoning_sink,
            usage_sink=usage_sink,
        )
    )

    create_call = fake_deepseek.chat.completions.calls[0]
    assert create_call["model"] == "deepseek-chat"
    assert create_call["response_format"] == {"type": "json_object"}
    assert "json" in create_call["messages"][0]["content"].lower()
    assert "TaskTree" in create_call["messages"][0]["content"]
    assert "CRITICAL: You MUST respond in the EXACT same language" in create_call["messages"][0]["content"]
    assert "If the user writes in Chinese" in create_call["messages"][0]["content"]
    assert result["root"]["children"][0]["client_node_id"] == "task-1"
    assert [event["message"] for event in reasoning_sink.events] == EXPECTED_USER_VISIBLE_REASONING_MESSAGES
    assert usage_sink.records[0].provider == "deepseek"
    assert usage_sink.records[0].input_tokens == 31
    assert usage_sink.records[0].output_tokens == 43


def test_deepseek_planner_accepts_zero_estimate_for_group_container():
    task_tree = _valid_task_tree()
    task_tree["root"]["estimated_minutes"] = 0
    fake_deepseek = FakeChatClient(json.dumps(task_tree))
    planner = DeepSeekPlannerClient(client=fake_deepseek, model="deepseek-chat")

    result = asyncio.run(planner.create_plan("Create a launch plan"))

    assert result["root"]["estimated_minutes"] == 0
    assert result["root"]["children"][0]["estimated_minutes"] == 2


def test_deepseek_planner_rejects_json_that_does_not_match_task_tree():
    fake_deepseek = FakeChatClient(json.dumps({"root": {"title": "missing fields"}}))
    planner = DeepSeekPlannerClient(client=fake_deepseek, model="deepseek-chat")

    with pytest.raises(LLMStructuredOutputError):
        asyncio.run(planner.create_plan("Create a launch plan"))


def test_deepseek_profiles_intent_with_json_mode():
    fake_deepseek = FakeChatClient(
        json.dumps(
            {
                "intent_type": "exploration_decision",
                "time_horizon": "days",
                "confidence_score": 0.77,
            }
        )
    )
    planner = DeepSeekPlannerClient(client=fake_deepseek, model="deepseek-chat")

    result = asyncio.run(planner.profile_intent("Should I change careers?"))

    create_call = fake_deepseek.chat.completions.calls[0]
    assert create_call["response_format"] == {"type": "json_object"}
    assert "IntentProfile" in create_call["messages"][0]["content"]
    assert result["intent_type"] == "exploration_decision"


def test_xiaomi_mimo_planner_uses_json_mode_and_records_usage():
    fake_mimo = FakeChatClient(json.dumps(_valid_task_tree()))
    planner = XiaomiMiMoPlannerClient(client=fake_mimo, model="mimo-v2-flash")
    reasoning_sink = ListReasoningSink()
    usage_sink = ListUsageSink()

    result = asyncio.run(
        planner.create_plan(
            "Create a launch plan",
            reasoning_sink=reasoning_sink,
            usage_sink=usage_sink,
        )
    )

    create_call = fake_mimo.chat.completions.calls[0]
    assert create_call["model"] == "mimo-v2-flash"
    assert create_call["response_format"] == {"type": "json_object"}
    assert "json" in create_call["messages"][0]["content"].lower()
    assert "TaskTree" in create_call["messages"][0]["content"]
    assert "CRITICAL: You MUST respond in the EXACT same language" in create_call["messages"][0]["content"]
    assert "If the user writes in Chinese" in create_call["messages"][0]["content"]
    assert result["root"]["children"][0]["client_node_id"] == "task-1"
    assert [event["message"] for event in reasoning_sink.events] == EXPECTED_USER_VISIBLE_REASONING_MESSAGES
    assert usage_sink.records[0].provider == "xiaomi"
    assert usage_sink.records[0].model == "mimo-v2-flash"
    assert usage_sink.records[0].total_tokens == 74


def test_xiaomi_mimo_planner_rejects_json_that_does_not_match_task_tree():
    fake_mimo = FakeChatClient(json.dumps({"root": {"title": "missing fields"}}))
    planner = XiaomiMiMoPlannerClient(client=fake_mimo, model="mimo-v2-flash")

    with pytest.raises(LLMStructuredOutputError):
        asyncio.run(planner.create_plan("Create a launch plan"))


def test_planner_factory_supports_openai_deepseek_and_xiaomi_mimo():
    assert isinstance(create_planner_client(provider="openai"), OpenAIPlannerClient)
    assert isinstance(create_planner_client(provider="deepseek"), DeepSeekPlannerClient)
    assert isinstance(create_planner_client(provider="xiaomi"), XiaomiMiMoPlannerClient)
    assert isinstance(create_planner_client(provider="xiaomi_mimo"), XiaomiMiMoPlannerClient)
