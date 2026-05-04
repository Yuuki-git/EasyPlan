# EasyPlan 🪐

[English](#english) | [中文](#中文)

---

<a name="english"></a>
## English

### 🚀 Intent-Driven Productivity & Minimal Friction
EasyPlan is a smart task and schedule management SaaS designed around the philosophy of **"Intentional Productivity"**. It leverages AI agents to decompose vague, overwhelming goals into actionable, "2-minute" micro-tasks, effectively lowering the barrier to action based on the BJ Fogg Behavior Model.

#### ✨ Core Features
- **Intent Capture**: A minimalist, Spotlight-style input for natural language goals.
- **Agentic Decomposition**: Powered by **LangGraph**, it iteratively breaks down intents into structured task trees.
- **Human-in-the-Loop (HITL)**: AI proposes plans; you retain ultimate control through a seamless confirmation flow.
- **SaaS Architecture**: Built for the cloud with multi-tenant data isolation and secure OAuth integrations via **MCP (Model Context Protocol)**.
- **Fluid Motion UI**: A "Balanced Minimalism" interface that grows organically as you plan.

#### 🛠️ Tech Stack
- **Frontend**: React (TypeScript), Zustand, Tailwind CSS, Framer Motion.
- **Backend**: FastAPI (Python), LangGraph, Pydantic V2, SQLAlchemy.
- **Persistence**: PostgreSQL + pgvector (for long-term memory).
- **Integration**: MCP Client for Todoist and other productivity tools.

---

<a name="中文"></a>
## 中文

### 🚀 意图驱动与极简启动
EasyPlan 是一款基于**“意图驱动（Intentional Productivity）”**理念设计的智能任务与日程管理 SaaS。它结合了最新的 AI Agent 技术和行为心理学（BJ Fogg 模型），旨在通过深度拆解将宏大且令人焦虑的目标转化为“两分钟即可启动”的微行动，从根本上降低行动阻力。

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

## 📂 Project Structure / 项目结构

```text
EasyPlan/
├── app/                # Backend (FastAPI + LangGraph)
├── frontend/           # Frontend (React + Vite)
├── docs/               # Documentation (PRD, Design, OpenAPI)
├── tests/              # Backend Test Suite
└── requirements.txt    # Python Dependencies
```

## ⚡ Quick Start / 快速开始

### Backend / 后端
```bash
python -m pip install -r requirements.txt
uvicorn app.main:app --reload
```

### Frontend / 前端
```bash
cd frontend
npm install
npm run dev
```

---

## 📄 License / 协议
Distributed under the **MIT License**. / 本项目基于 **MIT 协议** 开源。
