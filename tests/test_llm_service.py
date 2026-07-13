import asyncio
import json
from types import SimpleNamespace

import pytest

from app.agents.nodes import build_planner_prompt
from app.api.schemas import IntentProfile, TaskTree
from app.services.llm_service import (
    DeepSeekPlannerClient,
    LLMStructuredOutputError,
    ListReasoningSink,
    ListUsageSink,
    OpenAIPlannerClient,
    XiaomiMiMoPlannerClient,
    create_planner_client,
    _clean_json_response_text,
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


def _decision_task_tree() -> dict:
    tree = _valid_task_tree()
    tree["summary"] = "值得继续低成本探索，但在补齐信息前暂不购买。"
    tree["planning_context"] = {
        "schema_version": 1,
        "intent_type": "exploration_decision",
        "time_horizon": "days",
        "roadmap": [
            {
                "phase_id": "clarify",
                "order": 1,
                "title": "澄清",
                "objective": "补齐购买依据",
                "status": "current",
            },
            {
                "phase_id": "test",
                "order": 2,
                "title": "验证",
                "objective": "完成低成本验证",
                "status": "planned",
            },
            {
                "phase_id": "decide",
                "order": 3,
                "title": "决策",
                "objective": "记录购买结论",
                "status": "planned",
            },
        ],
        "current_phase": {
            "phase_id": "clarify",
            "title": "澄清",
            "objective": "补齐购买依据",
        },
        "next_action_client_node_id": "task-1",
    }
    tree["strategy_context"] = {
        "schema_version": 1,
        "strategy_type": "decision",
        "question": "现在是否应该购买这台二手车？",
        "options": ["继续验证", "暂缓购买"],
        "current_judgment": {
            "direction": "continue_exploring",
            "statement": "值得继续低成本探索，但在补齐信息前暂不购买。",
            "confidence": "medium",
        },
        "basis": [
            {
                "statement": "维修成本可能超过预算",
                "basis_type": "working_assumption",
            }
        ],
        "missing_information": ["真实车况和维修记录"],
        "experiments": [
            {
                "experiment_id": "inspect",
                "title": "预约检测",
                "hypothesis": "检测可以暴露主要车况风险",
                "success_signal": "取得一份检测报告",
                "effort_level": "low",
                "task_client_node_ids": ["task-1"],
            }
        ],
        "decision_gate": {
            "review_after": "取得检测报告后",
            "proceed_if": ["车况和维修预算均可接受"],
            "stop_if": ["发现重大事故或维修成本超预算"],
        },
    }
    return tree


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
    profile_prompt = parse_call["input"][0]["content"]
    assert parse_call["text_format"] is IntentProfile
    assert parse_call["model"] == "gpt-4o-2024-08-06"
    assert "context_checklist" in profile_prompt
    assert "跑腿杂事" in profile_prompt
    assert "买菜" in profile_prompt
    assert "拿快递" in profile_prompt
    assert "缴费" in profile_prompt
    assert "short_term_delivery" in profile_prompt
    assert "连续坐在电脑前" in profile_prompt
    assert "写代码" in profile_prompt
    assert "做 PPT" in profile_prompt
    assert "赶报告" in profile_prompt
    assert result == {
        "intent_type": "short_term_delivery",
        "time_horizon": "hours",
        "confidence_score": 0.92,
    }
    assert usage_sink.records[0].operation == "planner.profile_intent"
    assert usage_sink.records[0].total_tokens == 26


def test_intent_profile_prompt_uses_current_decision_window_for_exploration():
    prompt = build_planner_prompt(
        "我是否要考虑转行产品经理",
        intent_profile={"intent_type": "exploration_decision", "time_horizon": "days"},
    )
    from app.services.llm_service import _intent_profile_system_prompt

    profile_prompt = _intent_profile_system_prompt("DeepSeek")

    assert "current clarification and decision window" in profile_prompt
    assert "default to days" in profile_prompt
    assert "two-year cost" in profile_prompt
    assert "weekly available hours" in profile_prompt
    assert "探索决策" in prompt


class FakeChatCompletions:
    def __init__(self, content: str | list[str]):
        self.contents = list(content) if isinstance(content, list) else [content]
        self.calls: list[dict] = []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        index = min(len(self.calls) - 1, len(self.contents) - 1)
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=self.contents[index]))],
            usage=SimpleNamespace(prompt_tokens=31, completion_tokens=43, total_tokens=74),
        )


class FakeChatClient:
    def __init__(self, content: str | list[str]):
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


def test_deepseek_planner_parses_discriminated_delivery_strategy_context():
    task_tree = _valid_task_tree()
    task_tree["strategy_context"] = {
        "schema_version": 1,
        "strategy_type": "delivery",
        "deliverable": {
            "title": "Launch note",
            "format": "Document",
            "quality_bar": ["Contains the launch decision"],
        },
        "deadline": {"text": "No explicit deadline", "is_explicit": False},
        "time_plan": {
            "available_minutes": None,
            "planned_minutes": 2,
            "buffer_minutes": 0,
        },
        "scope": {"must_have": ["Decision"], "should_have": [], "can_cut": []},
        "workstreams": [
            {
                "workstream_id": "note",
                "title": "Launch note",
                "output": "Reviewable note",
                "task_client_node_ids": ["task-1"],
            }
        ],
        "critical_path_client_node_ids": ["task-1"],
    }
    fake_deepseek = FakeChatClient(json.dumps(task_tree))
    planner = DeepSeekPlannerClient(client=fake_deepseek, model="deepseek-chat")

    result = asyncio.run(planner.create_plan("Create a launch plan"))

    assert result["strategy_context"]["strategy_type"] == "delivery"
    assert result["strategy_context"]["workstreams"][0]["task_client_node_ids"] == [
        "task-1"
    ]


def test_deepseek_normalizes_low_information_decision_contract(monkeypatch):
    monkeypatch.setenv("EASYPLAN_STRATEGY_CONTEXT_ENABLED", "true")
    fake_deepseek = FakeChatClient(
        json.dumps(_decision_task_tree(), ensure_ascii=False)
    )
    planner = DeepSeekPlannerClient(client=fake_deepseek, model="deepseek-chat")
    prompt = build_planner_prompt(
        "我对这台二手车了解很少，不知道车况和维修成本，现在该不该买",
        intent_profile={
            "intent_type": "exploration_decision",
            "time_horizon": "days",
        },
    )

    result = asyncio.run(planner.create_plan(prompt))

    context = result["strategy_context"]
    assert context["current_judgment"]["confidence"] == "low"
    assert context["basis"][0]["basis_type"] == "working_assumption"
    assert context["basis"][0]["statement"].startswith("假设：")


def test_json_cleanup_preserves_normal_json():
    raw = json.dumps(_valid_task_tree(), ensure_ascii=False)

    assert _clean_json_response_text(raw) == raw


def test_xiaomi_mimo_planner_cleans_code_fence_json():
    fenced_json = f"```json\n{json.dumps(_valid_task_tree(), ensure_ascii=False)}\n```"
    fake_mimo = FakeChatClient(fenced_json)
    planner = XiaomiMiMoPlannerClient(client=fake_mimo, model="mimo-v2-flash")

    result = asyncio.run(planner.create_plan("Create a launch plan"))

    assert result["root"]["children"][0]["client_node_id"] == "task-1"
    assert len(fake_mimo.chat.completions.calls) == 1


def test_xiaomi_mimo_planner_removes_invalid_control_characters_before_json_parse():
    task_tree = _valid_task_tree()
    raw = json.dumps(task_tree, ensure_ascii=False)
    content = raw.replace("Open notes", "Open \x0bnotes")
    fake_mimo = FakeChatClient(content)
    planner = XiaomiMiMoPlannerClient(client=fake_mimo, model="mimo-v2-flash")

    result = asyncio.run(planner.create_plan("Create a launch plan"))

    assert result["root"]["children"][0]["title"] == "Open notes"
    assert len(fake_mimo.chat.completions.calls) == 1


def test_xiaomi_mimo_planner_retries_json_repair_without_replanning():
    broken = json.dumps(_valid_task_tree(), ensure_ascii=False).replace(
        '"summary": "Start with notes",',
        '"summary": "Start with notes"\n',
    )
    repaired = json.dumps(_valid_task_tree(), ensure_ascii=False)
    fake_mimo = FakeChatClient([broken, repaired])
    planner = XiaomiMiMoPlannerClient(client=fake_mimo, model="mimo-v2-flash")

    result = asyncio.run(planner.create_plan("Create a launch plan"))

    assert result["summary"] == "Start with notes"
    assert len(fake_mimo.chat.completions.calls) == 2
    repair_call = fake_mimo.chat.completions.calls[1]
    assert repair_call["temperature"] == 0.0
    assert repair_call["response_format"] == {"type": "json_object"}
    repair_prompt = "\n".join(message["content"] for message in repair_call["messages"])
    assert "Fix only JSON syntax" in repair_prompt
    assert "Do not replan" in repair_prompt
    assert "Expecting ',' delimiter" in repair_prompt
    assert broken in repair_prompt


def test_deepseek_planner_rejects_json_that_does_not_match_task_tree():
    fake_deepseek = FakeChatClient(json.dumps({"root": {"title": "missing fields"}}))
    planner = DeepSeekPlannerClient(client=fake_deepseek, model="deepseek-chat")

    with pytest.raises(LLMStructuredOutputError):
        asyncio.run(planner.create_plan("Create a launch plan"))


def test_deepseek_planner_normalizes_context_checklist_actions_into_group():
    task_tree = _valid_task_tree()
    task_tree["root"]["title"] = "上学前准备"
    task_tree["root"]["children"] = [
        {
            "client_node_id": "uniform",
            "title": "准备校服",
            "description": None,
            "verb": "准备",
            "estimated_minutes": 5,
            "node_type": "action",
            "depends_on": [],
            "children": [],
        },
        {
            "client_node_id": "water",
            "title": "装好水杯",
            "description": None,
            "verb": "装好",
            "estimated_minutes": 3,
            "node_type": "action",
            "depends_on": [],
            "children": [],
        },
        {
            "client_node_id": "homework",
            "title": "检查作业本",
            "description": None,
            "verb": "检查",
            "estimated_minutes": 5,
            "node_type": "action",
            "depends_on": [],
            "children": [],
        },
        {
            "client_node_id": "thermometer",
            "title": "放好体温表",
            "description": None,
            "verb": "放好",
            "estimated_minutes": 2,
            "node_type": "action",
            "depends_on": [],
            "children": [],
        },
    ]
    fake_deepseek = FakeChatClient(json.dumps(task_tree, ensure_ascii=False))
    planner = DeepSeekPlannerClient(client=fake_deepseek, model="deepseek-chat")
    prompt = build_planner_prompt(
        "明天带孩子上学前要准备校服、水杯、作业本和体温表",
        intent_profile={"intent_type": "context_checklist"},
    )

    result = asyncio.run(planner.create_plan(prompt))

    assert len(result["root"]["children"]) == 1
    group = result["root"]["children"][0]
    assert group["node_type"] == "group"
    assert group["title"] == "上学前准备"
    assert [child["client_node_id"] for child in group["children"]] == [
        "uniform",
        "water",
        "homework",
        "thermometer",
    ]


def test_deepseek_planner_repairs_schema_mismatch_without_replanning():
    invalid = _valid_task_tree()
    invalid["planning_context"] = {
        "schema_version": 1,
        "intent_type": "exploration_decision",
        "time_horizon": "days",
        "roadmap": [
            {
                "phase_id": "phase_01",
                "order": 1,
                "title": "澄清问题",
                "objective": "明确核心顾虑",
                "status": "current",
            },
            {
                "phase_id": "phase_02",
                "order": 2,
                "title": "收集信息",
                "objective": "补齐决策依据",
                "description": "This extra key violates the schema.",
                "status": "planned",
            },
            {
                "phase_id": "phase_03",
                "order": 3,
                "title": "形成结论",
                "objective": "记录可解释的决定",
                "status": "planned",
            },
        ],
        "current_phase": {
            "phase_id": "phase_01",
            "title": "澄清问题",
            "objective": "明确核心顾虑",
            "completion_rule": "all_ai_actions_completed",
        },
        "next_action_client_node_id": "task-1",
    }
    repaired = json.loads(json.dumps(invalid))
    del repaired["planning_context"]["roadmap"][1]["description"]
    fake_deepseek = FakeChatClient(
        [
            json.dumps(invalid, ensure_ascii=False),
            json.dumps(repaired, ensure_ascii=False),
        ]
    )
    planner = DeepSeekPlannerClient(client=fake_deepseek, model="deepseek-chat")

    result = asyncio.run(planner.create_plan("Help me decide whether to attend graduate school"))

    assert result["planning_context"]["roadmap"][1]["objective"] == "补齐决策依据"
    assert len(fake_deepseek.chat.completions.calls) == 2
    repair_prompt = "\n".join(
        message["content"]
        for message in fake_deepseek.chat.completions.calls[1]["messages"]
    )
    assert "schema" in repair_prompt.lower()
    assert "Do not replan" in repair_prompt
    assert "Extra inputs are not permitted" in repair_prompt


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


@pytest.mark.parametrize(
    "intent_text",
    [
        "我想转行产品经理，但不知道现在是否适合做这个决定",
        "我在犹豫要不要读研，担心两年时间和经济成本是否值得",
        "我是否应该开始一个周末副业？目前每周只有 5 小时空闲",
        "我是否应该搬去另一个城市发展？目前收入不稳定，也要照顾家人",
    ],
)
def test_deepseek_normalizes_exploration_profile_to_current_decision_window(intent_text):
    fake_deepseek = FakeChatClient(
        json.dumps(
            {
                "intent_type": "exploration_decision",
                "time_horizon": "months",
                "confidence_score": 0.88,
            }
        )
    )
    planner = DeepSeekPlannerClient(client=fake_deepseek, model="deepseek-chat")

    result = asyncio.run(planner.profile_intent(intent_text))

    assert result["time_horizon"] == "days"


def test_deepseek_normalizes_same_trip_checklist_to_hours():
    fake_deepseek = FakeChatClient(
        json.dumps(
            {
                "intent_type": "context_checklist",
                "time_horizon": "minutes",
                "confidence_score": 0.9,
            }
        )
    )
    planner = DeepSeekPlannerClient(client=fake_deepseek, model="deepseek-chat")

    result = asyncio.run(
        planner.profile_intent("去公司前要带门禁卡、耳机、合同原件，还要记得寄快递")
    )

    assert result["intent_type"] == "context_checklist"
    assert result["time_horizon"] == "hours"


def test_deepseek_normalizes_month_long_delivery_project_to_long_term_growth():
    fake_deepseek = FakeChatClient(
        json.dumps(
            {
                "intent_type": "short_term_delivery",
                "time_horizon": "weeks",
                "confidence_score": 0.86,
            }
        )
    )
    planner = DeepSeekPlannerClient(client=fake_deepseek, model="deepseek-chat")

    result = asyncio.run(planner.profile_intent("一个月整理并发布个人网站"))

    assert result["intent_type"] == "long_term_growth"
    assert result["time_horizon"] == "months"


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


def test_planner_factory_defaults_to_deepseek(monkeypatch):
    monkeypatch.delenv("EASYPLAN_LLM_PROVIDER", raising=False)

    assert isinstance(create_planner_client(), DeepSeekPlannerClient)
