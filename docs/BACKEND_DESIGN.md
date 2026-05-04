# EasyPlan 后端设计文档

## 1. 设计目标

EasyPlan 后端负责把用户的自然语言意图拆解为可执行微任务，并在用户确认后同步到 Todoist 等外部任务系统。MVP 的核心不是“自动替用户做完一切”，而是建立一个可恢复、可审计、可人工介入的 Agent 工作流。

本设计遵循以下原则：

- **HITL 优先**：AI 只提出计划，用户确认后才执行外部写入。
- **状态可恢复**：LangGraph 每次运行必须绑定 `thread_id`，并通过 PostgreSQL Checkpointer 保存中断点。
- **结构化输出**：LLM 输出必须通过 Pydantic v2 校验，不能让非结构化文本直接进入任务表。
- **工具动态发现**：外部系统通过 MCP 暴露能力，后端通过 `tools/list` 发现工具，通过 `tools/call` 调用工具，不把 Todoist 的具体工具名写死在 Agent 内部。

参考资料：

- LangGraph Persistence: https://docs.langchain.com/oss/python/langgraph/persistence
- LangGraph Interrupts/HITL: https://docs.langchain.com/oss/python/langgraph/human-in-the-loop
- MCP Tools Specification: https://modelcontextprotocol.io/specification/2025-06-18/server/tools

## 2. 技术栈

| 层级 | 选择 | 说明 |
| --- | --- | --- |
| API 网关 | FastAPI | 异步优先，自动生成 OpenAPI/Swagger |
| Agent 编排 | LangGraph | `StateGraph` + Checkpointer + `interrupt()` 实现 HITL |
| 数据校验 | Pydantic v2 | 约束 `TaskTree`、工具调用计划、确认请求 |
| 数据库 | PostgreSQL | 业务数据、Thread 元信息、Checkpoint 读模型、同步记录 |
| 向量能力 | pgvector | Future 阶段用于偏好记忆与语义检索 |
| ORM | SQLAlchemy 2.x async | 与 FastAPI async 生命周期一致 |
| 外部工具 | MCP Client | 连接 Todoist、Microsoft To Do 等 MCP Server |
| 实时通信 | SSE 优先，WebSocket 可选 | 前端需要流式 reasoning、plan_ready、sync_progress |

### 2.1 4C4G 运行约束

MVP 按 4C4G 单实例或小规格容器设计，默认配置必须保守：

| 项目 | 建议值 | 说明 |
| --- | --- | --- |
| FastAPI workers | 1-2 | 4G 内存下避免多 worker 复制过多连接池和模型客户端 |
| SQLAlchemy pool_size | 5 | API、SSE、后台任务共享，先小后调 |
| SQLAlchemy max_overflow | 5 | 短时尖峰使用，避免连接数失控 |
| PostgreSQL statement_timeout | 10s | 避免慢查询拖住连接 |
| 单用户 planner 并发 | 1 | 同一用户不能同时运行两个拆解图 |
| 全局 planner 并发 | 依环境 4-8 | 用信号量或队列保护 LLM 和数据库 |
| MCP 每 provider 连接数 | 3-5 | 远程工具调用不占满数据库连接 |

pgvector 仅作为 Future memory 的预留能力。MVP 不创建大规模 embedding 写入任务，也不在 planner 主路径做向量检索，避免 4G 内存环境下索引和后台任务过早膨胀。

## 3. 后端模块结构

```text
app/
├── api/
│   ├── routes_intents.py
│   ├── routes_threads.py
│   ├── routes_confirmations.py
│   └── routes_integrations.py
├── agents/
│   ├── graph.py
│   ├── nodes.py
│   ├── state.py
│   └── schemas.py
├── models/
│   ├── user.py
│   ├── thread.py
│   ├── checkpoint.py
│   ├── task.py
│   ├── integration.py
│   └── sync.py
├── services/
│   ├── thread_service.py
│   ├── task_service.py
│   ├── checkpoint_service.py
│   ├── mcp_registry.py
│   ├── mcp_client_pool.py
│   └── sync_service.py
├── db/
│   ├── session.py
│   └── migrations/
└── main.py
```

## 4. 核心领域模型

### 4.1 TaskTree Pydantic 模型

LLM 的输出先进入 Pydantic，再进入数据库。

```python
from pydantic import BaseModel, Field
from typing import Literal

class TaskNode(BaseModel):
    client_node_id: str = Field(description="前端/LLM 侧临时节点 ID，用于树内引用")
    title: str = Field(min_length=1, max_length=160)
    description: str | None = Field(default=None, max_length=1000)
    verb: str = Field(description="叶子任务必须包含具体动词")
    estimated_minutes: int = Field(ge=1, lt=5)
    node_type: Literal["group", "action"]
    depends_on: list[str] = Field(default_factory=list)
    children: list["TaskNode"] = Field(default_factory=list)

class TaskTree(BaseModel):
    root: TaskNode
    summary: str = Field(max_length=500)
    assumptions: list[str] = Field(default_factory=list)
```

校验规则：

- `action` 叶子节点 `estimated_minutes < 5`；“两分钟法则”作为规划目标，`<5` 作为后端硬校验。
- `action` 叶子节点必须有明确动词，例如“打开、列出、写下、发送、检查”。
- `depends_on` 只能引用同一棵树内存在的 `client_node_id`。
- 不允许依赖环。
- `group` 可以超过 5 分钟，但必须能递归拆到 `action` 节点。

如果校验发现叶子任务 `estimated_minutes >= 5`，`task_tree_validator_node` 不直接把错误暴露给前端，而是把不合格节点交给 `planner_refinement_node` 自动继续拆解。只有达到最大再拆解次数仍失败时，才返回可解释错误。

### 4.2 Pydantic JSON 与实时 Reasoning 流

EasyPlan 同时需要“最终结构化 JSON”和“实时推理进度”。两者必须分离：

- **最终结果通道**：LLM 的计划输出必须使用 structured output/function calling/json schema，并进入 `TaskTree` Pydantic 模型校验。只有校验后的 `TaskTree` 可以写入 `agent_threads.task_tree` 和 `tasks`。
- **实时进度通道**：SSE `reasoning` 事件由后端节点主动生成安全摘要，例如“正在识别依赖关系”“正在检查任务粒度”。它不是模型 chain-of-thought，也不作为 Pydantic 解析来源。
- **模型流式 token**：如果底层模型支持 streaming，后端只消费它来更新内部进度，不把原始 token 直接透传给前端。
- **错误通道**：Pydantic 校验失败时，后端推送 `event: reasoning` 的短摘要和 `event: error` 的可解释错误码，不推送原始 LLM 响应。

推荐执行顺序：

```text
planner_node started
-> SSE reasoning: "正在识别核心目标"
-> LLM structured output call
-> SSE reasoning: "正在检查任务是否足够小"
-> Pydantic TaskTree validation
-> valid: SSE plan_ready(TaskTree)
-> invalid: planner_refinement_node 或 TASK_TREE_VALIDATION_FAILED
```

这样前端能看到连续进度，后端仍能保证最终数据是 Pydantic JSON。

## 5. 数据库 Schema

### 5.1 设计说明

数据库分成四类表：

- **业务事实表**：用户、任务、任务依赖、外部同步结果。
- **Agent 会话表**：`agent_threads` 记录一次意图拆解会话的业务状态。
- **Checkpoint 表**：`agent_checkpoints` 保存 LangGraph 状态检查点的业务读模型；底层 Checkpointer 表必须带 `user_id` 租户维度，不能直接使用无租户过滤的默认表结构。
- **MCP 集成表**：用户授权、MCP Server 注册、工具发现缓存、工具调用记录。

`agent_threads.thread_id` 是 HITL 的关键主键。FastAPI 启动 LangGraph 时必须传入：

```python
config = {"configurable": {"user_id": user_id, "thread_id": thread_id}}
```

用户确认时，后端用同一个 `user_id + thread_id` 执行：

```python
graph.ainvoke(Command(resume=confirmation_payload), config=config)
```

### 5.2 Users

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

说明：

- MVP 可先支持邮箱密码，也预留 OAuth。
- 所有业务表用 `user_id` 做租户隔离，查询必须带用户边界。

### 5.3 Agent Threads

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

CREATE INDEX idx_agent_threads_user_status
    ON agent_threads(user_id, status, updated_at DESC);

CREATE INDEX idx_agent_threads_thread_id
    ON agent_threads(thread_id);
```

`status` 枚举建议：

| 状态 | 含义 |
| --- | --- |
| `running` | LangGraph 正在推理 |
| `awaiting_confirmation` | 已执行 `interrupt()`，等待用户确认/编辑/拒绝 |
| `confirmed` | 用户已确认，图正在恢复执行 |
| `syncing` | 正在调用 MCP 写入外部系统 |
| `succeeded` | 全部同步成功 |
| `partially_succeeded` | 部分任务同步成功，部分失败 |
| `rejected` | 用户拒绝该计划 |
| `cancelled` | 用户主动取消 |
| `failed` | 图执行或同步失败 |
| `expired` | 长时间未确认，系统归档 |

关键字段：

- `thread_id`：LangGraph Checkpointer 查找状态的稳定 ID。
- `lease_owner` / `lease_expires_at`：运行中图的轻量租约，用于防止重复 worker 同时执行同一 thread。
- `interrupt_payload`：给前端渲染确认页的数据，包含 `TaskTree`、问题说明、可选动作。
- `latest_checkpoint_id`：指向最近一次 checkpoint，方便排障和时间旅行。
- `task_tree`：当前用户可见版本，用户编辑后先更新这里，再作为 `Command(resume=...)` 输入恢复图。

### 5.4 Agent Checkpoints

```sql
CREATE TABLE agent_checkpoints (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL,
    thread_id TEXT NOT NULL,
    checkpoint_id TEXT NOT NULL,
    checkpoint_ns TEXT NOT NULL DEFAULT '',
    parent_checkpoint_id TEXT,
    node_name TEXT,
    graph_status TEXT NOT NULL,
    state_summary JSONB NOT NULL DEFAULT '{}'::jsonb,
    next_nodes TEXT[] NOT NULL DEFAULT '{}',
    interrupt_payload JSONB,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    FOREIGN KEY (user_id, thread_id)
        REFERENCES agent_threads(user_id, thread_id)
        ON DELETE CASCADE,
    UNIQUE(user_id, thread_id, checkpoint_ns, checkpoint_id)
);

CREATE INDEX idx_agent_checkpoints_thread_created
    ON agent_checkpoints(user_id, thread_id, created_at DESC);
```

设计意图：

- 多租户隔离要求所有 checkpoint 查询都必须同时绑定 `user_id` 和 `thread_id`。
- LangGraph Checkpointer 保存完整状态快照；`agent_checkpoints` 保存后端和前端需要查询的摘要。
- 每次 super-step 完成后，服务层把 `graph.get_state(config)` 中的 `checkpoint_id`、`next`、`values` 摘要同步到此表。
- 当 `planner_node` 后发生 `interrupt()` 时，`graph_status='interrupted'`，`interrupt_payload` 存前端确认所需内容。
- 不允许业务代码直接以裸 `thread_id` 查询 checkpoint；必须通过 `checkpoint_service` 注入当前登录用户。

### 5.5 Tasks

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

CREATE INDEX idx_tasks_user_status
    ON tasks(user_id, status, updated_at DESC);

CREATE INDEX idx_tasks_thread_parent
    ON tasks(thread_id, parent_task_id, sort_order);
```

`status` 枚举建议：

| 状态 | 含义 |
| --- | --- |
| `draft` | AI 生成但用户尚未确认 |
| `approved` | 用户确认 |
| `syncing` | 正在同步到外部系统 |
| `synced` | 已成功写入外部系统 |
| `sync_failed` | 外部同步失败 |
| `cancelled` | 被用户拒绝或取消 |

### 5.6 Task Dependencies

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

说明：

- 树形层级用 `tasks.parent_task_id` 表达。
- 跨分支依赖用 `task_dependencies` 表达。
- 写入前必须做 DAG 校验，拒绝依赖环。

### 5.7 Integrations

```sql
CREATE TABLE integrations (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    provider TEXT NOT NULL,
    display_name TEXT NOT NULL,
    external_account_id TEXT,
    status TEXT NOT NULL DEFAULT 'connected',
    auth_type TEXT NOT NULL,
    encrypted_credentials BYTEA NOT NULL,
    credential_version INT NOT NULL DEFAULT 1,
    scopes TEXT[] NOT NULL DEFAULT '{}',
    token_expires_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_used_at TIMESTAMPTZ,
    UNIQUE(user_id, provider)
);
```

说明：

- `provider` 例如 `todoist`、`microsoft_todo`。
- `encrypted_credentials` 必须使用 KMS 或应用层 envelope encryption，不能明文存 token。
- `scopes` 必须最小化，例如 Todoist 只申请创建任务所需权限。
- `external_account_id` 保存 Todoist 用户 ID 或账号标识，用于展示“已连接到哪个账号”。
- `token_expires_at` 使用带时区时间戳；刷新 token 时必须整体重加密并递增 `credential_version`。

### 5.8 OAuth States

```sql
CREATE TABLE oauth_states (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    provider TEXT NOT NULL,
    state TEXT NOT NULL UNIQUE,
    code_verifier_hash TEXT,
    redirect_uri TEXT NOT NULL,
    requested_scopes TEXT[] NOT NULL DEFAULT '{}',
    status TEXT NOT NULL DEFAULT 'pending',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at TIMESTAMPTZ NOT NULL,
    consumed_at TIMESTAMPTZ
);

CREATE INDEX idx_oauth_states_user_provider
    ON oauth_states(user_id, provider, created_at DESC);
```

说明：

- `oauth_states` 防 CSRF 和重复 callback，`state` 必须一次性消费。
- `code_verifier_hash` 用于 PKCE 校验；不保存明文 verifier。
- `expires_at` 默认 10 分钟，过期 state 由 retention job 清理。
- OAuth callback 成功后写入 `integrations.encrypted_credentials`，前端只接收连接状态，不接收 access token。

### 5.9 MCP Servers 与工具缓存

```sql
CREATE TABLE mcp_servers (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    provider TEXT NOT NULL UNIQUE,
    server_name TEXT NOT NULL,
    transport TEXT NOT NULL,
    endpoint TEXT,
    command_template JSONB,
    auth_scheme TEXT NOT NULL DEFAULT 'bearer',
    required_headers JSONB NOT NULL DEFAULT '{}'::jsonb,
    timeout_ms INT NOT NULL DEFAULT 10000,
    max_connections INT NOT NULL DEFAULT 10,
    allowed_hosts TEXT[] NOT NULL DEFAULT '{}',
    enabled BOOLEAN NOT NULL DEFAULT true,
    trust_level TEXT NOT NULL DEFAULT 'approved',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE mcp_tools (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    server_id UUID NOT NULL REFERENCES mcp_servers(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    title TEXT,
    description TEXT,
    input_schema JSONB NOT NULL,
    output_schema JSONB,
    annotations JSONB NOT NULL DEFAULT '{}'::jsonb,
    version_hash TEXT NOT NULL,
    discovered_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_seen_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    enabled BOOLEAN NOT NULL DEFAULT true,
    UNIQUE(server_id, name)
);

CREATE INDEX idx_mcp_tools_server_enabled
    ON mcp_tools(server_id, enabled);
```

说明：

- `mcp_servers` 是后端允许连接的 Server 白名单，不能由用户输入任意本地命令。
- SaaS 生产环境只允许 `transport='remote_sse'`、`transport='remote_streamable_http'` 或 `transport='internal_adapter'`。
- `transport='stdio'` 只允许本地开发或单机自托管模式，生产禁用；云端 FastAPI 进程不能依赖本地子进程式 MCP Server。
- `endpoint` 必须是 HTTPS URL，且 host 必须命中 `allowed_hosts`，防止 SSRF。
- `required_headers` 只保存非敏感 header 名称或模板，真实 token 从 `integrations.encrypted_credentials` 解密后运行时注入。
- `command_template` 仅供 dev/self-hosted stdio 使用，生产记录必须为空。
- `mcp_tools` 是 `tools/list` 的发现结果缓存，用 `version_hash` 判断 schema 是否变化。
- 如果 MCP Server 声明 `listChanged`，收到 `notifications/tools/list_changed` 后刷新工具缓存。

### 5.10 Sync Runs

```sql
CREATE TABLE sync_runs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    thread_id TEXT NOT NULL REFERENCES agent_threads(thread_id) ON DELETE CASCADE,
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    integration_id UUID NOT NULL REFERENCES integrations(id),
    request_id TEXT NOT NULL,
    provider TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'running',
    total_count INT NOT NULL DEFAULT 0,
    success_count INT NOT NULL DEFAULT 0,
    failure_count INT NOT NULL DEFAULT 0,
    started_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    completed_at TIMESTAMPTZ,
    error_message TEXT,
    UNIQUE(user_id, request_id)
);

CREATE TABLE sync_run_items (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    sync_run_id UUID NOT NULL REFERENCES sync_runs(id) ON DELETE CASCADE,
    task_id UUID NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    mcp_tool_name TEXT NOT NULL,
    request_payload JSONB NOT NULL,
    response_payload JSONB,
    status TEXT NOT NULL DEFAULT 'pending',
    external_task_id TEXT,
    external_url TEXT,
    idempotency_key TEXT NOT NULL,
    error_message TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE(sync_run_id, task_id),
    UNIQUE(idempotency_key)
);
```

说明：

- `sync_runs` 是一次用户确认后的外部同步批次。
- `sync_run_items` 是每个任务对应的一次 MCP 工具调用结果。
- `request_id` 由前端在确认同步时生成，同一用户下唯一；重复提交相同 `request_id` 时，后端直接返回已有 `sync_run` 状态，不再次恢复图或调用 MCP。
- `idempotency_key` 是强制字段，格式：`{thread_id}:{task_id}:{provider}:create_task`，用于重试和断点续传时避免重复创建。
- `sync_run_items.status` 建议枚举：`pending`、`running`、`synced`、`retryable_failed`、`failed`、`skipped`。

### 5.11 Confirmation Requests

```sql
CREATE TABLE confirmation_requests (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    thread_id TEXT NOT NULL REFERENCES agent_threads(thread_id) ON DELETE CASCADE,
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    request_id TEXT NOT NULL,
    action TEXT NOT NULL,
    payload_hash TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'accepted',
    sync_run_id UUID REFERENCES sync_runs(id),
    response_payload JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE(user_id, request_id)
);

CREATE INDEX idx_confirmation_requests_thread
    ON confirmation_requests(thread_id, created_at DESC);
```

说明：

- `/confirm` 收到请求后先在事务中插入 `confirmation_requests`。
- 如果 `UNIQUE(user_id, request_id)` 冲突，说明是重复点击或网络重试；后端读取原记录并返回同一结果。
- 如果同一个 `request_id` 携带了不同 `payload_hash`，返回 `409 REQUEST_ID_PAYLOAD_MISMATCH`，防止客户端误复用 request_id。
- 只有首次 accepted 的确认请求才允许执行 `Command(resume=...)`。

### 5.12 Audit Events

```sql
CREATE TABLE audit_events (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID REFERENCES users(id) ON DELETE SET NULL,
    thread_id TEXT,
    event_type TEXT NOT NULL,
    actor_type TEXT NOT NULL,
    payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_audit_events_thread
    ON audit_events(thread_id, created_at DESC);
```

建议记录：

- 用户提交意图。
- AI 生成计划。
- 用户确认、编辑、拒绝。
- MCP 工具发现变更。
- 外部写入成功或失败。

### 5.13 Checkpoint Retention 与自动清理

4C4G 部署资源有限，Checkpoint 需要默认启用保守清理策略：

| 数据 | 保留策略 | 处理方式 |
| --- | --- | --- |
| `agent_threads.awaiting_confirmation` | 7 天未确认 | 标记 `expired`，写入 `expires_at` 和审计事件 |
| `agent_checkpoints` 明细 | 7 天前且 thread 已终态 | 删除 checkpoint 摘要，仅保留 `agent_threads.latest_checkpoint_id` |
| `langgraph_checkpoints` 完整状态表 | 7 天前且 thread 已终态 | 通过维护任务按 `user_id + thread_id` 清理 |
| `sync_run_items.response_payload` | 30 天后 | 可压缩为摘要字段，避免长期保存大 JSON |
| `audit_events` | 90 天 | MVP 可保留 90 天，后续转冷存储 |

建议实现一个每日低峰运行的 `checkpoint_retention_job`：

```text
1. 找出 updated_at < now() - interval '7 days' 且 status='awaiting_confirmation' 的 thread
2. 更新 agent_threads.status='expired', expires_at=now()
3. 对 succeeded/rejected/cancelled/failed/expired 的旧 thread 删除 checkpoint 明细
4. vacuum/analyze 受影响表，保持查询计划稳定
5. 记录 audit_events(event_type='checkpoint_retention_completed')
```

运行策略：

- 清理任务每批最多处理固定数量 thread，例如 500，避免长事务。
- 清理只针对已终态或已过期会话，不删除 `tasks`、`sync_runs` 等业务事实。
- 本地开发环境可把 retention 调大或关闭，生产默认开启。

### 5.14 多租户 LangGraph Checkpointer 表

`langgraph-checkpoint-postgres` 的默认持久化结构适合单租户或可信内部环境。EasyPlan 是多用户 SaaS，正式实现时必须使用带 `user_id` 的自定义 Checkpointer，或者在官方 saver 外包一层强制租户过滤的 Repository。任何 checkpoint 读写都不能只依赖 `thread_id`。

建议自定义完整状态表：

```sql
CREATE TABLE langgraph_checkpoints (
    user_id UUID NOT NULL,
    thread_id TEXT NOT NULL,
    checkpoint_ns TEXT NOT NULL DEFAULT '',
    checkpoint_id TEXT NOT NULL,
    parent_checkpoint_id TEXT,
    checkpoint JSONB NOT NULL,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (user_id, thread_id, checkpoint_ns, checkpoint_id),
    FOREIGN KEY (user_id, thread_id)
        REFERENCES agent_threads(user_id, thread_id)
        ON DELETE CASCADE
);

CREATE INDEX idx_langgraph_checkpoints_latest
    ON langgraph_checkpoints(user_id, thread_id, created_at DESC);

CREATE TABLE langgraph_checkpoint_writes (
    user_id UUID NOT NULL,
    thread_id TEXT NOT NULL,
    checkpoint_ns TEXT NOT NULL DEFAULT '',
    checkpoint_id TEXT NOT NULL,
    task_id TEXT NOT NULL,
    idx INT NOT NULL,
    channel TEXT NOT NULL,
    value JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (user_id, thread_id, checkpoint_ns, checkpoint_id, task_id, idx),
    FOREIGN KEY (user_id, thread_id, checkpoint_ns, checkpoint_id)
        REFERENCES langgraph_checkpoints(user_id, thread_id, checkpoint_ns, checkpoint_id)
        ON DELETE CASCADE
);
```

实现要求：

- `configurable` 中必须同时传入 `user_id` 和 `thread_id`，例如 `{"configurable": {"user_id": user_id, "thread_id": thread_id}}`。
- 自定义 Checkpointer 的 `get/put/list` 方法必须把 `user_id` 加入 WHERE 条件。
- `thread_id` 仍保持全局唯一，但安全边界不能依赖唯一性，只能依赖当前登录用户和数据库约束。
- 恢复历史状态、列出 checkpoint、清理 checkpoint 都必须以 `user_id + thread_id` 为组合条件。
- 如果后续继续使用官方表，需要通过 migration 增加 `user_id` 列、复合索引和 Repository 级强制过滤，不允许直接暴露官方 saver 给业务代码。

## 6. LangGraph 状态设计

### 6.1 Agent State

```python
from typing import Literal, TypedDict

class AgentState(TypedDict, total=False):
    user_id: str
    thread_id: str
    intent_text: str
    route: Literal["create_plan", "query_status"]
    reasoning_events: list[dict]
    task_tree: dict
    validation_errors: list[str]
    replan_attempts: int
    human_decision: dict
    refinement_feedback: str
    request_id: str
    selected_provider: str
    discovered_tools: list[dict]
    tool_call_plan: list[dict]
    sync_results: list[dict]
    error: dict
```

### 6.1.1 State Pruning 规则

4C4G 环境下，LangGraph State 必须强力裁剪。原则是：Checkpoint 只保存恢复图所需的最小业务状态，实时展示数据走 SSE event buffer，大对象走业务表或直接丢弃。

允许进入 Checkpoint 的字段：

| 字段 | 上限 | 说明 |
| --- | --- | --- |
| `intent_text` | 2 KB | 用户原始意图，超长输入先摘要再入图 |
| `task_tree` | 64 KB 或 200 nodes | 当前可确认任务树 |
| `validation_errors` | 最近 10 条 | 只保留错误摘要 |
| `reasoning_events` | 最近 20 条摘要 | 只保留给恢复 UI 用的短状态，不保存长文本 |
| `tool_call_plan` | 叶子任务数量对应的精简参数 | 不保存完整第三方响应 |
| `sync_results` | 每项状态摘要 | 详细 response 在 `sync_run_items` 中裁剪保存 |

禁止进入 Checkpoint 的内容：

- 完整 prompt、system message、few-shot 示例。
- 模型 chain-of-thought 或长篇推理文本。
- LLM 原始响应全文。
- MCP `tools/list` 全量 schema；只保存工具名和 `version_hash`，完整 schema 在 `mcp_tools`。
- MCP `tools/call` 原始大响应；只保存 `external_task_id`、`external_url`、错误摘要。
- OAuth token、Authorization header、第三方敏感 payload。

执行规则：

- 每个节点返回前调用 `prune_agent_state(state)`，确保状态可序列化且体积受控。
- 单个 checkpoint JSON 建议软上限 128 KB，超过 256 KB 直接标记 `STATE_TOO_LARGE` 并中止本轮图执行。
- `planner_node` 和 `planner_refinement_node` 只能接收 `intent_text + task_tree summary + feedback + validation_errors summary`，不能带完整历史 reasoning。
- SSE event buffer 默认保留 15 分钟，用于 UI 流式展示；它不是 Checkpoint 的替代品，也不参与 LangGraph 恢复。

### 6.2 状态机拓扑图

主路径：

```text
START
-> router_node
-> context_loader_node
-> planner_node
-> task_tree_validator_node
-> valid?
-> human_review_node
-> interrupt()
-> user confirms in frontend
-> Command(resume={ action: "approve", request_id: "...", task_tree: ... })
-> persist_approved_tasks_node
-> mcp_tool_discovery_node
-> tool_call_planner_node
-> executor_node
-> sync_result_node
-> END
```

任务粒度不合格时的自动再拆解路径：

```text
planner_node
-> task_tree_validator_node
-> invalid: leaf estimated_minutes >= 5
-> planner_refinement_node
-> task_tree_validator_node
-> valid?
-> human_review_node
```

`planner_refinement_node` 会把不合格叶子节点、校验错误和原始意图传回 LLM，要求继续拆解到 `<5` 分钟。该循环必须设置上限，建议 `max_replan_attempts=3`；超过上限后返回 `TASK_TREE_VALIDATION_FAILED`，不要把不合格任务交给用户确认。

用户编辑路径：

```text
human_review_node
-> interrupt()
-> Command(resume={ action: "edit", task_tree: edited_tree })
-> task_tree_validator_node
-> human_review_node
-> interrupt()
-> Command(resume={ action: "approve", request_id: "...", task_tree: validated_tree })
-> persist_approved_tasks_node
-> ...
```

用户自然语言 refine 路径：

```text
human_review_node
-> interrupt()
-> Command(resume={ action: "refine", feedback: "拆得再细一点，先做论文摘要部分" })
-> planner_node
-> task_tree_validator_node
-> valid?
-> human_review_node
-> interrupt()
-> Command(resume={ action: "approve", request_id: "...", task_tree: refined_tree })
-> persist_approved_tasks_node
-> ...
```

`refine` 与 `edit` 的区别：

- `edit` 接收前端提交的结构化 `TaskTree`，适合用户已经在 UI 上改过节点。
- `refine` 接收自然语言 `feedback`，例如“任务太多了，保留今天能做的部分”，由 Planner 结合原意图和当前任务树重新生成。
- `refine` 重新进入 `planner_node`，但输入必须是裁剪后的 `intent_text + task_tree summary + feedback`，不能把完整历史 reasoning 塞回 State。

用户拒绝路径：

```text
human_review_node
-> interrupt()
-> Command(resume={ action: "reject", reason: "not useful" })
-> cancel_thread_node
-> END
```

查询状态路径：

```text
START
-> router_node
-> status_reader_node
-> END
```

同步失败补偿路径：

```text
executor_node
-> sync_result_node
-> has_failed_items?
-> retry_policy_node
-> executor_node
-> sync_result_node
-> END 或 failed_thread_node
```

### 6.3 节点职责

| 节点 | 输入 | 输出 | 说明 |
| --- | --- | --- | --- |
| `router_node` | `intent_text` | `route` | 判断新建计划或查询状态 |
| `context_loader_node` | `user_id` | 偏好、历史摘要 | MVP 可只加载最近未完成任务，Future 接 pgvector memory |
| `planner_node` | 意图、偏好 | `TaskTree` 草案、reasoning events | 调 LLM 递归拆解 |
| `task_tree_validator_node` | `TaskTree` | 校验后的 `TaskTree` 或错误 | Pydantic + 依赖 DAG + `<5` 分钟硬规则 |
| `planner_refinement_node` | 校验错误、不合格叶子节点 | 更细粒度的 `TaskTree` | 自动要求 LLM 继续拆解，最多 3 次 |
| `human_review_node` | `TaskTree` | `interrupt_payload` | 调用 `interrupt()`，暂停图执行；支持 approve/edit/refine/reject |
| `persist_approved_tasks_node` | 用户确认后的树 | `tasks` 草稿转 approved | 只在确认后写入 approved 状态 |
| `mcp_tool_discovery_node` | provider/integration | `discovered_tools` | 调 MCP `tools/list`，刷新缓存 |
| `tool_call_planner_node` | 任务树、工具 schema | `tool_call_plan` | 按 inputSchema 生成参数 |
| `executor_node` | `tool_call_plan` | `sync_results` | 调 MCP `tools/call` |
| `sync_result_node` | `sync_results` | thread/task/sync 状态 | 汇总成功、部分成功、失败 |
| `cancel_thread_node` | 拒绝原因 | `rejected` | 归档会话，不调用外部工具 |

### 6.4 HITL 关键实现细节

`human_review_node` 不直接执行外部副作用，只负责中断：

```python
from langgraph.types import interrupt, Command

async def human_review_node(state: AgentState):
    decision = interrupt({
        "type": "task_tree_review",
        "user_id": state["user_id"],
        "thread_id": state["thread_id"],
        "task_tree": state["task_tree"],
        "allowed_actions": ["approve", "edit", "refine", "reject"],
    })

    return {"human_decision": decision}
```

注意事项：

- `interrupt()` 不要包在 `try/except` 中，否则可能吞掉 LangGraph 的中断信号。
- 恢复执行时节点会从头重新运行，`interrupt()` 前的代码必须无副作用或幂等。
- `Command(resume=...)` 必须传 JSON 可序列化数据。
- 前端刷新后通过 `GET /api/threads/{thread_id}` 读取 `agent_threads.interrupt_payload` 恢复待确认页面。

### 6.5 State Isolation 中间件

任何读取、恢复或订阅 Thread 的 API 都必须先经过 `ThreadOwnershipMiddleware` 或等价 service guard：

```text
request auth -> current_user.id
-> load agent_threads where user_id=current_user.id and thread_id=:thread_id
-> not found: 404
-> status/action guard
-> build LangGraph config with user_id + thread_id
-> call graph.get_state / graph.ainvoke / events
```

约束：

- `GET /api/threads/{thread_id}`、`GET /events`、`POST /confirm` 都必须使用同一套 ownership guard。
- LangGraph config 必须包含当前登录用户的 `user_id`，并传给自定义 Checkpointer。
- 禁止任何代码路径调用 `graph.get_state({"configurable": {"thread_id": thread_id}})` 这种裸 thread 恢复。
- SSE 连接建立后也要绑定 `user_id`；断线重连不能绕过鉴权。
- `thread_id` 猜测、旧链接分享、跨用户 request_id 重放都应返回 404 或 403，不能泄露 thread 是否存在。

## 7. 并发控制与资源保护

### 7.1 Planner Rate Limiting

`planner_node` 是最昂贵的节点，必须限流：

- 同一个用户同一时间最多只能有 1 个 `running/confirmed/syncing` 的拆解图。
- 用户重复提交意图时，如果已有运行中的 thread，返回 `409 PLANNER_ALREADY_RUNNING`，并带上已有 `thread_id`。
- 全局使用 `asyncio.Semaphore` 或轻量任务队列限制 planner 并发，4C4G 默认 4，压测后再调整。
- 对匿名或试用用户增加更严格的分钟级限流，例如 `3 requests / 10 minutes`。
- SSE 只负责消费事件，不应为每个连接启动新的 planner 任务。

建议用数据库事务获取用户级锁：

```sql
SELECT id
FROM agent_threads
WHERE user_id = :user_id
  AND status IN ('running', 'confirmed', 'syncing')
FOR UPDATE;
```

如果查询到记录，拒绝新 planner；否则创建新 `agent_threads` 并设置 `lease_owner`、`lease_expires_at`。

### 7.2 Thread Lease

为避免 worker 重启、重复恢复或并发确认导致同一图被执行多次，每个运行中 thread 使用轻量租约：

```text
1. worker 恢复图前更新 lease_owner=<instance_id>, lease_expires_at=now()+2 minutes
2. 更新条件必须包含 lease 为空或已过期
3. 执行期间定期续约
4. 图进入 interrupt 或终态时释放 lease
5. 其他 worker 抢不到 lease 时返回 409 或等待短重试
```

租约不替代 `request_id` 幂等；它解决并发执行，`request_id` 解决重复确认。

### 7.3 数据库连接池

4C4G 下连接池要小而可观测：

- `pool_size=5`、`max_overflow=5`、`pool_timeout=3s` 作为默认值。
- 后台任务、SSE 和 HTTP API 共用连接池时，禁止在 SSE 长连接中长期持有数据库连接。
- 所有查询必须短事务；LLM 调用和 MCP 调用期间不能持有数据库事务。
- PostgreSQL 设置 `idle_in_transaction_session_timeout`，防止异常请求占住连接。
- 指标至少包含 pool checked-out、overflow、timeout 次数和慢查询日志。

### 7.4 后台任务与队列建议

MVP 可以先用进程内队列，但接口要预留迁移到 Redis/Celery/RQ：

- `POST /api/intents` 只创建 thread 和排队任务，快速返回。
- planner worker 从队列消费，受全局并发限制。
- 任务事件写入轻量 event buffer，SSE 从 buffer 读取，不直接等待 LLM。
- 如果进程内队列满，返回 `429 PLANNER_QUEUE_FULL`，提示用户稍后重试。

## 8. API 设计

### 8.1 提交意图

```http
POST /api/intents
Content-Type: application/json
X-User-Timezone: Asia/Shanghai

{
  "intent_text": "这周末前我想把这篇论文初稿写完",
  "preferred_provider": "todoist"
}
```

返回：

```json
{
  "thread_id": "thr_01J...",
  "status": "running",
  "events_url": "/api/threads/thr_01J.../events"
}
```

### 8.2 SSE 事件流

```http
GET /api/threads/{thread_id}/events
Last-Event-ID: evt_01J...
```

事件类型：

```text
id: evt_01J001
event: reasoning
data: {"state_version":3,"message":"正在识别核心动作..."}

id: evt_01J002
event: checkpoint
data: {"state_version":4,"checkpoint_id":"...","node":"planner_node"}

id: evt_01J003
event: plan_ready
data: {"state_version":5,"thread_id":"...","task_tree":{...}}

id: evt_01J004
event: sync_progress
data: {"state_version":6,"success_count":3,"total_count":8}

id: evt_01J005
event: done
data: {"state_version":7,"status":"succeeded"}

id: evt_01J006
event: error
data: {"state_version":8,"code":"MCP_TOOL_CALL_FAILED","message":"Todoist 写入失败"}
```

状态快照对齐要求：

- 后端 SSE 事件必须包含递增 `id` 和 `state_version`。
- 前端重连前先调用 `GET /api/threads/{thread_id}` 获取快照，再用 `Last-Event-ID` 订阅后续事件。
- 如果 `Last-Event-ID` 已超出后端 event buffer 保留范围，后端返回 `event: snapshot_required`，前端重新拉取快照后再连接。
- SSE event buffer 只保存轻量事件，默认保留 15 分钟；业务状态以 `agent_threads` 和 checkpoint 为准。

事件 Data 负载规范：

| event | 必填字段 | 可选字段 | 说明 |
| --- | --- | --- | --- |
| `reasoning` | `state_version`, `message` | `code`, `node` | 后端生成的安全进度摘要，不包含 chain-of-thought |
| `checkpoint` | `state_version`, `checkpoint_id`, `node` | `next_nodes` | LangGraph super-step 持久化后发送 |
| `plan_ready` | `state_version`, `thread_id`, `task_tree` | `summary`, `assumptions` | `TaskTree` 已通过 Pydantic 校验，可进入 PENDING UI |
| `sync_progress` | `state_version`, `success_count`, `total_count` | `failure_count`, `current_task_id` | 外部工具写入进度 |
| `done` | `state_version`, `status` | `external_url`, `sync_run_id` | 终态事件，`status` 可为 `succeeded` 或 `partially_succeeded` |
| `error` | `state_version`, `code`, `message` | `retryable`, `details` | 进入 ERROR UI 的统一错误事件 |
| `snapshot_required` | 无 | `reason` | event buffer 缺失或 `Last-Event-ID` 过旧，前端必须重新拉取快照 |

字段约束：

- `state_version` 必须单调递增。
- `task_tree` 必须与 OpenAPI 中 `TaskTree` schema 一致。
- 所有事件 JSON 必须使用 UTF-8，且不包含 token、prompt、raw LLM response 或 MCP 原始大响应。

### 8.3 查询 Thread

```http
GET /api/threads/{thread_id}
```

用于页面刷新后恢复：

```json
{
  "thread_id": "thr_01J...",
  "status": "awaiting_confirmation",
  "state_version": 5,
  "last_event_id": "evt_01J003",
  "server_time": "2026-05-04T13:20:00+08:00",
  "intent_text": "这周末前我想把这篇论文初稿写完",
  "task_tree": {},
  "interrupt_payload": {},
  "latest_checkpoint_id": "1f0..."
}
```

### 8.4 用户确认/编辑/拒绝

```http
POST /api/threads/{thread_id}/confirm
Content-Type: application/json
X-User-Timezone: Asia/Shanghai

{
  "request_id": "req_01J...",
  "action": "approve",
  "task_tree": {
    "root": {}
  }
}
```

`action` 可选：

- `approve`：确认同步。
- `edit`：提交编辑后的任务树，后端重新校验，必要时再次中断给用户确认。
- `refine`：提交自然语言反馈，后端回到 Planner 重新生成任务树，不要求用户编辑 JSON。
- `reject`：拒绝本次计划，不调用 MCP。

`refine` 请求示例：

```json
{
  "request_id": "req_01J...",
  "action": "refine",
  "feedback": "任务还是太大了，请先聚焦今天 30 分钟内能启动的部分"
}
```

幂等要求：

- `request_id` 由前端生成，建议使用 UUID/ULID。
- 后端以 `UNIQUE(user_id, request_id)` 去重；相同 `request_id` 的重复请求必须返回第一次请求的处理结果。
- 如果重复 `request_id` 的 payload hash 不一致，返回 `409 REQUEST_ID_PAYLOAD_MISMATCH`。
- 后端只有在确认请求首次写入 `confirmation_requests` 后，才允许恢复 LangGraph，避免重复点击导致 Todoist 重复建任务。

### 8.5 集成与工具

```http
GET /api/integrations
GET /api/integrations/{provider}/tools
POST /api/integrations/{provider}/refresh-tools
```

工具接口只暴露已批准 MCP Server 的发现结果，方便前端展示“将同步到 Todoist”的透明提示。

### 8.6 时区与时间戳协议

通用规则：

- 所有数据库时间字段使用 `TIMESTAMPTZ`，所有 API 时间字段使用 ISO 8601 带时区字符串，例如 `2026-05-04T13:20:00+08:00`。
- 前端所有请求必须传 `X-User-Timezone`，值为 IANA timezone，例如 `Asia/Shanghai`。
- 后端接收到“这周末”“明天上午”等相对时间时，必须以 `X-User-Timezone` 解释用户意图，并在结构化结果中保存解析后的绝对时间。
- 后端内部定时任务和审计排序以 UTC 语义处理，但响应给前端时保留明确 offset，不返回无时区时间。
- 如果缺少或无法识别 `X-User-Timezone`，后端默认使用用户 profile 中的 timezone；仍缺失时使用 UTC，并在 response metadata 中标记 `timezone_fallback=true`。

### 8.7 OAuth 授权闭环

前后端分工：

```text
Frontend IntegrationSettings
-> GET /api/integrations/{provider}/oauth/start
-> Backend 创建 oauth_states，返回 authorization_url
-> Frontend 跳转 provider 授权页
-> Provider redirect 到 /api/integrations/{provider}/oauth/callback
-> Backend 校验 state + PKCE，交换 token
-> Backend envelope encrypt token，写 integrations.encrypted_credentials
-> Backend 重定向前端 /settings/integrations?provider=todoist&status=connected
-> Frontend GET /api/integrations，更新 isIntegrated=true
```

API：

```http
GET /api/integrations/{provider}/oauth/start
GET /api/integrations/{provider}/oauth/callback?code=...&state=...
DELETE /api/integrations/{provider}
```

安全要求：

- OAuth `state` 必须绑定 `user_id + provider + redirect_uri`，10 分钟过期，一次性消费。
- 支持 PKCE 的 provider 必须启用 PKCE。
- access token、refresh token 只存 `encrypted_credentials`，前端永不接触 token 明文。
- token 解密只发生在 `mcp_client_pool` 或 Adapter 调用外部服务前，调用结束后不写明文日志。
- 断开集成时删除或吊销远端 token，并将 `integrations.status='disconnected'`。

## 9. MCP 集成方案

### 9.1 角色划分

EasyPlan 后端在 MCP 架构中同时承担：

- **Host**：管理用户授权、工具可见性、安全策略、审计。
- **MCP Client**：与 Todoist 等 MCP Server 建立会话，执行 `initialize`、`tools/list`、`tools/call`。

Todoist、Microsoft To Do 等外部能力由 MCP Server 提供。后端不直接假设工具名，只依赖工具 schema 和能力匹配。

### 9.2 SaaS 部署约束

云端 SaaS 环境不能把 MCP stdio 作为正式集成方案。stdio 要求后端进程和 MCP Server 在同一台机器上以本地子进程方式通信，不适合多租户云部署、弹性扩缩容和远程第三方集成。

生产环境只支持两类方案：

| 方案 | 适用场景 | 说明 |
| --- | --- | --- |
| 远程 MCP Server | Todoist 等第三方提供 HTTPS/SSE 或 Streamable HTTP MCP endpoint | `mcp_client_pool` 通过 HTTPS 建立远程会话，使用用户授权 token 注入 Header |
| 内置 Adapter | 第三方没有可用远程 MCP Server，或远程 Server 不满足安全要求 | 后端用 Adapter 直接调用 Todoist API，但向 Agent 暴露同一套 `list_tools/call_tool` 接口 |

MVP 建议优先实现 `internal_adapter`，同时保留远程 MCP Client 抽象。这样即使 Todoist MCP Server 不可用，核心“一键注入”也不被阻塞。

### 9.3 动态发现流程

```text
用户连接 Todoist
-> integrations 写入授权信息
-> mcp_registry 找到 provider=todoist 的 mcp_servers 白名单配置
-> mcp_client_pool 建立 MCP session
-> initialize 能力协商
-> tools/list 拉取工具列表
-> 写入/更新 mcp_tools 缓存
-> 返回可用工具给 Agent 和前端
```

发现策略：

- 首次连接集成时立即发现工具。
- 每次执行同步前，如果 `mcp_tools.last_seen_at` 超过 TTL，重新执行 `tools/list`。
- 如果 Server 声明 `tools.listChanged=true` 并发送 `notifications/tools/list_changed`，立即刷新。
- `version_hash = sha256(name + input_schema + output_schema + annotations)`，schema 变化时记录审计事件。

### 9.4 mcp_client_pool 连接安全

`mcp_client_pool` 负责远程 MCP session 的生命周期，不允许业务节点直接创建 HTTP 客户端。

连接规则：

- 只连接 `mcp_servers.enabled=true` 且 `transport` 属于生产允许列表的 Server。
- `endpoint` 必须使用 HTTPS，禁止明文 HTTP、内网地址、localhost、link-local 地址和任意用户提交 URL。
- 对每个 provider 校验 `allowed_hosts`，禁止重定向到非白名单 host。
- Header 鉴权由后端统一注入，例如 `Authorization: Bearer <decrypted token>` 或 provider 指定的 OAuth header。
- Header 名称来自 `mcp_servers.required_headers`，Header 值来自当前用户的 `integrations.encrypted_credentials`，不能从前端请求透传。
- 所有请求设置 connect/read timeout，默认 10 秒；`tools/call` 长任务也必须有总超时。
- 每个 provider 使用小连接池，默认 `max_connections=10`，4C4G 部署可降到 3-5。
- 远程调用失败后使用短路器，连续失败达到阈值后暂停该 provider 一段时间，避免请求堆积。
- 日志必须脱敏 Authorization、Cookie、API Key 和工具返回中的敏感字段。

Adapter 规则：

- Adapter 实现内部接口 `list_tools(user_id, provider)` 和 `call_tool(user_id, provider, name, arguments)`。
- Adapter 仍写入 `mcp_tools` 和 `sync_run_items`，对 Agent 来说与远程 MCP Server 行为一致。
- Adapter 调第三方 REST API 时沿用同样的幂等、限流、审计和 token 解密策略。

### 9.5 工具选择策略

`tool_call_planner_node` 不直接写死 `todoist_create_task`，而是按以下顺序选择工具：

1. 在当前 provider 的已启用工具中查找具备“创建任务”语义的工具。
2. 匹配 `name/title/description/annotations`，例如包含 `create task`、`add todo`、`task.create`。
3. 检查 `input_schema` 是否能表达 `title`、`description`、`due`、`parent/project/section` 等字段。
4. 用 Pydantic 动态模型或 JSON Schema validator 校验生成的 arguments。
5. 若多个工具都匹配，选择 trust_level 更高、schema 更完整、最近成功率更高的工具。
6. 若没有工具匹配，停止在 `awaiting_confirmation` 或 `failed`，提示用户该集成暂不支持写入。

### 9.6 工具调用流程

```text
executor_node
-> 读取 approved tasks
-> 为每个 action 叶子任务生成 tool arguments 和 idempotency_key
-> upsert sync_run_items(status='pending')
-> 只选择 pending/retryable_failed 的 item
-> MCP tools/call(name, arguments)
-> 解析 structuredContent 或 content
-> 保存 external_task_id/external_url
-> 更新 task.status='synced'
-> 推送 sync_progress SSE
```

调用要求：

- 所有写操作必须发生在用户确认之后。
- 每个工具调用都必须先写 `sync_run_items.idempotency_key`，没有 idempotency_key 禁止调用外部工具。
- 调用前用工具的 `input_schema` 做本地校验。
- 对网络错误、429、5xx 做指数退避重试。
- 对 schema 错误、权限错误不盲目重试，直接进入可解释失败。
- MVP 在创建成功后只保存 `external_task_id`/`external_url`，不继续追踪外部完成状态。

### 9.6.1 部分失败与断点续传

同步批次必须支持断点续传：

```text
sync_run.status='partially_succeeded'
-> 用户或后台任务触发 retry
-> sync_service 读取 sync_run_items
-> 跳过 status='synced' 且 external_task_id 不为空的 item
-> 仅重试 status IN ('pending', 'retryable_failed') 的 item
-> 使用原 idempotency_key 再次调用 Adapter/MCP
-> 汇总后更新 sync_run success_count/failure_count/status
```

断点续传规则：

- `idempotency_key` 对每个外部创建动作全局唯一，数据库 `UNIQUE(idempotency_key)` 是硬约束。
- 如果后端在“外部创建成功但响应丢失”时重试，Adapter/MCP 层必须先用 `idempotency_key` 查询本地 `sync_run_items`，已有 `external_task_id` 则直接返回成功摘要。
- 对不支持幂等键的第三方 API，EasyPlan 仍以本地 `idempotency_key` 去重；必要时在 Todoist task description 写入不可见或低干扰的 EasyPlan trace id。
- `retryable_failed` 用于网络错误、429、5xx；`failed` 用于 schema、权限、认证等不可自动重试错误。
- 断点续传不重新进入 Planner，不改变 `task_tree`，只恢复外部写入阶段。

### 9.7 Todoist 示例

假设 Todoist MCP Server 经 `tools/list` 返回一个创建任务工具：

```json
{
  "name": "todoist_create_task",
  "description": "Create a Todoist task",
  "inputSchema": {
    "type": "object",
    "properties": {
      "content": {"type": "string"},
      "description": {"type": "string"},
      "due_string": {"type": "string"}
    },
    "required": ["content"]
  }
}
```

EasyPlan 生成调用：

```json
{
  "name": "todoist_create_task",
  "arguments": {
    "content": "列出论文初稿的 3 个核心论点",
    "description": "来自 EasyPlan：论文初稿计划",
    "due_string": "this weekend"
  }
}
```

如果未来 Todoist Server 把工具名改为 `tasks_add`，只要 `tools/list` 的描述和 schema 可匹配，Agent 不需要改代码。

### 9.8 MCP 安全边界

- 只允许连接 `mcp_servers` 白名单中的 Server。
- SaaS 生产环境禁用本地 STDIO MCP Server；`command_template` 只可用于本地开发或自托管单机模式。
- 远程 MCP Server 必须使用 HTTPS，并通过 `mcp_client_pool` 统一注入鉴权 Header。
- 工具描述和 annotations 视为不可信，不能覆盖后端的权限策略。
- 每次外部写入前，UI 必须明确展示目标 provider 和待写入任务数量。
- destructive 工具默认禁用，MVP 只开放创建任务。
- token 加密存储，运行时按用户和 provider 解密，调用结束后不落明文日志。

## 10. 端到端业务流程

### 10.1 新建计划并同步

```text
1. Frontend POST /api/intents
2. Backend 创建 agent_threads(status='running')
3. Backend 启动 LangGraph，传入 thread_id
4. router_node 判断 create_plan
5. planner_node 生成 TaskTree
6. task_tree_validator_node 校验任务树
7. human_review_node interrupt，Checkpointer 保存状态
8. Backend 更新 agent_threads(status='awaiting_confirmation')
9. Frontend 收到 plan_ready，展示任务树
10. 用户确认，Frontend 生成 `request_id`
11. Frontend POST /api/threads/{thread_id}/confirm
12. Backend 写入 `confirmation_requests`，用 `request_id` 做幂等去重
13. Backend 用同一 thread_id + Command(resume=...) 恢复图
14. persist_approved_tasks_node 写入 approved tasks
15. mcp_tool_discovery_node 刷新工具
16. executor_node 调 tools/call 写入 Todoist
17. sync_result_node 汇总结果
18. Frontend 收到 done
```

### 10.2 页面刷新恢复

```text
1. Frontend 本地保存 thread_id
2. 页面刷新后 GET /api/threads/{thread_id}
3. 如果 status='awaiting_confirmation'，渲染 task_tree 和 interrupt_payload
4. 用户继续确认/编辑/拒绝
5. Backend 用同一 thread_id 恢复 LangGraph
```

### 10.3 MVP 外部状态边界

PM 决策：MVP 只做“一键注入”。任务成功同步到 Todoist 后，EasyPlan 将本地任务标记为 `synced`，记录 `external_task_id` 和 `external_url`，本次计划即完成。

MVP 不做以下能力：

- 不轮询 Todoist 完成状态。
- 不接收 Todoist webhook 回写完成状态。
- 不在 EasyPlan 内维护外部任务的持续生命周期。
- 不做 Todoist 到 EasyPlan 的双向同步。

双向同步和外部完成状态追踪作为 v1.1 能力设计。

## 11. 错误处理

| 错误码 | 场景 | 处理 |
| --- | --- | --- |
| `TASK_TREE_VALIDATION_FAILED` | LLM 输出不符合模型或任务规则 | 自动进入 `planner_refinement_node` 继续拆解，最多 3 次；仍失败则返回可解释错误 |
| `THREAD_NOT_FOUND` | thread_id 不存在或不属于当前用户 | 404 |
| `THREAD_NOT_AWAITING_CONFIRMATION` | 非待确认状态调用 confirm | 409 |
| `REQUEST_ID_PAYLOAD_MISMATCH` | 重复 `request_id` 携带了不同 payload | 409，拒绝执行，要求客户端生成新的 request_id |
| `PLANNER_ALREADY_RUNNING` | 同一用户已有运行中的拆解图 | 409，返回已有 `thread_id` |
| `PLANNER_QUEUE_FULL` | 全局 planner 队列已满 | 429，提示稍后重试 |
| `CHECKPOINT_RESUME_FAILED` | Checkpointer 无法恢复 | 标记 failed，提示用户重新生成 |
| `STATE_TOO_LARGE` | 裁剪后 LangGraph State 仍超过上限 | 中止图执行，记录摘要，提示用户缩短输入或拆分目标 |
| `MCP_TRANSPORT_NOT_ALLOWED` | SaaS 生产环境配置了 stdio 或非 HTTPS endpoint | 禁用该集成并记录审计事件 |
| `MCP_REMOTE_AUTH_FAILED` | 远程 MCP Header 鉴权失败 | 标记 integration 需要重新授权 |
| `MCP_SERVER_UNAVAILABLE` | MCP Server 不可用 | 进入部分失败或失败状态 |
| `MCP_IDEMPOTENCY_KEY_REQUIRED` | 同步 item 缺少 idempotency_key | 拒绝外部调用，标记 sync_run failed |
| `MCP_TOOL_NOT_FOUND` | 找不到创建任务工具 | 提示集成不支持当前动作 |
| `MCP_TOOL_SCHEMA_MISMATCH` | 工具 schema 与参数不匹配 | 刷新 tools/list 后重试一次 |
| `EXTERNAL_AUTH_EXPIRED` | Todoist token 失效 | 标记集成 disconnected，引导重新授权 |

## 12. 测试策略

### 12.1 单元测试

- `TaskTree` Pydantic 校验：叶子任务超过 5 分钟、缺少动词、依赖不存在、依赖环。
- `planner_node`：最终输出必须能通过 `TaskTree` Pydantic 校验，同时只向 SSE 推送安全 reasoning 摘要。
- `human_review_node`：`refine` 动作接收自然语言 feedback，并回到 Planner 重新生成计划。
- `prune_agent_state`：删除 prompt/raw response/长 reasoning/MCP 大响应，超限时返回 `STATE_TOO_LARGE`。
- `router_node`：新建计划与查询状态分类。
- `task_tree_validator_node`：识别非法树，并对 `estimated_minutes >= 5` 的叶子触发自动再拆解。
- `confirmation_requests`：重复 `request_id` 返回同一结果，不一致 payload 返回 409。
- `ThreadOwnershipMiddleware`：跨用户访问 `thread_id` 返回 404/403，不能恢复 LangGraph。
- 自定义 Checkpointer：`get/put/list` 查询都包含 `user_id` 条件。
- `mcp_client_pool`：拒绝 stdio 生产配置、非 HTTPS endpoint、非白名单 host 和敏感 header 日志。
- planner 限流：同一用户已有运行中 graph 时返回 `PLANNER_ALREADY_RUNNING`。
- 时区解析：`X-User-Timezone=Asia/Shanghai` 下“明天上午”解析结果带 `+08:00` offset，缺失 header 时走 fallback 标记。
- OAuth state：过期、重复消费、跨用户 state 都会被拒绝；token 明文不会出现在响应或日志 payload 中。
- `tool_call_planner_node`：给定 MCP tool schema，生成合法 arguments。
- `mcp_registry`：工具 schema 变化时更新 `version_hash`。

### 12.2 集成测试

- LangGraph 执行到 `human_review_node` 后产生 interrupt，并写入 checkpoint。
- 使用同一 `thread_id` + `Command(resume=...)` 能恢复执行。
- 页面刷新场景下，`GET /api/threads/{thread_id}` 能取回待确认任务树。
- 重复调用 `/confirm` 且 `request_id` 相同，只创建一个 `sync_run`。
- 部分失败 retry 只重试 `pending/retryable_failed` item，跳过已存在 `external_task_id` 的 `synced` item。
- 缺少 `idempotency_key` 的 sync item 不会调用 MCP/Adapter。
- `checkpoint_retention_job` 能把 7 天前未确认会话标记为 expired，并清理终态 checkpoint。
- 远程 MCP mock server 通过 HTTPS/SSE + Authorization header 完成 `initialize`、`tools/list` 和 `tools/call`。
- 两个并发请求同时为同一用户创建 planner 时，只有一个 thread 进入 `running`。
- SSE 断线后，前端先拉取 thread 快照，再用 `Last-Event-ID` 重连，UI state_version 单调递增不回跳。
- OAuth start/callback 能完成 Todoist mock 授权，并只把加密凭证写入 `integrations.encrypted_credentials`。
- MCP mock server 返回 `tools/list` 和 `tools/call`，确认任务能同步并写入 `sync_run_items`。

### 12.3 端到端测试

- 用户提交意图 -> 收到 reasoning SSE -> 收到 plan_ready -> 确认 -> Todoist mock 收到创建任务请求 -> 前端收到 success。
- 用户编辑任务树 -> 后端重新校验 -> 再确认 -> 同步成功。
- LLM 首次生成了超过 5 分钟的叶子任务 -> 后端自动要求继续拆解 -> 前端只看到合格任务树。
- Todoist 第三个任务失败 -> 前两个任务保持 synced，thread 进入 `partially_succeeded`。

## 13. 实施建议

1. **优先打通 HITL 最小闭环**：先实现 `POST /api/intents`、SSE、`/confirm`、LangGraph Checkpointer 和 mock MCP Server，再接真实 Todoist。
2. **SaaS 生产禁用 stdio MCP**：Todoist 优先走 `internal_adapter` 或远程 HTTPS/SSE MCP；stdio 仅保留给本地开发。
3. **Checkpointer 必须多租户化**：完整 checkpoint 表和摘要表都带 `user_id`，所有恢复前先过 ownership guard。
4. **4C4G 先按小连接池和低并发配置**：单用户 planner 并发为 1，全局 planner 并发 4 起步，数据库连接池 `5+5` 起步。
5. **不要直接依赖 LangGraph 内部 checkpoint 表做业务查询**：内部表适合恢复图，业务状态应落在 `agent_threads` 和 `agent_checkpoints`。
6. **确认同步必须双层幂等**：`confirmation_requests.request_id` 防重复恢复图，`sync_run_items.idempotency_key` 防重复创建外部任务。
7. **SSE 必须支持快照对齐**：重连前先拉 `GET /api/threads/{thread_id}`，再用 `Last-Event-ID` 续接事件。
8. **所有时间必须有时区**：请求携带 `X-User-Timezone`，数据库用 `TIMESTAMPTZ`，API 返回 ISO 8601 带 offset。
9. **OAuth 闭环必须前后端打通**：前端只拿连接状态，后端负责 state/PKCE、token 交换、加密存储和断开授权。
10. **HITL 必须支持自然语言 refine**：用户可以输入反馈让 Planner 重新生成，不要求用户编辑 JSON。
11. **State 必须强力裁剪**：Checkpoint 只保存恢复必需的最小状态，长 reasoning、raw response、prompt 和大 MCP payload 禁止入库。
12. **MCP 部分失败必须可断点续传**：`idempotency_key` 是强制字段，retry 只重试未成功 item。
13. **任务粒度校验要自动修复**：`estimated_minutes >= 5` 时优先让 LLM 继续拆解，不把不合格树推给用户。
14. **默认开启 Checkpoint 清理**：4C4G 环境下不要无限保留 checkpoint，7 天未确认会话自动过期，终态 checkpoint 定期清理。
15. **MVP 只开放创建任务工具**：删除、移动、批量修改等 destructive 操作留到后续，并单独加更强确认。
16. **MVP 只做一键注入**：同步到 Todoist 成功即完成，不做双向状态追踪；外部完成状态回写放到 v1.1。
17. **reasoning 流不要暴露完整 chain-of-thought**：前端可以展示状态摘要，例如“正在识别依赖关系”，不要输出模型内部推理全文。
18. **为 Future memory 预留 pgvector，但不要提前复杂化**：MVP 可以只保存结构化偏好，等复盘功能明确后再引入向量检索。

## 14. 待确认问题

这些问题不阻塞 MVP 设计，但实现前建议产品侧确认：

1. MVP 是否只支持 Todoist，还是首版必须同时支持 Microsoft To Do？
2. 用户编辑任务树时，是否允许新增任意层级的 group，还是只允许编辑叶子 action？
3. “两分钟法则”在 UI 上是否需要显式展示？本文档建议后端只暴露预计分钟数和拆解结果，不向用户强调规则本身。
