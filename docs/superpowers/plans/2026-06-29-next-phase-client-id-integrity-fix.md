# Next-Phase Client ID Integrity Fix Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reject next-phase previews that reuse committed `client_node_id` values and prevent confirmation from succeeding when no new phase tasks are inserted.

**Architecture:** Keep the existing TaskTree and database schema. Enforce fresh IDs at the next-phase prompt and validator boundary, then add a transactional persistence guard that checks the database and uses strict task inserts for next-phase confirmation while preserving conflict-safe idempotency for initial-plan retries.

**Tech Stack:** Python 3.11, LangGraph, Pydantic, SQLAlchemy async, PostgreSQL, pytest

---

### Task 1: Lock the next-phase generation and validation contract

**Files:**
- Modify: `app/agents/nodes.py`
- Test: `tests/test_agent_graph.py`

- [x] **Step 1: Write failing prompt and validator tests**

Add tests that assert the next-phase prompt requires every proposed node ID to be absent from the committed tree, and that `_validate_task_tree` reports the concrete overlapping IDs.

- [x] **Step 2: Run the focused tests and verify RED**

Run:

```bash
python -m pytest tests/test_agent_graph.py -q
```

Expected: the new prompt assertion and cross-tree validation assertion fail.

- [x] **Step 3: Implement the minimal prompt and cross-tree checks**

Add one next-phase prompt rule and compare all proposed IDs with all committed IDs after both trees pass `TaskTree` parsing. Append a deterministic validation error for each overlap so the existing limited replan loop receives actionable feedback.

- [x] **Step 4: Run the focused tests and verify GREEN**

Run:

```bash
python -m pytest tests/test_agent_graph.py -q
```

Expected: all graph tests pass.

### Task 2: Make next-phase persistence fail atomically on reused IDs

**Files:**
- Modify: `app/agents/nodes.py`
- Test: `tests/test_task_persistence.py`

- [x] **Step 1: Write a failing persistence regression test**

Add a test with committed tasks already stored under the proposed next-phase IDs. Assert `persist_internal_tasks_node` raises, inserts no new tasks, and does not update the thread to `confirmed` or `succeeded`.

- [x] **Step 2: Run the focused test and verify RED**

Run:

```bash
python -m pytest tests/test_task_persistence.py -q
```

Expected: the node currently returns `succeeded` because `ON CONFLICT DO NOTHING` silently accepts the collision.

- [x] **Step 3: Implement the transactional guard and strict next-phase insert**

Before next-phase task insertion, query existing `(user_id, thread_id, client_node_id)` rows and raise on any overlap. Use ordinary PostgreSQL `INSERT` for next-phase task rows so a concurrent collision also aborts the transaction. Keep `ON CONFLICT DO NOTHING` for initial-plan retries and dependency idempotency.

- [x] **Step 4: Run the focused tests and verify GREEN**

Run:

```bash
python -m pytest tests/test_task_persistence.py -q
```

Expected: all persistence tests pass, including existing initial-plan retry coverage.

### Task 3: Verify the backend regression surface

**Files:**
- Verify: `app/agents/nodes.py`
- Verify: `tests/test_agent_graph.py`
- Verify: `tests/test_task_persistence.py`

- [x] **Step 1: Run the focused backend suites**

```bash
python -m pytest tests/test_agent_graph.py tests/test_task_persistence.py -q
```

- [x] **Step 2: Run the complete backend suite**

```bash
python -m pytest tests -q
```

- [x] **Step 3: Inspect the final diff**

Confirm the diff contains no frontend, API schema, database schema, or unrelated refactor changes.
