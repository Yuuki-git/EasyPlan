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

2026-07-05 RC 验证：

- Backend：`265 passed`
- Frontend Node 状态测试：通过
- Mounted `useSSE` Hook：`11 passed`
- Portfolio 组件测试：`11 passed`
- Build、lint、`git diff --check`：通过

DeepSeek 32-case：`32/32`。Pass Rate、Intent Classification、Strategy
Compliance、JSON Parse、Horizon Accuracy、Action Quality 和 Done Criteria
Coverage 均为 `100%`；Average Actionability Score 为 `99.85%`，Abstract Task
Violation Rate 为 `0.75%`。

## 12. 后续版本

### v1.2.7 - Planning Model Differentiation

- 长期目标 Roadmap 进一步高层化。
- 短期目标使用 deliverables/workstreams。
- 探索决策使用独立 decision route。

### v1.3.0 - Task Copilot

- 解释这一步、帮我开始、我卡住了。
- 拆得更细、降低难度、给模板。
