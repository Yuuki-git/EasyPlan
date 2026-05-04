# 后端开发任务指令集 (For Codex)

## 1. 任务背景
你需要构建 EasyPlan 的后端服务。这是一个基于意图驱动的智能任务管理系统，核心能力是通过 AI 将模糊目标拆解为可执行微任务。

## 2. 核心技术栈要求
- **语言：** Python 3.10+
- **网关：** FastAPI (异步优先)
- **工作流：** LangGraph (必须使用 Checkpointer 机制实现 HITL)
- **校验：** Pydantic v2 (用于约束模型输出 JSON Schema)
- **数据库：** PostgreSQL (推荐配合 SQLAlchemy 或 Tortoise ORM)
- **工具协议：** MCP (用于集成 Todoist 等第三方服务)

## 3. 核心节点逻辑 (LangGraph Nodes)
1. **`router_node`**: 解析用户输入，判断是新建计划还是查询状态。
2. **`planner_node`**: 
   - 使用 LLM 进行循环递归拆解。
   - 每一个叶子节点必须满足：预计耗时 < 5分钟，且包含具体的动词。
   - 输出必须符合定义的 `TaskTree` Pydantic 模型。
3. **`executor_node`**: 
   - 接收人类确认信号后触发。
   - 循环调用 MCP 工具将任务写入外部系统。

## 4. 重点实现：HITL (人工干预)
- 在 `planner_node` 完成后，必须调用 LangGraph 的中断机制。
- 状态需要持久化到 PostgreSQL，以便用户刷新页面后能找回未确认的计划。
- 提供 `/confirm` 接口，接收 `thread_id` 以恢复图的执行。

## 5. 交付要求
- 完整的 API 文档 (Swagger/OpenAPI)。
- 高质量的单元测试，尤其是针对任务拆解逻辑的边界测试。
- 模块化的项目结构 (app/api, app/agents, app/models, app/services)。
