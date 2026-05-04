# EasyPlan 🪐 - 智能任务管理 SaaS

### 🚀 意图驱动与极简启动
EasyPlan 是一款基于**“意图驱动（Intentional Productivity）”**理念设计的智能任务与日程管理 SaaS。它结合了最新的 AI Agent 技术和行为心理学（BJ Fogg 模型），旨在通过深度拆解将宏大且令人焦虑的目标转化为“两分钟即可启动”的微行动，从根本上降低行动阻力。

[English Version](./README_EN.md)

---

#### ✨ 核心功能
- **意图捕获**：极简的 Spotlight 风格输入框，支持模糊口语化输入。
- **智能 Agent 拆解**：基于 **LangGraph** 实现多步推理，将意图降维拆解为结构化的任务树。
- **人在回路 (HITL)**：AI 负责规划建议，用户通过流畅的交互保留最终的执行控制权。
- **SaaS 架构**：专为云端设计，支持多租户数据隔离以及基于 **MCP (Model Context Protocol)** 的安全外部集成。
- **生长式 UI**：采用“平衡的极简主义”设计，界面随规划进度自然“生长”。

#### 🛠️ 技术栈
- **前端 (Frontend)**：React (TypeScript), Zustand, Tailwind CSS, Framer Motion。
- **后端 (Backend)**：FastAPI (Python), LangGraph, Pydantic V2, SQLAlchemy。
- **持久化 (Persistence)**：PostgreSQL + pgvector (用于长期记忆)。
- **外部集成 (Integration)**：MCP Client，支持 Todoist 等主流效率工具。

---

## 📂 项目结构

```text
EasyPlan/
├── app/                # 后端代码 (FastAPI + LangGraph)
├── frontend/           # 前端代码 (React + Vite)
├── docs/               # 文档 (PRD, 设计文档, OpenAPI)
├── tests/              # 后端测试套件
└── requirements.txt    # Python 依赖声明
```

## ⚡ 快速开始

### 后端 (Backend)
```bash
python -m pip install -r requirements.txt
uvicorn app.main:app --reload
```

推荐 Python 3.11+，以获得更好的异步性能。当前后端测试也保持 Python 3.10 兼容。

### Docker 部署
```bash
cp .env.example .env
docker compose up --build
```

- 后端 API: `http://localhost:8000`
- 前端静态站点: `http://localhost:8080`
- PostgreSQL + pgvector: `localhost:5432`

### 前端 (Frontend)
```bash
cd frontend
npm install
npm run dev
```

---

## 📄 开源协议
本项目基于 **MIT 协议** 开源。
