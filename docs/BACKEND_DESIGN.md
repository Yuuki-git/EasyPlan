# EasyPlan 后端设计文档

版本：`v1.2.0-backend`

## 1. 设计目标

EasyPlan 后端已从“外部任务系统注入”转向“原生任务看板”闭环。后端的核心职责是把用户自然语言意图拆解成结构化 `TaskTree`，通过 HITL 让用户确认、编辑或自然语言 refine，最终沉淀为 EasyPlan 内部任务数据。

v1.2.0 的产品方向来自 PM 文档：

- **原生任务引擎**：任务进入内部 `tasks` / `task_dependencies`，支持“我的一天”和“计划中”等原生视图。
- **Scope Horizon**：宏大目标首次只规划启动阶段，不一次性生成沉重长计划。
- **Fog of War**：阶段完成后再基于上下文解锁下一阶段任务。
- **Deep Immersion**：AI reasoning 只在等待时安抚用户，`plan_ready` 后前端可折叠淡出。
- **废弃外部同步**：Todoist / Microsoft To Do / MCP / OAuth 同步不再是 v1.2.0 后端主线。

后端原则：

- 所有 thread、checkpoint、task 查询必须绑定 `user_id`。
- LLM 输出只进入 `TaskTree` 结构化模型，不把原始 prompt、推理全文或裸响应写入业务表。
- `TaskNode.estimated_minutes` 的 Pydantic schema 只做 `1..43200` 宽泛兜底；“action 必须小于 5 分钟”只在 LangGraph validator 中执行并触发 replan。
- SSE 业务错误事件统一使用 `agent_error`，避免与浏览器原生 `error` 事件冲突。
- 4C4G 环境下优先控制连接池、并发 LLM 调用和 checkpoint 体积。

## 2. 技术栈

| 层级 | 选择 | 说明 |
| --- | --- | --- |
| API 网关 | FastAPI | 异步优先，自动生成 OpenAPI / Swagger |
| 认证 | JWT + PostgreSQL users | 普通 API 仅 Header 鉴权；SSE 额外支持 query token |
| Agent 编排 | LangGraph | `StateGraph` + Checkpointer + `interrupt()` 实现 HITL |
| 数据校验 | Pydantic v2 | 约束 `TaskTree`、确认请求、快照响应 |
| 数据库 | PostgreSQL | users、threads、checkpoints、tasks、dependencies |
| ORM | SQLAlchemy 2.x async | 与 FastAPI async 生命周期一致 |
| 实时通信 | SSE | Async Queue 长连接，支持增量重播和快照对齐 |
| LLM Provider | OpenAI / DeepSeek / Xiaomi MiMo | 统一 `PlannerClient` 协议，最终输出 `TaskTree` |

## 3. 运行约束

| 项目 | 建议值 | 说明 |
| --- | --- | --- |
| FastAPI workers | 1-2 | 避免 4G 内存下重复创建过多连接池和 provider client |
| SQLAlchemy pool_size | 5 | API、SSE、后台图任务共享 |
| SQLAlchemy max_overflow | 5 | 控制短时尖峰连接数 |
| 单用户 planner 并发 | 1 | 同一用户不能同时运行多个拆解图 |
| 全局 planner 并发 | 4 起步 | 用队列或信号量保护 LLM 与数据库 |
| Checkpoint 保留 | 7 天起步 | 过期或终态会话归档/清理 |
| AgentState 大小 | 强剪裁 | 不保存 prompt、raw response、推理全文 |

## 4. 模块结构

```text
app/
├── api/
│   ├── auth.py
│   ├── dependencies.py
│   ├── exceptions.py
│   ├── routes_intents.py
│   ├── routes_threads.py
│   ├── schemas.py
│   └── sse.py
├── agents/
│   ├── graph.py
│   ├── nodes.py
│   └── state.py
├── models/
│   ├── checkpoint.py
│   ├── task.py
│   ├── thread.py
│   └── user.py
├── services/
│   ├── agent_runtime.py
│   ├── checkpoint_service.py
│   ├── llm_service.py
│   └── thread_repository.py
└── db/
    └── session.py
```

已移除旧外部集成层：integration routes、MCP adapters、OAuth callback service、sync service 以及相关模型。v1.2.0 不再围绕 Todoist / Microsoft To Do 做一键注入。

## 5. 数据库 Schema

### 5.1 Users

```sql
CREATE TABLE users (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    email CITEXT NOT NULL UNIQUE,
    password_hash TEXT,
    display_name TEXT,
    status TEXT NOT NULL DEFAULT 'active',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

### 5.2 Agent Threads

```sql
CREATE TABLE agent_threads (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    thread_id TEXT NOT NULL UNIQUE,
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    intent_text TEXT NOT NULL,
    status TEXT NOT NULL,
    current_node TEXT,
    next_nodes TEXT[] NOT NULL DEFAULT '{}',
    lease_owner TEXT,
    lease_expires_at TIMESTAMPTZ,
    interrupt_payload JSONB,
    latest_checkpoint_id TEXT,
    task_tree JSONB,
    error_code TEXT,
    error_message TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    interrupted_at TIMESTAMPTZ,
    completed_at TIMESTAMPTZ,
    expires_at TIMESTAMPTZ,
    UNIQUE(user_id, thread_id)
);
```

推荐状态：

| 状态 | 含义 |
| --- | --- |
| `running` | LangGraph 正在规划或校验 |
| `awaiting_confirmation` | 已执行 `interrupt()`，等待用户确认/编辑/refine/拒绝 |
| `confirmed` | 用户已确认，准备写入原生任务表 |
| `succeeded` | 内部任务写入完成 |
| `rejected` | 用户拒绝本次计划 |
| `failed` | 图执行或持久化失败 |
| `expired` | 长时间未确认，系统归档 |

### 5.3 LangGraph Checkpoints

Checkpointer 持久层必须带 `user_id`，恢复时所有查询必须同时绑定 `user_id + thread_id`，不能只按 `thread_id` 恢复。

```sql
CREATE TABLE langgraph_checkpoints (
    user_id UUID NOT NULL,
    thread_id TEXT NOT NULL,
    checkpoint_ns TEXT NOT NULL DEFAULT '',
    checkpoint_id TEXT NOT NULL,
    parent_checkpoint_id TEXT,
    type TEXT,
    checkpoint JSONB NOT NULL,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (user_id, thread_id, checkpoint_ns, checkpoint_id)
);
```

当前 `TenantAwareMemorySaver` 已封装多租户 config。后续替换为 PostgreSQL Checkpointer 时必须保持同样的租户边界。

### 5.4 Tasks

当前模型已包含任务树落库的核心列，并已补齐 `view_bucket` 作为原生看板蓄水池字段。

```sql
CREATE TABLE tasks (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    thread_id TEXT NOT NULL REFERENCES agent_threads(thread_id) ON DELETE CASCADE,
    parent_task_id UUID REFERENCES tasks(id) ON DELETE CASCADE,
    client_node_id TEXT NOT NULL,
    title TEXT NOT NULL,
    description TEXT,
    node_type TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'draft',
    view_bucket TEXT NOT NULL DEFAULT 'planned',
    estimated_minutes INT,
    sort_order INT NOT NULL DEFAULT 0,
    ai_generated BOOLEAN NOT NULL DEFAULT true,
    user_edited BOOLEAN NOT NULL DEFAULT false,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE(thread_id, client_node_id)
);
```

v1.2 后续建议在 `metadata` 或迁移列中继续表达：

| 字段 | 用途 |
| --- | --- |
| `view_bucket` | `my_day` / `planned` / `backlog` |
| `phase_key` | `phase_1`、`phase_2` 等 Fog of War 阶段 |
| `scope_horizon` | 首次规划范围，例如 `starter_phase` |
| `planned_for` | 计划日期，必须带时区语义 |
| `completed_at` | 完成时间，`TIMESTAMPTZ` |
| `unlock_source_task_ids` | 下一阶段生成时参考的已完成任务 |

任务状态：

| 状态 | 含义 |
| --- | --- |
| `draft` | AI 生成但用户尚未确认 |
| `active` | 用户确认后进入看板 |
| `today` | 用户加入“我的一天” |
| `completed` | 用户在原生看板内完成 |
| `archived` | 历史归档 |

### 5.5 Task Dependencies

```sql
CREATE TABLE task_dependencies (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    task_id UUID NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    depends_on_task_id UUID NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE(task_id, depends_on_task_id),
    CHECK(task_id <> depends_on_task_id)
);
```

## 6. LangGraph 状态机拓扑

当前主链路：

```text
START
-> planner_node
-> task_tree_validator_node
-> valid: human_review_node
-> interrupt(task_tree_review)

human_review_node
-> approve: persist_internal_tasks_node
-> refine: planner_node
-> edit: task_tree_validator_node
-> reject: END

task_tree_validator_node
-> needs_replan: planner_node
-> failed: failed_validation_node
-> END
```

v1.2.0 目标链路：

```text
approve
-> persist_internal_tasks_node
-> emit(done)
-> native task board

all phase_1 tasks completed
-> phase_unlock_planner_node
-> task_tree_validator_node
-> human_review_node
-> persist_internal_tasks_node
```

validator 规则：

- Pydantic 负责结构宽泛校验：最大深度、节点数、字段类型、`estimated_minutes 1..43200`。
- `task_tree_validator_node` 负责业务规则：action 节点 `< 5` 分钟、动词存在、ID 唯一、`depends_on` 不引用不存在节点、依赖无环。
- 发现 action 超过 5 分钟时返回 `needs_replan`，最多重试 `MAX_REPLAN_ATTEMPTS`。

## 7. AgentState 剪裁

Checkpoint 只保留恢复需要的最小状态：

| 字段 | 是否保留 | 说明 |
| --- | --- | --- |
| `user_id`, `thread_id` | 保留 | 多租户恢复边界 |
| `intent_text` | 保留但截断 | 超过 2000 字符截断 |
| `task_tree` | 保留 | 前端确认和恢复需要 |
| `reasoning_events` | 保留摘要 | 只保留最近 20 条安全摘要 |
| `validation_errors` | 保留 | replan 输入 |
| `prompt` | 删除 | 不入 checkpoint |
| `raw_llm_response` | 删除 | 不入 checkpoint |

## 8. LLM 规划策略

统一 `PlannerClient` 协议：

```python
class PlannerClient(Protocol):
    async def create_plan(
        self,
        prompt: str,
        reasoning_sink: ReasoningSink | None = None,
    ) -> dict[str, Any]: ...
```

要求：

- OpenAI 使用 Structured Outputs，最终解析为 `TaskTree`。
- DeepSeek 与 Xiaomi MiMo 使用 JSON mode，并由后端 `TaskTree.model_validate()` 强校验。
- 所有 provider 必须保持任务字段语言与用户输入一致。
- reasoning SSE 只发安全、用户可见的阶段性文案，不发 chain-of-thought。
- usage 埋点只记录 token 数、provider、model、operation，不记录 prompt 或原始响应。
- Scope Horizon 必须进入 planner prompt：宏大目标首次只生成启动阶段任务。
- 后续 Few-Shot 示例应偏向“短、扁平、动词开头、可立即启动”的任务树。

当前用户可见 reasoning message：

| code | message |
| --- | --- |
| `LLM_PLANNING_STARTED` | `正在分析您的核心目标...` |
| `LLM_SCHEMA_LOCKED` | `正在将目标拆解为可执行的微行动...` |
| `LLM_PLAN_PARSED` | `正在为您评估每项任务的时间与依赖关系...` |
| `LLM_USAGE_RECORDED` | `计划生成完毕，请查阅。` |

## 9. SSE 协议

事件流由 `AgentRuntime` 的进程内 event buffer 与 `asyncio.Queue` subscriber 驱动。

| event | 说明 |
| --- | --- |
| `reasoning` | 安全进度摘要 |
| `checkpoint` | 图节点推进 |
| `plan_ready` | 任务树已生成并通过校验，进入用户确认 |
| `done` | 终态事件 |
| `agent_error` | 统一业务错误事件，避免浏览器原生 `error` 冲突 |
| `snapshot_required` | 前端必须重新拉取 thread 快照 |

重连规则：

- 优先使用 `Last-Event-ID` Header。
- 原生 `EventSource` 可用 `last_event_id` query fallback。
- 游标不在进程内 buffer 时返回 `snapshot_required`，前端重新拉取 `GET /api/threads/{thread_id}`。
- `agent_error` 和 `done` 都是终态事件，流应关闭。

## 10. 全局异常兜底

FastAPI 注册 `Exception` 级兜底 handler：

- 服务端 logger 记录完整 traceback、path、method。
- 前端固定收到脱敏 JSON：

```json
{
  "error_code": "INTERNAL_ERROR",
  "message": "服务器在思考时走神了，请稍后再试。"
}
```

禁止把 traceback、SQL、token、模型原始响应或内部路径暴露给客户端。

## 11. API 流程

### 11.1 新建计划

```text
Frontend POST /api/intents
-> JWT 解析 user_id
-> 创建 agent_threads(status='running')
-> BackgroundTasks 启动 AgentRuntime
-> LangGraph planner_node 生成 TaskTree
-> validator 校验两分钟法则、动词开头、依赖合法性
-> human_review_node interrupt
-> AgentRuntime 持久化 agent_threads(status='awaiting_confirmation')
-> SSE plan_ready
```

### 11.2 用户确认

```text
Frontend POST /api/threads/{thread_id}/confirm
-> 按 user_id + thread_id 校验归属
-> approve/edit/refine/reject 转成 Command(resume=...)
-> refine 回到 planner_node
-> edit 回到 validator
-> approve 进入 persist_internal_tasks_node，写入原生任务表
-> reject 结束本次计划
```

### 11.3 v1.2.1 原生看板基建

```text
approve
-> 展开 TaskTree
-> 写入 tasks / task_dependencies
-> GET /api/tasks?view_bucket=my_day|planned|backlog
-> PATCH /api/tasks/{task_id}
-> POST /api/tasks/{task_id}/complete
-> 若 phase_1 清空，允许触发下一阶段规划
```

## 12. v1.2.0 准备清单

已完成：

- JWT 注册/登录持久化到 PostgreSQL。
- Thread 恢复前强制 `user_id + thread_id` 归属校验。
- LangGraph checkpointer 已封装多租户 config。
- AgentState 已裁剪 prompt、raw response 和大 payload。
- Async Queue SSE 支持长连接、增量重播、断点后推送。
- SSE 错误事件改为 `agent_error`。
- 外部集成与同步代码已移除，OpenAPI 不再暴露相关接口。
- 全局 500 异常响应已脱敏。
- `persist_internal_tasks_node` 已在 approve 后展开 `TaskTree`，保留 `client_node_id -> parent_task_id` 层级映射并写入 `tasks` / `task_dependencies`。
- 已暴露 `GET /api/tasks` 与 `PATCH /api/tasks/{task_id}`，所有查询和更新均绑定 `user_id`。

待实现：

- 原生任务完成 API：`POST /api/tasks/{task_id}/complete`。
- My Day / Planned 查询索引与排序策略。
- Scope Horizon prompt 与 `metadata.scope_horizon` 落库约定。
- Fog of War 阶段解锁：完成 Phase 1 后生成 Phase 2。
- PostgreSQL Checkpointer 替换 `TenantAwareMemorySaver`。
- checkpoint retention job：清理 7 天前过期或终态会话。
- 单用户 planner 并发锁，保护 4C4G 环境。
