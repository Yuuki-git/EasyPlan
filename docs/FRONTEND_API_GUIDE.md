# EasyPlan Frontend API Integration Guide

本文档为前端开发人员提供与 EasyPlan 后端服务交互的详细指南。

## 1. 全局配置

### 1.1 Base URL
- **开发环境**: `http://localhost:8000` (已在 Vite Proxy 中配置)
- **生产环境**: 相对路径 `/api` (由 Nginx 转发)

### 1.2 强制请求头 (Mandatory Headers)
所有写入类请求（POST）及快照请求必须携带以下 Header：
- `Content-Type: application/json`
- `X-User-Timezone`: 用户本地时区字符串（例如 `Asia/Shanghai`），用于 AI 准确解析“这周末”、“明天”等时间概念。

---

## 2. 核心业务流程接口

### 2.1 捕获意图 (Capture Intent)
用户在首页输入模糊目标并提交。

- **Endpoint**: `POST /api/intents`
- **Payload**:
  ```json
  {
    "intent_text": "这周末我想把那篇关于量子力学的论文初稿写完",
    "preferred_provider": "todoist" 
  }
  ```
- **Response (202 Accepted)**:
  ```json
  {
    "thread_id": "uuid-string",
    "status": "running",
    "events_url": "/api/threads/uuid-string/events"
  }
  ```
- **后续操作**: 前端应立即保存 `thread_id` 并根据 `events_url` 建立 SSE 连接。

### 2.2 状态对齐 (State Alignment)
用于页面刷新或 SSE 断线重连后恢复 UI 状态。

- **Endpoint**: `GET /api/threads/{thread_id}`
- **Response (200 OK)**: 返回 `ThreadSnapshot` 对象，包含当前的 `task_tree`、`status` 和 `intent_text`。

### 2.3 确认与微调 (Confirm & Refine)
当 AI 生成任务树（PENDING 态）后，用户进行的操作。

- **Endpoint**: `POST /api/threads/{thread_id}/confirm`
- **Payload (确认并同步)**:
  ```json
  {
    "request_id": "unique-uuid", 
    "action": "approve"
  }
  ```
- **Payload (对话式微调)**:
  ```json
  {
    "request_id": "unique-uuid",
    "action": "refine",
    "feedback": "太长了，帮我缩减一半"
  }
  ```

---

## 3. 实时流事件 (SSE Events)

建立连接: `GET /api/threads/{thread_id}/events`

原生 `EventSource` 不能设置 `Authorization` Header；建立 SSE 连接时请使用 `events_url + "?token=" + encodeURIComponent(accessToken)`。普通 API 请求仍使用 `Authorization: Bearer <access_token>`。

| 事件名 (Event) | 负载数据 (Data) | 描述 |
| :--- | :--- | :--- |
| `reasoning` | `{ "content": "..." }` | AI 的思考链路，前端追加到日志列表。 |
| `plan_ready` | `TaskTree` JSON | 任务拆解完成，前端应渲染任务树并进入 PENDING 态。 |
| `sync_status` | `{ "node_id": "...", "status": "success/error" }` | 任务同步到第三方工具的实时进度。 |
| `sync_complete`| `{ "status": "success/partial_error" }` | 整个同步流程结束的信号。 |

---

## 4. 集成管理 (Integrations)

### 4.1 获取集成状态
- **Endpoint**: `GET /api/integrations`
- **Response**: `IntegrationStatus[]`

### 4.2 OAuth 授权流程
1. 前端调用 `GET /api/integrations/todoist/oauth/start` 获取 `authorization_url`。
2. 引导用户在新窗口打开该 URL。
3. 后端处理回调后，前端通过 SSE 或定时轮询更新 `is_integrated` 状态。

---

## 5. 错误处理与幂等性
- **422 Unprocessable Entity**: 输入校验失败（如任务超过 5 分钟限制），请检查 `detail` 字段。
- **幂等性**: 在同步请求中务必携带生成的 `request_id`。后端会根据此 ID 确保 24 小时内不重复创建任务。
