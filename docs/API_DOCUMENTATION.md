# EasyPlan Backend API

版本：`v1.2.6-rc.1`
OpenAPI：[`docs/openapi.json`](./openapi.json)
本地地址：`http://localhost:8000`

## 1. 通用约定

普通登录接口使用：

```http
Authorization: Bearer <access_token>
```

涉及意图解析、确认或阶段生成的请求应携带：

```http
X-User-Timezone: Asia/Shanghai
```

SSE 因浏览器 `EventSource` 无法设置 Header，额外支持：

```text
?token=<access_token>
```

所有 thread、task 和 checkpoint 操作均绑定当前 `user_id`。不存在和无权限资源统一使用 `404`，避免泄露其他租户数据。

## 2. 错误格式

参数校验使用 FastAPI `422`：

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

状态冲突使用 `409`：

```json
{
  "detail": {
    "error_code": "REQUEST_ID_MISMATCH",
    "message": "Request does not match the current run"
  }
}
```

SSE 业务错误统一使用 `agent_error`，不使用浏览器保留的 `error` 事件名。

## 3. Health

### GET `/health`

```json
{
  "status": "ok",
  "service": "easyplan-backend"
}
```

## 4. Auth

### POST `/api/auth/register`

```json
{
  "email": "user@example.com",
  "password": "strong-password",
  "display_name": "Yuuki"
}
```

### POST `/api/auth/token`

```json
{
  "email": "user@example.com",
  "password": "strong-password"
}
```

响应：

```json
{
  "access_token": "...",
  "token_type": "bearer",
  "expires_at": "2026-07-04T12:00:00Z"
}
```

## 5. Intent

### POST `/api/intents`

启动一个新的 initial planning run。

请求：

```json
{
  "intent_text": "我想转行产品经理，但不知道怎么开始",
  "preferred_provider": "native",
  "planner_provider": "deepseek"
}
```

响应：`202 Accepted`

```json
{
  "thread_id": "thr_...",
  "request_id": "uuid",
  "status": "running",
  "events_url": "/api/threads/thr_.../events?run_type=initial&request_id=uuid"
}
```

前端必须保存 `thread_id` 和 `request_id`，并使用返回的 run 身份建立 SSE。

## 6. Thread Snapshot

### GET `/api/threads/{thread_id}`

用于刷新、重连和跨视图恢复。

主要字段：

```json
{
  "thread_id": "thr_...",
  "status": "running",
  "state_version": 0,
  "last_event_id": null,
  "server_time": "2026-07-04T12:00:00Z",
  "intent_text": "...",
  "task_tree": null,
  "interrupt_payload": null,
  "latest_checkpoint_id": null
}
```

`interrupt_payload` 可能是 initial review、next-phase running/review，或 confirmed/cancelled/failed terminal envelope。

## 7. SSE

### GET `/api/threads/{thread_id}/events`

参数：

| 参数 | 必填 | 说明 |
| --- | --- | --- |
| `request_id` | 是 | 当前 run 的唯一身份 |
| `run_type` | 否 | `initial` 或 `next_phase`，默认 `initial` |
| `last_event_id` | 否 | query cursor fallback |
| `token` | SSE 登录时 | EventSource query token |

也支持标准 `Last-Event-ID` Header。

每个 run 事件都包含：

```json
{
  "thread_id": "thr_...",
  "run_type": "next_phase",
  "request_id": "uuid",
  "state_version": 42
}
```

事件：

| 事件 | 附加字段 | 说明 |
| --- | --- | --- |
| `reasoning` | `message` | 当前 run 的轻量进度 |
| `checkpoint` | `node` | LangGraph 节点进度 |
| `plan_ready` | `task_tree` | 计划预览可用 |
| `done` | `status` | 当前 run 完成 |
| `agent_error` | `code`, `message` | 脱敏业务错误 |
| `snapshot_required` | `reason` | cursor 无法回放，需要快照对齐 |

事件缓存、订阅和 terminal 结束均按 `thread_id + run_type + request_id` 隔离。历史 run 的 `done` 不得截断新 run。

## 8. Human Review

### POST `/api/threads/{thread_id}/confirm`

请求：

```json
{
  "request_id": "uuid",
  "action": "approve"
}
```

`action`：

- `approve`
- `edit`
- `refine`
- `reject`

Refine 示例：

```json
{
  "request_id": "new-run-uuid",
  "action": "refine",
  "feedback": "任务太多了，保留今天能启动的部分"
}
```

initial/refine/next-phase 都必须使用当前预览对应的真实 request ID。重复确认和错误 request ID 返回 `409`。

next-phase approve 进入 `SYNCING` 后不可取消。

## 9. Next Phase

### POST `/api/threads/{thread_id}/phases/next`

当前阶段 AI Action 全部完成后，在同一 thread 中生成下一阶段预览。

```json
{
  "request_id": "uuid"
}
```

响应：`202 Accepted`

```json
{
  "thread_id": "thr_...",
  "request_id": "uuid",
  "status": "running",
  "events_url": "/api/threads/thr_.../events?run_type=next_phase&request_id=uuid"
}
```

常见 `409`：

- 当前阶段尚未完成。
- 已存在进行中的下一阶段 request。
- request ID 已取消或已确认。
- 计划缺少有效 phase 数据。

### DELETE `/api/threads/{thread_id}/phases/next/cancel`

Query：

```text
request_id=<uuid>
```

允许取消：

- `phase_generation_state/running`
- stalled generation
- `next_phase_review/awaiting_confirmation`
- 同一 request 的重复取消

不允许取消 confirming/confirmed request。

成功返回最新 `ThreadSnapshot`，保留 committed task tree，并写入 cancelled tombstone。

### GET `/api/threads/{thread_id}/phases/next/commit`

Query：

```text
request_id=<uuid>
```

用于确认后的确定性对齐：

```json
{
  "thread_id": "thr_...",
  "request_id": "uuid",
  "status": "confirmed",
  "current_phase_id": "phase-2",
  "task_tree": {},
  "tasks": []
}
```

`status` 可能为：

- `confirmed`
- `incomplete`
- `running`
- `awaiting_confirmation`
- `confirming`
- `cancelled`
- `failed`
- `unknown`

前端只有在 request 已 confirmed、current phase 已前进且新 phase 任务存在时，才能清除 preview。

## 10. Thread Lifecycle

### DELETE `/api/threads/{thread_id}`

删除当前用户的 thread 及其任务。

- `204`：成功
- `404`：不存在或不属于当前用户

## 11. Tasks

### GET `/api/tasks`

Query：

```text
view_bucket=planned|my_day|backlog
```

`my_day` 是虚拟视图，任务仍保留原 thread 和项目结构。

### POST `/api/tasks`

```json
{
  "title": "补充用户访谈问题",
  "description": null,
  "view_bucket": "planned",
  "is_in_my_day": false,
  "parent_task_id": null,
  "thread_id": "thr_..."
}
```

规则：

- 有 `parent_task_id` 时继承 parent thread。
- 无 parent 但有 `thread_id` 时，在当前项目创建 root task。
- 两者都没有时，创建 manual thread。
- `view_bucket=my_day` 会规范化为 planned task + `is_in_my_day=true`。

### PATCH `/api/tasks/{task_id}`

支持：

```json
{
  "title": "更新后的标题",
  "description": null,
  "status": "completed",
  "view_bucket": "planned",
  "is_in_my_day": true,
  "estimated_minutes": null,
  "sort_order": 2
}
```

- `description: null` 清空描述。
- `estimated_minutes: null` 清空预计时间。
- `title/status/view_bucket/is_in_my_day/sort_order` 显式 `null` 返回 `422`。
- 至少提供一个变更字段。

### DELETE `/api/tasks/{task_id}`

- `204`：删除成功
- `404`：不存在或不属于当前用户

## 12. TaskResponse 扩展字段

除基础任务字段外，响应可包含：

```json
{
  "done_criteria": "完成后可明确判断结果",
  "start_hint": "先打开现有材料",
  "fallback_action": "如果时间不足，先完成最小版本",
  "source": "ai",
  "phase_id": "phase-2",
  "phase_order": 2
}
```

这些字段从任务 metadata 中读取，旧任务缺失时返回 `null`。

## 13. 长期执行循环 API

该能力仅对启用 schema v2 的 `long_term_growth` thread 生效。所有接口都要求
JWT、`X-User-Timezone`，并按 `user_id + thread_id` 校验所有权。

### POST `/api/threads/{thread_id}/practice-loops/{loop_id}/schedule-today`

为指定循环创建或返回今天的 occurrence task：

- task 物理归属仍是 `planned`，初次创建时 `is_in_my_day=true`；
- 同一个 loop 同时最多保留一个 active occurrence；
- 当天已有完成日志、当前周已达到配额或 loop 不可用时返回 `409`；
- 重复请求不会创建重复 occurrence。

未来日期 occurrence 不会预生成。用户之后可通过普通 Task PATCH 控制
`is_in_my_day`。

### PUT `/api/threads/{thread_id}/phases/{phase_id}/review`

创建或更新当前阶段 draft review。请求可包含 checkpoint evidence、difficulty、
next capacity 和 early-review 标记。响应包含系统 recommendation 与 readiness
statistics，系统事实不可由客户端覆盖。

### POST `/api/threads/{thread_id}/phases/{phase_id}/review/decision`

finalize 当前阶段复盘：

- `proceed`：接受当前阶段结果；
- `extend`：延长当前循环，但总周期不得超过 12 周；
- `adjust`：为下一本地周创建新的 loop revision；
- `override`：覆盖系统 recommendation，必须提交可见的 `override_reason`。

只有 finalized `proceed` 或 `override` review 才能调用下一阶段生成接口。

### ThreadSnapshot 扩展

schema v2 snapshot 增加 `long_term_execution`，包含：

- 当前 loop 的本周、累计和 required completions；
- `one_off_ready/process_ready/outcome_ready`；
- `recommendation` 与 `review_available`；
- draft、最近 finalized review 与完整 review history。

schema v1 与非长期 thread 返回 `null`，保持旧客户端兼容。

## 14. 契约来源

修改接口时必须同步：

- `app/api/schemas.py`
- `docs/openapi.json`
- 本文档
- `docs/FRONTEND_API_GUIDE.md`
- 对应 contract/integration tests
