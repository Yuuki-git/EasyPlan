# EasyPlan 后端开发指令

适用版本：`v1.3.0` 及后续维护补丁

## 1. 当前目标

当前主线是在稳定意图驱动规划与同 thread 阶段推进的基础上，提供单任务
Task Copilot。不要把 task assist 扩大成整份计划重写，也不要恢复外部任务平台同步。

主流程：

```text
Intent -> Profile -> Strategy -> Plan -> Validate -> Review -> Persist
                                               |
                                               `-> Next Phase in same thread
```

DeepSeek 是当前主验收 provider。

## 2. 必须保持的业务不变量

### 意图与规划

- 只使用四类 intent：`long_term_growth`、`short_term_delivery`、
  `context_checklist`、`exploration_decision`。
- `planning_context.time_horizon` 必须匹配 `IntentProfile`。
- long-term 默认只展开当前 phase，不一次性生成完整执行路线。
- short-term 不复制 long-term Roadmap。
- exploration 应先给当前判断，再给探索与决策路径。
- v1.2.8 开关开启时严格遵循 `planning_context / strategy_context` 矩阵：short-term
  使用 delivery、exploration 使用 decision、long-term/checklist 必须为 null。
- strategy context 的 Action 引用和时间算术必须走共享纯 Validator；不要在路由、
  Runtime 或 Eval 中复制另一套规则。

### Action Quality

- 保留 `done_criteria`、`start_hint`、`fallback_action`。
- validator 只修复低质量任务，保持 intent 和策略。
- 较长 action 缺完成标准时必须 replan。

### Thread 与任务

- 所有查询和写入绑定 `user_id`。
- 下一阶段必须追加到当前 thread。
- completed phase 不得被后续模型修改。
- next-phase `client_node_id` 必须与 thread 内既有节点不冲突。
- task、task tree 和 confirmed envelope 必须事务一致。
- 禁止用静默冲突忽略制造“确认成功但任务未写入”。
- task assist proposal 在 Apply 前不得修改 task 或 `AgentThread.task_tree`。
- task assist Apply 必须锁定 run/task，检查 owner、request、expiry 和 stale timestamp。
- assist children 的 `source=task_assist`，不计入 phase AI Action 数。
- roll-up 父任务状态只能由现存 assist children 确定性驱动。

## 3. Agent Run 与 SSE

每个 run 由以下组合唯一标识：

```text
thread_id + run_type + request_id
```

要求：

- initial、refine、next phase 都使用真实且可区分的 request id。
- 所有 SSE 业务事件使用统一 envelope，携带 `event_id`、run identity、
  `event_type`、run-scoped `seq`、`created_at` 与 `payload`。
- 事件缓存和终态按 run 隔离。
- 历史 `done` 或 `plan_ready` 不能结束或污染当前 run。
- `Last-Event-ID` 只在同一 run 内解释。
- 业务错误事件名固定为 `agent_error`。
- 无法增量恢复时发送 `snapshot_required`。
- 长耗时 run 使用 `still_running` heartbeat，且 terminal 或取消后必须停止。
- `sync_status` / `sync_complete` 是 stage-only 事件，payload 只表达
  `stage`、`label`、`state_version`；不要加入成功/失败 `status`。
- SSE stage 是用户可见状态文案，不得暴露 chain-of-thought、prompt、provider
  payload、secret 或 traceback。
- `task_assist` 使用独立 run key、缓存、订阅队列和终态；不得进入规划 stream。

## 4. 下一阶段状态边界

- `running` / `awaiting_confirmation` 可以取消。
- `confirming` 表示确认已接受，不允许取消。
- 取消必须同时校验 thread、user、run type 和 request id。
- `cancel_run()` 只为仍在进程内执行的 active run 保留取消标记。
- `run_next_phase()` 必须在 `finally` 清理 active/cancelled run key。
- commit receipt 是前端确认 next phase 真正提交的权威来源。

## 5. API 契约

当前核心接口：

```text
POST   /api/intents
GET    /api/threads/{thread_id}
GET    /api/threads/{thread_id}/events
POST   /api/threads/{thread_id}/confirm
POST   /api/threads/{thread_id}/phases/next
DELETE /api/threads/{thread_id}/phases/next/cancel
GET    /api/threads/{thread_id}/phases/next/commit
DELETE /api/threads/{thread_id}
GET    /api/tasks
POST   /api/tasks
PATCH  /api/tasks/{task_id}
DELETE /api/tasks/{task_id}
POST   /api/tasks/{task_id}/assist
GET    /api/tasks/{task_id}/assist/{request_id}
GET    /api/tasks/{task_id}/assist/{request_id}/events
DELETE /api/tasks/{task_id}/assist/{request_id}
POST   /api/tasks/{task_id}/assist/{request_id}/apply
```

修改接口或 schema 时同步更新：

- `app/api/schemas.py`
- 路由测试
- `docs/openapi.json`
- `docs/API_DOCUMENTATION.md`
- `docs/FRONTEND_API_GUIDE.md`

## 6. My Day 与项目

- `my_day` 是虚拟视图，不改变任务原 thread。
- 同一 `task_id` 在 planned / my_day 中共享状态。
- 项目内新增 root task 必须使用传入的 `thread_id`。
- 子任务继承父任务 thread。
- 仅在没有任何项目或父任务上下文时，后端才允许创建 manual thread。

## 7. Provider 与安全

- 发布验收使用 DeepSeek。
- API key 只从环境变量读取，不写入仓库、日志或测试快照。
- 仅保存必要 usage 元数据，不保存 raw prompt、完整推理或裸响应。
- 模型返回必须经过 JSON 清理、repair retry、Pydantic 和业务 validator。
- Task Assist Provider 固定为 DeepSeek，前端不得选择或覆盖模型。
- Task Assist 只发送目标 task、两层 ancestor 和必要项目摘要，不发送其他项目任务。

## 8. 长期执行循环不变量

- schema v2 只允许用于新 `long_term_growth`；不得改变 schema v1 或其他 intent。
- 不预生成未来 occurrence。
- schedule、complete、review 都必须按 `user_id + thread_id` 验证所有权。
- 同一 loop 每个本地日期最多计数一次，周配额不结转。
- task 完成与 completion log 必须位于同一事务，失败时共同回滚。
- 调整频率必须创建下一本地周生效的 revision，历史 revision 和日志不可修改。
- 下一阶段必须读取 finalized review，且 decision 只能是 `proceed` 或 `override`。
- `override` 必须保留理由；不得在 snapshot 或历史记录中隐藏。
- schedule 初始可加入 My Day，但不得阻止用户之后修改 `is_in_my_day`。

## 9. 测试要求

行为变更先补失败测试，再修改实现。至少运行：

```bash
python -m pytest tests -q
```

涉及 next-phase 时增加或维护以下覆盖：

- 同一 thread 的第二次 run
- 历史 done 不截断当前 run
- request id 不匹配返回冲突
- running / pending 可取消
- confirming 不可取消
- cancelled run key 最终回收
- client node id 跨 phase 冲突拒绝
- phase task/tree/envelope 事务一致
- 多租户越权拒绝

发布候选环境另运行：

```bash
python tests/run_evals.py --provider deepseek
```

Horizon Eval 必须使用双字段契约，不得恢复旧的混合字段：

- `expected_profile_horizon` 只表示目标总体跨度，且必须是
  `minutes/hours/days/weeks/months` 之一；
- `scope_horizon_rule` 只表示本轮任务树展开窗口，且必须与 intent 类型匹配；
- Profile Horizon Match 只比较 IntentProfile，Scope Horizon Compliance 只检查任务树；
- 合并 Horizon Accuracy 要求两者同时通过，严格发布门槛还要求两项各自为 `100%`；
- 禁止为旧 `expected_horizon` 保留兼容读取路径。

涉及长期执行循环时增加或维护：

- schema v1/v2 兼容和非长期拒绝；
- 本地周边界、无结转和 revision 历史目标；
- schedule 幂等、租户隔离和每日唯一日志；
- completion/log 原子性及删除语义；
- review evidence、decision、override reason 与 next-phase gate；
- snapshot/OpenAPI/前端类型契约；
- 原始 32 Eval 用例不回退，新增长期用例通过。

## 10. 交付纪律

- 只修改任务需要的模块。
- 不绕过 repository 的 ownership 和事务边界。
- 不通过放宽 schema、吞异常或移除校验让测试转绿。
- 不把 provider-specific 兼容补丁混入无关 RC 修复。
- 完成后报告测试命令、结果和仍存在的风险。
