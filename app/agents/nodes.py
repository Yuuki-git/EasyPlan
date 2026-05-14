from datetime import datetime, timezone
import inspect
import re
from typing import Any, Protocol
from uuid import UUID, uuid4

from pydantic import ValidationError
from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert

from app.agents.state import AgentState, DISALLOWED_CHECKPOINT_KEYS, prune_state
from app.api.schemas import IntentProfile, TaskTree
from app.models.task import Task, TaskDependency
from app.models.thread import AgentThread
from app.services.llm_service import ListReasoningSink, ReasoningSink, emit_reasoning


MAX_REPLAN_ATTEMPTS = 3
MAX_TOP_LEVEL_NODES = 12
MAX_CHILDREN_PER_TOP_LEVEL = 3
LONG_TERM_MAX_DEPTH = 4
LONG_TERM_SCOPE_KEYWORDS = (
    "全年",
    "一年",
    "12个月",
    "十二个月",
    "半年",
    "完整周期",
    "完整计划",
    "全部阶段",
    "所有阶段",
    "长期计划",
    "每周",
    "每月",
)
LONG_TERM_HORIZON_PATTERNS = (
    r"第[一二三四五六七八九十\d]+周",
    r"第[一二三四五六七八九十\d]+个月",
    r"[一二三四五六七八九十\d]+\s*个月.{0,6}计划",
    r"(每天|每日|每周|每月).{0,12}(坚持|学习|训练|复习|背|练)",
    r"(完整|全部|全年|长期).{0,8}(周期|计划|路线|课程)",
)
LONG_TERM_CURRICULUM_TERMS = (
    "基础",
    "训练",
    "模拟",
    "复盘",
    "强化",
    "冲刺",
    "课程",
    "长期",
)
EXPLORATION_EXECUTION_PATTERNS = (
    r"[三四五六七八九十\d]+\s*个月.{0,8}(转行|创业|学习|执行).{0,8}计划",
    r"(直接|立即).{0,6}(辞职|转行|创业|报名|投递|执行)",
    r"(转行|创业|长期学习).{0,8}(执行计划|学习计划|路线图)",
)
EXPLORATION_DISCOVERY_TERMS = (
    "澄清",
    "写下",
    "列出",
    "收集",
    "调研",
    "访谈",
    "聊",
    "找",
    "JD",
    "岗位",
    "比较",
    "成本收益",
    "小实验",
    "验证",
    "担忧",
    "原因",
    "决策",
)
LOW_VALUE_ICEBREAKER_TERMS = (
    "打开电脑",
    "打开 word",
    "打开Word",
    "打开文档",
    "打开文件",
    "新建文档",
    "新建文件",
    "准备开始",
    "准备写",
    "想一想",
    "坐下",
    "整理桌面",
)

HARD_RULES_PROMPT = """硬性规则：
1. 整个任务树最多只能包含 12 个顶层节点（Group/Action）。
2. 每个顶层节点最多只能包含 3 个子节点。
3. 绝对禁止排满整个周期。
4. 对于长周期或宏大目标，只能输出【当前启动阶段 Phase 1】的行动地图。
5. 禁止输出 Markdown、解释性段落或 schema 外字段。
6. title 和 description 必须短而具体，避免长文本导致 JSON 截断。"""

INTENT_STRATEGY_PROMPTS = {
    "long_term_growth": """策略：这是长周期成长型目标。你需要使用「破冰法则 + 视野控制」。
第一个任务必须是极其简单的破冰动作，建议 <=5 分钟，用来降低启动阻力。
但后续任务可以是 25-60 分钟的深度工作。
不要排满整个周期，只输出当前启动阶段 Phase 1，且 Phase 1 只覆盖最近 72 小时内可以启动的行动。
可以给 3-5 个高层阶段作为 roadmap；roadmap 只能是阶段标题和目的，不允许 estimated_minutes，不允许具体日期，不允许子任务。
如果需要输出 roadmap，只能放在 assumptions 里；TaskTree.root.children 只能放当前 Phase 1 tasks。
Phase 1 建议覆盖未来 24-72 小时。
禁止排满整个备考期、训练期、写作期或长期周期。
禁止生成“第1周/第2周/第3个月”这种长期排期任务。

<反面教材>
动作：「背 50 个 N3 单词」，耗时：120 分钟。问题：启动阻力过高，容易拖延。
错误：为 N3 制定 3 个月每日学习计划。
原因：排满全周期，造成认知负担，违反 Scope Horizon。

<正面教材>
动作：「在淘宝搜索 N3 备考教材」，耗时：2 分钟。原因：低门槛破冰。
动作：「完成一套词汇摸底测试」，耗时：45 分钟。原因：进入真实评估。
正确：
roadmap:
1. 摸底与资料准备
2. 词汇语法基础
3. 听力阅读训练
4. 真题模拟
5. 考前复盘

当前 Phase 1 tasks:
1. 搜索并保存一套 N3 真题，5 分钟
2. 完成 20 道词汇摸底题，20 分钟
3. 记录 10 个不会的词，10 分钟""",
    "short_term_delivery": """策略：这是短期交付任务。你需要使用「时间盒法则」。
绝对禁止生成「打开电脑 / 新建文档 / 打开 Word / 准备开始」等低价值破冰动作。
请直接按交付模块、逻辑顺序或时间块拆分。
每个任务必须有明确产出。

<反面教材>
动作：「打开 Word 准备写 PPT」，耗时：2 分钟。问题：这是废话，不是有效任务。

<正面教材>
动作：「列出商业计划书的核心痛点大纲」，耗时：15 分钟。
动作：「撰写商业模式章节」，耗时：45 分钟。""",
    "context_checklist": """策略：这是情境清单型任务。
无需深度拆解，也不需要破冰动作。
请按地理位置、工具环境、顺路关系或时间场景聚合。
目标是减少切换成本和遗漏，而不是生成复杂任务树。

<正面教材>
组：「下班路上」
动作：「去丰巢拿快递」
动作：「去超市买菜」

组：「手机处理」
动作：「缴电费」""",
    "exploration_decision": """策略：这是探索决策型任务。
不要直接生成死板执行清单，也不要假设用户已经做出决定。
不要假设用户已经决定。
请生成信息收集、问题澄清、最小成本测试和决策节点。
目标是降低不确定性，而不是强推执行。
不要生成长期执行计划。
当前阶段只生成信息收集、问题澄清、小实验和决策节点。
禁止直接生成完整转行计划、创业计划、长期学习计划。

<正面教材>
动作：「列出辞职做自媒体的 3 个核心担忧」，耗时：15 分钟。
动作：「找一位自媒体从业者聊现状」，耗时：60 分钟。
动作：「用一页纸比较继续上班和做自媒体的成本收益」，耗时：30 分钟。

<反面教材>
错误：直接制定 6 个月转行产品经理学习计划。
原因：用户尚未完成决策，不应该强行执行化。

<正面教材>
正确：
1. 写下转行产品经理的 3 个原因，10 分钟
2. 找 3 个产品经理 JD，20 分钟
3. 标出这些 JD 的共同要求，15 分钟
4. 约一位从业者聊 20 分钟，60 分钟
5. 用一页纸比较转行与不转行的成本收益，30 分钟""",
    "general": """策略：用户意图不够明确。
请生成保守、短小、可执行的启动计划。
不要输出过多任务。
如果缺少关键信息，第一步可以是澄清任务边界。""",
}


class PlannerClient(Protocol):
    async def create_plan(
        self,
        prompt: str,
        reasoning_sink: ReasoningSink | None = None,
    ) -> dict[str, Any]:
        """Create a TaskTree-shaped plan using an async LLM client."""


class IntentProfilerClient(Protocol):
    async def profile_intent(
        self,
        intent_text: str,
        reasoning_sink: ReasoningSink | None = None,
    ) -> dict[str, Any]:
        """Classify the user's intent before planning."""


class RuleBasedPlannerClient:
    async def create_plan(
        self,
        prompt: str,
        reasoning_sink: ReasoningSink | None = None,
    ) -> dict[str, Any]:
        await emit_reasoning(
            reasoning_sink,
            code="RULE_BASED_PLAN_CREATED",
            message="已生成本地兜底任务树，正在进入规则校验",
        )
        return {
            "root": {
                "client_node_id": "root",
                "title": "启动计划",
                "description": None,
                "verb": "规划",
                "estimated_minutes": 1,
                "node_type": "group",
                "depends_on": [],
                "children": [
                    {
                        "client_node_id": "task-1",
                        "title": "写下第一步",
                        "description": "根据当前意图写下一个可立即启动的动作。",
                        "verb": "写下",
                        "estimated_minutes": 2,
                        "node_type": "action",
                        "depends_on": [],
                        "children": [],
                    }
                ],
            },
            "summary": "默认启动计划",
            "assumptions": [],
        }


class RuleBasedIntentProfilerClient:
    async def profile_intent(
        self,
        intent_text: str,
        reasoning_sink: ReasoningSink | None = None,
    ) -> dict[str, Any]:
        normalized = intent_text.lower()
        if any(keyword in normalized for keyword in ("today", "tonight", "by ", "4pm", "deadline", "今天", "下午", "前必须")):
            profile = {
                "intent_type": "short_term_delivery",
                "time_horizon": "hours",
                "confidence_score": 0.72,
            }
        elif any(keyword in normalized for keyword in ("buy", "pick up", "顺便", "买", "快递", "缴", "交一下")):
            profile = {
                "intent_type": "context_checklist",
                "time_horizon": "hours",
                "confidence_score": 0.7,
            }
        elif any(keyword in normalized for keyword in ("should i", "whether", "迷茫", "辞职", "转行", "不知道")):
            profile = {
                "intent_type": "exploration_decision",
                "time_horizon": "days",
                "confidence_score": 0.68,
            }
        else:
            profile = {
                "intent_type": "long_term_growth",
                "time_horizon": "weeks",
                "confidence_score": 0.62,
            }
        await emit_reasoning(
            reasoning_sink,
            code="INTENT_PROFILED",
            message="正在识别任务类型...",
            node="intent_profiler_node",
        )
        return profile


def intent_profiler_node_factory(intent_profiler: IntentProfilerClient):
    async def intent_profiler_node(state: AgentState) -> AgentState:
        reasoning_sink = ListReasoningSink()
        raw_profile = await _call_intent_profiler(
            intent_profiler,
            state.get("intent_text", ""),
            reasoning_sink,
        )
        intent_profile = IntentProfile.model_validate(raw_profile).model_dump(mode="json")
        next_state: AgentState = {
            **state,
            "intent_profile": intent_profile,
            "reasoning_events": [
                *state.get("reasoning_events", []),
                *reasoning_sink.events,
                {
                    "node": "intent_profiler_node",
                    "code": "INTENT_PROFILE_READY",
                    "message": "已识别任务类型，正在进入计划拆解...",
                },
            ],
        }
        return prune_state(next_state)

    return intent_profiler_node


async def _call_intent_profiler(
    intent_profiler: IntentProfilerClient,
    intent_text: str,
    reasoning_sink: ReasoningSink,
) -> dict[str, Any]:
    parameters = inspect.signature(intent_profiler.profile_intent).parameters
    if "reasoning_sink" in parameters:
        return await intent_profiler.profile_intent(intent_text, reasoning_sink=reasoning_sink)
    return await intent_profiler.profile_intent(intent_text)


def build_planner_prompt(
    intent_text: str,
    *,
    intent_profile: dict[str, Any] | None = None,
    feedback: str | None = None,
    current_task_tree_summary: str | None = None,
    validation_errors: list[str] | None = None,
) -> str:
    intent_type = _intent_type_from_profile(intent_profile)
    parts = [
        "你是 EasyPlan 的任务拆解 Agent。",
        HARD_RULES_PROMPT,
        INTENT_STRATEGY_PROMPTS[intent_type],
        "输出必须是符合 TaskTree JSON Schema 的 JSON。",
        f"用户意图：{intent_text}",
    ]
    if current_task_tree_summary:
        parts.append(f"当前计划摘要：{current_task_tree_summary}")
    if feedback:
        parts.append(f"用户自然语言反馈：{feedback}")
    if validation_errors:
        parts.append(
            "验证失败，请继续拆解并按以下具体原因修正，不要重复输出同类错误："
            + "; ".join(validation_errors)
        )
    return "\n".join(parts)


def planner_node_factory(planner: PlannerClient):
    async def planner_node(state: AgentState) -> AgentState:
        prompt = build_planner_prompt(
            state.get("intent_text", ""),
            intent_profile=state.get("intent_profile"),
            feedback=state.get("refinement_feedback"),
            current_task_tree_summary=_task_tree_summary(state.get("task_tree")),
            validation_errors=state.get("validation_errors"),
        )
        reasoning_sink = ListReasoningSink()
        task_tree = await _call_planner(planner, prompt, reasoning_sink)
        next_state: AgentState = {
            **state,
            "task_tree": task_tree,
            "reasoning_events": [
                *state.get("reasoning_events", []),
                *reasoning_sink.events,
                {
                    "node": "planner_node",
                    "code": "PLAN_CREATED",
                    "message": "已生成结构化任务树，正在进行规则校验",
                },
            ],
        }
        pruned = prune_state(next_state)
        for key in DISALLOWED_CHECKPOINT_KEYS:
            if key in state:
                pruned[key] = None
        return pruned

    return planner_node


async def _call_planner(
    planner: PlannerClient,
    prompt: str,
    reasoning_sink: ReasoningSink,
) -> dict[str, Any]:
    parameters = inspect.signature(planner.create_plan).parameters
    if "reasoning_sink" in parameters:
        return await planner.create_plan(prompt, reasoning_sink=reasoning_sink)
    return await planner.create_plan(prompt)


async def task_tree_validator_node(state: AgentState) -> AgentState:
    errors = _validate_task_tree(
        state.get("task_tree"),
        intent_profile=state.get("intent_profile"),
    )
    if not errors:
        return {
            "task_tree": TaskTree.model_validate(state["task_tree"]).model_dump(mode="json"),
            "validation_status": "valid",
            "validation_errors": [],
        }

    attempts = state.get("replan_attempts", 0)
    return {
        "validation_status": "needs_replan" if attempts < MAX_REPLAN_ATTEMPTS else "failed",
        "validation_errors": errors,
        "replan_attempts": attempts + 1,
    }


def route_after_validation(state: AgentState) -> str:
    if state.get("validation_status") == "valid":
        return "human_review"
    if state.get("validation_status") == "needs_replan":
        return "planner"
    return "failed"


async def failed_validation_node(state: AgentState) -> AgentState:
    return {
        "error": {
            "code": "TASK_TREE_VALIDATION_FAILED",
            "message": "; ".join(state.get("validation_errors", [])),
        }
    }


async def persist_internal_tasks_node(state: AgentState) -> AgentState:
    from app.db.session import async_session

    tasks, dependencies = flatten_task_tree_for_persistence(
        state["task_tree"],
        user_id=state["user_id"],
        thread_id=state["thread_id"],
    )
    now = datetime.now(timezone.utc)
    async with async_session() as session:
        async with session.begin():
            await _persist_tasks_idempotently(session, tasks, dependencies)
            await session.execute(
                update(AgentThread)
                .where(
                    AgentThread.user_id == UUID(str(state["user_id"])),
                    AgentThread.thread_id == state["thread_id"],
                )
                .values(
                    status="succeeded",
                    current_node="persist_internal_tasks",
                    task_tree=state.get("task_tree"),
                    completed_at=now,
                    updated_at=now,
                )
            )
    return {"task_persistence_status": "succeeded"}


async def _persist_tasks_idempotently(
    session: Any,
    tasks: list[Task],
    dependencies: list[TaskDependency],
) -> None:
    tasks_by_generated_id = {task.id: task for task in tasks}
    client_id_by_generated_id = {
        task.id: task.client_node_id
        for task in tasks
    }
    task_layers = _group_tasks_by_depth(tasks, tasks_by_generated_id)
    actual_task_id_by_client_id: dict[str, UUID] = {}

    for depth in sorted(task_layers):
        rows = [
            _task_insert_row(
                task,
                parent_task_id=_resolve_parent_task_id(
                    task=task,
                    actual_task_id_by_client_id=actual_task_id_by_client_id,
                    client_id_by_generated_id=client_id_by_generated_id,
                ),
            )
            for task in task_layers[depth]
        ]
        if rows:
            await session.execute(
                insert(Task.__table__)
                .values(rows)
                .on_conflict_do_nothing(index_elements=["thread_id", "client_node_id"])
            )
        actual_task_id_by_client_id.update(
            await _load_task_ids_by_client_node_id(
                session,
                user_id=tasks[0].user_id,
                thread_id=tasks[0].thread_id,
                client_node_ids=[task.client_node_id for task in tasks],
            )
        )

    dependency_rows = _dependency_insert_rows(
        dependencies,
        actual_task_id_by_client_id=actual_task_id_by_client_id,
        client_id_by_generated_id=client_id_by_generated_id,
    )
    if dependency_rows:
        await session.execute(
            insert(TaskDependency.__table__)
            .values(dependency_rows)
            .on_conflict_do_nothing(index_elements=["task_id", "depends_on_task_id"])
        )


def _group_tasks_by_depth(
    tasks: list[Task],
    tasks_by_generated_id: dict[UUID, Task],
) -> dict[int, list[Task]]:
    depth_by_task_id: dict[UUID, int] = {}

    def depth_for(task: Task) -> int:
        if task.id in depth_by_task_id:
            return depth_by_task_id[task.id]
        if task.parent_task_id is None:
            depth = 0
        else:
            parent = tasks_by_generated_id[task.parent_task_id]
            depth = depth_for(parent) + 1
        depth_by_task_id[task.id] = depth
        return depth

    layers: dict[int, list[Task]] = {}
    for task in tasks:
        layers.setdefault(depth_for(task), []).append(task)
    return layers


def _resolve_parent_task_id(
    *,
    task: Task,
    actual_task_id_by_client_id: dict[str, UUID],
    client_id_by_generated_id: dict[UUID, str],
) -> UUID | None:
    if task.parent_task_id is None:
        return None
    parent_client_id = client_id_by_generated_id[task.parent_task_id]
    return actual_task_id_by_client_id[parent_client_id]


async def _load_task_ids_by_client_node_id(
    session: Any,
    *,
    user_id: UUID,
    thread_id: str,
    client_node_ids: list[str],
) -> dict[str, UUID]:
    result = await session.execute(
        select(Task).where(
            Task.user_id == user_id,
            Task.thread_id == thread_id,
            Task.client_node_id.in_(client_node_ids),
        )
    )
    rows = result.scalars().all()
    return {task.client_node_id: task.id for task in rows}


def _task_insert_row(task: Task, *, parent_task_id: UUID | None) -> dict[str, Any]:
    return {
        "id": task.id,
        "user_id": task.user_id,
        "thread_id": task.thread_id,
        "parent_task_id": parent_task_id,
        "client_node_id": task.client_node_id,
        "title": task.title,
        "description": task.description,
        "node_type": task.node_type,
        "status": task.status,
        "view_bucket": task.view_bucket,
        "estimated_minutes": task.estimated_minutes,
        "sort_order": task.sort_order,
        "ai_generated": task.ai_generated,
        "user_edited": task.user_edited,
        "metadata": task.metadata_,
    }


def _dependency_insert_rows(
    dependencies: list[TaskDependency],
    *,
    actual_task_id_by_client_id: dict[str, UUID],
    client_id_by_generated_id: dict[UUID, str],
) -> list[dict[str, UUID]]:
    rows: list[dict[str, UUID]] = []
    seen: set[tuple[UUID, UUID]] = set()
    for dependency in dependencies:
        task_client_id = client_id_by_generated_id[dependency.task_id]
        depends_on_client_id = client_id_by_generated_id[dependency.depends_on_task_id]
        task_id = actual_task_id_by_client_id[task_client_id]
        depends_on_task_id = actual_task_id_by_client_id[depends_on_client_id]
        pair = (task_id, depends_on_task_id)
        if pair in seen:
            continue
        seen.add(pair)
        rows.append(
            {
                "id": dependency.id,
                "task_id": task_id,
                "depends_on_task_id": depends_on_task_id,
            }
        )
    return rows


def flatten_task_tree_for_persistence(
    task_tree: dict[str, Any],
    *,
    user_id: str | UUID,
    thread_id: str,
    default_view_bucket: str = "planned",
) -> tuple[list[Task], list[TaskDependency]]:
    parsed = TaskTree.model_validate(task_tree)
    user_uuid = UUID(str(user_id))
    tasks: list[Task] = []
    dependency_pairs: list[tuple[str, str]] = []
    id_by_client_node_id: dict[str, UUID] = {}

    def visit(node: Any, parent_task_id: UUID | None, sort_order: int) -> None:
        task_id = uuid4()
        id_by_client_node_id[node.client_node_id] = task_id
        tasks.append(
            Task(
                id=task_id,
                user_id=user_uuid,
                thread_id=thread_id,
                parent_task_id=parent_task_id,
                client_node_id=node.client_node_id,
                title=node.title,
                description=node.description,
                node_type=node.node_type,
                status="active",
                view_bucket=default_view_bucket,
                estimated_minutes=node.estimated_minutes,
                sort_order=sort_order,
                ai_generated=True,
                user_edited=False,
                metadata_={},
            )
        )
        for dependency in node.depends_on:
            dependency_pairs.append((node.client_node_id, dependency))
        for child_index, child in enumerate(node.children):
            visit(child, task_id, child_index)

    visit(parsed.root, None, 0)

    dependencies = [
        TaskDependency(
            id=uuid4(),
            task_id=id_by_client_node_id[task_id],
            depends_on_task_id=id_by_client_node_id[depends_on_task_id],
        )
        for task_id, depends_on_task_id in dependency_pairs
    ]
    return tasks, dependencies


def _validate_task_tree(task_tree: Any, *, intent_profile: dict[str, Any] | None = None) -> list[str]:
    try:
        parsed = TaskTree.model_validate(task_tree)
    except ValidationError as exc:
        return [str(exc)]

    errors: list[str] = []
    seen: set[str] = set()
    nodes_by_id: dict[str, dict[str, Any]] = {}
    _collect_rule_errors(parsed.root.model_dump(mode="json"), seen, nodes_by_id, errors)
    _collect_dependency_errors(nodes_by_id, errors)
    _collect_global_size_errors(parsed, errors)
    _collect_strategy_errors(parsed, _intent_type_from_profile(intent_profile), errors)
    return errors


def _collect_rule_errors(
    node: dict[str, Any],
    seen: set[str],
    nodes_by_id: dict[str, dict[str, Any]],
    errors: list[str],
) -> None:
    client_node_id = node["client_node_id"]
    if client_node_id in seen:
        errors.append(f"{client_node_id}: duplicate client_node_id")
    seen.add(client_node_id)
    nodes_by_id[client_node_id] = node

    if node["node_type"] == "action":
        if not node.get("verb"):
            errors.append(f"{client_node_id}: verb is required")

    for child in node.get("children", []):
        _collect_rule_errors(child, seen, nodes_by_id, errors)


def _collect_dependency_errors(nodes_by_id: dict[str, dict[str, Any]], errors: list[str]) -> None:
    graph: dict[str, list[str]] = {}
    for node_id, node in nodes_by_id.items():
        dependencies = node.get("depends_on", [])
        graph[node_id] = list(dependencies)
        for dependency in dependencies:
            if dependency not in nodes_by_id:
                errors.append(f"{node_id}: depends_on references unknown node {dependency}")

    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(node_id: str, path: list[str]) -> None:
        if node_id in visiting:
            cycle_start = path.index(node_id) if node_id in path else 0
            cycle = " -> ".join([*path[cycle_start:], node_id])
            errors.append(f"dependency cycle detected: {cycle}")
            return
        if node_id in visited:
            return
        visiting.add(node_id)
        for dependency in graph.get(node_id, []):
            if dependency in nodes_by_id:
                visit(dependency, [*path, dependency])
        visiting.remove(node_id)
        visited.add(node_id)

    for node_id in nodes_by_id:
        visit(node_id, [node_id])


def _collect_global_size_errors(task_tree: TaskTree, errors: list[str]) -> None:
    top_level_nodes = task_tree.root.children
    if len(top_level_nodes) > MAX_TOP_LEVEL_NODES:
        errors.append(
            f"global_scope: top-level node count must be <= {MAX_TOP_LEVEL_NODES}"
        )
    for node in top_level_nodes:
        if len(node.children) > MAX_CHILDREN_PER_TOP_LEVEL:
            errors.append(
                f"{node.client_node_id}: children count must be <= {MAX_CHILDREN_PER_TOP_LEVEL}"
            )


def _collect_strategy_errors(task_tree: TaskTree, intent_type: str, errors: list[str]) -> None:
    if intent_type == "short_term_delivery":
        first_action = _first_action(task_tree)
        if first_action is not None and _is_low_value_icebreaker(first_action):
            errors.append(
                f"{first_action.client_node_id}: short_term_delivery first task is a low-value icebreaker"
            )
        return

    if intent_type == "long_term_growth":
        first_action = _first_action(task_tree)
        if first_action is None or first_action.estimated_minutes > 5:
            node_id = first_action.client_node_id if first_action is not None else "root"
            errors.append(
                f"验证失败：{node_id}: long_term_growth first action must be a low-barrier icebreaker。请把第一步改成必须 <= 5 分钟、具体、低阻力的启动动作。"
            )
        total_nodes = sum(1 for _ in _iter_task_nodes(task_tree.root))
        max_depth = _task_tree_depth(task_tree.root)
        if total_nodes > MAX_TOP_LEVEL_NODES + MAX_CHILDREN_PER_TOP_LEVEL:
            errors.append("验证失败：long_term_growth scope is too broad for Phase 1。请只保留高层 roadmap，并只展开当前 Phase 1 的 24-72 小时行动。")
        if max_depth > LONG_TERM_MAX_DEPTH:
            errors.append("验证失败：long_term_growth phase depth is too deep for Phase 1。请减少深层嵌套，只输出启动阶段行动。")
        if _contains_long_term_full_cycle_language(task_tree):
            errors.append(
                "验证失败：long_term_growth plan must stay within 72-hour Phase 1 instead of covering the full long-term cycle。请不要排满完整备考期、训练期或长期周期。"
            )
        if _contains_long_term_schedule_language(task_tree):
            errors.append(
                "验证失败：long_term_growth 出现第1周/第2周/第3个月/每天坚持等长期排期。请只展开当前 Phase 1 的 24-72 小时行动。"
            )
        if _top_level_looks_like_long_term_curriculum(task_tree):
            errors.append(
                "验证失败：long_term_growth 顶层任务像完整课程大纲。请把 roadmap 放入 assumptions，只在 tasks 中保留 Phase 1 行动。"
            )
        if _contains_overlong_long_term_action(task_tree):
            errors.append(
                "验证失败：long_term_growth tasks 中存在超过当前启动阶段的长期任务。请拆成 24-72 小时内可完成的行动。"
            )
        return

    if intent_type == "exploration_decision":
        if _contains_exploration_execution_language(task_tree):
            errors.append(
                "验证失败：exploration_decision 不应直接生成长期执行计划。请改为问题澄清、信息收集、小实验和决策节点。"
            )
        if not _contains_exploration_discovery_language(task_tree):
            errors.append(
                "验证失败：exploration_decision 缺少信息收集或决策节点。请加入澄清问题、调研、访谈、小实验或成本收益比较。"
            )
        return

    if intent_type == "context_checklist":
        max_depth = _task_tree_depth(task_tree.root)
        if max_depth > 3:
            errors.append("验证失败：context_checklist task tree is too deep for a checklist。请不要生成多层复杂任务树。")
        top_level_nodes = task_tree.root.children
        if len(top_level_nodes) > 1 and not any(node.node_type == "group" for node in top_level_nodes):
            errors.append("验证失败：context_checklist related actions should be grouped by context。请按地点、工具、时间或顺路关系聚合。")


def _intent_type_from_profile(intent_profile: dict[str, Any] | None) -> str:
    if not isinstance(intent_profile, dict):
        return "general"
    intent_type = intent_profile.get("intent_type")
    if isinstance(intent_type, str) and intent_type in INTENT_STRATEGY_PROMPTS:
        return intent_type
    return "general"


def _first_action(task_tree: TaskTree) -> Any | None:
    return next(
        (node for node in _iter_task_nodes(task_tree.root) if node.node_type == "action"),
        None,
    )


def _iter_task_nodes(node: Any):
    yield node
    for child in node.children:
        yield from _iter_task_nodes(child)


def _task_tree_depth(node: Any) -> int:
    if not node.children:
        return 1
    return 1 + max(_task_tree_depth(child) for child in node.children)


def _is_low_value_icebreaker(node: Any) -> bool:
    text = " ".join(
        value
        for value in (node.title, node.description or "", node.verb)
        if value
    ).lower()
    return any(term.lower() in text for term in LOW_VALUE_ICEBREAKER_TERMS)


def _contains_long_term_full_cycle_language(task_tree: TaskTree) -> bool:
    text = _task_tree_text(task_tree)
    return any(keyword in text for keyword in LONG_TERM_SCOPE_KEYWORDS)


def _contains_long_term_schedule_language(task_tree: TaskTree) -> bool:
    text = _task_tree_text(task_tree)
    return any(re.search(pattern, text) for pattern in LONG_TERM_HORIZON_PATTERNS)


def _top_level_looks_like_long_term_curriculum(task_tree: TaskTree) -> bool:
    stage_like_nodes = [
        node
        for node in task_tree.root.children
        if any(term in f"{node.title} {node.description or ''}" for term in LONG_TERM_CURRICULUM_TERMS)
    ]
    return len(stage_like_nodes) >= 3


def _contains_overlong_long_term_action(task_tree: TaskTree) -> bool:
    return any(
        node.node_type == "action" and node.estimated_minutes > 120
        for node in _iter_task_nodes(task_tree.root)
    )


def _contains_exploration_execution_language(task_tree: TaskTree) -> bool:
    text = _task_tree_text(task_tree)
    return any(re.search(pattern, text) for pattern in EXPLORATION_EXECUTION_PATTERNS)


def _contains_exploration_discovery_language(task_tree: TaskTree) -> bool:
    text = _task_tree_text(task_tree)
    return any(term in text for term in EXPLORATION_DISCOVERY_TERMS)


def _task_tree_text(task_tree: TaskTree) -> str:
    text_parts = [task_tree.summary, *task_tree.assumptions]
    for node in _iter_task_nodes(task_tree.root):
        text_parts.extend(
            value
            for value in (node.title, node.description or "", node.verb)
            if value
        )
    return " ".join(text_parts)


def _task_tree_summary(task_tree: dict[str, Any] | None) -> str | None:
    if not task_tree:
        return None
    summary = task_tree.get("summary")
    if isinstance(summary, str):
        return summary
    root = task_tree.get("root", {})
    title = root.get("title")
    return title if isinstance(title, str) else None
