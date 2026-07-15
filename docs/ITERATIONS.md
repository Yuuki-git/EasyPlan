# EasyPlan 迭代与决策日志 (Iteration & Decision Log)

本文档由产品经理 (PM) 维护，记录 EasyPlan 的架构演进、核心工程决策以及阶段性里程碑。旨在为技术复盘、开源贡献指引及商业化展示提供全景视角。

---

## 🚀 版本: v1.1.0-alpha (全栈架构打通与交互重构)
**时间**: 2024-05-05
**阶段目标**: 完成从 0 到 1 的技术底座搭建，实现前后端 E2E (End-to-End) 真实数据流闭环，确立基于“意图驱动”的产品体验范式。

### 🐛 工程挑战与技术瓶颈 (Engineering Challenges)
1. **分布式状态一致性问题**：LangGraph 工作流在执行 `__interrupt__` 挂起时，由于 Checkpointer 的短暂生命周期（内存级），导致前端重连或发起 Refine 请求时丢失上下文图状态。
2. **长连接 (SSE) 鉴权与流控**：标准的 `EventSource` API 无法携带 `Authorization` Header，导致严格的 JWT 网关拦截流式请求；Nginx 反向代理默认开启 Buffer，导致 SSE 流式输出发生阻塞（Chunked Transfer 失败）。
3. **数据库级事务与外键冲突**：初期认证模块采用了内存仓库 (`InMemoryUserRepository`)，而业务模块写入 PostgreSQL 时触发了严格的外键约束 (`ForeignKeyViolation`)。
4. **模型指令漂移 (Instruction Drift)**：多语言环境下，大语言模型 (LLM) 出现语言对齐失败（中文意图输出英文 JSON），且对“两分钟法则”颗粒度理解存在偏差。

### 💡 架构优化与解决方案 (Architectural Solutions)
1. **持久化与状态机韧性 (State Machine Resilience)**：重构 `agent_runtime.py`，实现 Checkpointer 单例化，并利用后台任务 (`BackgroundTasks`) 与真实 PostgreSQL 会话 (`AsyncSession`) 保证中断状态的强一致性落盘。
2. **协议层降级与切片重播**：在 FastAPI 依赖注入层 (`get_user_for_sse`) 引入 URL Query Token 降级鉴权；重写 SSE 发送器，引入 `asyncio.Queue` 和基于 `last_event_id` 的精准增量切片算法，解决断网重连时的“暴力回放”问题。
3. **延迟鉴权 (Lazy Auth) 模式**：前端采用 Zustand 实现意图拦截。未登录状态下允许用户无阻碍输入（降低 Fogg 模型中的门槛），拦截后平滑拉起 AuthModal，并在 JWT 签发后通过暂存态 (`pendingIntent`) 实现请求的无缝重播。
4. **提示词工程加固 (Prompt Engineering)**：在 System Prompt 中强制注入强约束指令（Hard Constraints）保障多语言一致性。针对颗粒度不足的问题，计划引入 Few-Shot Prompting 与 COT (Chain of Thought) 模板进行深度微调。
5. **护眼美学与情感化设计 (Empathetic UX)**：彻底做减法，废弃刺眼的纯白主题，将默认底色改为温润的“护眼羊皮纸 (Parchment)”。精细化问候语情绪引擎，区分“傍晚”的包裹感与“深夜”的释放感，配合低边界感的 Placeholder 彻底消除“空白画布恐惧”。
6. **游标清零与并发锁 (Connection Management)**：前端在 `useSSE.ts` 中引入 `isMounted` 闭包并发锁，并在生成新意图时强制清空游标引用，彻底封死单页面应用的连接池耗尽漏洞。
7. **全量异步图引擎 (Async Graph)**：剔除后端恶心的 `_run_async` 包装，全量切换至原生 `await graph.astream`；大幅放宽 Pydantic 对根节点的时长限制，将“微动作<5分钟”的裁决权还给 `validator_node`，允许大模型试错并自我纠偏。

### 🎉 业务成果 (Business Value)
* **高可用底座**：全栈通车 (React -> FastAPI -> LangGraph -> PostgreSQL)，具备云原生 4C4G 服务器的一键 Docker 化自动建表部署能力。
* **体验护城河**：成功实现了无需等待即可交互的“流式树状生成”与“对话式微调 (Refine)”核心链路。

---

## 📅 版本规划 (Roadmap)

### 🔜 v1.2 系列 (原生生态与沉浸式体验)
**战略方针 (Strategic Pivot)**：全面转向 UI-Driven Development (UDD)，暂缓外部 MCP 适配，分四个迭代阶段攻克原生闭环与真流式渲染。

#### 📍 v1.2.1: 打地基 —— 原生看板与数据落盘
*   **前端**：构建包含“我的一天”和“计划中”视图的 Native Task Board 组件树。
*   **后端**：设计 `tasks` 表，实现 `persist_internal_tasks_node` 将计划落盘。

#### 📍 v1.2.2: 交互重塑 —— 情绪价值与安全护城河 (Completed)
*   **文案与视觉升华**：彻底废弃“看板”这一企业级词汇，转向更私人的表达（“我的手帐”）。
*   **无痕心流与跨视图流转**：引入了极简的 `InlineTaskInput`，并在计划中视图加入了 `☀️ 加入我的一天` 的乐观更新 (Optimistic UI) 转移能力。
*   **原生任务清理 (Task Deletion)**：新增 `DELETE /api/tasks/{id}` 接口及极简的悬浮垃圾桶 `Trash2` 按钮，配合乐观删除与 Framer Motion 退场动画，补齐看板的最终闭环。
*   **情绪空状态 (Emotional Empty States)**：在不同视图清空后，展示带有温度的文案（如“今天辛苦了，去喝杯茶吧”），提供正向情绪反馈。
*   **划除的仪式感 (The Completion Ritual)**：任务勾选后触发 Framer Motion 弹簧动画，停留 2 秒后再伴随渐变滑出，延缓多巴胺释放。
*   **深水区排雷 (Security & DB Integrity)**：建立全局 401 鉴权熔断网防范 Token 过期；在 `task_repository.py` 严格引入 DDD 事务隔离 (`session.begin()`) 杜绝高并发脏写；使用 `isMounted` 并发锁和退场延时清理解决单页应用连接池泄漏与 Framer Motion 死锁。

#### 📍 v1.2.3: 意图画像与动态路由 (Intent Profiling & Routing) (Completed / Stable)
**总纲 (Minimum Closed Loop)**：`Intent Profiling → Strategy Routing → Few-shot Selection → JSON Size Control → Basic Eval`。

**🧠 核心 AI 能力升级 (Core AI Capabilities)**：
*   **评测集先行 (Eval Driven)**：建立 `planning_cases.jsonl`，初版包含 32 条核心测试用例，覆盖长周期成长型、短期产出型、情境清单型、探索决策型四类意图。以自动评测替代纯手感调参，初版基准集目标通过率达到 85%+。
*   **动态意图画像与路由 (Intent Profiling & Routing)**：引入 `Intent → Profile → Strategy` 管线。模型先判断任务的时间跨度、模糊程度、心理阻力和执行场景，再选择对应拆解策略，而不是对所有目标套用同一套规则。
*   **重塑破冰法则 (The Ice-breaker Task)**：废除全局强制 `<5分钟` 的固定拆解规则。通过动态 Few-shot 教导模型：长周期、高阻力目标需要低门槛破冰动作；短期冲刺任务则禁用“打开软件”“新建文档”等低价值动作。
*   **动态 Few-shot 注入 (Few-shot Selection)**：根据 Intent Profile 动态注入对应正反例，避免单一超长 Prompt。Few-shot 用于稳定意图分类、拆解粒度、任务语言风格和 JSON 结构。
*   **视野控制与规模限制 (Scope Horizon)**：针对宏大目标只展开“启动阶段”，保留高层阶段地图，但不排满全周期。在 Prompt 中加入硬约束：最多 12 个顶层任务，每个最多 3 个子任务，并限制标题与描述长度，降低 JSON 过长、截断和解析失败风险。
*   **轻量级策略校验 (Strategy Validator)**：Validator 不只检查 JSON 合法性，也检查策略红线。例如短期冲刺不得出现低智破冰动作，长周期目标不得排满三个月计划，情境清单不得生成复杂深度任务树。违规时触发有限次数 Replan，失败后进入降级方案。

**✨ 附加交互体验 (Additional UX Experience)**：
*   **轻量版迷雾解锁 (Fog of War Lite)**：用户完成启动阶段任务后，系统提示是否继续解锁下一阶段计划。v1.2.3 实现了手动触发式解锁闭环 (`generateNextPhasePlan`)。
*   **沉浸式行内编辑 (Inline Edit)**：支持看板任务双击直接修改标题、描述和时间。并且后端 `PATCH /api/tasks/{task_id}` 契约已升级，支持显式传入 `null` 来清空字段。
*   **SSE 韧性升级**：收到 `snapshot_required` 时，彻底清空游标并执行 250ms 延迟重连，攻克断流死锁。

**✅ v1.2.3 验收标准 (Acceptance Criteria)**：
1. 能精准识别 4 类 `intent_type`。
2. 能够根据 `intent_type` 动态注入对应的 Few-Shot 样本。
3. 测试用例验证：长周期目标仅展开最近 72 小时的细节。
4. 测试用例验证：Sprint（短期冲刺）产出物中不再生成低价值的破冰动作。
5. 测试用例验证：Brain Dump（情境清单）能按场景逻辑聚合。
6. JSON 输出被硬性卡死在最大节点数和字段长度限制内。
7. `planning_cases.jsonl` 初版不少于 32 条测试数据。
8. 自动评测脚本跑通，且策略采纳正确率达到 85% 以上。

#### 📍 v1.2.4: Action Quality & Fallback（任务质量与失败兜底） (Completed / RC.1)
v1.2.4 的目标是让 EasyPlan 从“策略正确的计划生成器”升级为“任务可执行的行动系统”。在 v1.2.3 已完成意图路由和策略校验的基础上，重点解决生成任务过于空泛、缺少完成标准、用户不知道如何开始，以及执行阻力过高时缺少降级动作的问题。

**核心能力**：
*   **Action Quality Validator**：新增任务质量校验器，拦截“学习语法 / 研究一下”等低可执行性任务，强制要求明确动词与合理耗时。
*   **Actionability Score**：为每个 Action 生成内部可执行性评分，用于 Validator 裁决与 Replan。
*   **完成标准 (done_criteria)**：关键任务必须说明做到什么程度算完成。
*   **开始提示 (start_hint)**：为高阻力任务提供最小启动提示（如“打开浏览器搜索PDF”）。
*   **降级动作 (fallback_action)**：当用户做不动时提供更小版本（如“做不动20题就做5题”）。

**验收标准**：
*   ✅ 保持 v1.2.3 指标不降的前提下，新增：`Action Quality Pass Rate >= 85%`、`Done Criteria Coverage >= 90%`、`JSON Parse Success Rate = 100%`。（注：DeepSeek 主验收已达到 32/32）。
*   **非目标**：本版本坚决不碰前端三层规划 UI、Task Copilot 和 Refine Diff。

#### 🩹 v1.2.4.1: Provider Robustness Patch (Backlog)
*   **Schema Enum Drift Repair**：将 `pydantic.ValidationError` 纳入 JSON Repair 重试链路，防范小模型幻觉枚举值（如输出 `node_type="leader"`）。
*   **Checklist 强制聚合**：在 Validator 中对 `context_checklist` 加入强校验，任务数 >=2 时必须存在 `group` 节点。

#### ✅ v1.2.5: 三层规划与阶段视野 (Three-Tier Planning) (Completed)
*   **执行领航员**：落地“远期只给地图，近期给计划，眼前给动作”。
*   **条件触发的 Roadmap UI**：路线图绝非全局标配，严格由 Intent Profile 决定显示逻辑：
    *   `long_term_growth`：默认显示 3-5 个高层阶段路线图，提供长期方向感但不展开。
    *   `exploration_decision`：显示“探索路线”（如“澄清问题 → 收集信息 → 验证 → 做决定”），降低决策不确定性。
    *   `short_term_delivery` & `context_checklist`：**不显示路线图**，直接聚焦时间盒交付与情境聚合。
*   **执行反馈**：增加 Current Phase 目标说明、Next Action 高亮，把计划列表升级为“执行引导界面”。
*   **生成态重定义**：生成界面按单次 run 管理，不复用上一次 intent、retry 或 next phase 的 reasoning 历史；SSE 重连允许恢复当前 run，但重复事件不得重复渲染。
*   **探索决策先答后拆**：`exploration_decision` 不再只展示生成过程，首屏必须先给一句当前判断，再进入阶段路线与行动树。
*   **生成态逃生口**：AI 生成与下一阶段预览支持取消；确认后的 `SYNCING` 是不可撤销提交，只允许返回当前计划并在后台继续完成。
*   **时间表达降精度**：生成态优先显示“低投入 / 中投入 / 较重投入”等时间档位；正式进入看板后再展示 rounded 预计时长，避免伪精确。
*   **信息架构澄清**：“全部计划”明确为跨项目聚合视图，“项目”保留为 thread 级长期容器；同一任务可同时存在于项目视图和“全部计划”视图中，但 Roadmap 与 Current Phase 只属于项目上下文。

**已完成工程闭环**：
*   **同 thread 阶段推进**：下一阶段在原 thread 中生成、预览和确认追加，不再创建新的计划。
*   **三层规划上下文**：`Roadmap / Current Phase / Next Action` 进入 TaskTree 契约；Next Action 由后端基于依赖和任务状态确定。
*   **Committed / Preview 分离**：预览存放于 `interrupt_payload`，确认前不会覆盖已提交的 `task_tree`。
*   **Run 身份协议**：initial、refine、next phase 均使用唯一 `request_id`；SSE 以 `thread_id + run_type + request_id` 隔离。
*   **刷新与竞态恢复**：引入 active run、游标作用域、快照请求栅栏和 commit receipt，阻止历史事件或旧 Phase 1 快照覆盖 Phase 2。
*   **请求级取消**：生成中和待确认预览支持幂等取消并写入 tombstone；迟到结果不能恢复已取消 run。
*   **确认边界**：确认后进入不可撤销 `SYNCING`；用户可返回当前计划，后台继续完成并最终更新 Phase 2。
*   **跨视图一致性**：全部计划、项目和我的一天共享同一任务 ID 与完成状态，不因视图切换破坏项目结构。

#### 🩹 v1.2.5.1: Generation Experience Patch (Closed / Absorbed into v1.2.5 RC.2)
*   **范围定位**：v1.2.5.1 只负责收口生成态稳定性与信息架构边界，不再增加新的核心功能。
*   **已收口问题**：
    *   `exploration_decision` 首屏先给“当前判断”，避免用户只看到 reasoning 却拿不到判断。
    *   新 intent / retry / next phase preview 按单次 run 管理，不残留上一轮 reasoning。
    *   SSE replay 去重、stalled 检测和“返回当前计划 / 取消本次生成”逃生口闭环。
    *   “全部计划”与“项目”语义拆清，避免看起来像两套并列容器。
    *   `exploration_decision` 场景下的 `time_horizon` 漂移与 raw validation error 暴露问题已专项修复。
*   **收口结果**：该补丁后续发现的 cross-run SSE、旧快照覆盖、active-run 恢复和生成取消问题已在 v1.2.5 RC.2 修复并纳入自动化测试。

#### ✅ v1.2.6: 总览层与回答层 (Portfolio Overview & Answer Layer) (RC.1)
*   **全部计划升级为总览层**：
    *   “全部计划”不再只是任务聚合流，而是所有计划的 portfolio overview。
    *   展示计划标题、当前阶段摘要、下一步动作或最近任务，并支持点击进入对应项目。
*   **探索决策回答层升级**：
    *   `exploration_decision` 固定输出为“当前判断 -> 判断依据 -> 下一步探索”，先回答问题，再给路线。
    *   当前判断必须是临时判断，不得伪装成最终结论。
*   **重试语义收口**：
    *   `Retry` 降级为异常恢复按钮，仅在失败、卡住或 SSE 中断时出现。
    *   非异常场景如用户想换一种拆法，应提供“重新生成”而不是泛化 `Retry`。
*   **生成态信息降噪**：
    *   每次新生成只展示当前 run；旧 reasoning、节点状态、预览树和错误不会进入新 run。
    *   stalled 状态重连同一 request，不创建重复规划请求。
*   **2026-07-05 RC 验收**：
    *   Backend：`265 passed`
    *   Frontend Node 状态测试：全部通过
    *   Mounted `useSSE` Hook：`11 passed`
    *   Portfolio 组件测试：`11 passed`
    *   Frontend build、lint：通过
    *   DeepSeek Eval：`32/32`，Pass Rate、Intent、Strategy、JSON、Horizon、Action Quality 和 Done Criteria Coverage 均为 `100%`
    *   Average Actionability Score：`99.85%`
    *   Abstract Task Violation Rate：`0.75%`

#### ✅ v1.2.7-A: 长期执行循环 (Long-Term Execution Loop) (Completed / Release Gate)
*   **Schema v2 边界**：
    *   仅新建 `long_term_growth` 计划使用 schema v2；旧计划与其他 intent 继续使用 schema v1。
    *   当前阶段最多包含 2 个 practice loop、2 个 outcome checkpoint，未来 occurrence 不会被预生成。
*   **循环执行规则**：
    *   周配额按本地周统计，不足部分不结转到下一周。
    *   同一 loop 每个本地自然日最多记录一次完成；完成任务与写入 completion log 位于同一事务。
    *   排程产生一个普通 planned task，并默认加入“我的一天”；之后是否保留在“我的一天”仍由用户控制。
    *   频率调整从下一本地周创建新 revision，不改写历史周目标与完成日志。
*   **阶段复盘与推进**：
    *   readiness 同时计算 one-off、过程达成率和 outcome evidence。
    *   用户可选择 `proceed`、`extend`、`adjust` 或带理由的 `override`。
    *   下一阶段生成必须存在 finalized `proceed` 或 `override` review；override 理由长期保留在项目 Phase Records。
*   **验收范围**：
    *   新增 10 条长期执行 Eval，用例总数扩展为 42。
    *   Backend：`324 passed`。
    *   Frontend：全部 `.test.mjs`、Hook `11 passed`、Portfolio `12 passed`、长期执行 `15 passed`，build 与 lint 通过。
    *   `git diff --check` 通过；未发现硬编码 API key，临时 Eval 日志已清理。
    *   case 34 的“本周天气”正则误判已修复；有限交付 loop 与显式周频率均有确定性 Validator/replan，Eval 复用同一 Validator。
    *   case 40 连续三次 DeepSeek 验证全部通过。
    *   DeepSeek Validator-aware 42-case 实测 `42/42`；Pass Rate、Intent、Strategy、JSON、Horizon、Action Quality、Done Criteria Coverage 与 Long-Term Loop Contract 均为 `100%`。

#### ✅ v1.2.7.2: SSE Reliability & Generation UX (Backend Implemented)
**版本目标**：把 AI 生成过程从“黑盒等待 + 一次性刷日志”升级为“实时、稳定、低噪音、可恢复的过程反馈”。

**执行计划**：详见 `docs/superpowers/plans/2026-07-08-v1.2.7.2-sse-reliability-generation-ux.md`。

**非目标**：
*   不新增规划能力。
*   不修改 intent 策略。
*   不展示真实 chain-of-thought，只展示用户可理解的过程状态。

**后端任务**：
*   **统一 SSE event envelope**：所有事件统一携带 `event_id`、`thread_id`、`request_id`、`run_type`、`event_type`、`seq`、`created_at` 与 `payload`；`plan_ready`、`done`、`agent_error` 也必须带完整 envelope。
*   **阶段级实时 emit**：不能等 Planner 全部完成后再一次性推送 reasoning；关键阶段开始时立即推送 `run_started`、`intent_profile_started`、`intent_profile_completed`、`strategy_selected`、`planning_started`、`validation_started`、`repair_started`、`persistence_started`、`plan_ready`、`done`、`agent_error`。
*   **长耗时 heartbeat**：LLM 调用或保存过程超过 5-8 秒时发送 `still_running`；heartbeat 必须携带同一个 `request_id`，并在 run cancelled / done / error 后停止。
*   **run-scoped replay / reconnect**：`Last-Event-ID` 按 `thread_id + run_type + request_id` 恢复；历史 `done` 不得截断当前 run；事件缓存必须有 run 边界；`snapshot_required` 只在事件缺口时触发。
*   **后端测试**：覆盖阶段事件在 Planner 完成前发出、长耗时 heartbeat、终态事件携带 run 身份、旧 run 不截断新 run、同一 thread 多次 run 不串流。

**后端落地状态**：
*   FastAPI / AgentRuntime 已统一输出 run-scoped SSE envelope。
*   `initial`、`refine`、`next_phase` 均支持真实 run identity。
*   Stage events 与 `still_running` heartbeat 已接入，取消或 terminal 后停止。
*   Replay / reconnect 继续按 `thread_id + run_type + request_id` 隔离。

**前端任务**：
*   **ReasoningStream 改造为生成过程面板**：展示“当前状态 + 最近 3-5 条动态 + 折叠详细日志”，不再作为无限日志流占据主界面。
*   **展开 / 折叠规则**：生成中默认展开；`done` 后自动折叠并保留“已完成规划”摘要；`error` / `stalled` 保持展开；30 秒前不显示网络超时提示。
*   **retry / new run 清理**：重试时清空上一轮可见过程日志，不把旧 reasoning 堆到新 run；可保留一条“上次失败原因”；新 intent 清空旧 run 的过程信息与 `nodeStatuses`。
*   **activeRun 严格过滤**：前端只处理 `thread_id`、`request_id`、`run_type` 与当前 `activeRun` 完全匹配的事件；旧 EventSource cleanup 后，旧 handler 不得继续写 store。
*   **前端测试**：覆盖生成开始后立即展示过程、`done` 后自动折叠、`error` / `stalled` 保持展开、30 秒前不显示网络超时、retry 不混入旧日志、不匹配事件被丢弃、next phase 不受旧 initial run 干扰。

**前后端联调验收**：
1. 用户点击生成后，1 秒内看到“正在理解目标”等过程反馈。
2. LLM 较慢时，每 5-8 秒有 `still_running` 反馈。
3. 30 秒前不出现网络超时提示。
4. 生成完成后，过程面板自动折叠。
5. 重试不会堆叠上一轮日志。
6. 刷新 / 重连后不会回放旧 run 干扰当前 run。
7. 下一阶段生成不会被历史 initial `done` / `plan_ready` 影响。
8. `agent_error` 能正确显示错误，并保留恢复入口。

**优先级**：
*   **P0**：SSE envelope、真实 `request_id` / `run_type` / `seq`、阶段级实时 emit、activeRun 严格过滤、旧 run 不串流、`done` 后折叠与 `error` 保持展开。
*   **P1**：heartbeat、retry 日志清理、run-scoped replay、重连去重、前端过程面板降噪。
*   **P2**：更细阶段文案、详细日志折叠区、run lifecycle 可观测性指标。

#### ✅ v1.2.8: 规划模型差异化 (Planning Model Differentiation) (Completed)
**版本目标**：让短期交付和探索决策不再只是“不同 Prompt 下的普通任务树”，而是拥有可校验、可展示的独立业务结构。

**设计与执行文档**：
*   设计规格：`docs/superpowers/specs/2026-07-10-v1.2.8-planning-model-differentiation-design.md`
*   前后端执行计划：`docs/superpowers/plans/2026-07-10-v1.2.8-planning-model-differentiation.md`

**规划范围**：
*   `short_term_delivery`：新增 delivery strategy context，表达交付物、截止约束、时间预算与缓冲、Must Have / Can Cut、workstreams 和关键路径；仍然不显示 Roadmap。
*   `exploration_decision`：新增 decision strategy context，结构化表达当前判断、判断置信度、依据、信息缺口、低成本实验和决策门槛；继续保留 schema v1 探索路线。
*   `TaskTree` 新增 optional `strategy_context`，继续通过现有 thread JSONB 持久化，不新增数据库 schema。
*   历史计划不迁移；旧 exploration summary 保留前端 fallback。
*   DeepSeek Eval 计划从 42 cases 扩展到 54 cases，并增加 Delivery / Decision Contract 指标。

**后端验收（2026-07-11）**：
*   optional discriminated `strategy_context`、纯确定性 Validator、intent-specific Prompt、JSONB/API/SSE 往返与 OpenAPI 已完成。
*   Backend：`378 passed`；静态 OpenAPI 与运行时 schema 一致，`git diff --check` 通过。
*   P1 Horizon 契约已拆分：`expected_profile_horizon` 只比较目标总体跨度，`scope_horizon_rule` 独立校验本轮计划展开窗口；旧 `expected_horizon` 字段及兼容路径已移除。
*   cases 1-8 使用 `expected_profile_horizon=months` 与 `scope_horizon_rule=long_term_phase_1_72h`，不再混用 72 小时 Scope 表达 Profile。
*   DeepSeek 54-case 重新实测为 `54/54`：Profile Horizon Accuracy、Scope Horizon Compliance、合并 Horizon Accuracy 以及其余核心指标和五项 v1.2.8 新指标均为 `100%`。
*   strict gate 独立要求 Profile Horizon Accuracy 与 Scope Horizon Compliance 各为 `100%`。
*   前端 Delivery Summary、Decision Card、legacy fallback、项目/Portfolio 接入已完成；Reviewer 确认无 P0/P1，剩余 strategy lifecycle 自动化覆盖缺口作为 P2 测试债务进入 v1.3.0。

**非目标**：
*   不改变 v1.2.7-A 的长期 schema v2、practice loop、outcome checkpoint 或 phase gate。
*   不修改 `context_checklist`，不引入 Task Copilot、自动重排、个性化或探索到长期计划的自动转换。

#### ✅ v1.3.0: 任务级副驾驶 (Task Copilot / Action Coach) (Completed / Released)
**版本目标**：在不重写整份计划的前提下，帮助用户解决单个任务的启动阻力、执行卡点和粒度过大问题。

**设计与执行文档**：
*   设计规格：`docs/superpowers/specs/2026-07-12-v1.3.0-task-copilot-action-coach-design.md`
*   前后端执行计划：`docs/superpowers/plans/2026-07-12-v1.3.0-task-copilot-action-coach.md`

**MVP 范围**：
*   `start`：生成 2–10 分钟的立即启动动作，确认后保存为 `start_hint`。
*   `unstick`：生成 2–3 个恢复选项，确认后把所选动作保存为 `fallback_action`。
*   `decompose`：预览并确认 2–5 个子任务；父任务进入 roll-up，由子任务完成状态确定性驱动。
*   使用独立 `task_assist` run、SSE 生命周期和持久化 proposal，不复用 plan-level `activeRun`。
*   Apply 前不修改任务；Apply 具备所有权、幂等、过期、stale-task 和事务回滚防线。

**实现与自动化验收（2026-07-13）**：
*   Pydantic/OpenAPI、`task_assist_runs`、DeepSeek 结构化 proposal、独立 SSE Runtime、取消/恢复、事务 Apply 和 roll-up 已实现。
*   Backend：`452 passed`；Task Assist DeepSeek Eval `18/18`，六项指标全部 `100%`。
*   原 Planning DeepSeek Eval 保持 `54/54`，Profile/Scope Horizon、Strategy Context、Action Quality 等 strict 指标全部 `100%`。
*   前端 Action Coach、独立 task-assist SSE、刷新恢复、stale 重试、Apply 和父任务 roll-up 已接入；Task Assist 专项 `24 passed`，build 与 lint 通过。
*   运行中取消成功后关闭并清理 panel，失败时保留面板并显示错误，不再留下空面板。
*   My Day 以父任务为承诺锚点：Assist children 只嵌套展示，不能独立加入，也不会因父节点缺失而成为顶层任务。
*   Planning Eval case 41 的“完整课程”误判已收窄并加入评分器回归测试；strict release gate 保持全指标 `100%`。
*   7 个 legacy `.test.mjs` VM harness 已全部完成适配并映射 `../lib/taskAssist` 导入；专项 Vitest、legacy tests、build、lint 和业务代码全量通过，满足 release gate 门禁。
*   Reviewer 最终复验、手工产品验收与发布门禁已通过，v1.3.0 于 2026-07-14 正式发布。

**非目标**：
*   不做开放式聊天、整份计划重排、Refine Diff、个性化、日历集成或探索到长期计划的自动转换。
*   “解释这一步”“给我模板”“降低难度”“缩短到 10 分钟”保留给后续扩展，不进入首版 MVP。

#### ✅ v1.3.1: 智能执行中枢与差分微调 (Execution Engine & Refine Diff) (Completed / Released)
**版本目标**：当现实条件在计划确认后发生变化时，以可预览、可校验、可原子应用的局部 Diff 调整当前执行层，而不是重写整份计划。

**设计与执行文档**：
*   设计规格：`docs/superpowers/specs/2026-07-14-v1.3.1-execution-engine-refine-diff-design.md`
*   前后端执行计划：`docs/superpowers/plans/2026-07-14-v1.3.1-execution-engine-refine-diff.md`

**MVP 范围**：
*   三种 mode：`time_budget`、`progress_recovery`、`context_change`。
*   四类 Diff：更新任务、新增少量任务、同级重排、调整当前项目的 My Day 投影。
*   首版整包确认、事务 Apply；不做逐条勾选、不允许 AI 删除任务。
*   已完成任务、历史阶段、Roadmap、长期循环、阶段复盘和 Task Assist children 保持不可变。
*   `time_budget` 按容量动态选择任务，保留确定性缓冲，最多聚焦 5 个父任务，不使用固定任务数量。
*   新增独立 durable run、`execution_refine` SSE、stale fingerprint、24-case DeepSeek Eval 和严格发布门禁。

**实现与发布验收（2026-07-15）**：
*   后端已实现独立 `execution_refine_runs`、项目级 scope/fingerprint、DeepSeek 结构化 Diff、确定性 Validator、run-scoped SSE、取消/恢复以及事务 Apply。
*   前端已实现项目内结构化输入、before/after Diff 预览、独立 Zustand/SSE 生命周期、刷新恢复、stale 重试和 Apply 后权威 snapshot 重载。
*   Apply 同事务更新 task rows 与 committed TaskTree；重复 Apply 返回幂等 receipt，跨项目、历史阶段、长期循环和 Assist children 保持受保护。
*   Backend：`523 passed`；前端 hooks、Portfolio、长期执行、Strategy、Task Assist、Execution Refine、14 个 legacy Node 测试、build 与 lint 全部通过。
*   DeepSeek Execution Refine Eval `24/24`、Planning Eval `54/54`、Task Assist Eval `18/18`，各自全部发布指标均为 `100%`，strict exit 均为 `0`。
*   `git diff --check` 通过；Reviewer 无剩余 P0/P1，v1.3.1 于 2026-07-15 正式发布。

#### ✅ 已提前完成：虚拟化“我的一天” (Virtual My Day)
*   此架构已在 v1.2.3 后期完成并在 v1.2.4 加固。使用 `is_in_my_day` 保留原计划结构，避免任务在不同视图之间物理迁移造成状态混乱，不再作为未来版本里程碑。

#### 📍 v1.4: 私人定制 (Personalized Planning)
*   轻量个性化偏好记忆，让 EasyPlan 根据用户偏好的任务粒度、常用场景、工作时长进行定制规划。
