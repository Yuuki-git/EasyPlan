# EasyPlan 🪐 - Intent-Driven Task Management SaaS (v1.0.0)

> **"Don't let grand visions become a burden. Let technology carry the weight of planning."**

EasyPlan is a minimalist task management tool built on the philosophy of **Intentional Productivity**. More than a simple to-do list, it's an intelligent Agent companion that understands your goals and decomposes them into "2-minute" micro-actions to lower the barrier to start.

[中文版本](./README.md) | [Quick Start](#-quick-start) | [Architecture](#-architecture)

---

## 🌟 Philosophy: Why EasyPlan?

Our design is rooted in the **BJ Fogg Behavior Model (Behavior = Motivation × Ability × Prompt)**.
- **Eliminate Starting Anxiety**: AI breaks big goals into tiny tasks, significantly boosting your "Ability" and making action a natural response.
- **Maintain Human Agency**: We stick to "Human-in-the-Loop (HITL)" design. AI handles the tedious planning; you retain ultimate control.
- **Absolute Certainty**: Industrial-grade "Resilient Sync" ensures your plans are synchronized accurately and without duplicates, even in unstable networks.

## ✨ Features (v1.0.0)

- **Spotlight Capture**: A single dynamic input box for fuzzy natural language goal entry.
- **Agentic Decomposition**: Powered by **LangGraph** for multi-step reasoning, enforcing the "2-minute rule" and "verb-driven" actions.
- **Natural Language Refinement**: Not satisfied with the plan? Just tell the AI what to change, and it re-plans instantly.
- **Fluid Motion UI**: "Balanced Minimalism" interface where the task tree grows organically as you plan.
- **Resilient Synchronization**: Supports **Todoist** integration with strong idempotency. Failed syncs can be retried without creating duplicates.
- **SaaS-Grade Security**: Multi-tenant isolation, JWT authentication, and OAuth2 integration closure.

## 🛠️ Architecture

### Backend - Python 3.11+
- **Framework**: FastAPI (Async high-performance gateway)
- **Agents**: LangGraph (State machine workflow) + OpenAI GPT-4o
- **Validation**: Pydantic V2 (Strict structured output)
- **Storage**: PostgreSQL (Tenant-aware checkpointing)

### Frontend - React + TS
- **State Mgmt**: Zustand (Lightweight global state)
- **Styling**: Tailwind CSS (Minimalist theme)
- **Animation**: Framer Motion (Plant-like growth transitions)
- **Communication**: SSE (Real-time stream with state alignment)

---

## 📂 Project Structure

```text
EasyPlan/
├── app/                # Backend core
│   ├── agents/         # LangGraph topology & nodes
│   ├── api/            # REST & SSE endpoints
│   ├── models/         # ORM Models
│   └── services/       # LLM & MCP sync services
├── frontend/           # Frontend React App
├── docs/               # Design docs & OpenAPI contract
└── tests/              # 31+ automated tests
```

## ⚡ Quick Start

### 1. Configuration
Refer to `.env.example` to create your `.env` file with `OPENAI_API_KEY`, etc.

### 2. Run Backend
```bash
python -m pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

### 3. Run Frontend
```bash
cd frontend
npm install
npm run dev
```

---

## 📄 License
Distributed under the **MIT License**.
