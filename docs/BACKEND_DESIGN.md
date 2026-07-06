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
- 每个事件携带 `thread_id`、`run_type`、`request_id` 和 `state_version`。
- 历史 run 的 `done` 不能截断或完成当前 run。
- `Last-Event-ID` 只在同一 run 内用于增量重放。
- 无法继续增量重放时发送 `snapshot_required`。

业务错误使用 `agent_error`，不使用浏览器保留的 `error` 事件名。

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
SSE 事件使用进程内递增版本。持久化 snapshot 版本尚未落地，因此前端必须使用
请求 gate 和 active run identity 拒绝过期异步响应。服务端 lease、request
idempotency 与前端 stale-response gate 共同防止：

- 重复生成
- 重复确认
- 旧快照覆盖新 phase
- 历史 SSE 事件回退 UI

## 10. 原生任务视图

- `planned` 是全部项目任务的来源。
- `my_day` 是基于 `is_in_my_day` 的虚拟视图。
- 将任务加入 My Day 不改变其项目和树结构。
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

2026-07-06 v1.2.7-A 发布验证的本地 pytest 为 `323 passed`。DeepSeek 42-case
实测为 `40/42`：Pass Rate 与 Strategy Compliance 为 `95.24%`；Intent、JSON、
Horizon、Action Quality 和 Done Criteria Coverage 均为 `100%`；Long-Term
Loop Contract Pass Rate 为 `94.44%`。此基线之后，case 34 评分误判已修复，
case 40 已加入确定性 Validator/replan，Eval 也复用运行时 Validator。受当前
环境外发限制，validator-aware `42/42` 仍需在允许外部调用的环境中复跑。

## 14. 非目标与后续

- 不恢复 Todoist、Microsoft To Do、MCP 或 OAuth 外部同步主线。
- v1.2.7-A 只完成长期执行循环；短期交付与探索规划模型留给 v1.2.7-B/C。
- 更强的个性化规划继续放入后续版本。
