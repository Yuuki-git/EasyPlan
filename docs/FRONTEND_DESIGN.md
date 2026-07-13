# EasyPlan 前端设计

版本：`v1.3.0`

## 1. 设计目标

前端承载两种不同工作：

1. 把模糊意图转化为可确认的 AI 规划。
2. 在原生任务看板中持续执行、调整和推进阶段。

因此 UI 不能把“生成中的草案”和“已经提交的计划”混为一种状态，也不能把
“全部计划”“具体项目”“我的一天”渲染成同一个层级。

## 2. 信息架构

### 全部计划

Portfolio 总览，只展示项目摘要、进度和进入项目的入口。这里不展示某一个项目
的 `Roadmap / Current Phase / Next Action`，也不提供无归属的项目内任务添加。

### 项目

单个 thread 的执行空间，展示：

- Roadmap：只在适合的 intent 类型中出现
- Current Phase：当前阶段及其任务
- Next Action：由后端根据当前状态确定
- 历史阶段
- 下一阶段生成与确认

### 我的一天

跨项目的执行视图，只聚合用户今天选择的任务。任务仍属于原项目，完成状态需要
在“我的一天”和项目中同步。

## 3. 主要组件

```text
App
|- Header
|- DynamicInput
|- ReasoningStream
|- TaskTreeVisualizer
|- ActionLayer
|- TaskBoard
   |- PortfolioOverview
   |- PlanningOverview
   |- BoardTaskNode
   `- InlineTaskInput
`- AuthModal
```

- `DynamicInput`：新意图和 refine 输入。
- `ReasoningStream`：显示简短生成进度，不展示内部推理链。
- `TaskTreeVisualizer`：确认前的规划草案。
- `ActionLayer`：确认、微调、取消、错误恢复。
- `PortfolioOverview`：全部计划总览。
- `PlanningOverview`：项目级三层规划及下一阶段生命周期。
- `TaskBoard`：任务列表、项目切换、My Day 和任务操作。

## 4. Zustand 状态边界

核心状态必须分开：

```ts
committedTaskTree: TaskTree | null;
previewTaskTree: TaskTree | null;
activeRun: {
  threadId: string;
  runType: 'initial' | 'next_phase';
  requestId: string;
} | null;
```

- `committedTaskTree` 只表示服务端已提交计划。
- `previewTaskTree` 只表示当前 request 等待确认的草案。
- `activeRun` 是 SSE 身份的唯一来源。
- `previewMode` 只控制展示模式，不能用来推断 run 身份。

项目切换、退出登录、返回全部计划和开始新意图时，要明确处理上述三种状态以及
对应 localStorage，避免上一用户或上一项目的内容泄漏到新上下文。

## 5. 生成状态机

| 状态 | 含义 | 允许操作 |
| --- | --- | --- |
| `INITIAL` | 没有活动生成 | 提交意图、浏览看板 |
| `THINKING` | 模型正在生成 | 取消本次生成 |
| `PENDING` | 草案等待决定 | 确认、微调、取消 |
| `SYNCING` | 确认已接受，正在提交 | 返回计划/全部计划/当前计划 |
| `ERROR` | 当前 run 失败 | 重试本次生成、返回计划、播种新想法 |

产品边界：

- `THINKING` 和 `PENDING` 的本地退出（放弃等待/放弃此计划）不暗示后端 run 已取消；对于 next_phase 可以发送取消请求。
- `SYNCING` 已进入不可撤销提交，不显示任何“取消”或“放弃”按钮。
- `SYNCING` 的“返回全部计划/返回当前计划”只改变视图，不清除 `activeRun`，后台同步和 SSE 依然保持，直到完成后通过 SSE 更新看板。
- `isRunStalled` (stalled 状态) 提供“重新连接”选项，通过增加 `sseReconnectNonce` 重新订阅当前 request，避免触发新的 intent/next_phase 生成请求。
- 每个新 run 开始前，均原子清空旧 reasoningLogs、previewTaskTree、nodeStatuses 和错误状态，不保留或堆积前序生成历史。

## 6. 下一阶段体验

下一阶段在当前项目页原地进行，不跳转到独立生成页：

1. 用户完成当前阶段后点击“解锁下一阶段”。
2. `PlanningOverview` 在 Current Phase 区域显示轻量 loading。
3. 生成完成后在原位置显示 `previewTaskTree`。
4. 用户确认后进入 `SYNCING`；可以返回当前计划等待。
5. commit receipt 确认同一 request 已提交后，以新 task tree 和 tasks 替换
   committed 内容。

生成过程中不能把旧阶段当作 preview，也不能让旧快照、旧 SSE `done` 或历史
initial run 覆盖新阶段。

## 7. SSE 与恢复

`useSSE` 只在存在 `activeRun` 时建立连接，并使用：

```text
threadId + runType + requestId
```

作为事件身份。以下事件必须被丢弃：

- 不属于当前 active run
- 来自已被替换的 EventSource
- 已经处理过的 event id
- 退出当前生成流程后迟到的事件

`alignState()` 和项目快照加载使用请求 gate，防止旧响应覆盖较新状态。刷新恢复
需要同时恢复：

- 当前 view
- selected project
- active run
- committed / preview task tree
- request id 和阶段生成状态

## 8. 错误与长等待

- SSE 长时间无事件时显示 stalled 提示，而不是立即展示重试。
- `agent_error` 进入明确错误面板。
- retry 开启新的 request 前先清理旧 reasoning、node status 和错误信息。
- 401 使用用户可理解的鉴权提示，并清理上一用户项目上下文。
- 409 展示服务端状态冲突，不进行本地假成功。
- 每个生成态都有离开路径，但离开 UI 与取消后端任务必须按状态区分。

## 9. 时间展示

- 生成界面使用粗粒度投入文案，避免给用户虚假的分钟级精确感。
- 正式任务看板继续显示经过取整的 `estimated_minutes`。
- 任务是否可执行主要由 `done_criteria`、`start_hint` 和
  `fallback_action` 保证，时间只是辅助信息。

## 10. 验证要求

```bash
cd frontend
npm run test:hooks
npm run build
npm run lint
```

同时运行 `frontend/tests/*.test.mjs`。SSE 和状态恢复变更应包含 Hook 级测试，不
能只依赖 store helper 单测。

## 11. v1.2.7-A 长期执行界面

schema v2 项目在当前阶段内组合三个独立区域：

1. `PracticeLoopPanel`：展示循环定义、本周进度、阶段累计和“安排到今天”。
2. `PhaseReviewPanel`：展示系统 readiness，收集结果证据、困难、下阶段容量和用户 decision。
3. `PhaseRecords`：在项目内展示已完成阶段的复盘历史、历史周目标和 override reason。

边界：

- future occurrence 不渲染，只有用户排程后才出现普通 Task；
- occurrence 默认加入 My Day，但用户可通过既有太阳按钮控制；
- loop-owned 标题和完成标准不可在任务卡中编辑；
- 已完成 occurrence 只读，删除/完成语义服从后端不可变日志约束；
- readiness 和 review availability 只读取 `longTermExecution` snapshot，不由组件推导；
- schema v1 继续使用既有 task-count unlock；
- Phase Records 仅出现在 selected project，不进入 Portfolio 或 My Day。

Store mutation 复用现有 snapshot gate。schedule、review update、review decision
完成后重新加载项目快照；occurrence completion 同时刷新执行进度和当前任务视图。

## 12. 验证要求

除原有 Hook、Portfolio、build 和 lint 外，运行：

```bash
cd frontend
npm run test:long-term
```

长期执行测试必须覆盖 selector、store、loop panel、review panel、phase records、
schema-v1 fallback、My Day 同 task ID 同步以及 stale snapshot 防线。

## 13. v1.3.0 Task Copilot

未完成普通 Action 上提供独立的 Action Coach panel，首版包含
“帮我开始”“我卡住了”“拆得更细”。该 panel 使用独立 task-assist run 和 SSE
状态，不复用全局计划生成的 `activeRun`、reasoning 或 preview tree。

Apply 前只展示 proposal；确认后才更新 `start_hint`、`fallback_action` 或创建 roll-up
子任务。项目和 My Day 共享同一个 task ID，Apply 后保持当前视图。

实现边界：

- task、request 和 mode 持久化到 localStorage，刷新后读取 durable snapshot 恢复同一 run；
- SSE 必须同时校验 thread、task、request、run type、event allowlist 和 event ID，并拒绝旧 EventSource 的迟到事件；
- running 取消成功后清理状态并关闭 panel；失败时保留 panel 和 run identity，展示可见错误；
- stale Apply 统一识别 `TASK_ASSIST_CONTEXT_STALE`，保留 mode 和用户补充信息供重新生成；
- decompose children 在项目和 My Day 中嵌套于父 Action；父任务有未完成 children 时 checkbox 禁用；
- Assist child 不显示 My Day 按钮，不能独立加入 My Day；缺少父节点的 child 不得渲染为顶层任务；
- 父任务是 My Day 的承诺锚点，My Day 使用平铺 API 数据重建层级，不修改 child 自身的 `is_in_my_day`。

完整设计与执行任务：

- `docs/superpowers/specs/2026-07-12-v1.3.0-task-copilot-action-coach-design.md`
- `docs/superpowers/plans/2026-07-12-v1.3.0-task-copilot-action-coach.md`
