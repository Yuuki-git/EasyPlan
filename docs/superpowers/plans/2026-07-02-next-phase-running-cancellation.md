# Next-Phase Running Cancellation Implementation Plan

> **Status: Core running cancellation is implemented. Remaining RC work is limited to the two reviewer findings in “Final Closure Tasks”: SYNCING UI semantics and cancelled-run registry reclamation.**

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the visible next-phase cancel action work during generation and guarantee that a cancelled request cannot later emit a preview or persist tasks.

**Architecture:** Cancellation is request-scoped and stored as a terminal database tombstone. `AgentRuntime` also receives an in-memory cancellation signal for fast shutdown, while a locked persistence fence remains authoritative across workers and races. The frontend exits only after the request-scoped DELETE succeeds and clears the matching active run atomically.

**Tech Stack:** FastAPI, SQLAlchemy async sessions, LangGraph runtime, SSE, React, TypeScript, Zustand, pytest, Vitest

---

## Source Design

Implement exactly:

`docs/superpowers/specs/2026-07-02-next-phase-running-cancellation-design.md`

## Scope Guard

This patch:

- supports cancellation of next-phase `running`, `stalled`, and `awaiting_confirmation`
- treats `SYNCING`/confirming as irreversible and non-cancellable
- preserves the committed plan and all persisted tasks
- does not cancel initial intent generation
- does not redesign the next-phase UI
- does not change Phase 2 task insertion
- does not require immediate interruption of an in-flight provider HTTP request

## Final Closure Tasks

The backend cancellation contract and original Phase 2 rollback regression are already green. Execute only the following two tasks before the final verification sweep.

### Closure Task A: Separate THINKING and SYNCING Controls

**Files:**
- Modify: `frontend/src/components/PlanningOverview.tsx:196`
- Modify: `frontend/src/store/useAppStore.ts`
- Modify: `frontend/tests/useSSE.lifecycle.test.tsx`
- Modify: `frontend/tests/generationRun.test.mjs`

- [ ] **Step 1: Add failing UI/state tests**

Assert:

```typescript
// THINKING
expect(screen.getByRole('button', { name: '取消本次生成' })).toBeVisible();

// SYNCING
expect(screen.queryByRole('button', { name: '取消本次生成' })).toBeNull();
expect(screen.getByRole('button', { name: '返回当前计划' })).toBeVisible();
```

Also assert clicking the SYNCING return action:

```javascript
assert.deepEqual(state.activeRun, activeRunBeforeClick);
assert.equal(state.appState, 'SYNCING');
assert.equal(state.previewMode, 'next_phase');
assert.equal(MockEventSource.instances[0].closed, false);
```

- [ ] **Step 2: Add transient UI-only state**

Add to the store:

```typescript
isSyncingViewDismissed: boolean;
dismissSyncingView: () => void;
```

Implement:

```typescript
dismissSyncingView: () => set({ isSyncingViewDismissed: true }),
```

This action must not call `returnToCommittedPlan()` and must not modify `activeRun`, `appState`, `previewMode`, `phaseRequestId`, SSE state, or committed data.

- [ ] **Step 3: Reset the dismissed flag at lifecycle boundaries**

Set `isSyncingViewDismissed: false` when:

- next-phase generation starts
- a preview becomes pending
- confirmation starts
- confirmation succeeds or fails
- cancellation succeeds
- a new intent starts
- the selected project changes

Do not persist the flag to localStorage.

- [ ] **Step 4: Split the render branches**

Replace:

```typescript
appState === 'THINKING' || appState === 'SYNCING'
```

with distinct behavior:

- `THINKING`: show `正在规划下一阶段...` and `取消本次生成`
- `SYNCING && !isSyncingViewDismissed`: show `正在追加到当前计划...` and `返回当前计划`
- `SYNCING && isSyncingViewDismissed`: render the normal committed current-phase view while background SSE remains connected

- [ ] **Step 5: Verify background completion after dismissal**

In the mounted Hook test:

1. enter SYNCING for request A
2. click `返回当前计划`
3. assert EventSource A remains open
4. dispatch matching `done`
5. assert committed Phase 2 appears and `activeRun` clears normally

- [ ] **Step 6: Run frontend tests**

```text
node frontend/tests/generationRun.test.mjs
cd frontend
npm run test:hooks
npm run build
npm run lint
```

Expected: PASS.

### Closure Task B: Tie Cancelled-Run Keys to Active Runtime Ownership

**Files:**
- Modify: `app/services/agent_runtime.py`
- Modify: `tests/test_agent_runtime.py`

- [ ] **Step 1: Add failing lifecycle tests**

Add:

```python
def test_cancelled_run_key_is_removed_when_run_exits():
    ...
    assert run_key not in runtime._cancelled_runs


def test_orphaned_cancel_requests_do_not_enter_runtime_registry():
    ...
    for index in range(1000):
        runtime.cancel_run(
            thread_id=f"thread-{index}",
            run_type="next_phase",
            request_id=f"request-{index}",
        )
    assert len(runtime._cancelled_runs) == 0
```

Also prove a locally active cancellation key still suppresses late events until the run exits.

- [ ] **Step 2: Add explicit active-run ownership**

Use:

```python
self._active_runs: set[EventRunKey] = set()
self._cancelled_runs: set[EventRunKey] = set()
```

Register `run_key` in `_active_runs` under the runtime lock before any database-state check or graph construction.

- [ ] **Step 3: Store cancellation only for locally active runs**

Inside `cancel_run()`:

```python
with self._lock:
    if run_key in self._active_runs:
        self._cancelled_runs.add(run_key)
    subscribers = self._subscribers.pop(run_key, [])
```

If the run has not started in this process or belongs to another worker, do not retain an orphaned in-memory key. The already-committed database tombstone is authoritative for that case.

- [ ] **Step 4: Check the database tombstone before graph execution**

Immediately after registering `_active_runs`, load the thread state and require the matching `phase_generation_state/running` envelope.

If the request is already cancelled, confirmed, failed, or replaced:

- return before building or invoking the graph
- rely on `finally` to remove the active key

This closes the race where cancellation commits before the background coroutine begins.

- [ ] **Step 5: Reclaim the exact keys when execution ends**

Wrap the complete `run_next_phase()` lifecycle in `try/finally` and call:

```python
with self._lock:
    self._active_runs.discard(run_key)
    self._cancelled_runs.discard(run_key)
```

The `finally` must run when:

- the database preflight rejects the run
- cancellation is detected before graph construction
- cancellation is detected during chunk iteration
- the graph completes
- the graph raises

Do not remove the cancellation key inside `cancel_run()` itself; it must remain active until the locally running coroutine has observed cancellation.

- [ ] **Step 6: Preserve the database fence**

Runtime ownership is an optimization only. The existing database cancelled tombstone and persistence fence remain authoritative for pre-start cancellation, another worker, and process restart.

- [ ] **Step 7: Run runtime and full backend tests**

```text
python -m pytest tests/test_agent_runtime.py -q
python -m pytest tests -q
```

Expected: PASS; the 1000 orphan-cancellation probe leaves zero registry entries, and an active cancelled run is suppressed until its `finally` cleanup.

## File Map

- Modify: `app/api/routes_threads.py`
  - require cancellation `request_id`
  - call repository tombstone first, then runtime fast cancellation
- Modify: `app/services/thread_repository.py`
  - lock and cancel running or pending next-phase requests idempotently
- Modify: `app/services/agent_runtime.py`
  - track cancelled run keys
  - close stream subscribers
  - stop event emission
  - fence preview persistence against the database tombstone
- Modify: `app/api/schemas.py`
  - add a cancellation request/query contract only if needed by OpenAPI typing
- Modify: `docs/openapi.json`
  - regenerate after the route contract changes
- Modify: `tests/test_thread_repository.py`
  - cover state transitions, idempotency, ownership-safe lookup, and preservation
- Modify: `tests/test_agent_runtime.py`
  - cover subscriber shutdown and late interrupt suppression
- Modify: `tests/test_agent_routes_integration.py`
  - cover running cancellation, request mismatch, and runtime notification
- Modify: `tests/test_openapi_contract.py`
  - require `request_id` on the DELETE endpoint
- Modify: `frontend/src/store/useAppStore.ts`
  - send active run identity and reconcile 200/409 responses
- Modify: `frontend/src/components/PlanningOverview.tsx`
  - disable cancel while the request is in flight
- Modify: `frontend/tests/useSSE.lifecycle.test.tsx`
  - replace unconditional success mocking with the request-scoped contract
- Modify: `frontend/tests/generationRun.test.mjs`
  - cover cancellation cleanup and conflicts

### Task 1: Lock the Repository Contract with Failing Tests

**Files:**
- Modify: `tests/test_thread_repository.py`

- [ ] **Step 1: Add a running-cancellation test**

Create a thread whose committed `task_tree` is Phase 1 and whose envelope is:

```python
thread.status = "running"
thread.current_node = "next_phase_planner"
thread.lease_owner = "request-a"
thread.interrupt_payload = {
    "type": "phase_generation_state",
    "request_id": "request-a",
    "status": "running",
    "history": {},
}
```

Call:

```python
result = asyncio.run(
    repository.cancel_next_phase_request(
        user_id=USER_ID,
        thread_id=thread.thread_id,
        request_id="request-a",
    )
)
```

Assert:

```python
assert result is thread
assert thread.status == "succeeded"
assert thread.lease_owner is None
assert thread.lease_expires_at is None
assert thread.task_tree == committed_tree
assert thread.interrupt_payload["status"] == "cancelled"
assert thread.interrupt_payload["request_id"] == "request-a"
assert thread.interrupt_payload["history"]["request-a"]["status"] == "cancelled"
```

- [ ] **Step 2: Add stalled and pending-preview cases**

Use the same repository method for:

```python
{"type": "phase_generation_state", "request_id": "request-a", "status": "running"}
{"type": "next_phase_review", "request_id": "request-a", "status": "awaiting_confirmation"}
```

The stalled case is represented by an expired lease while the envelope remains `running`.

- [ ] **Step 3: Add idempotency and conflict cases**

Assert:

- cancelling the same cancelled request again returns the cancelled thread
- request B cannot cancel request A
- a confirmed request returns `ThreadStateConflictError`
- a failed or unrelated thread lifecycle returns `ThreadStateConflictError`
- no tasks or committed task tree values are changed

- [ ] **Step 4: Run the tests and verify they fail**

Run:

```text
python -m pytest tests/test_thread_repository.py -q
```

Expected: FAIL because `cancel_next_phase_request()` does not exist and the current method rejects `phase_generation_state/running`.

### Task 2: Implement the Transactional Cancellation Tombstone

**Files:**
- Modify: `app/services/thread_repository.py`
- Modify: `tests/test_thread_repository.py`

- [ ] **Step 1: Replace object-based cancellation with an atomic repository method**

Implement this signature:

```python
async def cancel_next_phase_request(
    self,
    *,
    user_id: UUID,
    thread_id: str,
    request_id: str,
) -> AgentThread | None:
```

Load the thread using:

```python
select(AgentThread).where(
    AgentThread.user_id == user_id,
    AgentThread.thread_id == thread_id,
).with_for_update()
```

- [ ] **Step 2: Validate the exact request lifecycle**

Allow only:

```python
payload["request_id"] == request_id
and (
    (payload["type"] == "phase_generation_state" and payload["status"] == "running")
    or (
        payload["type"] == "next_phase_review"
        and payload["status"] == "awaiting_confirmation"
    )
    or (
        payload["type"] == "phase_generation_state"
        and payload["status"] == "cancelled"
    )
)
```

Return the thread unchanged for the final `cancelled` case.

Raise request/state-specific `ThreadStateConflictError` for mismatch, confirmed, failed, or absent lifecycle states.

- [ ] **Step 3: Write the cancelled tombstone**

Use `_cancelled_phase_envelope()` and set:

```python
thread.status = "succeeded"
thread.current_node = "persist_internal_tasks"
thread.interrupt_payload = _cancelled_phase_envelope(
    payload,
    request_id=request_id,
    now=now,
)
thread.lease_owner = None
thread.lease_expires_at = None
thread.updated_at = now
```

Do not modify `thread.task_tree` or any `Task` row.

- [ ] **Step 4: Run repository tests**

Run:

```text
python -m pytest tests/test_thread_repository.py -q
```

Expected: PASS.

### Task 3: Add Failing Runtime Cancellation and Persistence-Race Tests

**Files:**
- Modify: `tests/test_agent_runtime.py`

- [ ] **Step 1: Test subscriber shutdown**

Create a next-phase subscriber for request A, call:

```python
runtime.cancel_run(
    thread_id="thread-1",
    run_type="next_phase",
    request_id="request-a",
)
```

Assert the stream terminates without yielding `done`, `agent_error`, or a later `plan_ready`.

- [ ] **Step 2: Test event suppression**

After cancellation, call `_append_event()` for request A and assert the event buffer remains unchanged.

Start request B on the same thread and assert its events remain deliverable.

- [ ] **Step 3: Test the authoritative persistence race**

Arrange a database thread with a cancelled request-A tombstone, then invoke the next-phase interrupt persistence path for request A.

Assert:

```python
assert stored_thread.interrupt_payload["status"] == "cancelled"
assert stored_thread.task_tree == committed_tree
assert not any("event: plan_ready" in event for event in buffered_events)
assert not any("event: done" in event for event in buffered_events)
assert not any("event: agent_error" in event for event in buffered_events)
```

- [ ] **Step 4: Run the tests and verify they fail**

Run:

```text
python -m pytest tests/test_agent_runtime.py -q
```

Expected: FAIL because runtime cancellation and the persistence fence do not exist.

### Task 4: Implement Runtime Fast Cancellation and Database Fence

**Files:**
- Modify: `app/services/agent_runtime.py`
- Modify: `tests/test_agent_runtime.py`

- [ ] **Step 1: Add a cancellation exception and run-key registry**

Add:

```python
class PhaseGenerationCancelled(Exception):
    pass
```

Initialize:

```python
self._cancelled_runs: set[EventRunKey] = set()
```

- [ ] **Step 2: Implement `cancel_run()`**

Use:

```python
def cancel_run(
    self,
    *,
    thread_id: str,
    run_type: RunType,
    request_id: str,
) -> None:
```

Under the runtime lock:

- add the exact `EventRunKey` to `_cancelled_runs`
- remove its subscribers from `_subscribers`
- do not cancel or alter another request on the same thread

Wake each removed subscriber with an internal `None` sentinel. Update subscriber queue typing and `stream_thread_events()` so `None` closes the generator without yielding an SSE event.

- [ ] **Step 3: Suppress events from cancelled runs**

Before formatting or buffering an event:

```python
if run_key in self._cancelled_runs:
    return False
```

Return a boolean from `_append_event()` so callers can detect suppression.

- [ ] **Step 4: Stop next-phase graph iteration cooperatively**

Before processing each chunk and immediately after `_append_chunk()`, check the run key. Return from `run_next_phase()` when cancelled so `_append_done()` is not reached.

When a new next-phase request starts for the same thread, remove obsolete cancelled run keys while preserving isolation from the new request.

- [ ] **Step 5: Fence `_persist_interrupt()` with a locked database check**

For `planning_mode == "next_phase"`:

- select the thread with `with_for_update()`
- require current payload type `phase_generation_state`
- require current payload request ID to match
- require current payload status `running`
- reject any matching cancelled history entry

Raise `PhaseGenerationCancelled` when cancellation won the race.

Update the same locked ORM object to `next_phase_review/awaiting_confirmation` and commit in that transaction.

- [ ] **Step 6: Handle normal cancellation separately**

In `_append_chunk()`, catch `PhaseGenerationCancelled` before the generic exception branch:

```python
except PhaseGenerationCancelled:
    return False
```

Do not call `_release_phase_failure()` and do not emit `agent_error` or `plan_ready`.

After the existing generic interrupt-persistence exception branch emits its safe failure event, also return `False`; a failed interrupt must not fall through and emit `plan_ready`.

Return `True` for normal chunks so `run_next_phase()` can stop cleanly on `False`.

- [ ] **Step 7: Run runtime tests**

Run:

```text
python -m pytest tests/test_agent_runtime.py -q
```

Expected: PASS.

### Task 5: Align the API Route and OpenAPI Contract

**Files:**
- Modify: `app/api/routes_threads.py`
- Modify: `tests/test_agent_routes_integration.py`
- Modify: `tests/test_openapi_contract.py`
- Modify: `docs/openapi.json`

- [ ] **Step 1: Add failing route tests**

Test:

```text
DELETE /api/threads/{thread_id}/phases/next/cancel?request_id=request-a
```

Cover:

- running request A returns `200`
- pending preview A returns `200`
- repeated cancellation A returns `200`
- request B against active A returns `409`
- confirmed A returns `409`
- missing `request_id` returns `422`
- another user's thread returns `404`

- [ ] **Step 2: Require request identity in the route**

Add:

```python
request_id: Annotated[str, Query(min_length=8, max_length=128)]
```

Call:

```python
thread = await repository.cancel_next_phase_request(
    user_id=current_user.id,
    thread_id=thread_id,
    request_id=request_id,
)
```

Return `404` when the repository returns `None`.

- [ ] **Step 3: Notify runtime only after the database commit succeeds**

Call:

```python
runtime.cancel_run(
    thread_id=thread_id,
    run_type="next_phase",
    request_id=request_id,
)
```

Do not notify runtime when repository validation returns `409`.

- [ ] **Step 4: Update dependencies and route tests**

Inject `AgentRuntime` into the cancel route and assert its `cancel_run()` receives the exact request identity.

- [ ] **Step 5: Regenerate and verify OpenAPI**

Require the DELETE operation's `request_id` query parameter.

Run:

```text
python -m pytest tests/test_agent_routes_integration.py tests/test_openapi_contract.py -q
```

Expected: PASS.

### Task 6: Add Failing Frontend Cancellation Contract Tests

**Files:**
- Modify: `frontend/tests/generationRun.test.mjs`
- Modify: `frontend/tests/useSSE.lifecycle.test.tsx`

- [ ] **Step 1: Test the request URL**

Seed:

```typescript
activeRun = {
  threadId: "project-1",
  runType: "next_phase",
  requestId: "request-a",
};
```

Call `cancelPlanPreview()` and assert:

```text
/api/threads/project-1/phases/next/cancel?request_id=request-a
```

- [ ] **Step 2: Test successful running cancellation**

Return a cancelled `ThreadSnapshot` containing the committed Phase 1 tree.

Assert:

```javascript
assert.equal(state.selectedProjectId, 'project-1');
assert.equal(state.activeRun, null);
assert.equal(state.previewMode, null);
assert.equal(state.phaseRequestId, null);
assert.equal(state.previewTaskTree, null);
assert.equal(state.appState, 'INITIAL');
assert.equal(localStorage.getItem('easyplan_active_run'), null);
```

- [ ] **Step 3: Test duplicate-click protection**

Hold the DELETE promise unresolved and invoke cancellation twice.

Assert only one request is sent and the cancel control is disabled while pending.

- [ ] **Step 4: Test `409` reconciliation**

Return `409`, then return a confirmed snapshot from alignment.

Assert the store adopts the confirmed snapshot instead of clearing it back to Phase 1.

- [ ] **Step 5: Test late-event rejection**

After successful cancellation, dispatch `plan_ready` and `done` for request A through the mounted Hook.

Assert the committed tree, selected project, and view remain unchanged.

- [ ] **Step 6: Run tests and verify failure**

Run:

```text
node frontend/tests/generationRun.test.mjs
cd frontend
npm run test:hooks
```

Expected: FAIL because the current frontend omits `request_id`, permits duplicate submission, and mocks running cancellation as unconditional success.

### Task 7: Implement the Frontend Request-Scoped Cancellation

**Files:**
- Modify: `frontend/src/store/useAppStore.ts`
- Modify: `frontend/src/components/PlanningOverview.tsx`
- Modify: `frontend/tests/generationRun.test.mjs`
- Modify: `frontend/tests/useSSE.lifecycle.test.tsx`

- [ ] **Step 1: Add cancellation pending state**

Add:

```typescript
isCancelPending: boolean;
```

Initialize it to `false` and clear it in reset, logout, new intent, and successful terminal transitions.

- [ ] **Step 2: Validate active run identity before sending**

At the start of `cancelPlanPreview()`:

```typescript
const { activeRun, selectedProjectId, isCancelPending } = get();
if (
  isCancelPending
  || !activeRun
  || activeRun.runType !== "next_phase"
  || activeRun.threadId !== selectedProjectId
) {
  return;
}
```

- [ ] **Step 3: Send the request-scoped DELETE**

Use:

```typescript
const url =
  `/api/threads/${selectedProjectId}/phases/next/cancel`
  + `?request_id=${encodeURIComponent(activeRun.requestId)}`;
```

Set `isCancelPending: true` before fetch and restore it in `finally`.

- [ ] **Step 4: Apply successful cancellation atomically**

From the returned snapshot, set:

```typescript
{
  view: "board",
  appState: "INITIAL",
  committedTaskTree: snapshot.task_tree || null,
  previewTaskTree: null,
  previewMode: null,
  phaseRequestId: null,
  basePhaseId: null,
  activeRun: null,
  isRunStalled: false,
  error: null,
}
```

Remove:

```text
easyplan_active_run
easyplan_preview_mode
easyplan_phase_request_id
easyplan_base_phase_id
```

Keep `selectedProjectId` unchanged.

- [ ] **Step 5: Reconcile a `409`**

On `409`, call `alignState(selectedProjectId)` before presenting an error.

After alignment:

- if the matching request is confirmed or cancelled, use the aligned state
- otherwise preserve the committed project and show `当前生成状态已变化，请重试。`

Do not blindly clear `activeRun` before alignment.

- [ ] **Step 6: Disable the visible control**

In `PlanningOverview`, bind `disabled={isCancelPending}` and show:

```text
正在取消...
```

Keep the cancel control in THINKING and pending-preview states. Hide it after confirmation enters SYNCING.

- [ ] **Step 7: Run frontend tests**

Run:

```text
node frontend/tests/generationRun.test.mjs
cd frontend
npm run test:hooks
```

Expected: PASS.

### Task 8: Full Verification and Reviewer Handoff

**Files:**
- No additional source files

- [ ] **Step 1: Run targeted backend tests**

```text
python -m pytest tests/test_thread_repository.py tests/test_agent_runtime.py tests/test_agent_routes_integration.py tests/test_openapi_contract.py -q
```

- [ ] **Step 2: Run full backend regression**

```text
python -m pytest tests -q
```

- [ ] **Step 3: Run frontend state and Hook tests**

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

- [ ] **Step 4: Run formatting verification**

```text
git diff --check
```

- [ ] **Step 5: Perform manual race acceptance**

1. Complete Phase 1.
2. Unlock Phase 2.
3. Cancel while loading.
4. Confirm the committed project remains visible.
5. Wait beyond normal generation duration.
6. Confirm no preview or new tasks appear.
7. Unlock again with a new request and confirm successfully.

## Ownership

### Backend

- Tasks 1-5
- Owns the tombstone, request matching, runtime stop, persistence fence, and OpenAPI contract
- Must preserve existing Phase 2 persistence behavior

### Frontend

- Tasks 6-7
- Owns request-scoped DELETE, pending UI state, state reconciliation, and Hook coverage
- Must preserve the selected project and committed plan

### Reviewer

- Task 8 plus the checklist below
- Must test a real running envelope, not only an awaiting preview

## Reviewer Checklist

- [ ] THINKING and PENDING cancellation sends the active next-phase request ID
- [ ] SYNCING hides cancellation and shows `返回当前计划`
- [ ] dismissing SYNCING preserves `activeRun`, EventSource, and background completion
- [ ] running, stalled, and awaiting-confirmation cancellation return `200`
- [ ] duplicate cancellation is idempotent
- [ ] mismatched and confirmed requests return `409`
- [ ] another user's thread returns `404`
- [ ] cancellation preserves committed task tree and tasks
- [ ] lease is released
- [ ] late persistence cannot replace the cancelled tombstone
- [ ] cancelled run emits no usable `plan_ready`, `done`, or `agent_error`
- [ ] cancelled-run keys exist only for locally active runs and are reclaimed in `finally`
- [ ] frontend clears active run only after successful cancellation or aligned terminal state
- [ ] late SSE events cannot mutate the board
- [ ] Hook test validates the request-scoped endpoint
- [ ] original Phase 2 rollback regression remains green

## RC Exit Gate

RC may proceed only when:

- the original Phase 2 rollback Bug remains fixed
- running cancellation succeeds for the matching request
- cancelled runs cannot revive through persistence or SSE
- all automated and manual gates above pass
