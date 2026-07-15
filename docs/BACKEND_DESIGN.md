# EasyPlan 后端设计

版本：`v1.2.6-rc.1`

## 1. 系统职责

EasyPlan 后端把用户意图转换为可执行、可确认、可持续推进的原生任务计划：

```text
Intent
-> Intent Profile
-> Strategy Routing
-> Planner
-> Validator
-> Human Review
-> Persist
-> Execute
-> Next Phase
```

它不是通用 Todo API。核心能力是根据意图类型、心理阻力、时间跨度和执行场景
选择规划策略，并通过阶段视野避免一次性展开过长计划。

## 2. 技术栈

| 层 | 技术 |
| --- | --- |
| API | FastAPI |
| Agent 编排 | LangGraph |
| 数据契约 | Pydantic v2 |
| 数据库 | PostgreSQL + SQLAlchemy 2.x async |
| 状态推送 | Server-Sent Events |
| 前端 | React + TypeScript + Zustand |
| 主验收模型 | DeepSeek |

模型输出始终经过结构化解析、修复重试、Pydantic 校验和业务 validator，不能将
裸模型输出直接写入任务表。

## 3. 意图与策略

支持四类 intent：

| intent_type | 规划策略 |
| --- | --- |
| `long_term_growth` | 路线图、当前阶段、低阻力起步 |
| `short_term_delivery` | 时间盒和交付导向 |
| `context_checklist` | 按地点、工具、顺路关系聚合 |
| `exploration_decision` | 当前判断、信息收集、小实验和决策节点 |

`IntentProfile` 是后续策略和时间视野的来源。`planning_context.time_horizon` 必须
与 profile 一致，不允许 planner 在后续节点自行漂移。

v1.2.8 将阶段导航与意图业务模型拆开：`planning_context` 只负责 Roadmap、当前阶段
和 Next Action；optional `strategy_context` 使用 `strategy_type` discriminator，分别
承载短期交付的 delivery contract 与探索决策的 decision contract。发布矩阵为：

| intent_type | planning_context | strategy_context |
| --- | --- | --- |
| `long_term_growth` | schema v2 | `null` |
| `short_term_delivery` | `null` | `delivery` |
| `context_checklist` | `null` | `null` |
| `exploration_decision` | schema v1 | `decision` |

纯服务 `app/services/strategy_context.py` 负责 intent 映射、Action 引用、交付时间算术、
预算缓冲、关键路径、决策实验和 gate 校验。它不依赖 LangGraph、数据库或 LLM；运行时
Validator 和 Eval 复用同一实现，并以稳定错误码提供局部 replan 反馈。

## 4. 任务契约

AI Action 除标题与时间外，还支持：

- `done_criteria`：完成到什么程度
- `start_hint`：如何开始
- `fallback_action`：做不动时的降级动作

运行时 Action Quality Validator 会检查：

- actionability score
- 抽象任务
- 无效的完成标准、开始提示或降级动作
- 较长任务缺失完成标准

不合格任务触发局部 replan，保持原 intent 和策略，不重写整棵树。

## 5. 三层规划

适用的 intent 使用：

```text
Roadmap
-> Current Phase
-> Next Action
```

- Roadmap 提供远期方向，不一次性生成全部执行任务。
- Current Phase 是当前可执行范围。
- Next Action 由后端根据任务状态确定性重算。
- 当前阶段完成度只统计当前 phase 的 AI action，手动任务不阻塞解锁。
- 历史 phase 在后续生成中不可被模型修改。

`short_term_delivery` 和 `context_checklist` 默认不展示 Roadmap。

## 6. Thread 与持久化

一个项目对应一个 agent thread。初始规划确认后写入该 thread；下一阶段也在同一
thread 中追加，不能创建新的项目。

持久化关键不变量：

- 所有 thread、task 和 checkpoint 操作绑定 `user_id`。
- 子任务继承父任务的 thread。
- 项目内手动 root task 显式使用当前 `thread_id`。
- next-phase task 的 `client_node_id` 必须在 thread 内全局唯一。
- next phase 的 task、task tree 和 confirmed envelope 在同一事务中提交。
- 冲突必须显式失败，不能通过 `ON CONFLICT DO NOTHING` 制造假成功。

Action Quality 字段继续存入现有 task metadata，保持旧任务兼容。

## 7. Request-scoped Agent Run

每次 initial、refine 或 next-phase 运行都由真实 request 身份标识：

```text
EventRunKey = thread_id + run_type + request_id
```

运行时规则：

- SSE buffer 和终态按 run 隔离。
- 每个事件使用统一 envelope，携带 `event_id`、`thread_id`、`run_type`、
  `request_id`、`event_type`、`seq`、`created_at` 和 `payload`。
- `event_id` 派生自 `thread_id:run_type:request_id:seq`，`seq` 在单个 run
  内单调递增，不同 request 不共享计数器。
- 历史 run 的 `done` 或 `plan_ready` 不能截断、完成或污染当前 run。
- `Last-Event-ID` 只在同一 run 内用于增量重放。
- 无法继续增量重放时发送 `snapshot_required`。
- 长耗时 run 每 5-8 秒发送 `still_running` heartbeat；`done`、`agent_error`
  或用户取消后停止。
- `sync_status` / `sync_complete` 是 stage-only 事件，payload 不包含成功/失败
  `status`；终态仍由 `done` 或 `agent_error` 表达。

业务错误使用 `agent_error`，不使用浏览器保留的 `error` 事件名。阶段事件
是产品级状态反馈，不得携带 chain-of-thought、原始 prompt、provider payload、
secret 或 traceback。

## 8. 下一阶段状态机

```text
idle
-> running
-> awaiting_confirmation
-> confirming
-> confirmed
```

异常或退出分支包括 `cancelled` 和 `failed`。

### 生成

`POST /api/threads/{thread_id}/phases/next` 取得 request lease，并在当前 thread
内生成 preview。

### 取消

`THINKING` / `PENDING` 可以调用：

```http
DELETE /api/threads/{thread_id}/phases/next/cancel?request_id=...
```

取消标记只为当前进程中的 active run 保留。`run_next_phase()` 在 `finally` 中
清理 active 和 cancelled key，避免运行时集合持续增长。

### 确认

确认请求必须与 pending envelope 的 `request_id` 一致。进入 `confirming` 后视为
不可撤销，前端只能收起生成面板。

### 提交回执

```http
GET /api/threads/{thread_id}/phases/next/commit?request_id=...
```

返回当前 request 的权威状态以及提交后的 task tree / tasks。前端应以该回执
判断新阶段是否真正可见，不能只凭任意 `done` 事件清理 preview。

## 9. Snapshot 与并发

`ThreadSnapshot` 提供 task tree、interrupt payload、phase generation envelope、
`state_version` 和 `last_event_id`。

当前 snapshot 中 `state_version=0`、`last_event_id=null` 仍是兼容占位值；
SSE 事件使用 run-scoped `seq` 和 envelope `event_id`。持久化 snapshot 版本
尚未落地，因此前端必须使用请求 gate 和 active run identity 拒绝过期异步响应。
服务端 lease、request
idempotency 与前端 stale-response gate 共同防止：

- 重复生成
- 重复确认
- 旧快照覆盖新 phase
- 历史 SSE 事件回退 UI

## 10. 原生任务视图

- `planned` 是全部项目任务的来源。
- `my_day` 是基于 `is_in_my_day` 的虚拟视图。
- 将任务加入 My Day 不改变其项目和树结构。
- `source=task_assist` child 禁止独立设置 `is_in_my_day=true`；非法 PATCH 在事务内
  回滚并返回 `TASK_ASSIST_CHILD_MY_DAY_FORBIDDEN`。My Day 只随显式父任务附带直接
  Assist children，父任务移出后这些隐式 children 不再返回。
- PATCH 根据 `user_id + task_id` 更新，同一任务在两个视图中保持一致。
- 删除 thread 会删除该用户在 thread 下的任务。

## 11. 模块边界

```text
app/
|- agents/       # profile、planner、validator、LangGraph state
|- api/          # routes、schemas、auth、SSE
|- db/           # async session
|- models/       # thread、task、user
`- services/     # runtime、repository、phase planning、LLM
```

- API 层负责身份、输入校验和 HTTP 语义。
- repository 负责事务、所有权和状态迁移。
- runtime 负责 run 生命周期、事件缓存与后台执行。
- agent nodes 负责模型调用、策略和任务树校验。

## 12. Schema v2 长期执行循环

v1.2.7-A 仅为新 `long_term_growth` 计划启用 schema v2。schema v1、旧任务和
其他 intent 继续沿用原状态机。

```text
TaskTree definitions
  -> practice_loops + immutable revisions
  -> schedule one occurrence to planned/My Day
  -> task completion + daily log in one transaction
  -> process/outcome readiness
  -> draft review
  -> finalized user decision
  -> next-phase gate
```

核心不变量：

- future occurrence 不预生成；每次只安排当前需要执行的一条任务；
- 同一 loop 每个本地日期最多一条完成日志；
- 周配额不足不结转，进度按每周生效的 revision 计算；
- 调整频率只创建下一本地周生效的新 revision，不改写历史；
- task completion 与 completion log 原子提交；
- schedule 默认加入 My Day，但 My Day 始终是用户控制的虚拟视图；
- 下一阶段必须有 finalized `proceed` 或 `override`；
- override reason 保留在 phase review history，供前端 Phase Records 展示。

持久化拆分为 loop definition、revision、completion log 和 phase review 四类表，
避免把执行历史塞回 TaskTree JSON。Readiness 使用纯计算服务，repository 负责
所有权、行锁、幂等和事务。

## 13. 验证基线

本地发布门槛：

```bash
python -m pytest tests -q
python tests/run_evals.py --provider deepseek
```

2026-07-06 v1.2.7-A 发布验证的本地 pytest 为 `324 passed`。DeepSeek
Validator-aware 42-case 实测为 `42/42`；Pass Rate、Intent、Strategy、JSON、
Horizon、Action Quality、Done Criteria Coverage 与 Long-Term Loop Contract
均为 `100%`。case 40 连续三次单独验证全部通过。

2026-07-11 v1.2.8 P1 Horizon 契约修正后的后端测试为 `378 passed`。54 条 Eval
已彻底移除语义混杂的 `expected_horizon`，改为两个严格字段：
`expected_profile_horizon` 只表示目标总体跨度，合法值为
`minutes/hours/days/weeks/months`；`scope_horizon_rule` 只表示本次计划的展开窗口，
合法值为 `long_term_phase_1_72h`、`short_term_delivery_window`、
`context_checklist_window`、`exploration_decision_window`。cases 1-8 的目标跨度为
`months`，Scope Rule 为 `long_term_phase_1_72h`。

DeepSeek 54-case 在新契约下重新实测为 `54/54`：Profile Horizon Accuracy、Scope
Horizon Compliance 与合并 Horizon Accuracy 均为 `100%`；JSON、Intent、Strategy、
Action Quality 和五项 Strategy Context 指标也均为 `100%`。合并 Horizon Accuracy
仅在 Profile Match 和 Scope Compliance 同时通过时计为通过；`--strict-exit` 还独立
要求这两项各自达到 `100%`，防止合并指标掩盖单侧回退。

## 14. v1.2.8 持久化边界

- `strategy_context` 随现有 `AgentThread.task_tree` JSONB 保存，无 migration 或新表；
- initial/refine preview、confirm、snapshot 与 SSE `plan_ready` 保留完整字段；
- Task 持久化只打散 `TaskNode`，不会把 strategy context 复制进每条 task metadata；
- `EASYPLAN_STRATEGY_CONTEXT_ENABLED=false` 保持旧 Prompt/Validator 行为，开启后只约束
  新 initial/refine 的 `short_term_delivery` 与 `exploration_decision`；
- next-phase、长期 schema v2、context checklist 与 SSE envelope 不变。

## 15. v1.3.0 Task Copilot / Action Coach

Task Assist 是独立于 plan graph 的 request-scoped run：

```text
POST assist -> running -> context_ready -> generating -> validating -> ready
                                                                      |
                                                                      v
                                                                   Apply
                                                                      |
                                                                      v
                                                                   applied
```

- `task_assist_runs` 以 `(user_id, task_id, request_id)` 唯一，并通过部分唯一索引保证同一
  `(user_id, task_id)` 最多存在一个 `status=running` 的 run；创建 run 前先锁定目标 task 行；
- running run 持久化 `lease_owner/lease_expires_at`，运行时 heartbeat 续租。过期 lease
  原子转为 `failed/TASK_ASSIST_INTERRUPTED`，释放 active slot 并允许新 request 重试；
- proposal 默认 24 小时过期；
- `start`、`unstick`、`decompose` 使用 `proposal_type` 判别联合和 DeepSeek JSON Output；
- 模型只接收目标任务、最多两层 ancestor、当前项目摘要、必要策略/阶段摘要和本次
  `user_context`，不接收其他任务、推理历史或认证信息；
- SSE 使用独立 `run_type=task_assist`、独立事件缓存、游标、heartbeat、取消标记和终态
  回收上限；进程内事件丢失时轮询 durable snapshot，恢复 `assist_ready/done` 或
  `agent_error`，不会在进程重启后无限返回 `still_running`；规划流仍只接受
  initial/next_phase/refine；
- Apply 锁定 run 与目标 task，校验 owner、状态、过期时间和 `target_task_updated_at`，
  重复 Apply 返回已保存 receipt；
- start 只更新 metadata `start_hint`，unstick 只更新所选 `fallback_action`；
- decompose 原子创建 2-5 个 `source=task_assist` 子任务与依赖，并在父任务 metadata
  标记 `assist_rollup=true`，不修改 `AgentThread.task_tree`；
- assist child 不计入 phase action 数。子任务全部完成时父任务自动完成，任一重开时
  父任务重开，删除后按剩余子任务重算；存在未完成子任务时拒绝直接完成父任务；
- My Day 查询通过父任务的虚拟映射连同直接 children 返回，不改变 child 自身的
  `is_in_my_day`，因此项目与 My Day 共享同一组 task ID 和 roll-up 状态；
- `EASYPLAN_TASK_ASSIST_ENABLED=false` 默认关闭，每用户同时最多两个 active run；
  `EASYPLAN_TASK_ASSIST_LEASE_SECONDS=30` 控制 durable run lease（最小 15 秒）。

2026-07-13 后端验收：`452 passed`；DeepSeek Task Assist Eval `18/18`，六项指标
全部 `100%`；Planning Eval `54/54`，全部 release-gate 指标保持 `100%`。

## 16. 非目标与后续

- 不恢复 Todoist、Microsoft To Do、MCP 或 OAuth 外部同步主线。
- v1.2.8 的 optional `strategy_context`、前端展示与完整 release gate 已完成。
- v1.3.0 的独立 task-assist run、结构化 proposal、确认式 Apply、父任务 roll-up、
  前端集成和发布门禁均已完成，并于 2026-07-14 发布。
- v1.3.1 已新增独立 post-commit `execution_refine` run。它只对当前可执行范围产生
  `update_task`、`add_task`、`reorder_siblings` 和 `set_my_day` Diff，不复用确认前
  plan-level `refine`，也不修改已完成历史、Roadmap、长期循环、阶段复盘或 Assist children。
- v1.3.1 Apply 以 scope fingerprint 防 stale，并在同一事务中双写 task rows 与
  committed TaskTree；durable snapshot、run-scoped SSE、取消、恢复与幂等 receipt 已完成。
- 2026-07-15 发布验收：Backend `523 passed`；Execution Refine `24/24`、Planning `54/54`、
  Task Assist `18/18`，全部发布指标 `100%` 且 strict exit 为 `0`。完整契约见：
  `docs/superpowers/specs/2026-07-14-v1.3.1-execution-engine-refine-diff-design.md`。
- 更强的个性化规划继续放入后续版本。
