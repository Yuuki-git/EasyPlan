# EasyPlan 产品需求规格说明书

版本：`v1.2.6-rc.1`
更新时间：2026-07-05

## 1. 产品定义

EasyPlan 是一个意图驱动的 AI 规划系统。系统先理解用户目标的类型、时间跨度、心理阻力和执行场景，再选择合适的规划模型，把模糊意图转化为可执行、可确认、可持续推进的计划。

核心链路：

```text
Intent -> Profile -> Strategy -> Plan -> Validate -> Confirm -> Execute -> Progress
```

核心原则：

- **意图驱动**：不同类型目标不能套用同一种拆解方式。
- **行为设计**：降低启动阻力，同时避免没有价值的“打开软件”式任务。
- **人在回路**：AI 提供规划，用户决定是否确认。
- **视野控制**：长期目标只展开当前阶段，未来保持为地图。
- **状态可信**：预览、确认、取消、刷新和重连不能破坏已提交计划。

## 2. 目标用户与问题

目标用户：

- 面对长期目标容易拖延的人。
- 需要在短时间内完成明确交付物的知识工作者。
- 需要整理情境清单或路线事项的用户。
- 面对转行、搬家、读研等选择，需要先判断再行动的人。

核心问题：

- 大目标造成启动焦虑。
- 普通 Todo 工具需要用户自己完成结构化。
- 计划任务过于抽象，不知道如何开始或如何判断完成。
- 长期计划一次铺满，后续很快失效。
- AI 生成过程容易卡住、重复或在刷新后恢复错误状态。

## 3. 意图与规划模型

### 3.1 `long_term_growth`

- 使用 3-5 个高层 Roadmap 阶段。
- 只展开 Current Phase。
- 当前阶段应包含低阻力但有价值的启动动作。
- 当前阶段完成后，由用户主动解锁下一阶段。

### 3.2 `short_term_delivery`

- 聚焦截止时间、交付物和时间盒。
- 禁止低价值破冰动作。
- 默认不展示长期 Roadmap。
- 后续版本将进一步升级为 deliverables/workstreams 模型。

### 3.3 `context_checklist`

- 按地点、工具、时间场景或顺路关系聚合。
- 多事项必须形成合理 Group。
- 默认不展示 Roadmap。

### 3.4 `exploration_decision`

- 首屏先给 1-2 句当前判断。
- 判断必须是临时判断，不伪装成最终结论。
- 随后提供判断依据和低成本验证路线。
- 路线结构通常为澄清、收集、验证、决策。

## 4. 任务质量

关键 Action 支持：

- `done_criteria`：完成标准。
- `start_hint`：启动提示。
- `fallback_action`：降级动作。
- `estimated_minutes`：进入正式看板后的预计时长。

Action Quality Validator 应拦截：

- “研究一下”“学习相关内容”等抽象任务。
- 无法判断完成的关键任务。
- 与 Intent Profile 策略冲突的拆解。
- 超出当前 Scope Horizon 的长期展开。

生成态优先使用粗粒度投入描述；正式任务看板展示 rounded 分钟。

## 5. 三层规划

### 5.1 Roadmap

- 只在 `long_term_growth` 和 `exploration_decision` 默认显示。
- 表达高层方向，不展开未来阶段任务。
- completed phase 不允许被后续模型修改。

### 5.2 Current Phase

- 显示当前阶段标题、目标和完成进度。
- 进度只统计当前阶段的 AI Action。
- 手动任务不阻塞阶段解锁。

### 5.3 Next Action

- 由后端根据依赖、状态和当前 phase 确定。
- 任务完成、删除或状态变化后重新计算。
- 不依赖模型在每次更新时重新生成。

## 6. 下一阶段生命周期

### 6.1 解锁

只有当前阶段 AI Action 全部完成时，用户才能解锁下一阶段。

下一阶段必须：

- 复用当前 thread。
- 使用新的唯一 `request_id`。
- 保持原 `intent_type` 和 `time_horizon`。
- 不修改 completed phase。

### 6.2 预览

- 生成过程留在当前项目页面。
- Current Phase 区域原位显示轻量加载。
- 新阶段生成后进入 preview。
- Preview 存在 `interrupt_payload`，确认前不得覆盖 committed `task_tree`。

### 6.3 确认

- 用户确认后，阶段任务追加到同一 thread。
- task tree、tasks 和 terminal envelope 必须事务一致。
- 前端通过 commit receipt 校验 request、current phase 和实际任务。

### 6.4 取消与确认边界

```text
THINKING -> 允许取消生成
PENDING  -> 允许取消预览
SYNCING  -> 已确认，不允许取消
```

- 取消必须携带匹配的 `request_id`。
- 取消写入 terminal tombstone 并释放 lease。
- 迟到模型结果不得恢复已取消 run。
- SYNCING 时用户可以“返回当前计划”，但后台提交继续。
- 返回当前计划不能清除 active run 或断开当前 SSE。

## 7. 生成与恢复体验

- 每次 initial、refine 和 next phase 是独立 run。
- 新 run 不展示上一轮 reasoning。
- SSE 事件携带真实 `thread_id + run_type + request_id`。
- 前端只接受当前 active run 的事件。
- 相同事件不得重复渲染。
- 旧 EventSource handler 不得更新新 run。
- 旧快照响应不得覆盖更新阶段。
- 刷新可恢复 running、pending 和 committed 状态。
- stalled 或 error 必须提供退出或恢复动作。
- stalled 时只重连当前 request，不创建新的规划 run。
- Retry 只用于异常恢复；正常换一种拆法使用“重新生成”。
- 新 run 只展示自身 reasoning、节点状态和预览，不拼接历史 run。

## 8. 信息架构

### 8.1 全部计划

- 跨项目 portfolio overview。
- 每个项目卡片展示计划标题、当前阶段、阶段进度和 Next Action 或最近任务。
- 点击卡片进入对应项目。
- 不拥有独立 Roadmap 或 Current Phase。

### 8.2 项目

- thread 级长期执行容器。
- 承载 Roadmap、Current Phase、Next Action 和项目任务。
- 项目内新增任务必须保留在当前 thread。

### 8.3 我的一天

- 虚拟任务视图。
- 使用 `is_in_my_day`，不迁移任务所属项目。
- 与项目和全部计划共享相同 `task_id` 与完成状态。

## 9. 原生任务能力

- 创建、编辑、完成和删除任务。
- 加入或移出“我的一天”。
- 项目内创建手动 root task。
- 展示完成标准、启动提示和降级动作。
- 任务完成后持久化，刷新状态保持。
- 单任务请求失败只回滚该任务，不覆盖其他任务状态。

## 10. 非功能要求

### 安全

- 所有 thread、task 和 checkpoint 操作绑定 `user_id`。
- 越权与不存在资源统一避免泄露。
- API 与 SSE 错误不得暴露 traceback、SQL、token 或模型裸响应。

### 一致性

- `request_id` 是 run、确认和取消的幂等边界。
- committed 与 preview 状态分离。
- next-phase task ID 在同一 thread 中必须唯一。
- 确认成功必须有可验证的 commit receipt。

### 可恢复性

- SSE 支持 cursor 恢复。
- event buffer 不允许历史 terminal 截断新 run。
- 快照请求只允许最新响应写入。
- 用户退出或登出必须清理对应 active run 上下文。

### 性能

- 面向 4C4G 部署控制数据库连接、LLM 并发、事件缓存和 checkpoint 体积。
- reasoning 只用于短暂进度反馈，不长期写入业务表。

## 11. 当前验收状态

### v1.2.7-A 长期执行循环

- 仅新建 `long_term_growth` 计划可使用 `planning_context.schema_version=2`；schema v1 和非长期计划行为不变。
- 当前阶段可定义 0-2 个循环练习与 1-2 个结果检查点，不生成未来日期 occurrence。
- 循环练习按用户时区统计：每个本地自然日最多完成一次，每周配额不跨周结转。
- “安排到今天”只创建当前 occurrence，默认加入“我的一天”；用户仍可手动移出或重新加入。
- 阶段 readiness 由 one-off 完成度、循环过程达成率和 outcome evidence 共同决定。
- 阶段复盘由用户最终决定 `proceed`、`extend`、`adjust` 或 `override`；override 必须填写原因。
- 只有 finalized `proceed` 或 `override` 才能解锁下一阶段。
- 频率调整通过不可变 revision 从下一本地周生效，历史周配额、日志和复盘记录不得重写。

2026-07-06 v1.2.7-A RC 本地验证：

- Backend：`324 passed`
- Frontend Node 状态测试：全部通过
- Mounted `useSSE` Hook：`11 passed`
- Portfolio：`12 passed`
- 长期执行：`15 passed`
- Build、lint、`git diff --check`：通过

DeepSeek Validator-aware 42-case 实测为 `42/42`。Pass Rate、Intent
Classification、Strategy Compliance、JSON Parse、Horizon Accuracy、Action
Quality、Done Criteria Coverage 和 Long-Term Loop Contract 均为 `100%`。
case 40 连续三次单独验证也全部通过。

## 12. 已完成与后续版本

### v1.2.8 - Planning Model Differentiation (Completed)

- `TaskTree` 已新增 optional `strategy_context`，与负责阶段视野的 `planning_context` 分离。
- 短期目标使用 delivery context，结构化表达交付物、截止约束、时间预算与缓冲、范围取舍、workstreams 和关键路径；不增加 Roadmap。
- 探索决策使用 decision context，结构化表达当前判断、置信度、依据、信息缺口、低成本实验和决策门槛。
- 历史计划继续兼容，现有 summary 解析仅作为 legacy fallback。
- v1.2.7-A 长期执行循环与 `context_checklist` 保持不变。
- 设计规格：`docs/superpowers/specs/2026-07-10-v1.2.8-planning-model-differentiation-design.md`。
- 执行计划：`docs/superpowers/plans/2026-07-10-v1.2.8-planning-model-differentiation.md`。

### v1.3.0 - Task Copilot (Completed / Released)

- 首版只包含三个任务级入口：`帮我开始`、`我卡住了`、`拆得更细`。
- 每次辅助是单轮、结构化 proposal，不建立开放式聊天历史。
- `帮我开始`确认后只更新 `start_hint`；`我卡住了`确认后只更新所选 `fallback_action`。
- `拆得更细`确认后创建 2–5 个 assist children，父任务使用确定性 roll-up，不重复计入阶段进度。
- 生成和 Apply 留在当前项目或“我的一天”，不跳回全局生成页面。
- 用户确认前不修改任务；Apply 需要所有权、幂等、过期和 stale-task 校验。
- 运行中取消成功后关闭 Action Coach 并清理本地 run；取消失败时保留面板并显示可恢复错误，不留下空面板。
- 父任务是 My Day 的承诺锚点。Assist children 仅随已加入 My Day 的父任务嵌套显示，不能独立加入 My Day，也不能降级为顶层任务。
- My Day 不修改 child 自身的 `is_in_my_day`；父任务移出后，其隐式 Assist children 同步消失。
- 设计规格：`docs/superpowers/specs/2026-07-12-v1.3.0-task-copilot-action-coach-design.md`。
- 执行计划：`docs/superpowers/plans/2026-07-12-v1.3.0-task-copilot-action-coach.md`。

### v1.3.1 - Execution Engine & Refine Diff (Completed / Released)

- 面向计划确认后的现实变化，而不是确认前的 plan refine 或单任务 Task Copilot。
- 首版包含 `time_budget`、`progress_recovery`、`context_change` 三种模式。
- 输出结构化 `PlanDiff`，只允许更新任务、新增少量任务、同级重排和调整当前项目的 My Day 投影。
- 用户预览 before/after 后整包确认；Apply 之前不修改任务，不支持部分 Apply。
- 不允许 AI 删除任务；“稍后处理”通过降低排序和移出当前项目的 My Day 表达，项目任务仍保留。
- 已完成任务、历史阶段、Roadmap、阶段复盘、practice loop、outcome checkpoint 和 Assist children 不可修改。
- 长期计划只调整当前阶段；schema v1 只调整 committed tree 中的 active AI actions。
- task rows 与 `AgentThread.task_tree` 必须在同一事务中保持一致，Apply 具备 fingerprint stale 校验和幂等 receipt。
- 独立 DeepSeek Execution Refine Eval 为 24 cases，发布时同时保持 Planning 54/54 与 Task Assist 18/18。
- 设计规格：`docs/superpowers/specs/2026-07-14-v1.3.1-execution-engine-refine-diff-design.md`。
- 执行计划：`docs/superpowers/plans/2026-07-14-v1.3.1-execution-engine-refine-diff.md`。
- 2026-07-15 发布验收：Backend `523 passed`；Execution Refine `24/24`、Planning `54/54`、Task Assist `18/18`，全部发布指标 `100%` 且 strict exit 为 `0`。
