# EasyPlan 🪐 - 意图驱动的 AI 规划系统

**EasyPlan 是一个意图驱动的 AI 规划系统。它先识别用户目标类型，再选择合适的拆解策略，把模糊意图转化为可执行、可调整、可持续推进的任务地图。** 它不只是一个待办清单，而是一个懂得行为心理学的智能 Agent 伙伴。

```text
[核心管线 Architecture Flow]
Intent Capture → Intent Profile → Strategy Router → Planner → Validator → Task Board → Phase Progression
```

[English](./README_EN.md) | [快速开始](#-快速开始) | [技术架构](#-技术架构)

---

## 🌟 核心理念：为什么选择 EasyPlan？

我们的设计根植于 **BJ Fogg 行为模型（行为 = 动机 × 能力 × 提示）**。

- **消除启动焦虑**：AI 根据目标类型和心理阻力选择合适的任务粒度，让行动更容易开始。
- **保留人类意志**：坚持“人在回路（HITL）”设计。AI 负责规划，用户保留确认、微调和取消的权利。
- **控制规划视野**：长期目标只展开当前阶段，未来阶段保持为地图，避免一次性生成很快失效的庞大计划。
- **原生执行闭环**：计划确认后直接进入 EasyPlan 的任务系统，并通过“全部计划”“项目”和“我的一天”持续执行。

## ✨ 功能特性

- **Spotlight 极简捕获**：以动态输入框为中心，支持模糊、口语化的目标输入。
- **意图画像与动态路由**：识别长期成长、短期交付、情境清单和探索决策，并采用不同规划策略。
- **任务质量护栏**：Runtime Validator 检查任务是否具体、可开始、可完成，并支持 `done_criteria`、`start_hint` 和 `fallback_action`。
- **三层规划**：通过 `Roadmap → Current Phase → Next Action` 区分远期方向、近期计划和眼前动作。
- **阶段推进**：当前阶段完成后，可在同一项目中生成、预览并追加下一阶段。
- **长期执行循环**：长期成长计划支持每周练习配额、结果证据和阶段复盘；用户确认 `proceed` 或带理由的 `override` 后再进入下一阶段。
- **探索决策回答层**：先给出“当前判断 → 判断依据 → 下一步探索”，再将不确定问题拆成低成本验证动作。
- **对话式微调**：用户可以用自然语言调整计划，无需手工重建任务树。
- **原生任务视图**：“全部计划”以项目卡片汇总阶段、进度和下一步，“项目”承载具体计划，“我的一天”聚合当天行动。
- **稳定生成体验**：SSE 按 request 隔离，支持刷新恢复、当前 run 重连、重复事件过滤、生成取消和错误重试。
- **企业级安全基座**：多租户数据隔离与基于 JWT 的严格鉴权。

## 📊 评测基准 (Planning Eval)

DeepSeek 是当前主验收 Provider。2026-07-06 在确定性 Validator 接入前记录的 42-case 基线为：

- **Cases Passed**: 40/42
- **Pass Rate**: 95.24%
- **Intent Classification Accuracy**: 100.00%
- **JSON Parse Success Rate**: 100.00%
- **Strategy Compliance Rate**: 95.24%
- **Horizon Accuracy**: 100.00%
- **Action Quality Pass Rate**: 100.00%
- **Done Criteria Coverage**: 100.00%
- **Long-Term Loop Contract Pass Rate**: 94.44%

case 34 评分误判与 case 40 运行时兜底已修复；Validator-aware 42-case 仍需在允许外部调用的环境中复跑后才能关闭正式发布门槛。

## 🛠️ 技术架构

### 后端 (Backend) - Python 3.11+

- **框架**：FastAPI
- **智能体**：LangGraph + DeepSeek
- **校验**：Pydantic V2
- **存储**：PostgreSQL + SQLAlchemy 2.x async
- **实时通信**：Request-scoped SSE

### 前端 (Frontend) - React + TypeScript

- **构建**：Vite
- **状态管理**：Zustand
- **样式**：Tailwind CSS
- **动效**：Framer Motion
- **测试**：Vitest + React Testing Library

---

## 📂 项目结构

```text
EasyPlan/
├── app/                # 后端核心逻辑
│   ├── agents/         # LangGraph、Planner 与 Validator
│   ├── api/            # REST、SSE 与认证接口
│   ├── models/         # 数据库 ORM 模型
│   └── services/       # Runtime、Repository 与 LLM 服务
├── frontend/           # React 前端
│   ├── src/components/ # 规划、项目与任务组件
│   ├── src/hooks/      # SSE 生命周期
│   └── src/store/      # Zustand 状态管理
├── docs/               # 产品、架构与 API 文档
└── tests/              # 后端、Agent、契约与 Eval 测试
```

## ⚡ 快速开始

### 1. 克隆项目

```bash
git clone https://github.com/Yuuki-git/EasyPlan.git
cd EasyPlan
```

### 2. 配置环境变量

```bash
cp .env.example .env
```

至少配置：

```text
DATABASE_URL
EASYPLAN_JWT_SECRET
JWT_SECRET_KEY
EASYPLAN_LLM_PROVIDER=deepseek
DEEPSEEK_API_KEY
```

### 3. 数据库初始化

后端启动时会自动检测并初始化 PostgreSQL 表结构，无需手动运行 SQL 脚本。

### 4. 本地启动

```bash
# 后端 (Terminal 1)
python -m pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 8000

# 前端 (Terminal 2)
cd frontend
npm install
npm run dev
```

### 5. 使用示例

1. **输入意图**：输入 `我想转行产品经理，但不知道怎么开始`。
2. **AI 规划**：系统识别为探索决策，先给出当前判断，再生成验证路线和任务。
3. **自然语言微调**：输入 `我每周只能投入三个小时`，让 AI 调整计划。
4. **确认保存**：计划进入当前项目和原生任务看板。
5. **阶段推进**：完成当前阶段后，在同一项目中解锁下一阶段。

---

## 🚀 生产环境部署

建议使用 Docker Compose 部署：

```bash
docker-compose up -d
docker-compose logs -f backend
```

---

## 📅 路线图 (Roadmap)

### v1.2.7-A - Long-Term Execution Loop

- 长期成长计划通过每周练习循环持续执行，不预生成未来每日任务。
- 阶段是否可推进同时参考过程完成度和结果证据，并由用户完成阶段复盘。
- 旧计划和非长期计划继续使用原有 schema v1 行为。

### 后续方向

- v1.2.7-B/C：短期交付与探索决策采用更具差异化的规划模型。
- 围绕单个任务提供“帮我开始”“我卡住了”“拆得更细”等 Action Coach 能力。
- 根据用户偏好的任务粒度、工作时长和常见阻力进行个性化规划。

---

## 📄 开源协议

本项目基于 **MIT 协议** 开源。
