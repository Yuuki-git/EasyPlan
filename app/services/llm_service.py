import json
import logging
import os
from dataclasses import dataclass
from typing import Any, Protocol

from pydantic import ValidationError

from app.api.schemas import TaskTree


DEFAULT_OPENAI_PLANNER_MODEL = "gpt-4o-2024-08-06"
DEFAULT_DEEPSEEK_PLANNER_MODEL = "deepseek-chat"
DEFAULT_DEEPSEEK_BASE_URL = "https://api.deepseek.com"
DEFAULT_XIAOMI_MIMO_PLANNER_MODEL = "mimo-v2-flash"
DEFAULT_XIAOMI_MIMO_BASE_URL = "https://api.xiaomimimo.com/v1"
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
        message="已记录本次模型调用的 token usage",
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
            message="正在分析目标、时间复杂度和可执行边界",
        )
        await emit_reasoning(
            reasoning_sink,
            code="LLM_SCHEMA_LOCKED",
            message="正在按 TaskTree 结构组织任务，并匹配动词开头规则",
        )

        response = await self._openai_client.responses.parse(
            model=self.model,
            input=[
                {
                    "role": "system",
                    "content": (
                        "You are EasyPlan's planner. Return only a TaskTree structured "
                        "output. Do not include hidden reasoning or markdown."
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
            message="结构化任务树已生成，正在进入规则校验",
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
            message="正在使用 DeepSeek JSON mode 生成任务树草案",
        )
        await emit_reasoning(
            reasoning_sink,
            code="LLM_SCHEMA_LOCKED",
            message="正在用 TaskTree schema 约束 JSON 输出，并准备后端校验",
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
            message="DeepSeek JSON 已通过 TaskTree 校验，正在进入规则校验",
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
            message="正在使用小米 MiMo JSON mode 生成任务树草案",
        )
        await emit_reasoning(
            reasoning_sink,
            code="LLM_SCHEMA_LOCKED",
            message="正在用 TaskTree schema 约束 MiMo JSON 输出，并准备后端校验",
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
            message="小米 MiMo JSON 已通过 TaskTree 校验，正在进入规则校验",
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


def _json_mode_system_prompt(provider_name: str) -> str:
    schema = json.dumps(TaskTree.model_json_schema(), ensure_ascii=False, separators=(",", ":"))
    return (
        f"You are EasyPlan's planner running on {provider_name}. Output valid json only. "
        "The json must match this Pydantic TaskTree schema exactly. "
        "Do not include markdown, commentary, hidden reasoning, or extra keys. "
        f"TaskTree JSON Schema: {schema}"
    )
