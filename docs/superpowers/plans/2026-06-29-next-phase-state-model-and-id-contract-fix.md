# Next-Phase State Model and ID Contract Fix Implementation Plan

> **Status: Superseded on 2026-07-01.** The split-state and `client_node_id` work in this plan remains valid, but it does not close cross-run SSE replay or stale snapshot writes. Use `docs/superpowers/plans/2026-07-01-next-phase-cross-run-sse-snapshot-fix.md` as the authoritative P0 plan.

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix next-phase planning so phase preview never regresses back to the committed phase during generation or confirmation, and so confirmed next-phase tasks cannot be silently dropped by `client_node_id` conflicts.

**Architecture:** Split committed and preview planning state on the frontend instead of overloading a single `taskTree`. On the backend, keep the same-thread preview contract and existing task persistence path, but add a hard guard so next-phase trees cannot reuse committed `client_node_id` values and then disappear behind `ON CONFLICT DO NOTHING`.

**Tech Stack:** React, TypeScript, Zustand, FastAPI, LangGraph, PostgreSQL upsert semantics, pytest, Node-based frontend tests

---

## File Map

- Modify: `frontend/src/store/useAppStore.ts`
  - introduce separate committed vs preview planning state and stop `alignState()` from overwriting local next-phase preview with committed phase data
- Modify: `frontend/src/components/TaskBoard.tsx`
  - render next-phase preview from preview-only state instead of the shared committed tree
- Modify: `frontend/src/components/PlanningOverview.tsx`
  - read committed planning context for the project shell while showing preview content only in the dedicated next-phase area
- Test: `frontend/tests/generationRun.test.mjs`
  - add state-model coverage for next-phase running, pending, confirm, and cancel
- Test: `frontend/tests/stateRestoration.test.mjs`
  - add restore coverage proving `alignState()` preserves local preview during running/stalled next-phase
- Modify: `app/agents/nodes.py`
  - add next-phase prompt and validator rules that reject or repair cross-phase `client_node_id` reuse
- Test: `tests/test_agent_graph.py`
  - lock the next-phase prompt/validator contract
- Test: `tests/test_task_persistence.py`
  - prove next-phase confirmation cannot silently succeed when preview IDs collide with committed IDs

## Scope Guard

This fix does **not**:

- redesign roadmap strategy
- add user-supplied checkpoint inputs before next-phase generation
- introduce a new database table
- replace the interrupt-payload preview contract
- change the initial planning flow

The fix is successful if:

- preview phase rendering is stable through running, pending, confirm, and refresh
- confirmed next-phase tasks always appear after confirmation
- duplicate cross-phase `client_node_id` values are blocked or rewritten before persistence

### Task 1: Separate Committed and Preview Planning State on the Frontend

**Files:**
- Modify: `frontend/src/store/useAppStore.ts`
- Test: `frontend/tests/generationRun.test.mjs`

- [ ] Add separate frontend state for committed planning data and next-phase preview data instead of relying on one shared `taskTree`.
- [ ] Keep the committed project plan in committed state when `generateNextPhasePlan()` starts; do not treat the old phase tree as preview.
- [ ] During next-phase `THINKING`, store preview-only loading state without replacing committed phase data.
- [ ] During next-phase `PENDING`, populate preview-only task tree from `interrupt_payload.task_tree`.
- [ ] Update store tests so unlocking next phase proves committed phase data remains stable while preview state evolves separately.
- [ ] Run: `node frontend/tests/generationRun.test.mjs`
- [ ] Expected: next-phase store tests pass and explicitly show committed state and preview state diverging correctly.

### Task 2: Stop `alignState()` from Overwriting Local Preview with Committed Phase Data

**Files:**
- Modify: `frontend/src/store/useAppStore.ts`
- Test: `frontend/tests/stateRestoration.test.mjs`

- [ ] Change `alignState()` so `running/stalled + local next_phase` does not prefer `snapshot.task_tree` over local preview state.
- [ ] Keep committed tree sourced from `snapshot.task_tree`, but keep preview tree sourced from local preview state or `interrupt_payload.task_tree`, depending on snapshot status.
- [ ] Ensure refresh recovery for:
  - running next-phase
  - stalled next-phase
  - pending next-phase
  all restore into board mode without regressing preview to committed phase content.
- [ ] Add a regression test that seeds a local phase2 preview, calls `alignState()`, and asserts the preview remains phase2 rather than being overwritten by committed phase1.
- [ ] Run: `node frontend/tests/stateRestoration.test.mjs`
- [ ] Expected: restore tests pass and include the explicit no-overwrite regression.

### Task 3: Render Preview Only from Preview State

**Files:**
- Modify: `frontend/src/components/TaskBoard.tsx`
- Modify: `frontend/src/components/PlanningOverview.tsx`

- [ ] Update `TaskBoard` so next-phase preview rendering reads from preview-only state rather than the shared committed tree.
- [ ] Keep roadmap, current project shell, and committed task area sourced from committed planning state.
- [ ] Show next-phase loading and preview only in the dedicated preview region; do not let committed phase1 masquerade as phase2 preview before `plan_ready`.
- [ ] Manually verify:
  - click `Unlock Phase N`
  - phase1 committed content remains the board truth until preview is ready
  - phase2 preview appears only when preview data actually exists

### Task 4: Preserve Confirm and Cancel Semantics with the Split State Model

**Files:**
- Modify: `frontend/src/store/useAppStore.ts`
- Test: `frontend/tests/generationRun.test.mjs`

- [ ] On cancel, clear preview-only state and restore the committed board without re-fetching fake preview from committed tree.
- [ ] On confirm, keep preview visible until committed snapshot/tasks reload completes, then swap committed state forward and clear preview state.
- [ ] Add a confirm regression test proving the UI does not bounce back to phase1 preview while reloading committed phase2.
- [ ] Run: `node frontend/tests/generationRun.test.mjs`
- [ ] Expected: confirm/cancel tests pass under the split-state model.

### Task 5: Reject Cross-Phase `client_node_id` Collisions Before Persistence

**Files:**
- Modify: `app/agents/nodes.py`
- Test: `tests/test_agent_graph.py`
- Test: `tests/test_task_persistence.py`

- [ ] Tighten `NEXT_PHASE_PROMPT` so newly generated next-phase nodes must not reuse any `client_node_id` from the committed task tree.
- [ ] Extend next-phase validation to compare preview-tree node IDs against the committed tree, not only against duplicates inside the preview tree itself.
- [ ] On collision, fail validation and replan or reject; do not allow confirmation to proceed into a silent `ON CONFLICT DO NOTHING` drop.
- [ ] Add backend tests proving:
  - reusing committed `client_node_id` in next-phase preview is invalid
  - confirmation cannot silently persist zero new tasks while reporting success
- [ ] Run: `python -m pytest tests/test_agent_graph.py tests/test_task_persistence.py -q`
- [ ] Expected: backend prompt/validator/persistence tests pass with the new collision guard.

### Task 6: Final Verification

**Files:**
- No additional source files

- [ ] Run: `python -m pytest tests -q`
- [ ] Expected: full backend suite passes.
- [ ] Run: `node frontend/tests/generationRun.test.mjs`
- [ ] Expected: pass.
- [ ] Run: `node frontend/tests/stateRestoration.test.mjs`
- [ ] Expected: pass.
- [ ] Run: `cd frontend && npm run build`
- [ ] Expected: build passes.
- [ ] Run: `cd frontend && npm run lint`
- [ ] Expected: lint passes.
- [ ] Manual acceptance:
  - unlock next phase and confirm phase1 does not get mistaken for preview
  - wait for preview and confirm phase2 actually appears
  - confirm refresh during running/stalled/pending keeps preview stable
  - confirm append no longer results in “confirmation succeeded but no new tasks appeared”

## Reviewer Checklist

- [ ] Frontend keeps committed plan state and next-phase preview state separate
- [ ] `alignState()` no longer overwrites local phase2 preview with committed phase1 during running/stalled next-phase
- [ ] Before `plan_ready`, phase1 committed tree is not rendered as fake phase2 preview
- [ ] Confirm flow does not bounce back to phase1 while reloading committed phase2
- [ ] Backend rejects or repairs next-phase trees that reuse committed `client_node_id`
- [ ] No path remains where confirmation reports success but phase2 tasks are silently dropped
