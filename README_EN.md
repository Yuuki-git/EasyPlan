# EasyPlan 🪐 - Intent-Driven AI Planning System (v1.2.4-rc.1)

> **Current:** v1.2.4-rc.1 Action Quality & Fallback
> **Status:** RC acceptance completed. DeepSeek is the default and primary planning provider.

**EasyPlan is an intent-driven AI planning system. It identifies the user's goal type first, then selects the appropriate decomposition strategy, translating fuzzy intents into actionable, adjustable, and sustainable task maps.** More than a simple to-do list, it's an intelligent Agent companion that understands behavioral psychology.

```text
[Architecture Flow]
Intent Capture → Intent Profile → Strategy Router → Planner (w/ Action Quality) → Runtime Validator → Task Board ⇌ Refine / Fog Unlock
```

[中文版本](./README.md) | [Quick Start](#-quick-start) | [Architecture](#-architecture)

---

## 🌟 Philosophy: Why EasyPlan?

Our design is rooted in the **BJ Fogg Behavior Model (Behavior = Motivation × Ability × Prompt)**.
- **Eliminate Starting Anxiety**: AI breaks big goals into tiny tasks, significantly boosting your "Ability" and making action a natural response.
- **Maintain Human Agency**: We stick to "Human-in-the-Loop (HITL)" design. AI handles the tedious planning; you retain ultimate control.
- **Seamless Closed-Loop**：Say goodbye to clunky external syncing. A built-in "My Day" and "Planned" task board ensures your data is private, lighting-fast, and distraction-free.

## ✨ Features (v1.2.4)

- **Spotlight Capture**: A single dynamic input box for fuzzy natural language goal entry.
- **Agentic Decomposition**: Powered by **LangGraph** for multi-step reasoning, dynamically selecting ice-breaker, time-boxing, context aggregation, or exploration strategies based on the intent profile.
- **Action Quality Guardrails**: Built-in Runtime Validator enforcing explicit `done_criteria` and `start_hint` to prevent vague or unactionable LLM outputs.
- **Natural Language Refinement**: Not satisfied with the plan? Just tell the AI what to change, and it re-plans the diff instantly with structured replan feedback.
- **Fluid Motion UI**: "Balanced Minimalism" interface with a parchment theme, elegant collapsible hints, and organic task tree growth.
- **Enterprise-Grade Security**: Multi-tenant isolation and strict JWT-based authentication.

## 📊 Planning Eval
EasyPlan adopts an **Eval-Driven** approach for LLM tuning.
- **Primary Provider**: DeepSeek (v1.2.4 Achieved 100%)
- **Compatibility Provider**: Xiaomi MiMo
- **Core Cases**: 32 Core Cases
- **Intent Classification Accuracy**: 100.00%
- **JSON Parse Success Rate**: 100.00% (with robust JSON Repair fallback)
- **Strategy Compliance Rate**: 100.00%
- **Horizon Accuracy**: 100.00%
- **Action Quality Pass Rate**: 100.00%
- **Overall Pass Rate**: 100.00%

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

### 🔜 v1.2.5 (Three-Tier Planning & Execution Guide - *Current Focus*)
- **Conditional Roadmap**: Roadmaps are no longer a standard feature. They are only displayed for "long-term goals" and "exploration decisions" as high-level breadcrumbs.
- **Phase Progress Awareness**: Allow users to clearly perceive the current phase's progress and unlock conditions.
- **Next Action Highlights**: Visually emphasize the single most important task to execute right now, completely eliminating choice paralysis.

---

## 📄 License
Distributed under the **MIT License**.
