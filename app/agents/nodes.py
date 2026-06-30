from datetime import datetime, timezone
import inspect
import json
import re
from typing import Any, Protocol
from uuid import UUID, uuid4

from pydantic import ValidationError
from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert

from app.agents.state import AgentState, DISALLOWED_CHECKPOINT_KEYS, prune_state
from app.api.schemas import ACTION_QUALITY_FIELDS, IntentProfile, TaskTree
from app.models.task import Task, TaskDependency
from app.models.thread import AgentThread
from app.services.action_quality import score_action_node
from app.services.llm_service import ListReasoningSink, ReasoningSink, emit_reasoning
from app.services.phase_planning import (
    choose_next_action,
    phase_planning_enabled,
    validate_next_phase_transition,
)


MAX_REPLAN_ATTEMPTS = 3
MAX_TOP_LEVEL_NODES = 12
MAX_CHILDREN_PER_TOP_LEVEL = 3
LONG_TERM_MAX_DEPTH = 4
ACTION_QUALITY_MIN_RUNTIME_SCORE = 70
LONG_ACTION_DONE_CRITERIA_MINUTES = 20
INVALID_DONE_CRITERIA_VALUES = ("完成任务", "学习完成", "完成即可")
INVALID_START_HINT_VALUES = ("开始做", "准备开始")
INVALID_FALLBACK_ACTION_VALUES = ("少做一点", "降低难度")
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
    r"(执行|制定).{0,10}(转行|创业).{0,12}计划",
    r"(直接|立即).{0,6}(辞职|转行|创业|报名|投递|执行)",
    r"(报名|投递|辞职).{0,12}(课程|岗位|项目)",
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

RULE_PRIORITY_PROMPT = """规则优先级：
1. intent_type 对应策略高于普通任务拆解习惯。
2. Scope Horizon 高于计划完整性。宁可少给，也不要排满全周期。
3. Strategy Compliance 高于任务数量。宁可生成 4 个正确任务，也不要生成 12 个错误任务。
4. JSON Schema 合法性高于表达丰富度。
5. 当前阶段可执行性高于长期完整性。"""

HARD_RULES_PROMPT = """硬性规则：
1. 整个任务树最多只能包含 12 个顶层节点（Group/Action）。
2. 每个顶层节点最多只能包含 3 个子节点。
3. Scope Horizon 规则：
   - 对 long_term_growth，只允许输出当前启动阶段 Phase 1 的任务。
   - Phase 1 默认覆盖未来 24-72 小时。
   - 可以提及未来阶段名称，但不得展开未来阶段的具体任务。
   - 不得生成完整周期计划、每日打卡表、周计划、月计划或备考全程表。
   - 如果用户要求长期完整计划，也只能输出高层阶段名称，不得输出未来阶段任务。
   - Roadmap 仅允许写入 planning_context.roadmap；不得把未来阶段展开为 TaskTree 任务。
4. 禁止输出 Markdown、解释性段落或 schema 外字段。
5. title 和 description 必须短而具体，避免长文本导致 JSON 截断。
6. assumptions 必须是字符串数组；默认 assumptions 为 []；所有 estimated_minutes 必须是 >=1 的整数；字符串内不要包含未转义换行。"""

ACTION_QUALITY_PROMPT = """Action Quality 字段生成要求：
1. 对所有 Action，尽量生成 done_criteria；done_criteria 必须具体说明做到什么程度算完成。
2. start_hint 必须是用户可以立刻执行的第一步。
3. fallback_action 必须是更小、更低门槛的替代动作。
4. 对 estimated_minutes >= 20 的 Action，建议生成 fallback_action。
5. 不要为了补字段扩大任务树规模，不要在 planning_context 之外新增 roadmap/current_phase/next_action 字段。
6. 字段值必须是一句短句，建议 <=30 汉字；不要包含英文双引号、换行、列表或多句解释。

无效字段内容禁止：
- done_criteria: “完成任务”
- start_hint: “开始做”
- fallback_action: “少做一点”
- done_criteria: “学习完成”
- start_hint: “准备好材料”

有效字段示例：
- done_criteria: “保存 1 个可打开的 N3 真题链接”
- start_hint: “打开浏览器搜索“N3 真题 PDF””
- fallback_action: “如果没有精力做 20 题，就先做前 5 题”"""

THREE_TIER_PLANNING_PROMPT = """三层规划契约：
1. planning_context 必须存在，并生成 3-5 个 Roadmap 阶段。
2. roadmap 只包含 phase_id、order、title、objective、status；不得包含 estimated_minutes、日期或子任务。
3. 首次规划只能把第 1 个阶段标记为 current，其余阶段标记为 planned。
4. current_phase 必须逐字段匹配 roadmap 中唯一的 current 阶段。
5. TaskTree.root 只能展开当前 Phase 1 的任务，不得展开未来阶段。
6. next_action_client_node_id 必须引用当前阶段内一个 Action；服务端持久化时会确定性复核。
7. intent_type 和 time_horizon 必须与已识别的 IntentProfile 完全一致。
8. assumptions 保持为 []，不得把 Roadmap 编码进 assumptions。"""

NO_PHASE_PLANNING_PROMPT = """三层规划契约：
planning_context 必须为 null。不要生成 Roadmap、Current Phase 或 Next Action。"""

PHASE_PLANNING_DISABLED_PROMPT = """三层规划功能开关已关闭：
planning_context 必须为 null，并保持 v1.2.4 TaskTree 输出。"""

NEXT_PHASE_PROMPT = """下一阶段规划模式：
1. completed 阶段必须逐字段保持不变，禁止修改 phase_id、order、title、objective 或 status。
2. intent_type 和 time_horizon 必须保持不变，不得重新分类。
3. 原 current 阶段只能变为 completed，且除 status 外不得修改。
4. 只能展开一个新的 current phase；TaskTree.root 只能包含该阶段任务。
5. 只允许重写原 roadmap 的 planned 后缀，不得生成所有未来阶段的任务。
6. 所有 Action 继续满足 Action Quality、Scope Horizon 和依赖规则。
7. 新阶段 TaskTree.root 及其所有后代节点必须使用全新的 client_node_id，不得复用已提交计划中的任何 client_node_id。
8. 不得创建新 thread，不得输出服务器 request/lease 字段。"""

INTENT_STRATEGY_PROMPTS = {
    "long_term_growth": """策略：这是长周期成长型目标。你需要使用「破冰法则 + 视野控制」。
第一个任务必须是极其简单的破冰动作，建议 <=5 分钟，用来降低启动阻力。
但后续任务可以是 25-60 分钟的深度工作。
不要排满整个周期，只输出当前启动阶段 Phase 1，且 Phase 1 只覆盖最近 72 小时内可以启动的行动。
可以给 3-5 个高层阶段作为 roadmap；roadmap 只能是阶段标题和目的，不允许 estimated_minutes，不允许具体日期，不允许子任务。
Roadmap 必须写入 planning_context.roadmap；TaskTree.root.children 只能放当前 Phase 1 tasks。
Phase 1 建议覆盖未来 24-72 小时。
禁止排满整个备考期、训练期、写作期或长期周期。
禁止生成“第1周/第2周/第3个月”这种长期排期任务。

long_term_growth 禁止：
- 完整备考周期计划
- 每日打卡表
- 未来几周或几个月的详细任务
- 第一项就是高压力深度任务
- 超出 Phase 1 的具体行动
- 第一项写成安装环境、自我评估、明确目标、草拟大纲、训练计划、学习计划或备考计划

long_term_growth 必须：
- root.children 中第一个 action 的 estimated_minutes 必须 <= 5。
- 第一个 action 只能是低阻力启动动作，例如搜索一篇资料、保存一个样例、写下一个问题、选定一个最小素材。
- 即使用户当前已经能做较长动作，也必须先安排 <=5 分钟破冰；实际跑步、训练、写作、练习放在第二步以后。
- 首个破冰 Action 必须生成 start_hint，且 start_hint 必须是打开页面、搜索关键词、写下一个问题等立刻可做的第一步。
- 第一个 action 不得是安装环境、自我评估、明确目标、草拟大纲、训练计划、学习计划或备考计划。
- Phase 1 任务标题中不要写“训练计划”“学习计划”“备考计划”“长期路线”“课程大纲”。
- summary 写成“Phase 1 启动计划”，不要回显完整长期目标。
- assumptions 必须是 []；Roadmap 只能写入 planning_context.roadmap，未来阶段不得写入 TaskTree 任务。

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

short_term_delivery 禁止：
- 打开电脑
- 打开 Word
- 新建文档
- 准备开始
- 想一想
- 搜集资料但没有明确产出

<反面教材>
动作：「打开 Word 准备写 PPT」，耗时：2 分钟。问题：这是废话，不是有效任务。

<正面教材>
动作：「列出商业计划书的核心痛点大纲」，耗时：15 分钟。
动作：「撰写商业模式章节」，耗时：45 分钟。""",
    "context_checklist": """策略：这是情境清单型任务。
无需深度拆解，也不需要破冰动作。
请按地理位置、工具环境、顺路关系或时间场景聚合。
目标是减少切换成本和遗漏，而不是生成复杂任务树。

context_checklist 禁止：
- 深度父子任务树
- 长期阶段计划
- 复杂推理任务
- 每个琐事再拆成多个子任务

context_checklist 必须：
- 如果有 2 个以上零散事项，root.children 必须使用 group 节点。
- 当清单中有 2 个以上事项，且可按地点、工具、顺路关系或时间场景聚合时，优先使用 Group，不要直接输出多个散乱顶层 Action。
- 同一地点、工具或时间场景的事项必须放入同一个 Group，不得拆成多个顶层 Action。
- root.children 顶层必须全部是 group 节点，不允许把多个事项作为顶层 action 平铺。
- 即使只有一个场景，也建立一个 group，例如“出门前”“通勤路上”“手机处理”“缴费处理”。
- group 按位置、工具、顺路关系、出门前/路上/到家后等场景命名。
- 每个 group 下面放 1-3 个 action；不要把所有零散事项平铺成多个顶层 action。

<正面教材>
组：「下班路上」
动作：「去丰巢拿快递」
动作：「去超市买菜」

组：「手机处理」
动作：「缴电费」""",
    "exploration_decision": """策略：这是探索决策型任务。
先给 1-2 句当前判断，继续写入现有 task_tree.summary，例如“可以考虑，但先做低成本验证”。
summary 必须严格按这个顺序输出三段：
当前判断：先直接回答用户现在更适合继续探索、暂缓执行，还是暂不建议进入长期投入。
判断依据：说明 1-2 条当前依据，例如信息缺口、成本收益、现实约束或风险点。
下一步探索：再给信息收集、问题澄清、低成本验证和决策节点。
不要只给探索路线、不回答问题本身。
summary 必须是当前判断，不要只是“探索澄清计划”这类抽象标题。
不要直接生成长期执行计划，也不要假设用户已经做出最终决定。
不要假设用户已经决定。
请生成信息收集、问题澄清、最小成本测试和决策节点。
目标是降低不确定性，而不是强推执行。
当前阶段只生成信息收集、问题澄清、小实验和决策节点.
任务必须围绕澄清问题、信息收集、低成本验证、决策依据。
禁止直接生成完整转行计划、创业计划、长期学习计划。
禁止直接生成长期执行计划或连续投入型任务。
信息收集、小实验、决策节点任务建议生成 start_hint。
先给 1-2 句当前判断，再给判断依据和下一步探索。
当前判断必须先回答“现在更像值得继续探索，还是暂不建议立刻执行”，但不要把判断写成最终定论。
这 1-2 句当前判断必须继续写入现有 task_tree.summary，不新增字段，不改 schema，不扩 API。

exploration_decision 禁止：
- 假设用户已经做出最终决定
- 直接生成长期执行计划
- 连续投入型任务
- 生成打卡式任务
- 跳过信息收集和低成本验证

exploration_decision 输出措辞：
- root.children 最多 5 个顶层任务。
- summary 必须写成“当前判断：... 判断依据：... 下一步探索：...”，继续写入现有 task_tree.summary，不新增任何字段。
- summary 必须先回答问题，再说明依据和下一步探索；不要只给探索路线，也不要只写空泛标题；assumptions 必须是 []。
- 不要在 summary、assumptions、title、description 或 verb 中写“执行计划”“学习计划”“路线图”“报名”“投递”“直接”“立即”。
- 如果用户原文包含辞职、转行或创业，把用户原词改写为“方向A/选项A/当前选择”，保持探索口吻。
- 任务标题应使用“澄清/列出/收集/访谈/比较/验证/决策记录”，不要使用“制定计划/开始执行/报名课程/投递岗位”。

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
    planning_mode: str = "initial",
    committed_task_tree: dict[str, Any] | None = None,
    current_phase_task_summary: str | None = None,
) -> str:
    intent_type = _intent_type_from_profile(intent_profile)
    phase_contract = _phase_contract_prompt(
        intent_type=intent_type,
        planning_mode=planning_mode,
    )
    parts = [
        "你是 EasyPlan 的任务拆解 Agent。",
        RULE_PRIORITY_PROMPT,
        HARD_RULES_PROMPT,
        ACTION_QUALITY_PROMPT,
        INTENT_STRATEGY_PROMPTS[intent_type],
        phase_contract,
        "输出必须是符合 TaskTree JSON Schema 的 JSON。",
        f"用户意图：{intent_text}",
    ]
    if planning_mode == "next_phase":
        if current_phase_task_summary:
            parts.append(f"当前阶段任务完成摘要：{current_phase_task_summary}")
        if committed_task_tree:
            parts.append(
                "已提交计划（只读基线）："
                + json.dumps(committed_task_tree, ensure_ascii=False, separators=(",", ":"))
            )
    if current_task_tree_summary:
        parts.append(f"当前计划摘要：{current_task_tree_summary}")
    if feedback:
        parts.append(f"用户自然语言反馈：{feedback}")
    if validation_errors:
        if planning_mode == "next_phase":
            repair_instruction = (
                "验证失败，请只修复新阶段中被指出的任务；保持 intent_type/time_horizon 和所有 completed 阶段不变，"
                "不要重写整棵任务树，不要改写已提交阶段，不要重复输出同类错误："
            )
        else:
            repair_instruction = (
                "验证失败，请继续拆解并按以下具体原因修正；只修复验证指出的低质量任务，保持原 intent_type 和策略不变，"
                "不要重写整棵任务树，不得借验证修复机会改写既有 roadmap，不要重复输出同类错误："
            )
        parts.append(repair_instruction + "; ".join(validation_errors))
    return "\n".join(parts)


def _phase_contract_prompt(*, intent_type: str, planning_mode: str) -> str:
    if not phase_planning_enabled():
        return PHASE_PLANNING_DISABLED_PROMPT
    if planning_mode == "next_phase":
        return NEXT_PHASE_PROMPT
    if intent_type in {"long_term_growth", "exploration_decision"}:
        return THREE_TIER_PLANNING_PROMPT
    return NO_PHASE_PLANNING_PROMPT


def planner_node_factory(planner: PlannerClient):
    async def planner_node(state: AgentState) -> AgentState:
        prompt = build_planner_prompt(
            state.get("intent_text", ""),
            intent_profile=state.get("intent_profile"),
            feedback=state.get("refinement_feedback"),
            current_task_tree_summary=_task_tree_summary(state.get("task_tree")),
            validation_errors=state.get("validation_errors"),
            planning_mode=state.get("planning_mode", "initial"),
            committed_task_tree=state.get("committed_task_tree"),
            current_phase_task_summary=state.get("current_phase_task_summary"),
        )
        reasoning_sink = ListReasoningSink()
        task_tree = _normalize_task_tree_phase_contract(
            await _call_planner(planner, prompt, reasoning_sink),
            intent_profile=state.get("intent_profile"),
            planning_mode=state.get("planning_mode", "initial"),
            committed_task_tree=state.get("committed_task_tree"),
        )
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


def _normalize_task_tree_phase_contract(
    task_tree: dict[str, Any],
    *,
    intent_profile: dict[str, Any] | None,
    planning_mode: str,
    committed_task_tree: dict[str, Any] | None,
) -> dict[str, Any]:
    parsed = TaskTree.model_validate(task_tree)
    context = parsed.planning_context
    if context is None:
        return parsed.model_dump(mode="json")

    expected_intent_type, expected_horizon = _expected_phase_contract_identity(
        intent_profile=intent_profile,
        planning_mode=planning_mode,
        committed_task_tree=committed_task_tree,
    )
    if expected_intent_type is None or expected_horizon is None:
        return parsed.model_dump(mode="json")
    if context.intent_type == expected_intent_type and context.time_horizon == expected_horizon:
        return parsed.model_dump(mode="json")

    normalized_context = context.model_copy(
        update={
            "intent_type": expected_intent_type,
            "time_horizon": expected_horizon,
        }
    )
    return parsed.model_copy(update={"planning_context": normalized_context}).model_dump(mode="json")


def _expected_phase_contract_identity(
    *,
    intent_profile: dict[str, Any] | None,
    planning_mode: str,
    committed_task_tree: dict[str, Any] | None,
) -> tuple[str | None, str | None]:
    if planning_mode == "next_phase" and committed_task_tree is not None:
        try:
            committed_context = TaskTree.model_validate(committed_task_tree).planning_context
        except ValidationError:
            committed_context = None
        if committed_context is not None:
            return committed_context.intent_type, committed_context.time_horizon

    intent_type = _intent_type_from_profile(intent_profile)
    time_horizon = (intent_profile or {}).get("time_horizon")
    if intent_type not in {"long_term_growth", "exploration_decision"}:
        return None, None
    if not isinstance(time_horizon, str):
        return None, None
    return intent_type, time_horizon


async def task_tree_validator_node(state: AgentState) -> AgentState:
    errors = _validate_task_tree(
        state.get("task_tree"),
        intent_profile=state.get("intent_profile"),
        planning_mode=state.get("planning_mode"),
        committed_task_tree=state.get("committed_task_tree"),
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


async def finalize_phase_rejection_node(state: AgentState) -> AgentState:
    from app.db.session import async_session

    request_id = state.get("phase_request_id")
    if state.get("planning_mode") != "next_phase" or not request_id:
        return {}

    now = datetime.now(timezone.utc)
    async with async_session() as session:
        async with session.begin():
            result = await session.execute(
                select(AgentThread)
                .where(
                    AgentThread.user_id == UUID(str(state["user_id"])),
                    AgentThread.thread_id == state["thread_id"],
                )
                .with_for_update()
            )
            thread = result.scalar_one_or_none()
            if thread is None:
                raise RuntimeError("phase thread not found during cancellation")
            envelope = _terminal_phase_envelope(
                thread.interrupt_payload,
                request_id=request_id,
                status="cancelled",
                now=now,
            )
            await session.execute(
                update(AgentThread)
                .where(
                    AgentThread.user_id == UUID(str(state["user_id"])),
                    AgentThread.thread_id == state["thread_id"],
                )
                .values(
                    status="succeeded",
                    current_node="cancel_phase",
                    lease_owner=None,
                    lease_expires_at=None,
                    interrupt_payload=envelope,
                    updated_at=now,
                )
            )
    return {"phase_request_status": "cancelled"}


async def persist_internal_tasks_node(state: AgentState) -> AgentState:
    from app.db.session import async_session

    tasks, dependencies = flatten_task_tree_for_persistence(
        state["task_tree"],
        user_id=state["user_id"],
        thread_id=state["thread_id"],
    )
    now = datetime.now(timezone.utc)
    is_next_phase = state.get("planning_mode") == "next_phase"
    phase_request_id = state.get("phase_request_id") if is_next_phase else None
    if is_next_phase and not phase_request_id:
        raise RuntimeError("phase_request_id is required for next-phase persistence")
    async with async_session() as session:
        async with session.begin():
            phase_thread: AgentThread | None = None
            if is_next_phase:
                thread_result = await session.execute(
                    select(AgentThread)
                    .where(
                        AgentThread.user_id == UUID(str(state["user_id"])),
                        AgentThread.thread_id == state["thread_id"],
                    )
                    .with_for_update()
                )
                phase_thread = thread_result.scalar_one_or_none()
                if phase_thread is None:
                    raise RuntimeError("phase thread not found during persistence")
                if _is_confirmed_phase_request(phase_thread, phase_request_id):
                    return {"task_persistence_status": "succeeded"}
                await _assert_next_phase_client_ids_are_new(session, tasks)

            await _persist_tasks_idempotently(
                session,
                tasks,
                dependencies,
                require_new_task_ids=is_next_phase,
            )
            task_tree = await _with_server_derived_next_action(
                session,
                task_tree=state["task_tree"],
                user_id=UUID(str(state["user_id"])),
                thread_id=state["thread_id"],
            )
            update_values: dict[str, Any] = {
                "status": "succeeded",
                "current_node": "persist_internal_tasks",
                "task_tree": task_tree,
                "completed_at": now,
                "updated_at": now,
            }
            if is_next_phase:
                update_values.update(
                    lease_owner=None,
                    lease_expires_at=None,
                    interrupt_payload=_terminal_phase_envelope(
                        phase_thread.interrupt_payload,
                        request_id=phase_request_id,
                        status="confirmed",
                        now=now,
                    ),
                )
            await session.execute(
                update(AgentThread)
                .where(
                    AgentThread.user_id == UUID(str(state["user_id"])),
                    AgentThread.thread_id == state["thread_id"],
                )
                .values(**update_values)
            )
    return {"task_persistence_status": "succeeded"}


def _is_confirmed_phase_request(thread: AgentThread, request_id: str) -> bool:
    payload = thread.interrupt_payload
    if not isinstance(payload, dict):
        return False
    return (
        payload.get("type") == "phase_generation_state"
        and payload.get("request_id") == request_id
        and payload.get("status") == "confirmed"
        and (payload.get("history") or {}).get(request_id, {}).get("status") == "confirmed"
    )


async def _with_server_derived_next_action(
    session: Any,
    *,
    task_tree: dict[str, Any],
    user_id: UUID,
    thread_id: str,
) -> dict[str, Any]:
    parsed = TaskTree.model_validate(task_tree)
    context = parsed.planning_context
    if context is None or context.current_phase is None:
        return parsed.model_dump(mode="json")

    result = await session.execute(
        select(Task).where(
            Task.user_id == user_id,
            Task.thread_id == thread_id,
        )
    )
    persisted_tasks = list(result.scalars().all())
    task_by_client_id = {task.client_node_id: task for task in persisted_tasks}
    dependencies_by_task_id: dict[UUID, set[UUID]] = {}
    for node in _iter_task_nodes(parsed.root):
        task = task_by_client_id.get(node.client_node_id)
        if task is None:
            continue
        dependency_ids = {
            dependency_task.id
            for dependency_client_id in node.depends_on
            if (dependency_task := task_by_client_id.get(dependency_client_id)) is not None
        }
        if dependency_ids:
            dependencies_by_task_id[task.id] = dependency_ids

    next_task = choose_next_action(
        persisted_tasks,
        dependencies_by_task_id,
        context.current_phase.phase_id,
    )
    context.next_action_client_node_id = (
        next_task.client_node_id if next_task is not None else None
    )
    return parsed.model_dump(mode="json")


def _terminal_phase_envelope(
    current_payload: Any,
    *,
    request_id: str,
    status: str,
    now: datetime,
) -> dict[str, Any]:
    payload = current_payload if isinstance(current_payload, dict) else {}
    history = dict(payload.get("history") or {})
    history[request_id] = {
        "status": status,
        "updated_at": now.isoformat(),
    }
    return {
        "type": "phase_generation_state",
        "request_id": request_id,
        "status": status,
        "history": history,
    }


async def _persist_tasks_idempotently(
    session: Any,
    tasks: list[Task],
    dependencies: list[TaskDependency],
    *,
    require_new_task_ids: bool = False,
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
            statement = insert(Task.__table__).values(rows)
            if not require_new_task_ids:
                statement = statement.on_conflict_do_nothing(
                    index_elements=["thread_id", "client_node_id"]
                )
            await session.execute(statement)
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


async def _assert_next_phase_client_ids_are_new(
    session: Any,
    tasks: list[Task],
) -> None:
    existing_ids = await _load_task_ids_by_client_node_id(
        session,
        user_id=tasks[0].user_id,
        thread_id=tasks[0].thread_id,
        client_node_ids=[task.client_node_id for task in tasks],
    )
    if existing_ids:
        conflicting_ids = ", ".join(sorted(existing_ids))
        raise RuntimeError(
            "next-phase client_node_id values already exist in committed tasks: "
            f"{conflicting_ids}"
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
        "is_in_my_day": task.is_in_my_day,
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
    phase_metadata: dict[str, Any] = {"source": "ai"}
    if parsed.planning_context and parsed.planning_context.current_phase:
        current_phase = next(
            phase
            for phase in parsed.planning_context.roadmap
            if phase.phase_id == parsed.planning_context.current_phase.phase_id
        )
        phase_metadata.update(
            phase_id=current_phase.phase_id,
            phase_order=current_phase.order,
        )

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
                is_in_my_day=False,
                estimated_minutes=node.estimated_minutes,
                sort_order=sort_order,
                ai_generated=True,
                user_edited=False,
                metadata_=_action_quality_metadata(node, base_metadata=phase_metadata),
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


def _action_quality_metadata(node: Any, base_metadata: dict[str, Any] | None = None) -> dict[str, Any]:
    metadata = dict(base_metadata or {})
    for field in ACTION_QUALITY_FIELDS:
        value = getattr(node, field, None)
        if value is not None:
            metadata[field] = value
    return metadata


def _validate_task_tree(
    task_tree: Any,
    *,
    intent_profile: dict[str, Any] | None = None,
    planning_mode: str | None = None,
    committed_task_tree: dict[str, Any] | None = None,
) -> list[str]:
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
    intent_type = _intent_type_from_profile(intent_profile)
    _collect_phase_contract_errors(
        parsed,
        intent_profile=intent_profile,
        planning_mode=planning_mode,
        committed_task_tree=committed_task_tree,
        errors=errors,
    )
    _collect_strategy_errors(parsed, intent_type, errors)
    _collect_action_quality_errors(parsed, intent_type, errors)
    return errors


def _collect_phase_contract_errors(
    task_tree: TaskTree,
    *,
    intent_profile: dict[str, Any] | None,
    planning_mode: str | None,
    committed_task_tree: dict[str, Any] | None,
    errors: list[str],
) -> None:
    if planning_mode is None:
        return

    context = task_tree.planning_context
    intent_type = _intent_type_from_profile(intent_profile)
    if not phase_planning_enabled():
        if context is not None:
            errors.append("phase planning is disabled; planning_context must be null")
        return

    if planning_mode == "next_phase":
        if committed_task_tree is None:
            errors.append("next_phase requires committed_task_tree")
            return
        try:
            committed = TaskTree.model_validate(committed_task_tree)
        except ValidationError as exc:
            errors.append(f"committed_task_tree is invalid: {exc}")
            return
        if committed.planning_context is None or context is None:
            errors.append("next_phase requires planning_context in committed and proposed trees")
            return
        errors.extend(validate_next_phase_transition(committed.planning_context, context))
        _collect_cross_tree_client_id_errors(
            committed=committed,
            proposed=task_tree,
            errors=errors,
        )
        return

    if intent_type in {"long_term_growth", "exploration_decision"}:
        if context is None:
            errors.append(f"{intent_type} initial plan requires planning_context")
            return
        if context.intent_type != intent_type:
            errors.append("planning_context intent_type must match IntentProfile")
        expected_horizon = (intent_profile or {}).get("time_horizon")
        if isinstance(expected_horizon, str) and context.time_horizon != expected_horizon:
            errors.append("planning_context time_horizon must match IntentProfile")
    elif context is not None:
        errors.append(f"{intent_type} planning_context must be null")


def _collect_cross_tree_client_id_errors(
    *,
    committed: TaskTree,
    proposed: TaskTree,
    errors: list[str],
) -> None:
    committed_ids = {node.client_node_id for node in _iter_task_nodes(committed.root)}
    proposed_ids = {node.client_node_id for node in _iter_task_nodes(proposed.root)}
    for client_node_id in sorted(committed_ids & proposed_ids):
        errors.append(
            f"{client_node_id}: next_phase client_node_id already exists in committed tree; "
            "generate a new client_node_id for this node"
        )


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
                _format_validator_feedback(
                    error_code="LOW_VALUE_ICEBREAKER_IN_SPRINT",
                    intent_type=intent_type,
                    failed_rule="short_term_delivery 禁止项",
                    problem="short_term_delivery 的首个任务是“打开电脑/打开 Word/新建文档/准备开始/想一想”这类低价值破冰动作。",
                    offender=first_action,
                    fix_suggestion="删除低价值破冰，直接从有明确产出的时间盒任务开始。",
                )
            )
        return

    if intent_type == "long_term_growth":
        first_action = _first_action(task_tree)
        if first_action is None or first_action.estimated_minutes > 5:
            errors.append(
                _format_validator_feedback(
                    error_code="MISSING_LOW_BARRIER_ICEBREAKER",
                    intent_type=intent_type,
                    failed_rule="破冰法则",
                    problem="long_term_growth 的第一个 action 不是 <= 5 分钟的低阻力破冰动作。",
                    offender=first_action or task_tree.root,
                    fix_suggestion="把第一步改成必须 <= 5 分钟、具体、低阻力的启动动作；后续再进入 25-60 分钟深度任务。",
                )
            )
        total_nodes = sum(1 for _ in _iter_task_nodes(task_tree.root))
        max_depth = _task_tree_depth(task_tree.root)
        if total_nodes > MAX_TOP_LEVEL_NODES + MAX_CHILDREN_PER_TOP_LEVEL:
            errors.append(
                _format_validator_feedback(
                    error_code="HORIZON_OVER_EXPANDED",
                    intent_type=intent_type,
                    failed_rule="Scope Horizon",
                    problem="输出任务数量过多，像完整周期计划而不是当前启动阶段 Phase 1。",
                    offender=task_tree.root,
                    fix_suggestion="只保留当前启动阶段 Phase 1 的 24-72 小时行动；未来阶段最多保留标题，不得展开任务。",
                )
            )
        if max_depth > LONG_TERM_MAX_DEPTH:
            errors.append(
                _format_validator_feedback(
                    error_code="HORIZON_OVER_EXPANDED",
                    intent_type=intent_type,
                    failed_rule="Scope Horizon",
                    problem="任务树嵌套过深，超出当前启动阶段 Phase 1 的行动地图范围。",
                    offender=task_tree.root,
                    fix_suggestion="减少深层嵌套，只输出当前启动阶段 Phase 1 的 24-72 小时行动。",
                )
            )
        if _contains_long_term_full_cycle_language(task_tree):
            errors.append(
                _format_validator_feedback(
                    error_code="HORIZON_OVER_EXPANDED",
                    intent_type=intent_type,
                    failed_rule="Scope Horizon",
                    problem="输出包含完整周期、未来阶段或全程计划语言，违反 Scope Horizon。",
                    offender=task_tree.root,
                    fix_suggestion="只保留当前启动阶段 Phase 1 的任务；未来阶段最多保留标题，不得展开任务。",
                )
            )
        if _contains_long_term_schedule_language(task_tree):
            errors.append(
                _format_validator_feedback(
                    error_code="HORIZON_OVER_EXPANDED",
                    intent_type=intent_type,
                    failed_rule="Scope Horizon",
                    problem="输出出现第1周/第2周/第3个月/每天坚持等长期排期。",
                    offender=task_tree.root,
                    fix_suggestion="删除周计划、月计划和每日打卡表，只展开当前 Phase 1 的 24-72 小时行动。",
                )
            )
        if _top_level_looks_like_long_term_curriculum(task_tree):
            errors.append(
                _format_validator_feedback(
                    error_code="HORIZON_OVER_EXPANDED",
                    intent_type=intent_type,
                    failed_rule="Scope Horizon",
                    problem="顶层任务像完整课程或长期阶段大纲，不是当前启动阶段任务。",
                    offender=task_tree.root,
                    fix_suggestion="未来阶段最多写成 assumptions 中的高层标题；TaskTree.root.children 只保留 Phase 1 行动。",
                )
            )
        if _contains_overlong_long_term_action(task_tree):
            errors.append(
                _format_validator_feedback(
                    error_code="HORIZON_OVER_EXPANDED",
                    intent_type=intent_type,
                    failed_rule="Scope Horizon",
                    problem="tasks 中存在超过当前启动阶段的长期任务。",
                    offender=task_tree.root,
                    fix_suggestion="拆成 24-72 小时内可完成的 Phase 1 行动，不要展开完整周期。",
                )
            )
        return

    if intent_type == "exploration_decision":
        if not _has_exploration_answer_first_summary(task_tree.summary):
            errors.append(
                _format_validator_feedback(
                    error_code="EXPLORATION_ANSWER_MISSING",
                    intent_type=intent_type,
                    failed_rule="exploration_decision 回答层契约",
                    problem="summary 只给了探索路线，或没有先回答当前判断、判断依据和下一步探索。不要只给探索路线、不回答问题本身。",
                    offender=task_tree.root,
                    fix_suggestion="先回答当前判断，再给判断依据和下一步探索；继续写入现有 task_tree.summary，不新增字段。",
                )
            )
        if _contains_exploration_execution_language(task_tree):
            errors.append(
                _format_validator_feedback(
                    error_code="EXPLORATION_PREMATURE_EXECUTION",
                    intent_type=intent_type,
                    failed_rule="exploration_decision 禁止项",
                    problem="输出假设用户已经做出最终决定，并直接生成长期执行计划。",
                    offender=task_tree.root,
                    fix_suggestion="改为信息收集、问题澄清、低成本验证和决策节点，不要生成长期执行计划。",
                )
            )
        if not _contains_exploration_discovery_language(task_tree):
            errors.append(
                _format_validator_feedback(
                    error_code="EXPLORATION_DISCOVERY_MISSING",
                    intent_type=intent_type,
                    failed_rule="exploration_decision 策略",
                    problem="输出缺少信息收集、问题澄清、低成本验证或决策节点。",
                    offender=task_tree.root,
                    fix_suggestion="加入澄清问题、调研、访谈、小实验或成本收益比较任务。",
                )
            )
        return

    if intent_type == "context_checklist":
        max_depth = _task_tree_depth(task_tree.root)
        if max_depth > 3:
            errors.append(
                _format_validator_feedback(
                    error_code="CHECKLIST_TOO_DEEP",
                    intent_type=intent_type,
                    failed_rule="context_checklist 禁止项",
                    problem="情境清单被拆成深度父子任务树或复杂推理任务。",
                    offender=task_tree.root,
                    fix_suggestion="压平为轻量清单，按位置、工具、顺路关系或时间场景聚合。",
                )
            )
        top_level_nodes = task_tree.root.children
        if len(top_level_nodes) > 1 and not any(node.node_type == "group" for node in top_level_nodes):
            errors.append(
                _format_validator_feedback(
                    error_code="CHECKLIST_NOT_GROUPED",
                    intent_type=intent_type,
                    failed_rule="context_checklist 聚合规则",
                    problem="多个琐事没有按共同情境聚合，容易造成切换成本和遗漏。",
                    offender=task_tree.root,
                    fix_suggestion="按位置、工具、顺路关系或时间场景聚合；不要把每个琐事再拆成多个子任务。",
                )
            )


def _collect_action_quality_errors(task_tree: TaskTree, intent_type: str, errors: list[str]) -> None:
    for node in _iter_task_nodes(task_tree.root):
        if node.node_type != "action":
            continue

        quality = score_action_node(node)
        quality_issues = list(quality.reasons)
        has_low_quality_score = quality.score < ACTION_QUALITY_MIN_RUNTIME_SCORE

        if has_low_quality_score:
            errors.append(
                _format_action_quality_feedback(
                    error_code="ACTION_QUALITY_LOW_SCORE",
                    intent_type=intent_type,
                    task_title=node.title,
                    actionability_score=quality.score,
                    quality_issues=quality_issues,
                    offender=node,
                    problem="Action 可执行性分数过低，任务标题或完成标准过于空泛。",
                    fix_suggestion=(
                        "只修复该低质量任务：改成明确动词 + 明确对象 + 具体产出，补充可检查的 done_criteria。"
                    ),
                )
            )
            continue

        if quality.has_abstract_violation:
            errors.append(
                _format_action_quality_feedback(
                    error_code="ACTION_QUALITY_ABSTRACT_TASK",
                    intent_type=intent_type,
                    task_title=node.title,
                    actionability_score=quality.score,
                    quality_issues=quality_issues or ["abstract_task_violation"],
                    offender=node,
                    problem="Action 标题明显空泛，抽象词不能单独作为任务核心。",
                    fix_suggestion="只修复该低质量任务：把抽象动作改成具体对象、具体输出和可验证完成标准。",
                )
            )

        invalid_field_issues = _invalid_action_quality_field_issues(node)
        if invalid_field_issues:
            errors.append(
                _format_action_quality_feedback(
                    error_code="ACTION_QUALITY_INVALID_FIELD",
                    intent_type=intent_type,
                    task_title=node.title,
                    actionability_score=quality.score,
                    quality_issues=invalid_field_issues,
                    offender=node,
                    problem="Action Quality 字段使用了无效占位内容。",
                    fix_suggestion=(
                        "只修复该低质量任务：给出具体完成标准、可立即执行的第一步和更小替代动作。"
                    ),
                )
            )

        if (
            not quality.has_done_criteria
            and isinstance(node.estimated_minutes, int)
            and node.estimated_minutes >= LONG_ACTION_DONE_CRITERIA_MINUTES
        ):
            errors.append(
                _format_action_quality_feedback(
                    error_code="ACTION_QUALITY_MISSING_DONE_CRITERIA",
                    intent_type=intent_type,
                    task_title=node.title,
                    actionability_score=quality.score,
                    quality_issues=["missing_done_criteria"],
                    offender=node,
                    problem="预计时间较长的 Action 缺少 done_criteria，用户难以判断做到什么程度算完成。",
                    fix_suggestion="只修复该低质量任务：补充一句可检查的完成标准，不要拆大整棵任务树。",
                )
            )


def _invalid_action_quality_field_issues(node: Any) -> list[str]:
    issues: list[str] = []
    if _has_invalid_quality_value(getattr(node, "done_criteria", None), INVALID_DONE_CRITERIA_VALUES):
        issues.append("invalid_done_criteria")
    if _has_invalid_quality_value(getattr(node, "start_hint", None), INVALID_START_HINT_VALUES):
        issues.append("invalid_start_hint")
    if _has_invalid_quality_value(getattr(node, "fallback_action", None), INVALID_FALLBACK_ACTION_VALUES):
        issues.append("invalid_fallback_action")
    return issues


def _has_invalid_quality_value(value: Any, invalid_values: tuple[str, ...]) -> bool:
    if not isinstance(value, str):
        return False
    normalized = value.strip().strip("。.!！ ")
    if normalized in invalid_values:
        return True
    return any(invalid_value in normalized and len(normalized) <= len(invalid_value) + 4 for invalid_value in invalid_values)


def _format_action_quality_feedback(
    *,
    error_code: str,
    intent_type: str,
    task_title: str,
    actionability_score: int,
    quality_issues: list[str],
    offender: Any,
    problem: str,
    fix_suggestion: str,
) -> str:
    return "\n".join(
        [
            f"错误代码: {error_code}",
            f"intent_type: {intent_type}",
            "failed_rule: Action Quality",
            f"任务标题: {task_title}",
            f"actionability_score: {actionability_score}",
            f"quality_issues: {', '.join(quality_issues) if quality_issues else 'unknown'}",
            f"问题: {problem}",
            f"违规任务/组: {_node_summary(offender)}",
            f"修复建议: {fix_suggestion}",
            "修复约束: 保持原 intent_type 和策略不变；只修复该低质量任务；不要重写整棵任务树；不要新增 roadmap/current_phase/next_action。",
        ]
    )


def _format_validator_feedback(
    *,
    error_code: str,
    intent_type: str,
    failed_rule: str,
    problem: str,
    offender: Any,
    fix_suggestion: str,
) -> str:
    return "\n".join(
        [
            f"错误代码: {error_code}",
            f"intent_type: {intent_type}",
            f"failed_rule: {failed_rule}",
            f"问题: {problem}",
            f"违规任务/组: {_node_summary(offender)}",
            f"修复要求: {fix_suggestion}",
        ]
    )


def _node_summary(node: Any) -> str:
    title = _truncate_text(getattr(node, "title", "") or "")
    description = _truncate_text(getattr(node, "description", "") or "")
    client_node_id = getattr(node, "client_node_id", "unknown")
    node_type = getattr(node, "node_type", "unknown")
    estimated_minutes = getattr(node, "estimated_minutes", None)
    children = getattr(node, "children", []) or []
    summary = (
        f"{client_node_id} [{node_type}] title='{title}', "
        f"estimated_minutes={estimated_minutes}, children={len(children)}"
    )
    if description:
        summary += f", description='{description}'"
    return summary


def _truncate_text(value: str, limit: int = 80) -> str:
    return value if len(value) <= limit else f"{value[:limit]}..."


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


def _has_exploration_answer_first_summary(summary: str) -> bool:
    if not isinstance(summary, str):
        return False
    normalized = re.sub(r"\s+", "", summary)
    current_index = normalized.find("当前判断：")
    basis_index = normalized.find("判断依据：")
    next_index = normalized.find("下一步探索：")
    if min(current_index, basis_index, next_index) < 0:
        return False
    if not (current_index <= basis_index <= next_index):
        return False
    current_text = normalized[current_index + len("当前判断：") : basis_index]
    basis_text = normalized[basis_index + len("判断依据：") : next_index]
    next_text = normalized[next_index + len("下一步探索：") :]
    if not current_text or not basis_text or not next_text:
        return False
    route_only_markers = ("下一步", "先", "然后", "再", "最后")
    if current_text.startswith(route_only_markers):
        return False
    return True


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
