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

#### 📍 v1.2.2: 交互重塑 —— 情绪价值与微动效 (Emotional UX & Micro-interactions)
*   **文案去工业化**：废弃“看板”这一企业级词汇，转向更私人的表达（如“我的手帐”或“专注视图”）。
*   **文件夹派导航 (Project-Based Sidebar)**：采纳用户直觉建议，摒弃长列表瀑布流，在左侧边栏引入基于意图（Thread）的动态菜单项。点击特定计划，右侧仅沉浸式展示该计划的任务，彻底解决“多计划混杂导致的认知过载”。
*   **情绪空状态 (Emotional Empty States)**：在“我的一天”清空后，展示治愈系插画与文案（如“今天辛苦了，去喝杯茶吧”），提供正向情绪反馈。
*   **划除的仪式感 (The Completion Ritual)**：任务勾选后停留 2 秒再伴随渐变滑出，放大完成任务的多巴胺反馈。
*   **无痕返回 (The Return Path)**：引入全局快捷键（如 `Cmd+K` 或 `/`）或极简浮层，在不打断心流的情况下随时唤起“意图捕获”输入框。
*   **跨视图流转 (Cross-view Transfer)**：在计划中增加“☀️ 加入我的一天”入口，彻底打通意图拆解与日常执行的闭环。
*   **手动任务补充 (Manual Tasks)**：补充 `POST /api/tasks` 接口，激活侧边栏“添加任务”按钮，满足用户随时记事的诉求。
*   **行内编辑 (Inline Edit)**：支持看板任务双击直接修改文本/时间。

#### 📍 v1.2.3: 视野控制 —— 迷雾解锁与微调
*   **Prompting**：严格控制生成规模（总节点 <= 15），对宏大目标仅规划“启动阶段”。
*   **交互**：用户在看板完成当前阶段后，AI 主动介入动态解锁 (Fog of War) 下一阶段任务。

#### 📍 v1.2.4: 终极攻坚 —— 真·流式输出 (True Streaming JSON)
*   **全栈重构**：挑战最高难度，后端引入局部 JSON 解析器，前端容错渲染未闭合的残缺 JSON，实现任务树“打字机”般逐个节点生长的极限魔法动效。
SON，实现任务树“打字机”般逐个节点生长的极限魔法动效。
