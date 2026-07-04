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
- **Natural Language Refinement**: Adjust a plan conversationally without rebuilding the task tree by hand.
- **Native Task Views**: All Plans provides a portfolio view, projects hold individual plans, and My Day collects today's actions.
- **Resilient Generation**: Request-scoped SSE supports refresh recovery, duplicate-event filtering, cancellation, and retry.
- **Secure Multi-Tenancy**: Strict JWT authentication and tenant-scoped data access.

## 📊 Planning Eval

EasyPlan follows an **Eval-Driven** approach to model tuning. DeepSeek is the primary acceptance provider.

- **Core Cases**: 32
- **Intent Classification Accuracy**: 100.00%
- **JSON Parse Success Rate**: 100.00%
- **Strategy Compliance Rate**: 100.00%
- **Horizon Accuracy**: 100.00%
- **Action Quality Pass Rate**: 100.00%
- **Done Criteria Coverage**: 100.00%
- **Overall Pass Rate**: 100.00%

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

### v1.2.6 - Portfolio Overview & Answer Layer

- Upgrade All Plans into a clearer cross-project overview.
- Strengthen “current judgment → evidence → next exploration” for exploration decisions.
- Separate failure retry from normal regeneration and reduce repeated generation noise.

### Future Directions

- More differentiated planning models for long-term, short-term, and exploration goals.
- Task-level Action Coach capabilities such as “help me start,” “I am stuck,” and “break this down.”
- Personalized planning based on preferred task size, work duration, and common sources of resistance.

---

## 📄 License

Distributed under the **MIT License**.
