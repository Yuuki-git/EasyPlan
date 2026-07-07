# Active Run SSE Lifecycle Fix Implementation Plan

> **Status: Implementation incomplete after reviewer re-check.** Backend unique request IDs and the base `activeRun` type are present. The authoritative remaining work is the frontend-only closure section below; do not reopen Phase 2 persistence or backend SSE buffering.

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prevent EasyPlan from reopening a historical initial SSE stream after Phase 2 succeeds by making active run state explicit, nullable, and independent from preview UI state.

**Architecture:** Add `activeRun` as the sole authority for whether and which SSE stream is subscribed. `previewMode` remains a display concern and must never imply a network run. Give initial and refine runs unique request IDs, preserve that identity through preview and confirmation, and clear `activeRun` atomically after a verified terminal transition.

**Tech Stack:** React, TypeScript, Zustand, EventSource, Vitest, React Testing Library, FastAPI, pytest

---

## Release Classification

- Severity: P0
- Release position: blocks the current RC and v1.2.6 work
- Scope: SSE subscription lifecycle only
- Persistence status: Phase 2 task/tree transaction remains accepted and is not reopened by this plan
- Product behavior remains:
  - next-phase loading and preview stay inline on the project page
  - successful initial-plan save goes to “全部计划”
  - successful next-phase confirmation stays in the selected project

## State Contract

```typescript
export type AgentRunType = 'initial' | 'next_phase';

export interface ActiveRun {
  threadId: string;
  runType: AgentRunType;
  requestId: string;
}

activeRun: ActiveRun | null;
```

Rules:

1. `activeRun === null` means no EventSource may exist.
2. `previewMode` controls presentation only and cannot create an `ActiveRun`.
3. Initial creation, refine, and next-phase generation each receive a unique request ID.
4. A plan preview and its confirmation continuation retain the same request ID.
5. Success, cancellation, explicit exit, logout, project deletion, and new intent clear `activeRun`.
6. A callback may mutate state only when its server event identity matches both its captured run and the store's current `activeRun`.

## Remaining Frontend-Only Closure

The current implementation still violates the state contract in four places:

- `alignState()` clears a valid initial `activeRun` while the initial snapshot is `running` and has no interrupt payload yet.
- `confirmPlan()` creates a new request ID after refresh instead of reusing the recovered initial `activeRun.requestId`.
- generation exit paths clear preview state without clearing `activeRun` and `easyplan_active_run`.
- the mounted Hook test covers only next-phase success and does not exercise initial running, refreshed confirmation, or exit cleanup.

Backend initial/refine request IDs are already unique. This closure must remain frontend-only.

### Remaining Task A: Preserve Initial `activeRun` During Running Alignment

**Files:**
- Modify: `frontend/src/store/useAppStore.ts:1166`
- Test: `frontend/tests/stateRestoration.test.mjs`
- Test: `frontend/tests/useSSE.lifecycle.test.tsx`

- [ ] Add a failing restoration test with this starting state:

```javascript
activeRun = {
  threadId: 'thread-initial',
  runType: 'initial',
  requestId: 'request-a',
};
snapshot = {
  thread_id: 'thread-initial',
  status: 'running',
  task_tree: null,
  interrupt_payload: null,
};
```

- [ ] Call `alignState('thread-initial')` and assert:

```javascript
assert.deepEqual(state.activeRun, activeRun);
assert.equal(state.appState, 'THINKING');
assert.equal(localStorage.getItem('easyplan_active_run') !== null, true);
```

- [ ] Change `alignState()` so a local run is preserved when all are true:

```typescript
const preserveInitialRunning =
  snapshot.status === 'running'
  && currentActiveRun?.threadId === snapshot.thread_id
  && currentActiveRun.runType === 'initial'
  && currentActiveRun.requestId.length > 0;
```

- [ ] Treat `preserveInitialRunning` as a valid recovered run before the branch that removes `easyplan_active_run`.
- [ ] Do not invent an initial run when there is no local active run and no server request identity.
- [ ] Run:

```text
node frontend/tests/stateRestoration.test.mjs
cd frontend && npm run test:hooks
```

- [ ] Expected: initial generation remains subscribed after its first alignment.

### Remaining Task B: Reuse the Recovered Run ID During Initial Confirmation

**Files:**
- Modify: `frontend/src/store/useAppStore.ts:1411`
- Test: `frontend/tests/generationRun.test.mjs`
- Test: `frontend/tests/useSSE.lifecycle.test.tsx`

- [ ] Add a failing test that restores:

```javascript
activeRun = {
  threadId: 'thread-initial',
  runType: 'initial',
  requestId: 'request-a',
};
syncRequestId = null;
previewMode = 'initial';
```

- [ ] Call `confirmPlan()` and assert the request body contains `request_id: "request-a"`.
- [ ] Assert `activeRun.requestId` remains `"request-a"` after the POST is accepted.
- [ ] Replace request selection with the active run as the source of truth:

```typescript
const run = get().activeRun;
if (!run || run.threadId !== threadId) {
  set({
    appState: 'ERROR',
    error: '当前规划会话已失效，请重新生成。',
  });
  return;
}
const requestId = run.requestId;
```

- [ ] Do not call `generateUUID()` inside `confirmPlan()`.
- [ ] `syncRequestId` may remain temporarily for compatibility, but it must not override or replace `activeRun.requestId`.
- [ ] Use the same rule for both initial and next-phase confirmation; `previewMode` may select UI behavior but not request identity.
- [ ] Run:

```text
node frontend/tests/generationRun.test.mjs
cd frontend && npm run test:hooks
```

- [ ] Expected: refresh recovery A confirms and continues listening on A; no B is generated.

### Remaining Task C: Centralize Run Cleanup Across Every Exit

**Files:**
- Modify: `frontend/src/store/useAppStore.ts:287`
- Modify: `frontend/src/store/useAppStore.ts:305`
- Modify: `frontend/src/store/useAppStore.ts:547`
- Modify: `frontend/src/store/useAppStore.ts:641`
- Modify: `frontend/src/store/useAppStore.ts:955`
- Test: `frontend/tests/generationRun.test.mjs`
- Test: `frontend/tests/useSSE.lifecycle.test.tsx`

- [ ] Add one store helper and use it instead of repeating partial cleanup:

```typescript
const clearActiveRunStorage = () => {
  localStorage.removeItem('easyplan_active_run');
};
```

- [ ] In every exit transition, include `activeRun: null` in the same `set()` that clears preview/run UI state, then call `clearActiveRunStorage()`.
- [ ] Apply this to:
  - successful `cancelPlanPreview()`
  - both branches of `returnToCommittedPlan()`
  - `startNewIntent()`
  - `setSelectedProjectId(null)` when entering “全部计划”
  - `setView('board')` when it explicitly leaves generation for “全部计划”
  - deletion of the active thread
  - next-phase start failure or authentication failure
- [ ] Do not clear a run merely because ordinary background task data refreshes.
- [ ] Add one assertion per exit:

```javascript
assert.equal(state.activeRun, null);
assert.equal(localStorage.getItem('easyplan_active_run'), null);
```

- [ ] In the Hook test, exit while an EventSource is active and assert it closes without replacement.

### Remaining Task D: Complete the Mounted Hook Lifecycle Matrix

**Files:**
- Modify: `frontend/tests/useSSE.lifecycle.test.tsx`

- [ ] Keep the existing next-phase success case.
- [ ] Add `initial running`:
  - seed initial active run A
  - mock a running snapshot without interrupt payload
  - mount the Hook
  - assert one EventSource is created for A
- [ ] Add `refresh then confirm initial`:
  - restore initial pending preview and active run A
  - call `confirmPlan()`
  - assert POST uses A
  - assert the EventSource remains scoped to A
  - dispatch `done` for A and assert the valid initial completion path runs
- [ ] Add `exit during generation`:
  - mount with active run A
  - invoke each public exit action in parameterized cases
  - assert the source closes
  - dispatch a late event from the closed source
  - assert page, selected project, and committed tree do not change
- [ ] Assert EventSource construction counts explicitly:

```typescript
expect(MockEventSource.instances).toHaveLength(1);
```

- [ ] Remove the four diagnostic `console.log` statements from `useSSE.lifecycle.test.tsx`.
- [ ] Run: `cd frontend && npm run test:hooks`
- [ ] Expected: all four lifecycle classes pass without console noise.

### Remaining Task E: Frontend Closure Verification

**Files:**
- No additional source files

- [ ] Run:

```text
node frontend/tests/generationRun.test.mjs
node frontend/tests/stateRestoration.test.mjs
node frontend/tests/runEvents.test.mjs
node frontend/tests/sseCursor.test.mjs
cd frontend
npm run test:hooks
npm run build
npm run lint
```

- [ ] Run: `git diff --check`
- [ ] Manual acceptance:
  - submit a new intent and verify reasoning/preview arrives without reload
  - refresh an initial preview, confirm it, and verify completion arrives
  - exit generation through cancel, return, new intent, and “全部计划”
  - verify no late event changes the destination page
  - unlock and confirm Phase 2 and verify it remains visible

## Remaining Reviewer Checklist

- [ ] initial `running + no interrupt_payload` preserves local active run A
- [ ] refreshed initial confirmation sends A, never generates B
- [ ] all public exit paths clear memory and `easyplan_active_run`
- [ ] idle board subscribes to zero streams
- [ ] Hook tests cover initial running, refresh confirmation, exit cleanup, and next-phase success
- [ ] Hook test contains no diagnostic `console.log`

## File Map

- Modify: `app/api/schemas.py`
  - add initial-run request identity to create-intent response
- Modify: `app/api/routes_intents.py`
  - create a unique initial request ID and return a scoped event URL
- Modify: `app/api/routes_threads.py`
  - use the actual confirmation/refine request ID instead of fixed `"initial"`
- Modify: `app/services/agent_runtime.py`
  - remove `INITIAL_RUN_REQUEST_ID`
  - require explicit request identity for every initial/refine event
  - persist request identity into initial review envelopes
- Test: `tests/test_agent_runtime.py`
- Test: `tests/test_agent_routes_integration.py`
- Test: `tests/test_openapi_contract.py`
- Modify: `frontend/src/types/api.ts`
  - add `ActiveRun` and create-intent response identity types
- Modify: `frontend/src/store/useAppStore.ts`
  - own and transition `activeRun`
  - defend `finishAgentRun()` against inactive initial terminal events
- Modify: `frontend/src/hooks/useSSE.ts`
  - subscribe only from `activeRun`
- Modify: `frontend/src/lib/runEvents.ts`
  - expose a reusable active-run identity matcher if needed
- Modify: `frontend/package.json`
  - add the Hook-level test command and test dependencies
- Create: `frontend/tests/useSSE.lifecycle.test.tsx`
- Modify: `frontend/tests/generationRun.test.mjs`
- Modify: `frontend/tests/stateRestoration.test.mjs`

### Task 1: Add Failing Backend Tests for Unique Initial and Refine Runs

**Files:**
- Test: `tests/test_agent_runtime.py`
- Test: `tests/test_agent_routes_integration.py`
- Test: `tests/test_openapi_contract.py`

- [ ] Add an intent-route test asserting two intent creations produce two non-empty, unequal request IDs.
- [ ] Assert each response uses its own scoped URL:

```python
assert response.json()["events_url"].endswith(
    f"run_type=initial&request_id={response.json()['request_id']}"
)
```

- [ ] Add a runtime test that appends two initial/refine runs for one thread and proves streaming request B never replays request A's `plan_ready` or `done`.
- [ ] Add a confirmation-route test proving `action="refine"` schedules `resume_thread` with the payload's unique `request_id`, not `"initial"`.
- [ ] Update the OpenAPI assertion so `IntentCreateResponse` requires `request_id`.
- [ ] Run:

```text
python -m pytest tests/test_agent_runtime.py tests/test_agent_routes_integration.py tests/test_openapi_contract.py -q
```

- [ ] Expected before implementation: tests fail because initial/refine events use the constant `"initial"`.

### Task 2: Give Initial and Refine Runs Real Request IDs

**Files:**
- Modify: `app/api/schemas.py`
- Modify: `app/api/routes_intents.py`
- Modify: `app/api/routes_threads.py`
- Modify: `app/services/agent_runtime.py`
- Test: `tests/test_agent_runtime.py`
- Test: `tests/test_agent_routes_integration.py`

- [ ] Add request identity to the intent response:

```python
class IntentCreateResponse(BaseModel):
    thread_id: str
    request_id: UUID
    status: Literal["running"]
    events_url: str
```

- [ ] In `create_intent()`, generate one `request_id = uuid4()`, pass it to `runtime.run_new_thread()`, and return it in both `request_id` and `events_url`.
- [ ] Change `run_new_thread()` and `_run_new_thread()` to require `request_id: str`; pass it to every `_append_chunk()`, `_append_done()`, and `_append_error()` call.
- [ ] Remove `INITIAL_RUN_REQUEST_ID`.
- [ ] Change `_persist_interrupt()` so the initial `task_tree_review` envelope includes:

```python
{
    **interrupt_payload,
    "request_id": request_id,
    "run_type": "initial",
}
```

- [ ] In `confirm_thread()`, use `payload.request_id` for both initial approve and refine resumes. Do not replace it with a constant.
- [ ] Ensure a refine request uses a fresh request ID and all refine events are buffered under that run key.
- [ ] Run the targeted backend tests from Task 1.
- [ ] Expected: each initial/refine lifecycle has a unique stream identity.

### Task 3: Add Explicit Nullable `activeRun` to the Store

**Files:**
- Modify: `frontend/src/types/api.ts`
- Modify: `frontend/src/store/useAppStore.ts`
- Modify: `frontend/tests/generationRun.test.mjs`
- Modify: `frontend/tests/stateRestoration.test.mjs`

- [ ] Add the `ActiveRun` type from the State Contract.
- [ ] Add `activeRun: ActiveRun | null` to `AppStore`; initialize it from `easyplan_active_run` only after validating all three fields.
- [ ] Add two actions:

```typescript
setActiveRun: (run: ActiveRun | null) => void;
clearActiveRun: () => void;
```

- [ ] `setActiveRun()` must update store and `easyplan_active_run` atomically; `clearActiveRun()` removes the key.
- [ ] Set `activeRun` at these points:
  - after `POST /api/intents` returns its initial request identity
  - before a refine confirmation request is sent
  - when next-phase generation creates its request ID
  - when `alignState()` restores a matching running or awaiting-confirmation envelope
- [ ] Keep the same `activeRun.requestId` when confirming the preview generated by that run.
- [ ] Clear `activeRun` on:
  - verified `finishAgentRun()` success
  - preview cancellation
  - return/exit from the current generation
  - `startNewIntent()`
  - explicit logout/reset
  - deletion of the active thread
- [ ] Add store tests asserting Phase 2 success simultaneously results in:

```javascript
assert.equal(state.activeRun, null);
assert.equal(state.previewMode, null);
assert.equal(state.selectedProjectId, 'project-1');
assert.equal(state.committedTaskTree.planning_context.current_phase.phase_id, 'phase-2');
```

- [ ] Add restoration tests proving idle board snapshots do not manufacture an initial active run.
- [ ] Run:

```text
node frontend/tests/generationRun.test.mjs
node frontend/tests/stateRestoration.test.mjs
```

### Task 4: Make `useSSE` Subscribe Only to `activeRun`

**Files:**
- Modify: `frontend/src/hooks/useSSE.ts`
- Modify: `frontend/src/lib/runEvents.ts`

- [ ] Delete these derived fallbacks:

```typescript
const activeRunType = previewMode || 'initial';
const activeRequestId = previewMode === 'next_phase' ? (phaseRequestId || '') : 'initial';
```

- [ ] Read the complete run identity only from `activeRun`.
- [ ] Before `alignState()` or URL creation:

```typescript
if (!activeRun) {
  eventSourceRef.current?.close();
  eventSourceRef.current = null;
  return;
}
```

- [ ] Build the cursor key and URL from `activeRun.threadId`, `activeRun.runType`, and `activeRun.requestId`.
- [ ] After `await alignState()`, read `useAppStore.getState().activeRun` again. Abort before constructing EventSource unless it still matches the captured run.
- [ ] At the start of every event callback require:

```typescript
if (!isMounted || eventSourceRef.current !== es) return;
if (!matchesActiveRun(useAppStore.getState().activeRun, capturedRun)) return;
```

- [ ] Parse and validate the server payload identity before moving the cursor or mutating state.
- [ ] Reset the tracker only when a non-null active-run key changes. Clearing the run must close the source; it must not establish an `"initial"` fallback run.
- [ ] Keep stall and reconnect timers disabled when `activeRun === null`.
- [ ] Expected: Phase 2 completion causes cleanup and no replacement EventSource.

### Task 5: Add Defense in `finishAgentRun()`

**Files:**
- Modify: `frontend/src/store/useAppStore.ts`
- Modify: `frontend/tests/generationRun.test.mjs`

- [ ] Read `activeRun` at the start of `finishAgentRun(event)`.
- [ ] Reject every terminal event unless:

```typescript
activeRun !== null
&& activeRun.threadId === event.thread_id
&& activeRun.runType === event.run_type
&& activeRun.requestId === event.request_id
```

- [ ] In particular, an initial `done` is invalid when `activeRun === null`, even if `threadId` still references a project.
- [ ] Keep the existing next-phase commit proof.
- [ ] Clear `activeRun` in the same `set()` transition that commits Phase 2 and clears preview state.
- [ ] In the valid initial branch, preserve the approved behavior of returning to “全部计划”; do not run this branch for an inactive historical event.
- [ ] Add tests proving:
  - inactive initial `done` does nothing
  - mismatched initial `done` does nothing
  - valid active initial `done` returns to “全部计划”
  - valid Phase 2 `done` keeps `selectedProjectId` and committed Phase 2

### Task 6: Add the Missing Hook-Level Lifecycle Test

**Files:**
- Modify: `frontend/package.json`
- Create: `frontend/tests/useSSE.lifecycle.test.tsx`

- [ ] Add Vitest, jsdom, and React Testing Library as development dependencies and expose:

```json
{
  "scripts": {
    "test:hooks": "vitest run tests/useSSE.lifecycle.test.tsx"
  }
}
```

- [ ] Implement a controllable `MockEventSource` that records constructed URLs, listeners, close calls, and dispatched events.
- [ ] Mount a minimal harness that only calls `useSSE()`.
- [ ] Seed the store with an active Phase 2 run and a selected project.
- [ ] Assert exactly one EventSource is created with `run_type=next_phase` and the Phase 2 request ID.
- [ ] Dispatch matching Phase 2 `done`; mock the snapshot/tasks proof as confirmed Phase 2.
- [ ] Wait for store transition and assert:
  - `activeRun === null`
  - Phase 2 remains committed
  - selected project remains selected
  - the existing EventSource closes
  - no second EventSource is created
- [ ] Attempt to dispatch historical initial `plan_ready` and `done`; assert committed Phase 2 and selected project remain unchanged.
- [ ] Run: `cd frontend && npm run test:hooks`
- [ ] Expected: the full Hook lifecycle passes, not only helper/store tests.

### Task 7: Final Verification

**Files:**
- No additional source files

- [ ] Run backend tests:

```text
python -m pytest tests/test_agent_runtime.py tests/test_agent_routes_integration.py tests/test_openapi_contract.py -q
python -m pytest tests -q
```

- [ ] Run frontend tests:

```text
node frontend/tests/runEvents.test.mjs
node frontend/tests/sseCursor.test.mjs
node frontend/tests/generationRun.test.mjs
node frontend/tests/stateRestoration.test.mjs
cd frontend
npm run test:hooks
npm run build
npm run lint
```

- [ ] Run: `git diff --check`
- [ ] Manual acceptance:
  - save a new initial plan and arrive at “全部计划”
  - open a project, complete Phase 1, unlock and confirm Phase 2
  - watch Phase 2 remain visible for at least 10 seconds
  - refresh and confirm Phase 2 remains visible
  - confirm “Unlock Phase 2” does not reappear
  - refine an initial preview twice and verify no previous preview/done replays

## Ownership

### Backend

- Tasks 1-2
- Deliverable: unique request IDs for initial and refine runs, with scoped event URLs and envelopes

### Frontend

- Tasks 3-6
- Deliverable: nullable `activeRun`, zero SSE subscriptions while idle, terminal defense, and a mounted Hook regression test

### Reviewer

- Verify `previewMode === null` can never imply `run_type=initial`
- Verify Phase 2 success clears `activeRun` and does not create another EventSource
- Verify initial/refine events never use the constant `"initial"`
- Run the Hook-level lifecycle test and inspect its EventSource construction count
- Re-run the minimal replay probe and confirm historical initial events cannot mutate committed Phase 2

## RC Exit Gate

RC remains blocked until:

- board idle creates zero EventSource connections
- Phase 2 success creates no fallback initial subscription
- historical initial `plan_ready` and `done` cannot modify committed Phase 2
- initial and refine runs use unique request IDs end to end
- `finishAgentRun()` rejects terminal events without a matching `activeRun`
- the mounted `useSSE` lifecycle test passes
