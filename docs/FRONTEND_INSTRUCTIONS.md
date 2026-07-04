# EasyPlan 前端开发指令

适用版本：`v1.2.5-rc.2 Candidate` 及后续维护补丁

## 1. 当前目标

前端需要稳定承载：

- 新意图生成与确认
- 全部计划总览
- 具体项目执行
- 我的执行视图
- 同 thread 下一阶段生成与提交

RC 阶段优先修复生命周期和契约问题，不新增产品功能。

## 2. 页面层级

### 全部计划

- 是项目组合总览，不是单个项目页。
- 每个项目可点击进入对应项目。
- 不展示项目级 `Current Phase / Roadmap / Next Action`。
- 无项目上下文时不显示会隐式创建新项目的“添加任务”。

### 项目

- 展示当前项目的规划上下文和任务。
- 项目切换必须加载目标 thread snapshot。
- 下一阶段在项目页原地生成和预览，不跳到独立 AI 页面。

### 我的一天

- 是跨项目虚拟执行视图。
- 任务操作同步回原项目。
- 不替代“全部计划”或项目导航。

## 3. 状态模型

必须分离：

```ts
committedTaskTree
previewTaskTree
activeRun
```

规则：

- 项目板只从 `committedTaskTree` 读取已提交计划。
- 草案区只从 `previewTaskTree` 读取当前预览。
- SSE 只从 `activeRun` 获取身份。
- 不得使用 `previewMode || 'initial'` 推导 run。
- board idle 时不得建立历史 SSE 连接。

`activeRun` 必须包含：

```ts
{
  threadId: string;
  runType: 'initial' | 'next_phase';
  requestId: string;
}
```

## 4. 生成状态和按钮

| 状态 | 必须展示 | 禁止 |
| --- | --- | --- |
| `THINKING` | 轻量进度、取消 | 展示旧草案 |
| `PENDING` | 确认、微调、取消 | 本地假确认 |
| `SYNCING` | 返回当前计划 | 取消后端提交 |
| `ERROR` | 重试、返回计划、播种新想法 | 堆叠旧 reasoning |

`SYNCING` 的“返回当前计划”只收起界面，不清除 `activeRun` 或
`phaseRequestId`。后台提交完成后仍要更新当前项目。

## 5. SSE 防线

- 只接受与当前 `threadId + runType + requestId` 完全匹配的事件。
- handler 执行前检查 EventSource 仍是当前实例。
- 去重不能因为 request 清空而让旧 initial 终态重新生效。
- Phase 2 完成后清空 active run，不能自动回落订阅 initial。
- 退出、登出、返回全部计划和开始新意图时明确清理不再需要的 run。
- 迟到事件不得改变已退出页面。

## 6. 快照与刷新恢复

- 应用以持久化 `view=board` 启动时主动加载任务。
- 项目页恢复时同时加载 snapshot 和 planned tasks。
- `alignState()` 使用请求 gate；旧响应不能覆盖新 phase。
- 恢复 initial / next-phase running 时保留正确 active request。
- 恢复 awaiting confirmation 时使用 `interrupt_payload.task_tree`。
- next phase confirmed 后通过 commit receipt 获取权威 task tree / tasks。
- 退出登录时清理项目 id、task trees、board tasks、active run 和相关 storage。

## 7. 下一阶段

项目页流程：

1. 点击“解锁下一阶段”。
2. 当前阶段区域进入轻量 `THINKING`。
3. 收到当前 request 的 `plan_ready` 后显示 `previewTaskTree`。
4. 取消时恢复 committed snapshot。
5. 确认后进入 `SYNCING`，仅提供“返回当前计划”。
6. 收到当前 request 的完成事件后查询 commit receipt。
7. receipt 为 `confirmed` 且新 phase 可用后更新 board，再清 preview。

任何旧 initial `done`、历史 next-phase 事件或乱序 snapshot 都不能让 Phase 2
回退到 Phase 1。

## 8. 错误和重试

- 10 秒无事件可以进入 stalled 提示，但不要过早展示重试。
- 系统确认异常或超时后再给重试入口。
- retry 前清空上一 run 的 reasoning、node statuses、task preview 和 error。
- `agent_error` 面板提供：重试本次生成、返回当前计划、播种新想法。
- 401 展示中文鉴权恢复提示并拉起登录。
- 409 展示具体冲突原因，不直接清理本地 preview。

## 9. 任务交互

- `done_criteria` 常驻展示。
- `start_hint` / `fallback_action` 折叠在执行提示中。
- 点击 details/summary 不得冒泡为完成任务。
- 完成采用任务级乐观更新和任务级 rollback。
- 同一任务在 My Day 与项目中状态一致。
- 项目内手动任务必须发送当前 `thread_id`。

## 10. 类型与契约

- 类型以 `frontend/src/types/api.ts` 和 OpenAPI 为准。
- 不为绕过契约新增全文件 `any`。
- `interrupt_payload`、phase envelope、SSE payload 和 commit receipt 必须有明确类型。
- API 不支持的字段不要加入更新请求类型。

## 11. 验收要求

至少运行：

```bash
cd frontend
npm run test:hooks
npm run build
npm run lint
```

并运行全部 `frontend/tests/*.test.mjs`。

涉及生命周期时必须有 Hook 级用例覆盖：

- initial running
- 刷新后确认复用 request id
- next phase 完成后不重连 initial
- 历史 done 被拒绝
- 退出路径清理 active run
- THINKING 取消
- SYNCING 仅收起，不取消
- 乱序 snapshot 不覆盖新 phase

## 12. 交付纪律

- 遵循现有 React、Zustand、Tailwind 和组件结构。
- 不用 timeout/ref 驱动需要 React 重渲染的可见状态。
- 不通过整表 rollback 覆盖其他并发成功操作。
- 不保留调试脚本、console spam、临时密钥或无关格式化改动。
- 完成后报告变更范围、自动验证结果和需手动验收的路径。
