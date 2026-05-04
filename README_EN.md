# EasyPlan 🪐 - Intent-Driven Task Management SaaS

### 🚀 Intent-Driven Productivity & Minimal Friction
EasyPlan is a smart task and schedule management SaaS designed around the philosophy of **"Intentional Productivity"**. It leverages AI agents to decompose vague, overwhelming goals into actionable, "2-minute" micro-tasks, effectively lowering the barrier to action based on the BJ Fogg Behavior Model.

[中文版本](./README.md)

---

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

## 📂 Project Structure

```text
EasyPlan/
├── app/                # Backend (FastAPI + LangGraph)
├── frontend/           # Frontend (React + Vite)
├── docs/               # Documentation (PRD, Design, OpenAPI)
├── tests/              # Backend Test Suite
└── requirements.txt    # Python Dependencies
```

## ⚡ Quick Start

### Backend
```bash
python -m pip install -r requirements.txt
uvicorn app.main:app --reload
```

### Frontend
```bash
cd frontend
npm install
npm run dev
```

---

## 📄 License
Distributed under the **MIT License**.
