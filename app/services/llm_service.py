import os
from typing import Any, Protocol

from pydantic import ValidationError

from app.api.schemas import TaskTree


DEFAULT_OPENAI_PLANNER_MODEL = "gpt-4o-2024-08-06"


class LLMStructuredOutputError(RuntimeError):
    """Raised when the provider does not return a TaskTree structured output."""


class ReasoningSink(Protocol):
    async def emit(self, event: dict[str, Any]) -> None:
        """Receive safe, user-visible progress events."""


class ListReasoningSink:
    """Simple reasoning sink used by graph nodes and tests."""

    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    async def emit(self, event: dict[str, Any]) -> None:
        self.events.append(dict(event))


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


class OpenAIPlannerClient:
    """PlannerClient implementation using OpenAI Responses structured outputs."""

    def __init__(
        self,
        *,
        client: Any | None = None,
        model: str | None = None,
    ) -> None:
        self.model = model or os.getenv("EASYPLAN_OPENAI_MODEL", DEFAULT_OPENAI_PLANNER_MODEL)
        self._client = client

    async def create_plan(
        self,
        prompt: str,
        reasoning_sink: ReasoningSink | None = None,
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
        return task_tree.model_dump(mode="json")

    @property
    def _openai_client(self) -> Any:
        if self._client is None:
            from openai import AsyncOpenAI

            self._client = AsyncOpenAI()
        return self._client
