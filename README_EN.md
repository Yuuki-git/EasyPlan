# EasyPlan 🪐 - Intent-Driven AI Planning System (v1.2.3)

> **Current:** v1.2.3 Intent Profiling & Dynamic Routing  
> **Status:** Core pipeline implemented, eval expansion in progress

**EasyPlan is an intent-driven AI planning system. It identifies the user's goal type first, then selects the appropriate decomposition strategy, translating fuzzy intents into actionable, adjustable, and sustainable task maps.** More than a simple to-do list, it's an intelligent Agent companion that understands behavioral psychology.

```text
[Architecture Flow]
Intent Capture → Intent Profile → Strategy Router → Planner → Validator → Task Board ⇌ Refine / Fog Unlock
```

[中文版本](./README.md) | [Quick Start](#-quick-start) | [Architecture](#-architecture)

---

## 🌟 Philosophy: Why EasyPlan?

Our design is rooted in the **BJ Fogg Behavior Model (Behavior = Motivation × Ability × Prompt)**.
- **Eliminate Starting Anxiety**: AI breaks big goals into tiny tasks, significantly boosting your "Ability" and making action a natural response.
- **Maintain Human Agency**: We stick to "Human-in-the-Loop (HITL)" design. AI handles the tedious planning; you retain ultimate control.
- **Seamless Closed-Loop**：Say goodbye to clunky external syncing. A built-in "My Day" and "Planned" task board ensures your data is private, lighting-fast, and distraction-free.

## ✨ Features (v1.2.3)

- **Spotlight Capture**: A single dynamic input box for fuzzy natural language goal entry.
- **Agentic Decomposition**: Powered by **LangGraph** for multi-step reasoning, dynamically selecting ice-breaker, time-boxing, context aggregation, or exploration strategies based on the intent profile.
- **Natural Language Refinement**: Not satisfied with the plan? Just tell the AI what to change, and it re-plans the diff instantly.
- **Fluid Motion UI**: "Balanced Minimalism" interface with a parchment theme, where the task tree grows organically as you plan.
- **Native Task Engine**: A built-in task board supporting inline editing and cross-view transfers, creating a seamless "Plan -> Decompose -> Execute" experience.
- **Enterprise-Grade Security**: Multi-tenant isolation and strict JWT-based authentication.

## 📊 Planning Eval (Xiaomi Mimo API)
EasyPlan adopts an **Eval-Driven** approach for LLM tuning.
- **Core Cases**: 32 Core Cases
- **Intent Classification Accuracy**: 100.00%
- **JSON Parse Success Rate**: 100.00%
- **Strategy Compliance Rate**: 93.75%
- **Horizon Accuracy**: 96.875%
- **Overall Pass Rate**: 93.75%

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

### 🔜 v1.2.3 (Intent Profiling & Routing - *Current Focus*)
- **Evaluation Driven**: Establish `planning_cases.jsonl` to automatically test LLM decomposition quality.
- **Intent Profiling & Routing**: Introduce the `Intent → Profile → Strategy` pipeline. Dynamically switch Prompts based on time horizon and ambiguity.
- **Ice-breaker Redefined**: Only long-term, high-friction goals require low-barrier ice-breakers. Short sprints strictly forbid low-value actions like "Open Software".
- **Lightweight Strategy Validator**: Validator checks not only JSON validity but also strategy violations (e.g., forbidding long-term goals from planning out entire cycles).

---

## 📄 License
Distributed under the **MIT License**.
