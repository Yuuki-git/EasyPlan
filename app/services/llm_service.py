import json
import logging
import os
from dataclasses import dataclass
from typing import Any, Protocol

from pydantic import ValidationError

from app.api.schemas import IntentProfile, TaskTree


DEFAULT_OPENAI_PLANNER_MODEL = "gpt-4o-2024-08-06"
DEFAULT_DEEPSEEK_PLANNER_MODEL = "deepseek-chat"
DEFAULT_DEEPSEEK_BASE_URL = "https://api.deepseek.com"
DEFAULT_XIAOMI_MIMO_PLANNER_MODEL = "mimo-v2-flash"
DEFAULT_XIAOMI_MIMO_BASE_URL = "https://api.xiaomimimo.com/v1"
LANGUAGE_MATCH_INSTRUCTION = (
    "CRITICAL: You MUST respond in the EXACT same language as the user's prompt. "
    "If the user writes in Chinese, all  fields (title, description, summary) MUST be in Chinese."
)
USER_VISIBLE_REASONING_MESSAGES = {
    "LLM_PLANNING_STARTED": "正在分析您的核心目标...",
    "LLM_SCHEMA_LOCKED": "正在将目标拆解为可执行的微行动...",
    "LLM_PLAN_PARSED": "正在为您评估每项任务的时间与依赖关系...",
    "LLM_USAGE_RECORDED": "计划生成完毕，请查阅。",
}
logger = logging.getLogger(__name__)


class LLMStructuredOutputError(RuntimeError):
    """Raised when the provider does not return a TaskTree structured output."""


class ReasoningSink(Protocol):
    async def emit(self, event: dict[str, Any]) -> None:
        """Receive safe, user-visible progress events."""


@dataclass(frozen=True)
class LLMUsageRecord:
    provider: str
    model: str
    operation: str
    input_tokens: int | None
    output_tokens: int | None
    total_tokens: int | None


class UsageSink(Protocol):
    async def record(self, record: LLMUsageRecord) -> None:
        """Record model usage metrics without prompts or model output."""


class ListReasoningSink:
    """Simple reasoning sink used by graph nodes and tests."""

    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    async def emit(self, event: dict[str, Any]) -> None:
        self.events.append(dict(event))


class ListUsageSink:
    """Simple usage sink used by tests."""

    def __init__(self) -> None:
        self.records: list[LLMUsageRecord] = []

    async def record(self, record: LLMUsageRecord) -> None:
        self.records.append(record)


class LoggingUsageSink:
    """Default usage sink for structured application logs."""

    async def record(self, record: LLMUsageRecord) -> None:
        logger.info(
            "llm_usage",
            extra={
                "provider": record.provider,
                "model": record.model,
                "operation": record.operation,
                "input_tokens": record.input_tokens,
                "output_tokens": record.output_tokens,
                "total_tokens": record.total_tokens,
            },
        )


async def emit_reasoning(
    reasoning_sink: ReasoningSink | None,
    *,
    code: str,
    message: str,
    node: str = "planner_node",
) -> None:
    if reasoning_sink is None:
        return
    await reasoning_sink.emit({"node": node, "code": code, "message": message})


async def emit_usage(
    usage_sink: UsageSink | None,
    record: LLMUsageRecord,
    reasoning_sink: ReasoningSink | None = None,
) -> None:
    if usage_sink is not None:
        await usage_sink.record(record)
    await emit_reasoning(
        reasoning_sink,
        code="LLM_USAGE_RECORDED",
        message=USER_VISIBLE_REASONING_MESSAGES["LLM_USAGE_RECORDED"],
    )


class OpenAIPlannerClient:
    """PlannerClient implementation using OpenAI Responses structured outputs."""

    def __init__(
        self,
        *,
        client: Any | None = None,
        model: str | None = None,
        usage_sink: UsageSink | None = None,
    ) -> None:
        self.model = model or os.getenv("EASYPLAN_OPENAI_MODEL", DEFAULT_OPENAI_PLANNER_MODEL)
        self._client = client
        self.usage_sink = usage_sink or LoggingUsageSink()

    async def create_plan(
        self,
        prompt: str,
        reasoning_sink: ReasoningSink | None = None,
        usage_sink: UsageSink | None = None,
    ) -> dict[str, Any]:
        await emit_reasoning(
            reasoning_sink,
            code="LLM_PLANNING_STARTED",
            message=USER_VISIBLE_REASONING_MESSAGES["LLM_PLANNING_STARTED"],
        )
        await emit_reasoning(
            reasoning_sink,
            code="LLM_SCHEMA_LOCKED",
            message=USER_VISIBLE_REASONING_MESSAGES["LLM_SCHEMA_LOCKED"],
        )

        response = await self._openai_client.responses.parse(
            model=self.model,
            input=[
                {
                    "role": "system",
                    "content": (
                        "You are EasyPlan's planner. Return only a TaskTree structured "
                        "output. Do not include hidden reasoning or markdown. "
                        f"{LANGUAGE_MATCH_INSTRUCTION}"
                    ),
                },
                {"role": "user", "content": prompt},
            ],
            text_format=TaskTree,
            temperature=0.2,
            store=False,
        )
        parsed = getattr(response, "output_parsed", None)
        if parsed is None:
            raise LLMStructuredOutputError("OpenAI response did not include output_parsed")

        try:
            task_tree = parsed if isinstance(parsed, TaskTree) else TaskTree.model_validate(parsed)
        except ValidationError as exc:
            raise LLMStructuredOutputError(str(exc)) from exc

        await emit_reasoning(
            reasoning_sink,
            code="LLM_PLAN_PARSED",
            message=USER_VISIBLE_REASONING_MESSAGES["LLM_PLAN_PARSED"],
        )
        await emit_usage(
            usage_sink or self.usage_sink,
            _usage_record(
                provider="openai",
                model=self.model,
                operation="planner.create_plan",
                usage=getattr(response, "usage", None),
            ),
            reasoning_sink,
        )
        return task_tree.model_dump(mode="json")

    async def profile_intent(
        self,
        intent_text: str,
        reasoning_sink: ReasoningSink | None = None,
        usage_sink: UsageSink | None = None,
    ) -> dict[str, Any]:
        response = await self._openai_client.responses.parse(
            model=self.model,
            input=[
                {
                    "role": "system",
                    "content": _intent_profile_system_prompt("OpenAI"),
                },
                {"role": "user", "content": intent_text},
            ],
            text_format=IntentProfile,
            temperature=0.0,
            store=False,
        )
        parsed = getattr(response, "output_parsed", None)
        if parsed is None:
            raise LLMStructuredOutputError("OpenAI response did not include intent profile")

        try:
            intent_profile = parsed if isinstance(parsed, IntentProfile) else IntentProfile.model_validate(parsed)
        except ValidationError as exc:
            raise LLMStructuredOutputError(str(exc)) from exc

        await (usage_sink or self.usage_sink).record(
            _usage_record(
                provider="openai",
                model=self.model,
                operation="planner.profile_intent",
                usage=getattr(response, "usage", None),
            )
        )
        return intent_profile.model_dump(mode="json")

    @property
    def _openai_client(self) -> Any:
        if self._client is None:
            from openai import AsyncOpenAI

            self._client = AsyncOpenAI()
        return self._client


class DeepSeekPlannerClient:
    """PlannerClient implementation using DeepSeek JSON Output plus Pydantic validation."""

    def __init__(
        self,
        *,
        client: Any | None = None,
        model: str | None = None,
        base_url: str | None = None,
        usage_sink: UsageSink | None = None,
    ) -> None:
        self.model = model or os.getenv("EASYPLAN_DEEPSEEK_MODEL", DEFAULT_DEEPSEEK_PLANNER_MODEL)
        self.base_url = base_url or os.getenv("DEEPSEEK_BASE_URL", DEFAULT_DEEPSEEK_BASE_URL)
        self._client = client
        self.usage_sink = usage_sink or LoggingUsageSink()

    async def create_plan(
        self,
        prompt: str,
        reasoning_sink: ReasoningSink | None = None,
        usage_sink: UsageSink | None = None,
    ) -> dict[str, Any]:
        await emit_reasoning(
            reasoning_sink,
            code="LLM_PLANNING_STARTED",
            message=USER_VISIBLE_REASONING_MESSAGES["LLM_PLANNING_STARTED"],
        )
        await emit_reasoning(
            reasoning_sink,
            code="LLM_SCHEMA_LOCKED",
            message=USER_VISIBLE_REASONING_MESSAGES["LLM_SCHEMA_LOCKED"],
        )
        response = await self._deepseek_client.chat.completions.create(
            model=self.model,
            messages=[
                {
                    "role": "system",
                    "content": _deepseek_system_prompt(),
                },
                {"role": "user", "content": prompt},
            ],
            response_format={"type": "json_object"},
            temperature=0.2,
            max_tokens=int(os.getenv("EASYPLAN_DEEPSEEK_MAX_TOKENS", "4096")),
        )
        content = _first_message_content(response)
        if not content:
            raise LLMStructuredOutputError("DeepSeek response did not include JSON content")

        try:
            parsed_json = json.loads(content)
            task_tree = TaskTree.model_validate(parsed_json)
        except (json.JSONDecodeError, ValidationError) as exc:
            raise LLMStructuredOutputError(str(exc)) from exc

        await emit_reasoning(
            reasoning_sink,
            code="LLM_PLAN_PARSED",
            message=USER_VISIBLE_REASONING_MESSAGES["LLM_PLAN_PARSED"],
        )
        await emit_usage(
            usage_sink or self.usage_sink,
            _usage_record(
                provider="deepseek",
                model=self.model,
                operation="planner.create_plan",
                usage=getattr(response, "usage", None),
            ),
            reasoning_sink,
        )
        return task_tree.model_dump(mode="json")

    async def profile_intent(
        self,
        intent_text: str,
        reasoning_sink: ReasoningSink | None = None,
        usage_sink: UsageSink | None = None,
    ) -> dict[str, Any]:
        response = await self._deepseek_client.chat.completions.create(
            model=self.model,
            messages=[
                {
                    "role": "system",
                    "content": _intent_profile_system_prompt("DeepSeek"),
                },
                {"role": "user", "content": intent_text},
            ],
            response_format={"type": "json_object"},
            temperature=0.0,
            max_tokens=int(os.getenv("EASYPLAN_DEEPSEEK_PROFILE_MAX_TOKENS", "512")),
        )
        intent_profile = _parse_intent_profile_response(response, "DeepSeek")
        await (usage_sink or self.usage_sink).record(
            _usage_record(
                provider="deepseek",
                model=self.model,
                operation="planner.profile_intent",
                usage=getattr(response, "usage", None),
            )
        )
        return intent_profile.model_dump(mode="json")

    @property
    def _deepseek_client(self) -> Any:
        if self._client is None:
            from openai import AsyncOpenAI

            self._client = AsyncOpenAI(
                api_key=os.getenv("DEEPSEEK_API_KEY"),
                base_url=self.base_url,
            )
        return self._client


class XiaomiMiMoPlannerClient:
    """PlannerClient implementation using Xiaomi MiMo JSON mode plus Pydantic validation."""

    def __init__(
        self,
        *,
        client: Any | None = None,
        model: str | None = None,
        base_url: str | None = None,
        usage_sink: UsageSink | None = None,
    ) -> None:
        self.model = model or os.getenv("EASYPLAN_XIAOMI_MIMO_MODEL", DEFAULT_XIAOMI_MIMO_PLANNER_MODEL)
        self.base_url = base_url or os.getenv("XIAOMI_MIMO_BASE_URL", DEFAULT_XIAOMI_MIMO_BASE_URL)
        self._client = client
        self.usage_sink = usage_sink or LoggingUsageSink()

    async def create_plan(
        self,
        prompt: str,
        reasoning_sink: ReasoningSink | None = None,
        usage_sink: UsageSink | None = None,
    ) -> dict[str, Any]:
        await emit_reasoning(
            reasoning_sink,
            code="LLM_PLANNING_STARTED",
            message=USER_VISIBLE_REASONING_MESSAGES["LLM_PLANNING_STARTED"],
        )
        await emit_reasoning(
            reasoning_sink,
            code="LLM_SCHEMA_LOCKED",
            message=USER_VISIBLE_REASONING_MESSAGES["LLM_SCHEMA_LOCKED"],
        )
        response = await self._mimo_client.chat.completions.create(
            model=self.model,
            messages=[
                {
                    "role": "system",
                    "content": _json_mode_system_prompt("Xiaomi MiMo"),
                },
                {"role": "user", "content": prompt},
            ],
            response_format={"type": "json_object"},
            temperature=0.2,
            max_tokens=int(os.getenv("EASYPLAN_XIAOMI_MIMO_MAX_TOKENS", "4096")),
        )
        content = _first_message_content(response)
        if not content:
            raise LLMStructuredOutputError("Xiaomi MiMo response did not include JSON content")

        try:
            parsed_json = json.loads(content)
            task_tree = TaskTree.model_validate(parsed_json)
        except (json.JSONDecodeError, ValidationError) as exc:
            raise LLMStructuredOutputError(str(exc)) from exc

        await emit_reasoning(
            reasoning_sink,
            code="LLM_PLAN_PARSED",
            message=USER_VISIBLE_REASONING_MESSAGES["LLM_PLAN_PARSED"],
        )
        await emit_usage(
            usage_sink or self.usage_sink,
            _usage_record(
                provider="xiaomi",
                model=self.model,
                operation="planner.create_plan",
                usage=getattr(response, "usage", None),
            ),
            reasoning_sink,
        )
        return task_tree.model_dump(mode="json")

    async def profile_intent(
        self,
        intent_text: str,
        reasoning_sink: ReasoningSink | None = None,
        usage_sink: UsageSink | None = None,
    ) -> dict[str, Any]:
        response = await self._mimo_client.chat.completions.create(
            model=self.model,
            messages=[
                {
                    "role": "system",
                    "content": _intent_profile_system_prompt("Xiaomi MiMo"),
                },
                {"role": "user", "content": intent_text},
            ],
            response_format={"type": "json_object"},
            temperature=0.0,
            max_tokens=int(os.getenv("EASYPLAN_XIAOMI_MIMO_PROFILE_MAX_TOKENS", "512")),
        )
        intent_profile = _parse_intent_profile_response(response, "Xiaomi MiMo")
        await (usage_sink or self.usage_sink).record(
            _usage_record(
                provider="xiaomi",
                model=self.model,
                operation="planner.profile_intent",
                usage=getattr(response, "usage", None),
            )
        )
        return intent_profile.model_dump(mode="json")

    @property
    def _mimo_client(self) -> Any:
        if self._client is None:
            from openai import AsyncOpenAI

            self._client = AsyncOpenAI(
                api_key=os.getenv("XIAOMI_API_KEY"),
                base_url=self.base_url,
            )
        return self._client


def create_planner_client(
    *,
    provider: str | None = None,
    model: str | None = None,
    usage_sink: UsageSink | None = None,
) -> OpenAIPlannerClient | DeepSeekPlannerClient | XiaomiMiMoPlannerClient:
    selected_provider = (provider or os.getenv("EASYPLAN_LLM_PROVIDER", "openai")).strip().lower()
    if selected_provider == "openai":
        return OpenAIPlannerClient(model=model, usage_sink=usage_sink)
    if selected_provider == "deepseek":
        return DeepSeekPlannerClient(model=model, usage_sink=usage_sink)
    if selected_provider in {"xiaomi", "xiaomi_mimo"}:
        return XiaomiMiMoPlannerClient(model=model, usage_sink=usage_sink)
    raise ValueError(f"Unsupported planner provider: {selected_provider}")


def _usage_record(
    *,
    provider: str,
    model: str,
    operation: str,
    usage: Any,
) -> LLMUsageRecord:
    return LLMUsageRecord(
        provider=provider,
        model=model,
        operation=operation,
        input_tokens=_usage_value(usage, "input_tokens", "prompt_tokens"),
        output_tokens=_usage_value(usage, "output_tokens", "completion_tokens"),
        total_tokens=_usage_value(usage, "total_tokens"),
    )


def _usage_value(usage: Any, *names: str) -> int | None:
    if usage is None:
        return None
    for name in names:
        if isinstance(usage, dict):
            value = usage.get(name)
        else:
            value = getattr(usage, name, None)
        if value is not None:
            return int(value)
    return None


def _first_message_content(response: Any) -> str | None:
    choices = getattr(response, "choices", None) or []
    if not choices:
        return None
    message = getattr(choices[0], "message", None)
    return getattr(message, "content", None)


def _deepseek_system_prompt() -> str:
    return _json_mode_system_prompt("DeepSeek")


def _parse_intent_profile_response(response: Any, provider_name: str) -> IntentProfile:
    content = _first_message_content(response)
    if not content:
        raise LLMStructuredOutputError(f"{provider_name} response did not include intent profile JSON content")

    try:
        parsed_json = json.loads(content)
        return IntentProfile.model_validate(parsed_json)
    except (json.JSONDecodeError, ValidationError) as exc:
        raise LLMStructuredOutputError(str(exc)) from exc


def _intent_profile_system_prompt(provider_name: str) -> str:
    schema = json.dumps(IntentProfile.model_json_schema(), ensure_ascii=False, separators=(",", ":"))
    return (
        f"You are EasyPlan's intent profiler running on {provider_name}. Output valid json only. "
        "Classify the user's intent before planning. The json must match this Pydantic "
        "IntentProfile schema exactly. intent_type must be one of long_term_growth, "
        "short_term_delivery, context_checklist, exploration_decision. time_horizon must be "
        "one of minutes, hours, days, weeks, months. confidence_score must be between 0 and 1. "
        "Classification boundary: context_checklist means situational checklist chores that do "
        "not require deep focus and are highly dependent on location, errands, or small tools, "
        "such as 买菜, 拿快递, 缴费, and other 跑腿杂事. short_term_delivery means a focused "
        "delivery sprint requiring 连续坐在电脑前 or 书桌前, with deep cognitive output work, "
        "such as 写代码, 做 PPT, 赶报告. Keep this boundary strict when choosing between "
        "context_checklist and short_term_delivery. "
        "Do not include markdown, commentary, hidden reasoning, or extra keys. "
        f"IntentProfile JSON Schema: {schema} "
        f"{LANGUAGE_MATCH_INSTRUCTION}"
    )


def _json_mode_system_prompt(provider_name: str) -> str:
    schema = json.dumps(TaskTree.model_json_schema(), ensure_ascii=False, separators=(",", ":"))
    return (
        f"You are EasyPlan's planner running on {provider_name}. Output valid json only. "
        "The json must match this Pydantic TaskTree schema exactly. "
        "Do not include markdown, commentary, hidden reasoning, or extra keys. "
        f"TaskTree JSON Schema: {schema} "
        f"{LANGUAGE_MATCH_INSTRUCTION}"
    )
