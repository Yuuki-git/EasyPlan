# EasyPlan 前端 API 接入指南

版本：`v1.3.0-rc`

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
  ?run_type=initial|refine|next_phase
  &request_id={request_id}
  &token={access_token}
  &last_event_id={optional_cursor}
```

每个业务事件的 `data` 都是统一 envelope：

```json
{
  "event_id": "thread-id:next_phase:request-id:000001",
  "thread_id": "thread-id",
  "request_id": "request-id",
  "run_type": "next_phase",
  "event_type": "planning_started",
  "seq": 1,
  "created_at": "2026-07-08T00:00:00Z",
  "payload": {
    "stage": "planning_started",
    "label": "正在生成任务",
    "state_version": 12
  }
}
```

当前事件：

| 事件 | 用途 |
| --- | --- |
| `run_started` | run 已启动 |
| `intent_profile_started` | 正在判断目标类型 |
| `intent_profile_completed` | 意图画像完成 |
| `strategy_selected` | 已选择规划策略 |
| `planning_started` | Planner 开始生成任务 |
| `validation_started` | Validator 开始检查 |
| `repair_started` | 有限重试修复开始 |
| `persistence_started` | 开始保存计划 |
| `still_running` | 长耗时 run 心跳 |
| `plan_ready` | 预览任务树可用，进入 `PENDING` |
| `sync_status` | 确认后的保存/同步进度 |
| `sync_complete` | 保存/同步完成 |
| `done` | 当前 request 已完成 |
| `agent_error` | 当前 request 失败 |
| `snapshot_required` | 游标失效，需要重新读取线程快照 |

`sync_status` 和 `sync_complete` 是 stage-only 事件，只读取
`payload.stage`、`payload.label`、`payload.state_version`。不要从这两个事件读取
`payload.status`；成功/失败分别以 `done` 和 `agent_error` 为准。

前端只处理同时匹配 `thread_id + run_type + request_id` 的事件。旧
request 的 `done` 不能结束当前生成；旧 request 的 `plan_ready` 不能覆盖当前预览。
`EventSource` handler 还必须确认自己仍是当前连接，避免迟到事件修改新页面。
这些事件是产品状态反馈，不是模型 chain-of-thought。

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
它们是契约保留字段，不能作为前端防乱序的权威版本。SSE 事件自身使用
run-scoped `seq` 和 envelope `event_id`。

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

## 9. 长期执行循环

`ThreadSnapshot.long_term_execution` 是 schema v2 项目的执行权威状态。前端不得
用本地任务计数替代 backend readiness。

```http
POST /api/threads/{thread_id}/practice-loops/{loop_id}/schedule-today
PUT  /api/threads/{thread_id}/phases/{phase_id}/review
POST /api/threads/{thread_id}/phases/{phase_id}/review/decision
```

- schedule 成功后按 task ID 合并任务，并重新加载 selected project snapshot；
- occurrence 初次创建时会加入 My Day，之后继续复用普通 Task PATCH；
- 完成 occurrence 后重新加载 snapshot 和当前任务视图；
- `409` 仅更新 `practiceError`，不得覆盖已有 board tasks；
- review mutation 完成后重新读取 snapshot，不在前端自行计算 recommendation；
- schema v1 的 `long_term_execution` 为 `null`，继续使用原 unlock 逻辑。

UI 分工：

- `PracticeLoopPanel` 显示本周/累计进度，并只在后端允许时安排今天；
- `PhaseReviewPanel` 收集 evidence 和用户 decision，始终展示系统 recommendation；
- `PhaseRecords` 仅位于选中项目内，展示 finalized review、历史配额和 override reason；
- “全部计划”和“我的一天”不渲染项目级 Phase Records。

## 10. Task Assist / Action Coach

Task Assist 只用于未完成的普通 Action，使用独立于规划生成的 run。首版 mode：

```text
start      -> 保存 start_hint
unstick    -> 保存用户选择的 fallback_action
decompose  -> 创建 2-5 个 source=task_assist children，并启用父任务 roll-up
```

### 10.1 API

```http
POST   /api/tasks/{task_id}/assist
GET    /api/tasks/{task_id}/assist/{request_id}
GET    /api/tasks/{task_id}/assist/{request_id}/events
DELETE /api/tasks/{task_id}/assist/{request_id}
POST   /api/tasks/{task_id}/assist/{request_id}/apply
```

开始请求由前端生成唯一 request ID：

```json
{
  "request_id": "uuid",
  "mode": "start | unstick | decompose",
  "user_context": "可选补充信息，最多 1000 字"
}
```

Apply 请求只有 `unstick` 必须发送用户选择的 option：

```json
{
  "selected_option_id": "option id or null"
}
```

重复 Apply 返回已保存的 receipt。前端按 `affected_task_ids` 合并父任务和 children，
随后复用 `loadProjectSnapshot` 与当前 view 的 `fetchTasks`，不能整表覆盖较新的任务状态。

### 10.2 Run、SSE 与恢复

Task Assist 状态为：

```text
running -> ready -> applied
   |         |
   +-> cancelled / failed / expired
```

- 使用独立 `run_type=task_assist`，不能写入或读取 plan-level `activeRun`；
- SSE 只接受完整匹配 thread、task、request、run type 的 allowlist event；
- 通过 `event_id` 去重，handler 写 store 前确认 EventSource 仍是当前实例；
- allowlist 为 `run_started`、`task_context_ready`、`assist_generation_started`、
  `assist_validation_started`、`still_running`、`assist_ready`、`done`、`agent_error`；
- localStorage 只保存当前 task ID、request ID 和 mode；页面恢复时必须先查询 snapshot，
  不能创建新的 request 或重复调用 DeepSeek；
- `ready` 恢复 proposal，`running` 恢复进度流，terminal 状态清理本地 identity。

### 10.3 取消、错误与关闭

- running 状态关闭 panel 前必须先调用 DELETE 取消服务端 run；
- 取消成功后清理 task/request/mode、proposal、日志与错误，并关闭 panel；
- 取消失败时保留 panel 和 run identity，显示可见错误，不能留下空面板或本地假成功；
- ready proposal 只关闭不 Apply 时不修改业务任务；
- `TASK_ASSIST_CONTEXT_STALE` 显示“任务已变化，请重新生成建议”，重新生成时保留 mode
  与用户补充信息；
- `TASK_ASSIST_ACTIVE_RUN`、`TASK_ASSIST_INTERRUPTED`、过期、401 和 409 必须走结构化
  恢复提示，不显示原始 provider 错误。

### 10.4 Decompose、Project 与 My Day

- Assist children 使用现有 task ID 和 `parent_task_id`，项目页按父子层级展示；
- 父任务存在未完成 children 时 checkbox 禁用；全部 children 完成后父任务由后端自动完成；
- My Day 的父任务是承诺锚点。后端为 My Day parent 隐式返回直接 Assist children，
  但 child 自身保持 `is_in_my_day=false`；
- 前端必须从平铺 API 响应重建父子树。Assist child 只嵌套显示，不得成为顶层任务；
- Assist child 不显示 My Day 按钮。服务端也会以
  `TASK_ASSIST_CHILD_MY_DAY_FORBIDDEN` 拒绝独立加入；
- 父任务移出 My Day 后，其隐式 children 不再显示。

后端和前端 feature flag 均默认关闭：

```env
EASYPLAN_TASK_ASSIST_ENABLED=false
VITE_TASK_ASSIST_ENABLED=false
```

## 11. 前端验收命令

```bash
cd frontend
npm run test:hooks
npm run test:portfolio
npm run test:long-term
npm run test:strategy
npm run test:task-assist
node tests/runEvents.test.mjs
node tests/stateRestoration.test.mjs
npm run build
npm run lint
```

涉及 SSE 生命周期的改动至少覆盖：历史终态回放、跨 run 隔离、刷新恢复、退出清理、乱序快照、next-phase 提交后可见、连接卡住（stalled）重新连接、初始确认后返回全部计划且保留 active run。

Task Assist 还必须覆盖：running cancel 成功与失败、snapshot 恢复、stale Apply、错误身份事件、
平铺 My Day 数据的树重建、Assist child 无 My Day 按钮、orphan child 不成为顶层任务。
