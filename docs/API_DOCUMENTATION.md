# EasyPlan Backend API 接口文档

版本：`0.1.0`  
OpenAPI 源文件：[`docs/openapi.json`](./openapi.json)  
默认本地地址：`http://localhost:8000`

## 1. 通用约定

### 1.1 Content-Type

除 SSE 外，所有请求和响应默认使用：

```http
Content-Type: application/json
```

### 1.2 认证

需要登录态的接口使用 Bearer Token：

```http
Authorization: Bearer <access_token>
```

当前已接入认证的接口：

- `POST /api/intents`
- `GET /api/threads/{thread_id}`
- `GET /api/threads/{thread_id}/events`
- `POST /api/threads/{thread_id}/confirm`
- `GET /api/integrations/{provider}/oauth/start`

所有 thread、checkpoint、sync 查询必须接入同样的 `user_id` 租户过滤；恢复或订阅 thread 前，后端必须先校验该 thread 属于当前登录用户。

### 1.3 时区

涉及用户意图解析或确认动作的接口必须携带 IANA 时区：

```http
X-User-Timezone: Asia/Shanghai
```

非法时区会返回 `422`。所有时间戳必须使用 ISO 8601，并带时区信息。

### 1.4 错误格式

Pydantic/FastAPI 校验错误统一返回：

```json
{
  "detail": [
    {
      "loc": ["body", "intent_text"],
      "msg": "Field required",
      "type": "missing"
    }
  ]
}
```

业务错误会使用可解释的 `detail` 字段或 SSE `error` 事件返回。

## 2. Health

### GET `/health`

容器健康检查接口。

响应：

```json
{
  "status": "ok",
  "service": "easyplan-backend"
}
```

## 3. Auth

### POST `/api/auth/register`

注册用户并返回访问令牌。

请求：

```json
{
  "email": "user@example.com",
  "password": "correct-horse-battery-staple",
  "display_name": "Yuuki"
}
```

响应 `201`：

```json
{
  "access_token": "<jwt>",
  "token_type": "bearer",
  "expires_at": "2026-05-04T13:20:00+08:00"
}
```

### POST `/api/auth/token`

登录并获取访问令牌。

请求：

```json
{
  "email": "user@example.com",
  "password": "correct-horse-battery-staple"
}
```

响应：

```json
{
  "access_token": "<jwt>",
  "token_type": "bearer",
  "expires_at": "2026-05-04T13:20:00+08:00"
}
```

## 4. Intent Planning

### POST `/api/intents`

提交自然语言意图，创建 LangGraph thread，并返回 SSE 订阅地址。

Headers：

```http
Authorization: Bearer <access_token>
X-User-Timezone: Asia/Shanghai
```

行为：

- 创建 `AgentThread` 数据库记录，`user_id` 来自 JWT，不接受客户端透传。
- 在返回 `202` 前通过 `BackgroundTasks` 启动 LangGraph 后台规划。
- 后台运行使用 `build_task_graph()` 编译出的图，并在后台 worker 中消费 `graph.stream` 产生的 planner、validator、HITL interrupt 等节点事件。

请求：

```json
{
  "intent_text": "这周末前我想把这篇论文初稿写完",
  "preferred_provider": "todoist",
  "planner_provider": "openai",
  "planner_model": "gpt-4o-2024-08-06"
}
```

字段说明：

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `intent_text` | string | 是 | 用户自然语言意图，1-2000 字符 |
| `preferred_provider` | string | 否 | 外部同步目标，默认 `todoist` |
| `planner_provider` | enum | 否 | `openai`、`deepseek`、`xiaomi`，默认 `openai` |
| `planner_model` | string/null | 否 | 指定模型名；不传则用后端默认模型 |

响应 `202`：

```json
{
  "thread_id": "thr_01J...",
  "status": "running",
  "events_url": "/api/threads/thr_01J.../events"
}
```

## 5. Threads

### GET `/api/threads/{thread_id}`

获取 thread 快照，用于页面刷新、SSE 重连、状态对齐。

Headers：

```http
Authorization: Bearer <access_token>
```

响应：

```json
{
  "thread_id": "thr_01J...",
  "status": "awaiting_confirmation",
  "state_version": 5,
  "last_event_id": "evt_01J003",
  "server_time": "2026-05-04T13:20:00+08:00",
  "intent_text": "这周末前我想把这篇论文初稿写完",
  "task_tree": null,
  "interrupt_payload": null,
  "latest_checkpoint_id": "ckpt_01J..."
}
```

### GET `/api/threads/{thread_id}/events`

订阅 thread 的 Server-Sent Events。

Headers：

```http
Authorization: Bearer <access_token>
Last-Event-ID: evt_01J003
```

返回类型：

```http
Content-Type: text/event-stream
```

事件示例：

```text
id: evt_01J001
event: reasoning
data: {"state_version":3,"message":"正在识别核心动作...","code":"LLM_PLANNING_STARTED","node":"planner_node"}

id: evt_01J002
event: plan_ready
data: {"state_version":5,"thread_id":"thr_01J...","task_tree":{}}

id: evt_01J003
event: sync_progress
data: {"state_version":6,"success_count":3,"failure_count":0,"total_count":8}

id: evt_01J004
event: error
data: {"state_version":7,"code":"MCP_TOOL_CALL_FAILED","message":"Todoist 写入失败"}
```

事件类型：

| event | 必填字段 | 说明 |
| --- | --- | --- |
| `reasoning` | `state_version`, `message` | 安全进度摘要，不包含 chain-of-thought |
| `checkpoint` | `state_version`, `checkpoint_id`, `node` | LangGraph checkpoint 已持久化 |
| `plan_ready` | `state_version`, `thread_id`, `task_tree` | 任务树已通过校验 |
| `sync_progress` | `state_version`, `success_count`, `total_count` | 外部同步进度 |
| `done` | `state_version`, `status` | 终态事件 |
| `error` | `state_version`, `code`, `message` | 统一错误事件 |
| `snapshot_required` | 可选 `reason` | 前端必须重新拉取 thread 快照 |

### POST `/api/threads/{thread_id}/confirm`

对 HITL 中断进行确认、编辑、自然语言 refine 或拒绝。

Headers：

```http
Authorization: Bearer <access_token>
X-User-Timezone: Asia/Shanghai
```

行为：

- 后端先按 `user_id + thread_id` 查询 `AgentThread`，不存在或不属于当前用户时返回 `404`。
- `approve`、`edit`、`refine`、`reject` 都通过 LangGraph `Command(resume=...)` 恢复 HITL 中断。
- `refine` 接收自然语言 `feedback`，图会回到 planner 节点重新规划。

请求：

```json
{
  "request_id": "req_01J...",
  "action": "refine",
  "feedback": "任务还是太大了，请先聚焦今天 30 分钟内能启动的部分"
}
```

`action` 枚举：

| action | 说明 |
| --- | --- |
| `approve` | 用户确认任务树，进入外部同步 |
| `edit` | 用户提交编辑后的 `task_tree`，后端重新校验 |
| `refine` | 用户提交自然语言反馈，图回到 planner 重新生成 |
| `reject` | 用户拒绝计划，不调用外部工具 |

响应 `202`：

```json
{
  "thread_id": "thr_01J...",
  "request_id": "req_01J...",
  "status": "accepted"
}
```

幂等要求：

- `request_id` 由前端生成，推荐 UUID/ULID。
- 同一用户下重复 `request_id` 必须返回第一次处理结果。
- payload hash 不一致时应返回冲突错误。

## 6. Integrations

### GET `/api/integrations`

获取当前用户已连接的外部集成。

响应：

```json
[
  {
    "provider": "todoist",
    "display_name": "Todoist",
    "status": "connected",
    "is_integrated": true,
    "external_account_id": "todoist_user_123"
  }
]
```

当前外部任务 provider：

| provider | 说明 |
| --- | --- |
| `todoist` | Todoist 内置 Adapter，工具名 `todoist.create_task` |
| `microsoft_todo` | Microsoft Graph To Do 内置 Adapter，工具名 `microsoft_todo.create_task` |

### GET `/api/integrations/{provider}/tools`

获取指定 provider 暴露的 MCP 工具列表。

响应：

```json
{
  "tools": [
    {
      "name": "todoist.create_task",
      "title": "Create Todoist task",
      "input_schema": {}
    },
    {
      "name": "microsoft_todo.create_task",
      "title": "Create Microsoft To Do task",
      "input_schema": {}
    }
  ]
}
```

### POST `/api/integrations/{provider}/refresh-tools`

刷新指定 provider 的 MCP 工具发现结果。

响应：

```json
{
  "provider": "todoist",
  "status": "refresh_queued"
}
```

### GET `/api/integrations/{provider}/oauth/start`

启动 OAuth 授权流程。

Headers：

```http
Authorization: Bearer <access_token>
```

响应：

```json
{
  "provider": "todoist",
  "authorization_url": "https://todoist.com/oauth/authorize?...",
  "state": "oauth_state",
  "expires_at": "2026-05-04T13:30:00+08:00"
}
```

Microsoft To Do 授权时使用 provider `microsoft_todo`，授权 URL 指向 Microsoft identity platform，并请求 `offline_access User.Read Tasks.ReadWrite`。

安全要求：

- `state` 绑定 `user_id + provider + redirect_uri`。
- `state` 10 分钟过期，一次性消费。
- 前端不得接触 access token 或 refresh token。

### GET `/api/integrations/{provider}/oauth/callback`

OAuth provider 回调接口。

Query：

| 参数 | 必填 | 说明 |
| --- | --- | --- |
| `code` | 是 | OAuth 授权码 |
| `state` | 是 | 后端生成的一次性 state |

响应：

```json
{
  "provider": "todoist",
  "status": "connected"
}
```

Microsoft To Do callback 成功时返回：

```json
{
  "provider": "microsoft_todo",
  "status": "connected"
}
```

Microsoft To Do 幂等策略：

- Adapter 会把 `idempotency_key` 映射为稳定 category：`EasyPlan:<sha256-prefix>`。
- 创建前会读取目标 To Do list 的近期任务，若已存在相同 category，则直接返回已有任务，不再次 POST 创建。
- 新任务的 `body.content` 会附加 `EasyPlan idempotency_key: ...`，用于人工排查和后续迁移到 Graph open extension。

## 7. 核心数据结构

### TaskTree

```json
{
  "root": {
    "client_node_id": "root",
    "title": "论文初稿",
    "description": null,
    "verb": "规划",
    "estimated_minutes": 1,
    "node_type": "group",
    "depends_on": [],
    "children": [
      {
        "client_node_id": "task-1",
        "title": "打开论文文档",
        "description": null,
        "verb": "打开",
        "estimated_minutes": 2,
        "node_type": "action",
        "depends_on": [],
        "children": []
      }
    ]
  },
  "summary": "论文初稿启动计划",
  "assumptions": []
}
```

约束：

- `TaskTree` 最大深度：`8`
- 总节点数上限：`200`
- 单节点 `children` 最大数量：`20`
- `action.estimated_minutes` 必须 `< 5`
- `depends_on` 只能引用同一棵树内存在的 `client_node_id`
- 不允许依赖环

### ThreadSnapshot

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `thread_id` | string | 会话 ID |
| `status` | string | 当前状态 |
| `state_version` | integer | 状态版本，SSE 对齐使用 |
| `last_event_id` | string/null | 最新 SSE event id |
| `server_time` | datetime | 服务端当前时间 |
| `intent_text` | string | 原始用户意图 |
| `task_tree` | object/null | 当前任务树 |
| `interrupt_payload` | object/null | HITL 中断 payload |
| `latest_checkpoint_id` | string/null | 最新 checkpoint id |

## 8. 当前实现状态说明

以下接口已在 OpenAPI 中声明，但部分业务逻辑仍是 MVP 骨架：

- `POST /api/intents` 已接入 JWT、`AgentThread` 持久化和 LangGraph 后台运行；后续生产增强项是将后台任务迁移到独立 worker/队列。
- `GET /api/threads/{thread_id}/events` 已接入 thread 归属校验和 LangGraph runtime 事件流；当前事件 buffer 为进程内轻量实现，后续生产增强项是持久化事件游标。
- `POST /api/threads/{thread_id}/confirm` 已接入 HITL resume，支持 `refine` 自然语言反馈回到 planner。
- `GET /api/integrations`、`GET /tools` 当前为接口骨架，后续应接入数据库和 MCP tool registry。
- OAuth callback 已具备服务层闭环，生产环境需替换真实持久化 repository。
