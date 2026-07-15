import json
import logging
import os
import re
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Protocol

from pydantic import ValidationError

from app.api.schemas import (
    DecomposeAssistProposal,
    ExecutionRefineProposal,
    IntentProfile,
    StartAssistProposal,
    TaskAssistMode,
    TaskNode,
    TaskTree,
    UnstickAssistProposal,
)


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
JSON_REPAIR_MAX_ATTEMPTS = 2
CONTEXT_CHECKLIST_PROMPT_MARKER = "策略：这是情境清单型任务。"
CONTEXT_CHECKLIST_GROUP_PREFIX = "context_group_"
DECISION_STRATEGY_PROMPT_MARKER = "v1.2.8 探索决策 strategy_context 契约："
DECISION_LOW_INFORMATION_PATTERN = re.compile(
    r"(?:了解.{0,6}(?:很少|不多)|不知道|不清楚|信息不足|缺少(?:信息|资料|数据)|"
    r"know (?:very )?little|do not know|don't know|insufficient information)",
    flags=re.IGNORECASE,
)


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
        task_tree = _normalize_task_tree_for_prompt(task_tree, prompt)

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
        intent_profile = _normalize_intent_profile_for_input(intent_profile, intent_text)

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

        task_tree = await _parse_task_tree_json_content(
            content,
            provider_name="DeepSeek",
            repair_json=lambda invalid_content, error, cleaned_content: _repair_json_with_chat_completion(
                self._deepseek_client,
                model=self.model,
                provider_name="DeepSeek",
                invalid_content=invalid_content,
                error=error,
                cleaned_content=cleaned_content,
                max_tokens=int(os.getenv("EASYPLAN_DEEPSEEK_MAX_TOKENS", "4096")),
            ),
        )
        task_tree = _normalize_task_tree_for_prompt(task_tree, prompt)

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
        intent_profile = _normalize_intent_profile_for_input(intent_profile, intent_text)
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


class DeepSeekTaskAssistClient:
    """DeepSeek-only structured proposal client for task-scoped assistance."""

    def __init__(
        self,
        *,
        client: Any | None = None,
        model: str | None = None,
        base_url: str | None = None,
        usage_sink: UsageSink | None = None,
    ) -> None:
        self.model = model or os.getenv(
            "EASYPLAN_DEEPSEEK_MODEL",
            DEFAULT_DEEPSEEK_PLANNER_MODEL,
        )
        self.base_url = base_url or os.getenv("DEEPSEEK_BASE_URL", DEFAULT_DEEPSEEK_BASE_URL)
        self._client = client
        self.usage_sink = usage_sink or LoggingUsageSink()

    async def create_task_assist_proposal(
        self,
        *,
        mode: TaskAssistMode,
        prompt: str,
    ) -> dict[str, Any]:
        proposal_model = {
            "start": StartAssistProposal,
            "unstick": UnstickAssistProposal,
            "decompose": DecomposeAssistProposal,
        }[mode]
        response = await self._deepseek_client.chat.completions.create(
            model=self.model,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You generate one task-scoped EasyPlan Action Coach proposal. "
                        "Return exactly one JSON object matching the supplied schema. "
                        "Do not output markdown, reasoning, TaskTree, Roadmap, or unrelated tasks. "
                        f"Proposal JSON Schema: {json.dumps(proposal_model.model_json_schema(), ensure_ascii=False)} "
                        f"{LANGUAGE_MATCH_INSTRUCTION}"
                    ),
                },
                {"role": "user", "content": prompt},
            ],
            response_format={"type": "json_object"},
            temperature=0.1,
            max_tokens=int(os.getenv("EASYPLAN_TASK_ASSIST_MAX_TOKENS", "2048")),
        )
        content = _first_message_content(response)
        if not content:
            raise LLMStructuredOutputError("DeepSeek task assist response did not include JSON")
        try:
            payload = json.loads(_clean_json_response_text(content))
            proposal = proposal_model.model_validate(payload)
        except (json.JSONDecodeError, ValidationError) as exc:
            repaired = await _repair_structured_json(
                self._deepseek_client,
                model=self.model,
                invalid_content=content,
                error=exc,
                json_schema=proposal_model.model_json_schema(),
                max_tokens=int(os.getenv("EASYPLAN_TASK_ASSIST_MAX_TOKENS", "2048")),
            )
            try:
                payload = json.loads(_clean_json_response_text(repaired))
                proposal = proposal_model.model_validate(payload)
            except (json.JSONDecodeError, ValidationError) as repair_exc:
                raise LLMStructuredOutputError(
                    "DeepSeek task assist response did not match the proposal schema"
                ) from repair_exc
        await self.usage_sink.record(
            _usage_record(
                provider="deepseek",
                model=self.model,
                operation=f"task_assist.{mode}",
                usage=getattr(response, "usage", None),
            )
        )
        return proposal.model_dump(mode="json", exclude_unset=True)

    @property
    def _deepseek_client(self) -> Any:
        if self._client is None:
            from openai import AsyncOpenAI

            self._client = AsyncOpenAI(
                api_key=os.getenv("DEEPSEEK_API_KEY"),
                base_url=self.base_url,
            )
        return self._client


class DeepSeekExecutionRefineClient:
    """DeepSeek JSON Output client isolated to Execution Refine proposals."""

    def __init__(
        self,
        *,
        client: Any | None = None,
        model: str | None = None,
        base_url: str | None = None,
        usage_sink: UsageSink | None = None,
    ) -> None:
        self.model = model or os.getenv(
            "EASYPLAN_DEEPSEEK_MODEL",
            DEFAULT_DEEPSEEK_PLANNER_MODEL,
        )
        self.base_url = base_url or os.getenv(
            "DEEPSEEK_BASE_URL",
            DEFAULT_DEEPSEEK_BASE_URL,
        )
        self._client = client
        self.usage_sink = usage_sink or LoggingUsageSink()

    async def create_execution_refine_proposal(
        self,
        *,
        prompt: str,
    ) -> dict[str, Any]:
        schema = ExecutionRefineProposal.model_json_schema()
        max_tokens = int(os.getenv("EASYPLAN_EXECUTION_REFINE_MAX_TOKENS", "4096"))
        response = await self._deepseek_client.chat.completions.create(
            model=self.model,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You generate one bounded EasyPlan Execution Refine diff. "
                        "Return exactly one JSON object matching the supplied schema. "
                        "Never emit delete, status, phase, history, roadmap, review, loop, "
                        "checkpoint, source, parent, or client-ID mutations. "
                        "Do not output markdown, chain-of-thought, raw prompts, or unrelated tasks. "
                        f"Proposal JSON Schema: {json.dumps(schema, ensure_ascii=False)} "
                        f"{LANGUAGE_MATCH_INSTRUCTION}"
                    ),
                },
                {"role": "user", "content": prompt},
            ],
            response_format={"type": "json_object"},
            temperature=0.1,
            max_tokens=max_tokens,
        )
        content = _first_message_content(response)
        if not content:
            raise LLMStructuredOutputError(
                "DeepSeek execution refine response did not include JSON"
            )
        try:
            payload = json.loads(_clean_json_response_text(content))
            proposal = ExecutionRefineProposal.model_validate(payload)
        except (json.JSONDecodeError, ValidationError) as exc:
            repaired = await _repair_structured_json(
                self._deepseek_client,
                model=self.model,
                invalid_content=content,
                error=exc,
                json_schema=schema,
                max_tokens=max_tokens,
            )
            try:
                payload = json.loads(_clean_json_response_text(repaired))
                proposal = ExecutionRefineProposal.model_validate(payload)
            except (json.JSONDecodeError, ValidationError) as repair_exc:
                raise LLMStructuredOutputError(
                    "DeepSeek execution refine response did not match the proposal schema"
                ) from repair_exc
        await self.usage_sink.record(
            _usage_record(
                provider="deepseek",
                model=self.model,
                operation="execution_refine.generate",
                usage=getattr(response, "usage", None),
            )
        )
        return proposal.model_dump(mode="json", exclude_unset=True)

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

        task_tree = await _parse_task_tree_json_content(
            content,
            provider_name="Xiaomi MiMo",
            repair_json=lambda invalid_content, error, cleaned_content: _repair_json_with_chat_completion(
                self._mimo_client,
                model=self.model,
                provider_name="Xiaomi MiMo",
                invalid_content=invalid_content,
                error=error,
                cleaned_content=cleaned_content,
                max_tokens=int(os.getenv("EASYPLAN_XIAOMI_MIMO_MAX_TOKENS", "4096")),
            ),
        )
        task_tree = _normalize_task_tree_for_prompt(task_tree, prompt)

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
        intent_profile = _normalize_intent_profile_for_input(intent_profile, intent_text)
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
    selected_provider = (provider or os.getenv("EASYPLAN_LLM_PROVIDER", "deepseek")).strip().lower()
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


def _clean_json_response_text(content: str) -> str:
    text = _strip_code_fence(content.strip())
    text = _extract_outer_json_object(text)
    return "".join(
        char
        for char in text
        if char in "\t\n\r" or ord(char) >= 0x20
    )


def _strip_code_fence(content: str) -> str:
    if not content.startswith("```"):
        return content
    lines = content.splitlines()
    if lines and lines[0].strip().startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].strip().startswith("```"):
        lines = lines[:-1]
    return "\n".join(lines).strip()


def _extract_outer_json_object(content: str) -> str:
    start = content.find("{")
    if start == -1:
        return content.strip()

    in_string = False
    escaped = False
    depth = 0
    for index in range(start, len(content)):
        char = content[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue

        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return content[start : index + 1].strip()

    end = content.rfind("}")
    if end > start:
        return content[start : end + 1].strip()
    return content[start:].strip()


async def _parse_task_tree_json_content(
    content: str,
    *,
    provider_name: str,
    repair_json: Callable[[str, Exception, str], Awaitable[str]] | None = None,
    max_repair_attempts: int = JSON_REPAIR_MAX_ATTEMPTS,
) -> TaskTree:
    current_content = content
    for attempt in range(max_repair_attempts + 1):
        cleaned_content = _clean_json_response_text(current_content)
        try:
            parsed_json = json.loads(cleaned_content)
        except json.JSONDecodeError as exc:
            _log_json_decode_error(provider_name, exc, cleaned_content)
            if repair_json is None or attempt >= max_repair_attempts:
                raise LLMStructuredOutputError(_json_decode_error_message(exc, cleaned_content)) from exc
            current_content = await repair_json(current_content, exc, cleaned_content)
            continue

        try:
            return TaskTree.model_validate(parsed_json)
        except ValidationError as exc:
            if repair_json is None or attempt >= max_repair_attempts:
                raise LLMStructuredOutputError(str(exc)) from exc
            current_content = await repair_json(current_content, exc, cleaned_content)
            continue

    raise LLMStructuredOutputError(f"{provider_name} response did not include valid TaskTree JSON")


async def _repair_json_with_chat_completion(
    chat_client: Any,
    *,
    model: str,
    provider_name: str,
    invalid_content: str,
    error: Exception,
    cleaned_content: str,
    max_tokens: int,
) -> str:
    response = await chat_client.chat.completions.create(
        model=model,
        messages=_json_repair_messages(
            provider_name=provider_name,
            invalid_content=invalid_content,
            error=error,
            cleaned_content=cleaned_content,
        ),
        response_format={"type": "json_object"},
        temperature=0.0,
        max_tokens=max_tokens,
    )
    repaired_content = _first_message_content(response)
    if not repaired_content:
        raise LLMStructuredOutputError(f"{provider_name} JSON repair did not include JSON content")
    return repaired_content


async def _repair_structured_json(
    chat_client: Any,
    *,
    model: str,
    invalid_content: str,
    error: Exception,
    json_schema: dict[str, Any],
    max_tokens: int,
) -> str:
    response = await chat_client.chat.completions.create(
        model=model,
        messages=[
            {
                "role": "system",
                "content": (
                    "Repair the JSON so it matches the supplied schema. Preserve business meaning. "
                    "Return one JSON object only, without markdown or commentary."
                ),
            },
            {
                "role": "user",
                "content": (
                    f"Validation error: {error}\n"
                    f"JSON Schema: {json.dumps(json_schema, ensure_ascii=False)}\n"
                    f"Invalid JSON: {invalid_content}"
                ),
            },
        ],
        response_format={"type": "json_object"},
        temperature=0.0,
        max_tokens=max_tokens,
    )
    repaired = _first_message_content(response)
    if not repaired:
        raise LLMStructuredOutputError("DeepSeek task assist JSON repair returned no content")
    return repaired


def _json_repair_messages(
    *,
    provider_name: str,
    invalid_content: str,
    error: Exception,
    cleaned_content: str,
) -> list[dict[str, str]]:
    if isinstance(error, json.JSONDecodeError):
        repair_scope = "Fix only JSON syntax."
        failure_details = (
            "The previous response could not be parsed as JSON.\n"
            f"JSON parse error: {error.msg}\n"
            f"Error position: {error.pos}\n"
            f"Nearby 300-character excerpt:\n{_json_error_excerpt(cleaned_content, error.pos)}"
        )
    else:
        repair_scope = "Fix only TaskTree schema conformance."
        failure_details = (
            "The previous response is valid JSON but does not match the TaskTree schema.\n"
            f"Schema validation error:\n{error}\n"
            f"TaskTree JSON Schema:\n"
            f"{json.dumps(TaskTree.model_json_schema(), ensure_ascii=False, separators=(',', ':'))}"
        )
    return [
        {
            "role": "system",
            "content": (
                f"You repair JSON returned by {provider_name}. {repair_scope} "
                "Do not replan, rewrite, translate, reorder, or reinterpret any task content. "
                "For schema repair, add, remove, or rename fields only when the schema requires it. "
                "Return one valid JSON object only, with no markdown or commentary."
            ),
        },
        {
            "role": "user",
            "content": (
                f"{failure_details}\n\n"
                "Original output to repair, preserving all business field semantics:\n"
                f"{invalid_content}"
            ),
        },
    ]


def _log_json_decode_error(provider_name: str, error: json.JSONDecodeError, cleaned_content: str) -> None:
    logger.warning(
        "llm_json_decode_failed",
        extra={
            "provider": provider_name,
            "error_message": error.msg,
            "error_position": error.pos,
            "error_excerpt": _json_error_excerpt(cleaned_content, error.pos),
        },
    )


def _json_decode_error_message(error: json.JSONDecodeError, cleaned_content: str) -> str:
    return (
        f"{error.msg}: line {error.lineno} column {error.colno} (char {error.pos}); "
        f"nearby={_json_error_excerpt(cleaned_content, error.pos)!r}"
    )


def _json_error_excerpt(content: str, position: int, size: int = 300) -> str:
    radius = size // 2
    start = max(position - radius, 0)
    end = min(position + radius, len(content))
    return content[start:end]


def _deepseek_system_prompt() -> str:
    return _json_mode_system_prompt("DeepSeek")


def _normalize_task_tree_for_prompt(task_tree: TaskTree, prompt: str) -> TaskTree:
    normalized = _normalize_decision_strategy_context(task_tree, prompt)
    if CONTEXT_CHECKLIST_PROMPT_MARKER not in prompt:
        return normalized

    top_level_nodes = normalized.root.children
    if len(top_level_nodes) <= 1 or any(node.node_type == "group" for node in top_level_nodes):
        return normalized

    group_node = TaskNode(
        client_node_id=_next_context_group_id(normalized),
        title=_context_group_title(normalized, prompt),
        description=normalized.root.description,
        verb=normalized.root.verb or "归类",
        estimated_minutes=sum(max(node.estimated_minutes, 0) for node in top_level_nodes),
        node_type="group",
        depends_on=[],
        children=[node.model_copy(deep=True) for node in top_level_nodes],
    )
    normalized_root = normalized.root.model_copy(update={"children": [group_node]})
    return normalized.model_copy(update={"root": normalized_root})


def _normalize_decision_strategy_context(task_tree: TaskTree, prompt: str) -> TaskTree:
    context = task_tree.strategy_context
    if (
        DECISION_STRATEGY_PROMPT_MARKER not in prompt
        or context is None
        or context.strategy_type != "decision"
    ):
        return task_tree

    normalized_basis = [
        basis.model_copy(
            update={"statement": f"假设：{basis.statement.strip()}"}
        )
        if basis.basis_type == "working_assumption"
        and not re.match(r"^(?:假设|推测|暂定|assum(?:e|ption))", basis.statement.strip(), re.IGNORECASE)
        else basis
        for basis in context.basis
    ]
    judgment = context.current_judgment
    if DECISION_LOW_INFORMATION_PATTERN.search(_prompt_user_intent(prompt)):
        judgment = judgment.model_copy(update={"confidence": "low"})
    normalized_context = context.model_copy(
        update={"basis": normalized_basis, "current_judgment": judgment}
    )
    return task_tree.model_copy(update={"strategy_context": normalized_context})


def _context_group_title(task_tree: TaskTree, prompt: str) -> str:
    root_title = task_tree.root.title.strip()
    if root_title:
        return root_title

    user_intent = _prompt_user_intent(prompt)
    pattern_map = (
        (r"(上学前)", r"\1准备"),
        (r"(出差前)", r"\1准备"),
        (r"(去公司前)", r"\1准备"),
        (r"(出门前)", r"\1准备"),
        (r"(下班路上|回家路上|通勤路上)", "路上顺手处理"),
        (r"(下班后)", "下班后顺手处理"),
        (r"(月底前)", "账单处理"),
    )
    for pattern, replacement in pattern_map:
        match = re.search(pattern, user_intent)
        if match is None:
            continue
        if "\\1" in replacement:
            return replacement.replace("\\1", match.group(1))
        return replacement
    if any(keyword in user_intent for keyword in ("房租", "信用卡", "水电费", "电费")):
        return "账单处理"
    if any(keyword in user_intent for keyword in ("快递", "买菜", "超市", "药店", "加油")):
        return "顺路处理"
    return "当前情境"


def _prompt_user_intent(prompt: str) -> str:
    marker = "用户意图："
    if marker not in prompt:
        return ""
    return prompt.rsplit(marker, 1)[-1].splitlines()[0].strip()


def _next_context_group_id(task_tree: TaskTree) -> str:
    existing_ids = {node.client_node_id for node in _iter_task_nodes(task_tree.root)}
    index = 1
    while True:
        candidate = f"{CONTEXT_CHECKLIST_GROUP_PREFIX}{index:02d}"
        if candidate not in existing_ids:
            return candidate
        index += 1


def _iter_task_nodes(node: TaskNode) -> list[TaskNode]:
    nodes = [node]
    for child in node.children:
        nodes.extend(_iter_task_nodes(child))
    return nodes


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
        "A deliverable whose explicit target duration is several weeks or one or more months is "
        "long_term_growth even when the final artifact is a website, portfolio, report, or other "
        "project deliverable; short_term_delivery is limited to a near deadline sprint. "
        "For context_checklist, time_horizon describes the operational outing: same-trip errands "
        "such as 去公司前, 下班路上, or 明天上学前 normally use hours; 周末, 搬家, or 月底 "
        "multi-day checklists use days. "
        "For exploration_decision, time_horizon describes the current clarification and decision window, "
        "not the duration, cost, or long-term consequence of the option being considered. Unless the user "
        "explicitly schedules the exploration itself over another period, default to days. Mentions such as "
        "two-year cost, weekly available hours, career duration, or long-term relocation consequences do not "
        "make the current decision window weeks or months. "
        "Do not include markdown, commentary, hidden reasoning, or extra keys. "
        f"IntentProfile JSON Schema: {schema} "
        f"{LANGUAGE_MATCH_INSTRUCTION}"
    )


EXPLICIT_EXPLORATION_WINDOW_PATTERN = re.compile(
    r"(?:(?:未来|接下来|用|花)\s*(?:\d+|一|两|二|三|四|五|六|七|八|九|十)\s*"
    r"(?:天|周|个月)[^，。；]{0,16}(?:探索|调研|比较|验证|考虑|评估|决定)|"
    r"(?:探索|调研|比较|验证|考虑|评估|决定)[^，。；]{0,16}"
    r"(?:\d+|一|两|二|三|四|五|六|七|八|九|十)\s*(?:天|周|个月))"
)
EXPLICIT_MONTH_DURATION_PATTERN = re.compile(
    r"(?:\d+|一|两|二|三|四|五|六|七|八|九|十)\s*个?月|半年|一年|年底"
)
EXPLICIT_WEEK_DURATION_PATTERN = re.compile(
    r"(?:\d+|一|两|二|三|四|五|六|七|八|九|十)\s*周"
)
CHECKLIST_DAY_HORIZON_PATTERN = re.compile(r"周末|月底|搬家(?:前|时)?|这几天|未来几天")
CHECKLIST_MINUTE_HORIZON_PATTERN = re.compile(
    r"(?:\d+|一|两|二|三|四|五|六|七|八|九|十)\s*分钟(?:内|后|前)"
)


def _normalize_intent_profile_for_input(
    profile: IntentProfile,
    intent_text: str,
) -> IntentProfile:
    if profile.intent_type == "exploration_decision":
        if EXPLICIT_EXPLORATION_WINDOW_PATTERN.search(intent_text):
            return profile
        return profile.model_copy(update={"time_horizon": "days"})

    explicit_long_horizon = _explicit_long_duration_horizon(intent_text)
    if explicit_long_horizon is not None and profile.intent_type in {
        "long_term_growth",
        "short_term_delivery",
    }:
        return profile.model_copy(
            update={
                "intent_type": "long_term_growth",
                "time_horizon": explicit_long_horizon,
            }
        )

    if profile.intent_type == "context_checklist":
        if CHECKLIST_MINUTE_HORIZON_PATTERN.search(intent_text):
            horizon = "minutes"
        elif CHECKLIST_DAY_HORIZON_PATTERN.search(intent_text):
            horizon = "days"
        else:
            horizon = "hours"
        return profile.model_copy(update={"time_horizon": horizon})
    return profile


def _explicit_long_duration_horizon(intent_text: str) -> str | None:
    if EXPLICIT_MONTH_DURATION_PATTERN.search(intent_text):
        return "months"
    if EXPLICIT_WEEK_DURATION_PATTERN.search(intent_text):
        return "weeks"
    return None


def _json_mode_system_prompt(provider_name: str) -> str:
    schema = json.dumps(TaskTree.model_json_schema(), ensure_ascii=False, separators=(",", ":"))
    return (
        f"You are EasyPlan's planner running on {provider_name}. Output valid json only. "
        "The json must match this Pydantic TaskTree schema exactly. "
        "Do not include markdown, commentary, hidden reasoning, or extra keys. "
        f"TaskTree JSON Schema: {schema} "
        f"{LANGUAGE_MATCH_INSTRUCTION}"
    )
