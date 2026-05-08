# EasyPlan 🪐 - **Intent-Driven** Task Orchestration Tool (v1.1.0)

> **Detailed plan is all you need. Efficiency is everything.**  
> **"Don't let grand visions become a burden. Let technology carry the weight of planning."**

EasyPlan is a minimalist task management tool built on the philosophy of **Intentional Productivity**. More than a simple to-do list, it's an intelligent Agent companion that understands your goals and decomposes them into "2-minute" micro-actions to lower the barrier to start.

[中文版本](./README.md) | [Quick Start](#-quick-start) | [Architecture](#-architecture)

---

## 🌟 Philosophy: Why EasyPlan?

Our design is rooted in the **BJ Fogg Behavior Model (Behavior = Motivation × Ability × Prompt)**.
- **Eliminate Starting Anxiety**: AI breaks big goals into tiny tasks, significantly boosting your "Ability" and making action a natural response.
- **Maintain Human Agency**: We stick to "Human-in-the-Loop (HITL)" design. AI handles the tedious planning; you retain ultimate control.
- **Absolute Certainty**: Industrial-grade "Resilient Sync" ensures your plans are synchronized accurately and without duplicates, even in unstable networks.

## ✨ Features (v1.1.0)

- **Spotlight Capture**: A single dynamic input box for fuzzy natural language goal entry.
- **Agentic Decomposition**: Powered by **LangGraph** for multi-step reasoning, enforcing the "2-minute rule" and "verb-driven" actions.
- **Natural Language Refinement**: Not satisfied with the plan? Just tell the AI what to change, and it re-plans instantly.
- **Fluid Motion UI**: "Balanced Minimalism" interface where the task tree grows organically as you plan.
- **Resilient Synchronization**: Supports **Todoist** and **Microsoft To Do** integration with strong idempotency. Failed syncs can be retried without creating duplicates.
- **Enterprise-Grade Security**: Multi-tenant isolation, JWT authentication, and OAuth2 integration closure.

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
│   └── services/       # LLM & core business services
├── frontend/           # Frontend React App
├── docs/               # Design docs & OpenAPI contract
└── tests/              # 31+ automated tests
```

## ⚡ Quick Start

### 1. Clone Project
```bash
git clone https://github.com/your-username/EasyPlan.git
cd EasyPlan
```

### 2. Configuration
```bash
# Copy the environment template
cp .env.example .env

# Edit the .env file with your specific settings
# Required: DATABASE_URL, EASYPLAN_LLM_PROVIDER, EASYPLAN_JWT_SECRET
```

### 3. Database Initialization
EasyPlan features **Automated Schema Initialization**. The backend will automatically detect and create PostgreSQL tables on startup—no manual SQL execution required.

### 4. Run Locally
```bash
# Backend (Terminal 1)
python -m pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 8000

# Frontend (Terminal 2)
cd frontend && npm install && npm run dev
```

### 5. Usage Example
1. **Enter Intent**: Input `I want to finish a research report on AI agents by next Friday` on the home page.
2. **AI Decomposition**: The system shows the reasoning stream in real-time and "grows" a task tree (e.g., review latest papers, build architecture diagrams, write abstract, etc.).
3. **Refinement**: Feel it's too complex? Type `Reduce it to something I can start within 30 minutes`, and the AI will immediately reconstruct the plan.
4. **One-Click Save**: Click "Confirm & Save", and the tasks will seamlessly drop into your native task board, ready for execution.

---

## 🚀 Production Deployment

We recommend using Docker Compose for a one-click deployment. The system will handle stack orchestration and DB setup automatically.

```bash
# 1. Clone & Configure .env
# 2. Start Services
docker-compose up -d

# 3. Verify Deployment
docker-compose logs -f backend | grep "initialized"
```

---

## 📅 Roadmap

### 🔜 v1.2.0 (Native Ecosystem - *Current Focus*)
- **Native Task Board**: Moving away from external sync to introduce a built-in, professional-grade task management panel.
- **Deep Immersion**: Implementing auto-collapsing reasoning logs once the task tree is generated.
- **Inline Editing**: Support for direct text and time modifications on the generated task tree.
- **Scope Horizon**: For massive goals, AI will only plan the "activation phase" to prevent cognitive overload, strictly adhering to a 2-level hierarchy.

---

## 📄 License
Distributed under the **MIT License**.

*MIT License**.
