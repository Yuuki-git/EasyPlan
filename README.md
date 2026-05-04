# EasyPlan 🪐 - **意图驱动**的智能任务管理工具 (v1.0.0)

> **Detailed plan is all you need. Efficiency is everything.**  
> **“不要让宏大的愿景成为负担，让技术替你承受规划的重量。”**

EasyPlan 是一款基于 **意图驱动（Intentional Productivity）** 理念打造的极简任务管理工具。它不只是一个待办清单，而是一个能够理解你的目标、并将其降维拆解为“两分钟即可启动”微行动的智能 Agent 伙伴。

[English Version](./README_EN.md) | [快速开始](#-快速开始) | [技术架构](#-技术架构)

---

## 🌟 核心理念：为什么选择 EasyPlan？

我们的设计根植于 **BJ Fogg 行为模型（行为 = 动机 × 能力 × 提示）**。
- **消除启动焦虑**：AI 将大目标拆解为极小任务，极大提升了您的“能力”维度，让行动变得自然而然。
- **保留人类意志**：坚持“人在回路（HITL）”设计。AI 负责繁琐的规划，您保留最终的点击确认权。
- **极致的确定性**：引入工业级“断点续传”同步技术，确保在不稳定的网络环境下，您的计划也能精准、不重复地同步到外部工具。

## ✨ 功能特性 (v1.0.0)

- **Spotlight 极简捕获**：全应用以一个动态输入框为中心，支持模糊口语意图录入。
- **Agent 智能拆解**：基于 **LangGraph** 实现多步推理，强制遵循“两分钟法则”与“动词驱动”原则。
- **对话式微调 (Refine)**：对计划不满意？直接用自然语言告诉 AI，它会即时重新规划。
- **生长式 UI (Fluid Motion)**：界面采用“平衡极简主义”，任务树随规划进度丝滑生长。
- **断点续传同步**：支持 **Todoist** 集成，具备强大的幂等性校验，同步失败可一键重试而不产生重复任务。
- **企业级安全基座**：多租户数据隔离、JWT 鉴权、OAuth2 授权闭环。

## 🛠️ 技术架构

### 后端 (Backend) - Python 3.11+
- **框架**：FastAPI (异步高性能网关)
- **智能体**：LangGraph (状态机工作流) + OpenAI GPT-4o
- **校验**：Pydantic V2 (严格的结构化输出)
- **存储**：PostgreSQL (带租户隔离的 Checkpointer)

### 前端 (Frontend) - React + TS
- **状态机**：Zustand (轻量级全局状态管理)
- **样式**：Tailwind CSS (极简主题)
- **动效**：Framer Motion (植物生长式过渡)
- **通信**：SSE (带状态对齐与快照恢复的实时流)

---

## 📂 项目结构

```text
EasyPlan/
├── app/                # 后端核心逻辑
│   ├── agents/         # LangGraph 拓扑与节点定义
│   ├── api/            # REST & SSE 接口
│   ├── models/         # 数据库 ORM 模型
│   └── services/       # LLM 与 MCP 同步服务
├── frontend/           # 前端 React 应用
│   ├── src/components/ # 响应式 UI 组件
│   └── src/store/      # 状态管理
├── docs/               # 全套设计文档与 OpenAPI 契约
└── tests/              # 31+ 自动化测试用例
```

## ⚡ 快速开始

### 1. 克隆项目
```bash
git clone https://github.com/your-username/EasyPlan.git
cd EasyPlan
```

### 2. 配置环境变量
```bash
# 复制环境变量模板
cp .env.example .env

# 使用文本编辑器修改 .env 文件，填入您的配置
# 必填项：OPENAI_API_KEY, DATABASE_URL, JWT_SECRET_KEY
```

### 3. 数据库初始化
EasyPlan 具备**自动建表**功能。后端在启动时会自动检测并初始化 PostgreSQL 表结构，您无需手动运行 SQL 脚本。

### 4. 本地启动
```bash
# 后端 (Terminal 1)
python -m pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 8000

# 前端 (Terminal 2)
cd frontend && npm install && npm run dev
```

---

## 🚀 生产环境部署 (4C4G)

建议使用 Docker Compose 进行一键部署，系统会自动处理全栈联通与数据库初始化。

```bash
# 1. 克隆代码并配置 .env
# 2. 启动服务
docker-compose up -d

# 3. 验证部署
docker-compose logs -f backend | grep "initialized"
```

### 4. 演示样例 (Usage Example)
1. **输入意图**：在首页输入 `我想在下周五前写完一份关于 AI 智能体的研究报告`。
2. **AI 拆解**：系统实时展示推理流，并“生长”出任务树（如：查阅最新论文、构建架构图、撰写摘要等）。
3. **自然语言微调**：觉得太复杂？输入 `帮我缩减到 30 分钟内能启动的程度`，AI 将立即重构计划。
4. **一键同步**：点击“确认注入”，任务将瞬间同步至您的 Todoist 账号。

---

## 📄 开源协议
本项目基于 **MIT 协议** 开源。
