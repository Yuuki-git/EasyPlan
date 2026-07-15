# EasyPlan 🪐 - Intent-Driven AI Planning System

**EasyPlan is an intent-driven AI planning system. It identifies the user's goal type, selects an appropriate planning strategy, and turns fuzzy intentions into actionable, adjustable, and sustainable task maps.** More than a to-do list, it is an intelligent Agent companion informed by behavioral psychology.

```text
[Architecture Flow]
Intent Capture → Intent Profile → Strategy Router → Planner → Validator → Task Board → Phase Progression
```

[中文版本](./README.md) | [Quick Start](#-quick-start) | [Architecture](#-architecture)

---

## 🌟 Philosophy: Why EasyPlan?

Our design is rooted in the **BJ Fogg Behavior Model (Behavior = Motivation × Ability × Prompt)**.

- **Reduce starting anxiety**: AI chooses a suitable task size based on the goal type and psychological resistance.
- **Preserve human agency**: Human-in-the-Loop design keeps confirmation, refinement, and cancellation under the user's control.
- **Control the planning horizon**: Long-term goals expand only the current phase while future phases remain a high-level map.
- **Provide a native execution loop**: Confirmed plans flow directly into EasyPlan's All Plans, project, and My Day views.

## ✨ Features

- **Spotlight Capture**: A dynamic input for fuzzy, conversational goals.
- **Intent Profiling and Routing**: Different strategies for long-term growth, short-term delivery, context checklists, and exploration decisions.
- **Action Quality Guardrails**: Runtime validation for concrete, startable, and completable tasks with `done_criteria`, `start_hint`, and `fallback_action`.
- **Three-Tier Planning**: `Roadmap → Current Phase → Next Action` separates long-term direction, near-term planning, and the immediate step.
- **Phase Progression**: Generate, preview, and append the next phase inside the same project.
- **Long-Term Execution Loops**: Track weekly practice quotas, outcome evidence, and a user-controlled phase review before progressing.
- **Exploration Answer Layer**: Give a current judgment, supporting evidence, and next exploration steps before breaking uncertainty into low-cost validation actions.
- **Task-Level Action Coach**: Use “help me start,” “I am stuck,” and “break this down” to preview structured advice and apply only a confirmed local change.
- **Natural Language Refinement**: Adjust a plan conversationally without rebuilding the task tree by hand.
- **Native Task Views**: All Plans summarizes phase, progress, and next action by project; projects hold individual plans; My Day collects today's actions.
- **Resilient Generation**: Request-scoped SSE supports refresh recovery, reconnecting the current run, duplicate-event filtering, cancellation, and retry.
- **Secure Multi-Tenancy**: Strict JWT authentication and tenant-scoped data access.

## 📊 Planning Eval

EasyPlan follows an **Eval-Driven** approach to model tuning. DeepSeek is the primary acceptance provider. The current strict Planning Eval release baseline is:

- **Cases Passed**: 54/54
- **Pass Rate**: 100.00%
- **Intent Classification Accuracy**: 100.00%
- **JSON Parse Success Rate**: 100.00%
- **Strategy Compliance Rate**: 100.00%
- **Horizon Accuracy**: 100.00%
- **Action Quality Pass Rate**: 100.00%
- **Done Criteria Coverage**: 100.00%
- **Long-Term Loop Contract Pass Rate**: 100.00%

The independent Task Assist Eval passes **18/18** cases, with all six JSON, mode-match, actionability, scope, reference-integrity, and explicit-constraint metrics at **100%**.

## 🛠️ Architecture

### Backend - Python 3.11+

- **Framework**: FastAPI
- **Agent Workflow**: LangGraph + DeepSeek
- **Validation**: Pydantic V2
- **Storage**: PostgreSQL + SQLAlchemy 2.x async
- **Streaming**: Request-scoped SSE

### Frontend - React + TypeScript

- **Build Tool**: Vite
- **State Management**: Zustand
- **Styling**: Tailwind CSS
- **Animation**: Framer Motion
- **Testing**: Vitest + React Testing Library

---

## 📂 Project Structure

```text
EasyPlan/
├── app/                # Backend core
│   ├── agents/         # LangGraph, Planner, and Validator
│   ├── api/            # REST, SSE, and authentication
│   ├── models/         # Database ORM models
│   └── services/       # Runtime, repository, and LLM services
├── frontend/           # React frontend
│   ├── src/components/ # Planning, project, and task components
│   ├── src/hooks/      # SSE lifecycle
│   └── src/store/      # Zustand state
├── docs/               # Product, architecture, and API documentation
└── tests/              # Backend, Agent, contract, and Eval tests
```

## ⚡ Quick Start

### 1. Clone the Project

```bash
git clone https://github.com/Yuuki-git/EasyPlan.git
cd EasyPlan
```

### 2. Configure Environment Variables

```bash
cp .env.example .env
```

At minimum, configure:

```text
DATABASE_URL
EASYPLAN_JWT_SECRET
JWT_SECRET_KEY
EASYPLAN_LLM_PROVIDER=deepseek
DEEPSEEK_API_KEY
```

### 3. Initialize the Database

The backend automatically detects and initializes the PostgreSQL schema at startup. No manual SQL script is required.

### 4. Run Locally

```bash
# Backend (Terminal 1)
python -m pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 8000

# Frontend (Terminal 2)
cd frontend
npm install
npm run dev
```

### 5. Usage Example

1. **Enter an intent**: Type `I want to switch to product management, but I do not know where to start`.
2. **AI planning**: EasyPlan identifies an exploration decision, gives a current judgment, and creates a validation route.
3. **Refine naturally**: Type `I can only spend three hours per week` to adjust the plan.
4. **Confirm and save**: The plan enters the current project and native task board.
5. **Progress by phase**: Complete the current phase, then unlock the next phase in the same project.

---

## 🚀 Production Deployment

Docker Compose is recommended:

```bash
docker-compose up -d
docker-compose logs -f backend
```

---

## 📅 Roadmap

### v1.2.7-A - Long-Term Execution Loop

- Execute long-term growth plans through weekly practice loops without pre-generating future daily tasks.
- Combine process adherence with outcome evidence, then let the user finalize the phase review.
- Preserve schema-v1 behavior for legacy and non-long-term plans.

### v1.2.8 - Planning Model Differentiation

- Model short-term delivery through deliverables, time budgets, scope trade-offs, and critical paths.
- Model exploration decisions through structured judgments, unknowns, low-cost experiments, and decision gates.
- Preserve legacy plan compatibility; the strict DeepSeek Planning Eval passes 54/54 cases.

### v1.3.0 - Task Copilot / Action Coach

- Provide structured “help me start,” “I am stuck,” and “break this down” assistance for a single task.
- Do not mutate tasks before confirmation; Apply changes only local hints or creates traceable child tasks.
- Preserve parent-child hierarchy across project and My Day views, with deterministic parent roll-up.

### v1.3.1 - Execution Engine / Refine Diff

- Generate bounded project-level adjustments when available time, progress, deadlines, or priorities change.
- Preview before/after task updates, small additions, sibling reordering, and My Day changes, then apply the diff atomically.
- Keep completed work, historical phases, Roadmap, long-term loops, and Task Assist children immutable without regenerating the plan.
- Provide a dedicated durable run, recoverable SSE, scope fingerprints, and idempotent Apply; the strict DeepSeek Eval passes 24/24 cases.

### Future Directions

- v1.4: personalized planning based on preferred task size, work duration, and common sources of resistance.

---

## 📄 License

Distributed under the **MIT License**.
