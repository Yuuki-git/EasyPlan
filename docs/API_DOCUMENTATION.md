# EasyPlan Backend API 文档

版本：`v1.2.0`  
OpenAPI 源文件：[`docs/openapi.json`](./openapi.json)  
默认本地地址：`http://localhost:8000`

## 1. 通用约定

除 SSE 外，所有请求和响应默认使用：

```http
Content-Type: application/json
```

需要登录态的接口使用 Bearer Token：

```http
Authorization: Bearer <access_token>
```

`GET /api/threads/{thread_id}/events` 为原生 `EventSource` 兼容额外支持 query token：

```http
GET /api/threads/{thread_id}/events?token=<access_token>
```

普通 API 只接受 `Authorization` Header。SSE 接口优先读取 Header，Header 不存在时才读取 `token` query 参数。

涉及用户意图解析或确认动作的接口必须携带 IANA 时区：

```http
X-User-Timezone: Asia/Shanghai
```

非法时区返回 `422`。所有时间戳使用带时区的 ISO 8601。

## 2. 错误响应

Pydantic/FastAPI 参数错误保持标准 `422`：

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

未处理异常由全局异常处理器兜底，服务端日志记录完整 traceback，前端只收到脱敏 JSON：

```json
{
  "error_code": "INTERNAL_ERROR",
  "message": "服务器在思考时走神了，请稍后再试。"
}
```

## 3. Health

### GET `/health`

容器健康检查接口。

```json
{
  "status": "ok",
  "service": "easyplan-backend"
}
```

## 4. Auth

### POST `/api/auth/register`

注册用户并返回访问令牌。用户写入 PostgreSQL `users` 表，JWT `sub` 与 `users.id` 一致。

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
  "expires_at": "2026-05-08T13:20:00+08:00"
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
  "expires_at": "2026-05-08T13:20:00+08:00"
}
```

## 5. Intent Planning

### POST `/api/intents`

提交自然语言意图，创建 LangGraph thread，并返回 SSE 订阅地址。v1.2.0 起 EasyPlan 不再写入外部任务系统，规划结果进入内部任务看板闭环。

Headers：

```http
Authorization: Bearer <access_token>
X-User-Timezone: Asia/Shanghai
```

请求：

```json
{
  "intent_text": "这周末前我想把论文初稿写完",
  "preferred_provider": "native",
  "planner_provider": null,
  "planner_model": null
}
```

字段说明：

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `intent_text` | string | 是 | 用户自然语言意图，1-2000 字符 |
| `preferred_provider` | string | 否 | 内部目标，默认 `native`；保留该字段用于兼容旧客户端 |
| `planner_provider` | enum/null | 否 | `openai`、`deepseek`、`xiaomi`；不传或传 `null` 时使用后端 `EASYPLAN_LLM_PROVIDER` |
| `planner_model` | string/null | 否 | 指定模型名；不传则用后端默认模型 |

响应 `202`：

```json
{
  "thread_id": "thr_01J...",
  "status": "running",
  "events_url": "/api/threads/thr_01J.../events"
}
```

## 6. Threads

### GET `/api/threads/{thread_id}`

获取 thread 快照，用于页面刷新、SSE 重连和状态对齐。

```json
{
  "thread_id": "thr_01J...",
  "status": "awaiting_confirmation",
  "state_version": 5,
  "last_event_id": "evt_01J003",
  "server_time": "2026-05-08T13:20:00+08:00",
  "intent_text": "这周末前我想把论文初稿写完",
  "task_tree": null,
  "interrupt_payload": null,
  "latest_checkpoint_id": "ckpt_01J..."
}
```

### GET `/api/threads/{thread_id}/events`

订阅 thread 的 Server-Sent Events。

```http
Authorization: Bearer <access_token>
Last-Event-ID: evt_01J003
```

原生 `EventSource` 无法发送 Header 时：

```http
GET /api/threads/thr_01J.../events?token=<access_token>&last_event_id=evt_01J003
```

事件类型：

| event | 必填字段 | 说明 |
| --- | --- | --- |
| `reasoning` | `state_version`, `message` | 安全进度摘要，不包含 chain-of-thought |
| `checkpoint` | `state_version`, `node` | LangGraph 节点推进 |
| `plan_ready` | `state_version`, `thread_id`, `task_tree` | 任务树已通过校验，等待用户确认 |
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
| `approve` | 用户确认任务树，进入内部任务看板闭环 |
| `edit` | 用户提交编辑后的 `task_tree`，后端重新校验 |
| `refine` | 用户提交自然语言反馈，图回到 planner 重新生成 |
| `reject` | 用户拒绝本次计划 |

响应 `202`：

```json
{
  "thread_id": "thr_01J...",
  "request_id": "req_01J...",
  "status": "accepted"
}
```

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

## 8. 当前实现状态

- `POST /api/intents` 已接入 JWT、`AgentThread` 持久化和 LangGraph 后台运行。
- `GET /api/threads/{thread_id}/events` 已接入 thread 归属校验、增量重播和 Async Queue 长连接推送。
- `POST /api/threads/{thread_id}/confirm` 已接入 HITL resume，支持 `refine` 自然语言反馈回到 planner。
- 全局异常处理已接入，500 响应不会暴露 traceback、SQL、token 或内部实现细节。
- v1.2.0 的下一步是把用户确认后的 `TaskTree` 展开写入内部 `tasks` 与 `task_dependencies` 表，供原生任务看板消费。
