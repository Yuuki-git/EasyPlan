# EasyPlan 后端设计文档

## 1. 设计目标

EasyPlan v1.2.0 后端从“外部任务系统注入”切换为“原生任务看板”闭环。后端只负责把用户自然语言意图拆解为结构化任务树，经过用户确认后沉淀到内部数据库，为后续内置看板、筛选、编辑和复盘能力提供稳定底座。

核心原则：

- **HITL 优先**：AI 只提出计划，用户确认后才进入内部任务管理生命周期。
- **状态可恢复**：每次 LangGraph 运行绑定 `user_id + thread_id`，Checkpointer 必须具备多租户隔离。
- **结构化输出**：LLM 输出必须通过 `TaskTree` Pydantic 校验，不能把非结构化文本写入业务表。
- **原生闭环**：任务数据进入 PostgreSQL `tasks` / `task_dependencies`，不再依赖三方任务平台。
- **安全兜底**：未处理异常写完整服务端 traceback，但 API 只返回用户友好的脱敏 JSON。

## 2. 技术栈

| 层级 | 选择 | 说明 |
| --- | --- | --- |
| API 网关 | FastAPI | 异步优先，自动生成 OpenAPI/Swagger |
| 认证 | JWT + PostgreSQL users | 所有 thread/checkpoint/task 查询绑定 `user_id` |
| Agent 编排 | LangGraph | `StateGraph` + Checkpointer + `interrupt()` 实现 HITL |
| 数据校验 | Pydantic v2 | 约束 `TaskTree`、确认请求、快照响应 |
| 数据库 | PostgreSQL | 用户、Threads、Checkpoints、Tasks、TaskDependencies |
| ORM | SQLAlchemy 2.x async | 与 FastAPI async 生命周期一致 |
| 实时通信 | SSE | Async Queue 长连接，支持增量重播和快照对齐 |
| LLM Provider | OpenAI / DeepSeek / Xiaomi MiMo | 统一 `PlannerClient` 协议，最终输出 `TaskTree` |

## 3. 4C4G 运行约束

| 项目 | 建议值 | 说明 |
| --- | --- | --- |
| FastAPI workers | 1-2 | 避免 4G 内存下重复创建过多连接池和模型客户端 |
| SQLAlchemy pool_size | 5 | API、SSE、后台任务共享 |
| SQLAlchemy max_overflow | 5 | 控制短时尖峰连接数 |
| 单用户 planner 并发 | 1 | 同一用户不能同时运行多个拆解图 |
| 全局 planner 并发 | 4 起步 | 使用队列或信号量保护 LLM 和数据库 |
| Checkpoint 保留 | 7 天起步 | 过期会话归档或清理，防止 checkpoint 表膨胀 |

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

已移除的旧集成扩展层包括：集成路由、适配器、授权回调服务、数据推送服务与相关数据模型。v1.2.0 文档和 OpenAPI 不再暴露这些能力。

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
| `running` | LangGraph 正在推理 |
| `awaiting_confirmation` | 已执行 `interrupt()`，等待用户确认/编辑/refine/拒绝 |
| `confirmed` | 用户已确认，准备写入内部任务表 |
| `succeeded` | 内部任务写入完成 |
| `rejected` | 用户拒绝本次计划 |
| `failed` | 图执行或持久化失败 |
| `expired` | 长时间未确认，系统归档 |

### 5.3 LangGraph Checkpoints

底层 Checkpointer 表必须带 `user_id` 维度，恢复时所有查询必须同时绑定 `user_id + thread_id`。业务代码不得只凭 `thread_id` 恢复 checkpoint。

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

实现现状：`TenantAwareMemorySaver` 已封装多租户 config，下一步替换为 PostgreSQL Checkpointer 时必须保持同样的 `user_id + thread_id` 过滤边界。

### 5.4 Tasks

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

v1.2.0 看板建议状态：

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

```text
START
-> planner_node
-> task_tree_validator_node
-> valid: human_review_node
-> interrupt(task_tree_review)
-> approve: persist_internal_tasks_node
-> END

human_review_node
-> refine: planner_node
-> edit: task_tree_validator_node
-> reject: END

task_tree_validator_node
-> needs_replan: planner_node
-> failed: failed_validation_node
-> END
```

当前代码已经实现 `planner -> validator -> human_review interrupt -> refine/edit/reject/approve` 主链路。`persist_internal_tasks_node` 是 v1.2.0 看板写入的下一步，负责把确认后的 `TaskTree` 展开为 `tasks` 和 `task_dependencies`。

## 7. AgentState 裁剪

Checkpoint 只保留恢复必需的最小状态：

| 字段 | 是否保留 | 说明 |
| --- | --- | --- |
| `user_id`, `thread_id` | 保留 | 多租户恢复边界 |
| `intent_text` | 保留但截断 | 超过 2000 字符截断 |
| `task_tree` | 保留 | 前端确认和恢复所需 |
| `reasoning_events` | 保留摘要 | 只保留最近 20 条安全摘要 |
| `prompt` | 删除 | 不入库 |
| `raw_llm_response` | 删除 | 不入库 |
| `legacy_large_payload` | 删除 | 兼容旧状态清理，不再新增 |

## 8. LLM 规划

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
- 所有 provider 的系统提示词要求任务字段语言与用户输入一致。
- `reasoning` SSE 只发送安全阶段摘要，不发送 chain-of-thought。
- usage 埋点只记录 token 数、provider、model、operation，不记录 prompt 或原始响应。

## 9. SSE 协议

事件流由 `AgentRuntime` 的进程内事件 buffer 和 `asyncio.Queue` subscriber 驱动。

| event | 说明 |
| --- | --- |
| `reasoning` | 安全进度摘要 |
| `checkpoint` | 图节点推进 |
| `plan_ready` | 任务树已生成并通过校验 |
| `done` | 终态事件 |
| `error` | 统一错误事件 |
| `snapshot_required` | 前端需要重新拉取 thread 快照 |

重连规则：

- 优先使用 `Last-Event-ID` Header。
- 原生 `EventSource` 可用 `last_event_id` query fallback。
- 如果游标不在进程内 buffer，返回 `snapshot_required`，前端必须重新拉取 `GET /api/threads/{thread_id}`。

## 10. 全局异常兜底

FastAPI 注册 `Exception` 级兜底 handler：

- 服务端通过 logger 记录完整 traceback、path、method。
- 前端固定收到：

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
-> 先按 user_id + thread_id 校验归属
-> approve/edit/refine/reject 转成 Command(resume=...)
-> refine 回到 planner_node
-> edit 回到 validator
-> approve 进入内部任务写入
-> reject 结束本次计划
```

## 12. v1.2.0 准备清单

已完成：

- JWT 注册/登录持久化到 PostgreSQL。
- Thread 恢复前强制 `user_id + thread_id` 归属校验。
- LangGraph checkpointer 封装了多租户 config。
- AgentState 已裁剪 prompt、raw response 和旧大 payload。
- Async Queue SSE 支持长连接、增量重播、断点后推送。
- 旧外部集成与同步代码已移除，OpenAPI 不再暴露相关接口。
- 全局 500 异常响应已脱敏。

下一步：

- 新增 `persist_internal_tasks_node`，把确认后的 `TaskTree` 展开写入 `tasks`。
- 为原生看板增加 `GET /api/tasks`、`PATCH /api/tasks/{task_id}`、`POST /api/tasks/{task_id}/complete`。
- 将 `TenantAwareMemorySaver` 替换为 PostgreSQL Checkpointer，保持 `user_id + thread_id` 查询约束。
- 增加 checkpoint retention job，清理 7 天前过期或终态会话。
- 增加单用户 planner 并发锁，防止 4C4G 环境下 LLM 调用堆积。
