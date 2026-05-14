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

#### 📍 v1.2.3: 意图画像与动态路由 (Intent Profiling & Routing) (Completed)
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

#### 📍 v1.2.4: 策略守门员与体验兜底 (Strategy Validation & Fallbacks)
*   **Validator 策略校验**：Validator 升级，不再只检查 JSON 结构，更要校验“拆解策略是否匹配”（如短期目标排了三个月、探索型目标强行给清单等均判为错误并 Replan）。
*   **任务质量拦截**：拦截“提升能力”、“完善方案”等抽象描述，强制要求大模型修改为“具体、可执行的真实动作”。
*   **透明的策略短语**：在任务树上方，向用户展示一句极其简短的策略解释（如：“我已按回家路上、手机处理等场景整理”），建立人机信任。
*   **全方位失败兜底**：应对模型超时、JSON 异常等极端情况，前端提供细分安抚文案；后端引入不依赖大模型的基于规则的本地 Fallback Planner，确保系统绝对兜底。

#### 📍 v1.3 系列: 智能执行中枢 (Advanced Agentic Engine)
*   **虚拟化“我的一天” (Virtual My Day)**：废弃底层的物理 `view_bucket` 转移逻辑，改为引入 `is_in_my_day: boolean`。实现类似微软 To Do 的虚拟映射，让任务在加入我的一天的同时，始终保持在原计划树中的结构完整性。
*   **差分微调 (Refine Diff)**：重构 Refine 逻辑。大模型不再将整棵树推翻重来，而是输出 Diff（删除哪些、合并哪些），保留用户已接受的结构，打造真正的“协作修改”体验。
*   **信心指数与交互式澄清**：模型评估 `confidence_score`。遇到极端模糊输入（信心低）时放弃强拆，改为给出 2-3 个关键问题供用户选择，完成澄清。
*   **断点恢复引导 (Resume Prompts)**：结合迷雾解锁机制，在用户隔日回归时，生成真实的进度接续语（如：“上次完成了资料收集，现在只需写下3个观点”），对抗执行中断。
*   **UI 专属数据模型**：扩充大模型输出的字段，新增 `energy_level`, `context`, `is_breaker` 等元数据，为前端未来的高级看板筛选提供数据支撑。

#### 📍 基础设施建设 (Infrastructure & Dev Tools)
*   **评测集驱动 (Evaluation Driven)**：建立 `planning_cases.jsonl`，收集 50-100 条典型用例，构建自动测试 Pipeline，用以量化检验 Intent 识别率和拆解粒度，取代人工玄学调参。
*   **轻量级偏好记忆**：持久化用户的粗细粒度偏好和常用上下文，完成从“通用规划器”到“私人智囊”的进化。
*   **真·流式输出 (True Streaming)**：后端引入局部 JSON 增量解析，前端容错渲染残缺 JSON，实现极限魔法动效。
