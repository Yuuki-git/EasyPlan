# 后端开发任务指令集（For Codex）

## 1. 当前产品方向

EasyPlan v1.2.0 已转向原生任务看板闭环。后端不再优先建设 Todoist、Microsoft To Do、MCP 或 OAuth 同步能力；所有规划结果都应沉淀到 EasyPlan 自己的 PostgreSQL 任务数据中。

PM 文档中的后端重点：

- 原生任务引擎：支持“我的一天”“计划中”等看板视图。
- Scope Horizon：宏大目标首次只规划启动阶段。
- Fog of War：阶段完成后再动态解锁下一阶段。
- Deep Immersion：reasoning 只做安全、短暂、用户可见的进度安抚。

## 2. 核心技术要求

- **语言**：Python 3.10+，建议 Python 3.11+
- **网关**：FastAPI，异步优先
- **工作流**：LangGraph，必须使用 Checkpointer 机制实现 HITL
- **校验**：Pydantic v2，约束模型输出与 API 契约
- **数据库**：PostgreSQL + SQLAlchemy 2.x async
- **鉴权**：JWT，所有 thread/checkpoint/task 操作绑定 `user_id`
- **实时通信**：SSE + Async Queue，支持 `Last-Event-ID` 与 query fallback

## 3. LangGraph 节点要求

当前链路：

1. `planner_node`
   - 异步调用 LLM。
   - 强制输出 `TaskTree`。
   - prompt 必须包含：两分钟法则、动词开头、语言对齐、Scope Horizon。
   - 不保存 prompt 和 raw response 到 checkpoint。

2. `task_tree_validator_node`
   - 只在这里执行业务级微动作规则。
   - action 节点 `estimated_minutes` 必须 `< 5`。
   - 校验 ID 唯一、依赖存在、依赖无环。
   - 发现 action 超时必须触发 `needs_replan`，不要让 Pydantic 提前杀死。

3. `human_review_node`
   - 必须调用 `interrupt()`。
   - 支持 `approve`、`edit`、`refine`、`reject`。
   - `refine` 接收自然语言反馈，回到 `planner_node`。

4. `persist_internal_tasks_node`
   - 在用户 `approve` 后展开 `TaskTree`。
   - 写入 `tasks` 和 `task_dependencies`。
   - 写入时必须绑定 `user_id + thread_id`。
   - 为后续 My Day / Planned / Fog of War 保留 metadata。

## 4. API 契约要求

已暴露接口：

- `POST /api/auth/register`
- `POST /api/auth/token`
- `POST /api/intents`
- `GET /api/threads/{thread_id}`
- `GET /api/threads/{thread_id}/events`
- `POST /api/threads/{thread_id}/confirm`
- `GET /api/tasks?view_bucket=planned|my_day|backlog`
- `PATCH /api/tasks/{task_id}`
- `GET /health`

SSE 事件名：

- `reasoning`
- `checkpoint`
- `plan_ready`
- `done`
- `agent_error`
- `snapshot_required`

禁止发送 `event: error` 作为业务错误事件名，因为它会与浏览器原生保留事件冲突。

v1.2 后续接口草案：

- `POST /api/tasks/{task_id}/complete`
- `POST /api/threads/{thread_id}/unlock-next-phase`

在实现任何新接口或修改字段结构时，必须第一时间更新：

- `app/api/schemas.py`
- `docs/openapi.json`
- `docs/API_DOCUMENTATION.md`
- 对应测试

## 5. 数据与多租户要求

- 所有查询必须绑定 `user_id`。
- 恢复 checkpoint 时必须绑定 `user_id + thread_id`。
- task 查询和更新必须绑定 `user_id + task_id`。
- 不得只靠客户端传入的 `thread_id` 或 `task_id` 判定归属。
- 所有时间戳使用带时区语义：API 使用 ISO 8601，数据库使用 `TIMESTAMPTZ`。

## 6. 4C4G 稳健性要求

- 单用户同一时间最多运行一个 planner 图。
- 全局 planner 并发需要信号量或队列保护。
- AgentState 必须强剪裁：不保存 prompt、raw response、长推理文本。
- Checkpoint 需要 7 天保留/清理策略。
- SQLAlchemy 连接池保持小规模，避免 4G 内存被连接占满。

## 7. LLM 与 Reasoning 要求

- OpenAI 使用 Structured Outputs。
- DeepSeek / Xiaomi MiMo 使用 JSON mode 后再走 Pydantic 强校验。
- 所有模型输出字段必须与用户输入语言一致。
- reasoning message 是用户可见文案，禁止出现 `JSON mode`、`schema`、`token usage` 等内部实现词。
- usage 埋点只记录 provider、model、operation、token 数，不记录 prompt 和原始响应。

当前 reasoning 文案：

- `正在分析您的核心目标...`
- `正在将目标拆解为可执行的微行动...`
- `正在为您评估每项任务的时间与依赖关系...`
- `计划生成完毕，请查阅。`

## 8. 交付要求

- 代码必须模块化：`app/api`、`app/agents`、`app/models`、`app/services` 边界清晰。
- 所有后端行为变更必须有测试。
- 所有协议变更必须同步 OpenAPI 和接口文档。
- 不要恢复已废弃的外部同步、MCP adapter 或 OAuth callback 代码。
