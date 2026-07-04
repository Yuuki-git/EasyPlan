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

#### 📍 v1.2.5: 三层规划与阶段视野 (Three-Tier Planning) (RC.2 Candidate)
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

**2026-07-04 本地验收**：
*   Backend：`259 passed`
*   Frontend Node 状态测试：通过
*   Mounted `useSSE` Hook：`9 passed`
*   Build、lint、`git diff --check`：通过
*   DeepSeek 32/32 为此前成功基准；本轮未完成外部联网复跑。
*   RC 已知收尾：next-phase `SYNCING` 已正确只允许返回当前计划；初始规划 `ActionLayer` 仍显示本地取消入口，Stable 前需要统一确认后的不可撤销语义。

#### 🩹 v1.2.5.1: Generation Experience Patch (Closed / Absorbed into v1.2.5 RC.2)
*   **范围定位**：v1.2.5.1 只负责收口生成态稳定性与信息架构边界，不再增加新的核心功能。
*   **已收口问题**：
    *   `exploration_decision` 首屏先给“当前判断”，避免用户只看到 reasoning 却拿不到判断。
    *   新 intent / retry / next phase preview 按单次 run 管理，不残留上一轮 reasoning。
    *   SSE replay 去重、stalled 检测和“返回当前计划 / 取消本次生成”逃生口闭环。
    *   “全部计划”与“项目”语义拆清，避免看起来像两套并列容器。
    *   `exploration_decision` 场景下的 `time_horizon` 漂移与 raw validation error 暴露问题已专项修复。
*   **收口结果**：该补丁后续发现的 cross-run SSE、旧快照覆盖、active-run 恢复和生成取消问题已在 v1.2.5 RC.2 修复并纳入自动化测试。

#### 📍 v1.2.6: 总览层与回答层 (Portfolio Overview & Answer Layer)
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
    *   重新生成后默认只展示当前 run，旧 run 折叠成摘要，避免前端信息越堆越多。

#### 📍 v1.2.7: 规划模型 2.0 (Planning Model Differentiation)
*   **长期目标高层化**：
    *   `long_term_growth` 继续使用 Roadmap，但 phase 必须更高层，只展开当前 phase，未来 phase 更像地图而不是任务列表。
*   **短期目标模块化**：
    *   `short_term_delivery` 不直接复用长期 Roadmap，改为 deliverables / workstreams / execution lanes。
    *   核心目标是让短期目标强调交付结构，而不是伪装成长线阶段图。
*   **探索决策路线独立化**：
    *   `exploration_decision` 保留 decision route，但它是判断与验证路径，不应混成长线执行地图。
*   **评测升级**：
    *   后续 eval 不只检查“有没有 roadmap”，而是检查是否为对应 intent 采用了正确的规划模型。

#### 📍 v1.3.0: 任务级副驾驶 (Task Copilot / Action Coach)
*   围绕单个任务提供微观 AI 辅助：解释这一步、帮我开始、我卡住了、拆得更细、降低难度、给我模板。

#### 📍 v1.3.1: 智能执行中枢与差分微调 (Execution Engine & Refine Diff)
*   **动态调整**：支持根据执行状态动态调整计划，包括 Refine Diff、Resume Prompt。
*   **场景化指令**：“我今天只有 20 分钟”、“我落后了帮我重排”。
*   **交互式澄清**：利用 `intent_confidence` 进行模糊输入的选项澄清。

#### ✅ 已提前完成：虚拟化“我的一天” (Virtual My Day)
*   此架构已在 v1.2.3 后期完成并在 v1.2.4 加固。使用 `is_in_my_day` 保留原计划结构，避免任务在不同视图之间物理迁移造成状态混乱，不再作为未来版本里程碑。

#### 📍 v1.4: 私人定制 (Personalized Planning)
*   轻量个性化偏好记忆，让 EasyPlan 根据用户偏好的任务粒度、常用场景、工作时长进行定制规划。
