# EasyPlan 前端 API 接入指南

版本：`v1.2.6-rc.1 Candidate`

本文描述当前前端与后端的真实契约。字段定义以
[`docs/openapi.json`](./openapi.json) 和
[`app/api/schemas.py`](../app/api/schemas.py) 为准。

## 1. 通用约定

- 开发环境由 Vite 代理 `/api` 到后端。
- 普通 API 使用 `Authorization: Bearer <access_token>`。
- SSE 使用 query token，因为原生 `EventSource` 不能设置 Authorization header。
- 写请求发送 `Content-Type: application/json`。
- 需要理解自然语言时间的请求发送 `X-User-Timezone`，例如 `Asia/Shanghai`。
- `401` 必须清理当前用户的本地项目上下文并拉起登录恢复。
- `409` 表示请求身份或线程状态冲突，不应由前端假装成功。

## 2. 创建初始规划

```http
POST /api/intents
```

```json
{
  "intent_text": "我想转行产品经理，但不知道是否适合"
}
```

响应为 `202 Accepted`：

```json
{
  "thread_id": "thread-id",
  "request_id": "request-id",
  "status": "running",
  "events_url": "/api/threads/thread-id/events?run_type=initial&request_id=request-id"
}
```

前端收到响应后必须立即保存：

```ts
type ActiveRun = {
  threadId: string;
  runType: 'initial' | 'next_phase';
  requestId: string;
};
```

不要根据 `previewMode` 推导当前 run，也不要在 board idle 状态订阅历史 SSE。

## 3. Request-scoped SSE

```http
GET /api/threads/{thread_id}/events
  ?run_type=initial|next_phase
  &request_id={request_id}
  &token={access_token}
  &last_event_id={optional_cursor}
```

每个业务事件都包含真实 run 身份：

```json
{
  "thread_id": "thread-id",
  "run_type": "next_phase",
  "request_id": "request-id",
  "state_version": 12
}
```

当前事件：

| 事件 | 用途 |
| --- | --- |
| `reasoning` | 简短进度反馈，不展示内部推理 |
| `checkpoint` | 节点状态更新 |
| `plan_ready` | 预览任务树可用，进入 `PENDING` |
| `done` | 当前 request 已完成 |
| `agent_error` | 当前 request 失败 |
| `snapshot_required` | 游标失效，需要重新读取线程快照 |

前端只处理同时匹配 `thread_id + run_type + request_id` 的事件。旧
`EventSource` handler 还必须确认自己仍是当前连接，避免迟到事件修改新页面。

## 4. 快照恢复

```http
GET /api/threads/{thread_id}
```

`ThreadSnapshot` 用于刷新、断线重连和状态校准，核心字段包括：

- `status`
- `task_tree`
- `interrupt_payload`
- `state_version`
- `last_event_id`

当前实现中，snapshot 的 `state_version` 固定为 `0`，`last_event_id` 为 `null`；
它们是契约保留字段，不能作为前端防乱序的权威版本。SSE 事件自身仍带递增的
`state_version`。

前端恢复时应区分：

- `task_tree`：已提交计划，即 `committedTaskTree`
- `interrupt_payload.task_tree`：等待确认的草案，即 `previewTaskTree`
- `phase_generation_state`：下一阶段 request 的运行或终态

快照请求必须经过请求序号或等价 gate 校验，并结合 active run identity 判断。
较早发出的响应不得覆盖较新的 phase 或项目状态。

## 5. 确认与微调

```http
POST /api/threads/{thread_id}/confirm
```

确认：

```json
{
  "request_id": "当前 activeRun.requestId",
  "action": "approve"
}
```

微调：

```json
{
  "request_id": "当前 activeRun.requestId",
  "action": "refine",
  "feedback": "减少任务数量，先保留最关键的步骤"
}
```

初始规划、refine 和下一阶段都必须复用各自真实的 `request_id`。刷新恢复后也
不得生成新的确认 ID。

## 6. 下一阶段

### 6.1 开始生成

```http
POST /api/threads/{thread_id}/phases/next
```

```json
{
  "request_id": "由前端生成的唯一 request id"
}
```

返回的 `events_url` 已绑定 `run_type=next_phase` 和 `request_id`。下一阶段始终
追加到当前 thread，不创建新项目。

### 6.2 取消生成或预览

```http
DELETE /api/threads/{thread_id}/phases/next/cancel?request_id={request_id}
```

只允许取消尚未确认的 next-phase request：

- `THINKING` / `PENDING`：可以取消
- `SYNCING`：已经接受确认，不可撤销

取消成功后使用响应快照恢复已提交计划，并清理 `activeRun`、
`previewTaskTree` 和对应 localStorage。

### 6.3 查询提交结果

```http
GET /api/threads/{thread_id}/phases/next/commit?request_id={request_id}
```

该接口是 next-phase `done` 后的权威提交回执。只有回执确认同一 request 已
`confirmed`，且返回新阶段 task tree / tasks 后，前端才清除 preview。

## 7. 前端状态语义

| 状态 | 用户操作 | 网络语义 |
| --- | --- | --- |
| `INITIAL` | 提交新意图 | 无 active run 时不订阅 SSE |
| `THINKING` | 放弃等待（如果是 initial）或取消（如果是 next_phase） | next phase 可后端取消；initial 当前只结束本地等待 |
| `PENDING` | 确认、微调、放弃此计划/取消 | 等待用户决定 |
| `SYNCING` | 返回当前计划/返回全部计划 | 已确认，不再允许取消，后台提交依然进行 |
| `ERROR` | 重试本次生成、返回当前计划、播种新想法 | 仅处理当前 request |

- `SYNCING` 的“返回全部计划” (对于新 initial) 或“返回当前计划” (对于 refine/next_phase) 只是前台改变视图/收起生成面板，绝不能清除 `activeRun` 或 `phaseRequestId`；后台完成后仍需由当前 request 的 `done` 更新项目。
- 在 `stalled` (连接卡住) 状态下触发的“重新连接”操作只增加 `sseReconnectNonce` 来重置 EventSource，绝不发送新的创建请求。

## 8. 原生任务 API

```http
GET    /api/tasks?view_bucket=planned|my_day
POST   /api/tasks
PATCH  /api/tasks/{task_id}
DELETE /api/tasks/{task_id}
```

- 项目内新增任务必须带 `thread_id`。
- 无项目上下文时不要显示会隐式创建项目的“添加任务”入口。
- “我的一天”是虚拟视图，不能改变任务所属项目。
- `planned` 与 `my_day` 中同一 `task_id` 的状态必须保持一致。

## 9. 前端验收命令

```bash
cd frontend
npm run test:hooks
npm run test:portfolio
node tests/runEvents.test.mjs
node tests/stateRestoration.test.mjs
npm run build
npm run lint
```

涉及 SSE 生命周期的改动至少覆盖：历史终态回放、跨 run 隔离、刷新恢复、退出清理、乱序快照、next-phase 提交后可见、连接卡住（stalled）重新连接、初始确认后返回全部计划且保留 active run。
