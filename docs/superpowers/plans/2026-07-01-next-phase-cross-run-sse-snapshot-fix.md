# Next-Phase Cross-Run SSE and Snapshot Fix Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ensure a next-phase run can only be completed by its own SSE events and its own confirmed snapshot, so historical terminal events and stale snapshot responses can never return the board from phase2 to phase1.

**Architecture:** Give each next-phase event stream an explicit `(thread_id, run_type, request_id)` identity. Buffer and subscribe by that run identity, then make the frontend validate server-supplied identity before accepting an event. Use one shared latest-request gate for all thread snapshot reads, and keep the preview until a matching confirmed snapshot proves that the phase advanced and its tasks exist.

**Tech Stack:** FastAPI, Python async generators, SSE, React, TypeScript, Zustand, Node tests, pytest

---

## Release Classification

- Severity: P0
- Release position: pre-v1.2.6 hotfix; blocks the current RC
- Product behavior remains unchanged:
  - next-phase generation stays inline on the project page
  - generation shows lightweight loading
  - confirmation appends to the same project/thread
- This plan does not add checkpoint input, roadmap redesign, cached draft display, or a new product feature.

## File Map

- Modify: `app/services/agent_runtime.py`
  - carry run identity through event production
  - buffer events and subscribers by run identity
  - prevent an old run's terminal event from ending a new run's stream
- Modify: `app/api/routes_threads.py`
  - accept and forward next-phase stream identity
  - return a run-scoped `events_url`
  - pass confirmation request identity into resume events
- Modify: `app/api/schemas.py`
  - document SSE run identity fields if shared response models are used
- Test: `tests/test_agent_runtime.py`
  - prove historical `done` cannot truncate a later run
- Test: `tests/test_agent_routes_integration.py`
  - prove the next-phase events URL and confirmation events retain request identity
- Modify: `frontend/src/types/api.ts`
  - add typed SSE event metadata
- Modify: `frontend/src/hooks/useSSE.ts`
  - connect to the current run, validate event identity, and ignore stale EventSource handlers
- Modify: `frontend/src/lib/runEvents.ts`
  - separate server identity validation from event-id deduplication
- Modify: `frontend/src/lib/sseCursor.ts`
  - scope the cursor by thread, run type, and request ID
- Create: `frontend/src/store/snapshotRequestGate.ts`
  - provide one shared latest-request gate for all thread snapshot reads
- Modify: `frontend/src/store/useAppStore.ts`
  - guard `alignState()` and `loadProjectSnapshot()`
  - verify next-phase commit before clearing preview
- Test: `frontend/tests/runEvents.test.mjs`
  - reject mismatched and replayed events
- Test: `frontend/tests/sseCursor.test.mjs`
  - reset the cursor when the run changes within one thread
- Test: `frontend/tests/stateRestoration.test.mjs`
  - reject a late phase1 snapshot after phase2 is loaded
- Test: `frontend/tests/generationRun.test.mjs`
  - keep preview until matching confirmation and phase advancement are proven
- Create: `frontend/tests/snapshotRequestGate.test.mjs`
  - prove only the newest snapshot request may write

## Contract

Every next-phase SSE payload, including `reasoning`, `checkpoint`, `plan_ready`, `done`, and `agent_error`, must include:

```json
{
  "thread_id": "thread-123",
  "run_type": "next_phase",
  "request_id": "8d6e...",
  "state_version": 42
}
```

The stream URL must identify the requested run:

```text
/api/threads/{thread_id}/events?run_type=next_phase&request_id={request_id}
```

For this hotfix, frontend request sequencing is the source of truth for blocking stale snapshot writes. The existing snapshot `state_version = 0` and `last_event_id = null` fields must not be used as freshness evidence.

### Task 1: Add Failing Backend Cross-Run Replay Tests

**Files:**
- Test: `tests/test_agent_runtime.py`

- [ ] Add a test that buffers an old next-phase `done` for request A, then buffers `plan_ready` for request B on the same thread.
- [ ] Stream request B without a cursor and collect its first events.
- [ ] Assert request A's `done` is absent and request B's `plan_ready` is delivered.

Use this behavioral shape:

```python
async def collect_until_terminal(stream):
    received = []
    async for event in stream:
        received.append(event)
        if "event: done" in event or "event: agent_error" in event:
            break
    return received


def test_new_run_stream_is_not_truncated_by_historical_done():
    runtime = AgentRuntime(graph_factory=lambda **_: AsyncStreamGraph())
    runtime._append_done(
        "thread-1",
        status="completed",
        run_type="next_phase",
        request_id="request-a",
    )
    runtime._append_event(
        "thread-1",
        "plan_ready",
        {"task_tree": {"root": {}}},
        run_type="next_phase",
        request_id="request-b",
    )

    events = asyncio.run(
        collect_until_terminal(
            runtime.stream_thread_events(
                user_id="user-1",
                thread_id="thread-1",
                run_type="next_phase",
                request_id="request-b",
            )
        )
    )

    payload = "\n".join(events)
    assert '"request_id":"request-a"' not in payload
    assert '"request_id":"request-b"' in payload
    assert "event: plan_ready" in payload
```

- [ ] Add a second test proving a cursor from request A is not reused to position request B.
- [ ] Run: `python -m pytest tests/test_agent_runtime.py -q`
- [ ] Expected before implementation: the new tests fail because buffering is thread-scoped.

### Task 2: Make Backend SSE Run-Scoped

**Files:**
- Modify: `app/services/agent_runtime.py`
- Modify: `app/api/routes_threads.py`
- Modify: `app/api/schemas.py`
- Test: `tests/test_agent_runtime.py`
- Test: `tests/test_agent_routes_integration.py`

- [ ] Introduce one immutable run key:

```python
@dataclass(frozen=True)
class EventRunKey:
    thread_id: str
    run_type: Literal["initial", "next_phase"]
    request_id: str
```

- [ ] Change event and subscriber storage from `thread_id` keys to `EventRunKey` keys.
- [ ] Pass `run_type` and `request_id` from `run_next_phase()` through `_append_chunk()`, `_append_event()`, `_append_done()`, and `_append_error()`.
- [ ] For `resume_thread()`, pass the confirmation payload's real `request_id`; derive `run_type` from the pending envelope before scheduling the background resume.
- [ ] Inject `thread_id`, `run_type`, and `request_id` into every event payload on the server. Do not let callers supply or overwrite these fields.
- [ ] Extend `stream_thread_events()` with required run identity for `next_phase`; replay and terminal detection must operate only inside that run's event segment.
- [ ] Keep the existing initial-plan stream compatible. If initial runs do not yet have a request ID, use one explicit initial-run identity consistently rather than mixing it with next-phase events.
- [ ] Return a scoped URL from `POST /api/threads/{thread_id}/phases/next`:

```python
events_url=(
    f"/api/threads/{thread_id}/events"
    f"?run_type=next_phase&request_id={request_id}"
)
```

- [ ] Add route tests that assert the URL contains the request identity and that `plan_ready`, `done`, and `agent_error` payloads expose the same identity.
- [ ] Run:

```text
python -m pytest tests/test_agent_runtime.py tests/test_agent_routes_integration.py tests/test_openapi_contract.py -q
```

- [ ] Expected: old terminal events cannot appear in or terminate a newer request's stream.

### Task 3: Add Failing Frontend Event-Identity Tests

**Files:**
- Modify: `frontend/tests/runEvents.test.mjs`
- Modify: `frontend/tests/sseCursor.test.mjs`

- [ ] Replace the existing assumption that the same event ID becomes valid merely because the local request ID changed.
- [ ] Test server identity independently:

```javascript
assert.equal(
  matchesRunIdentity(
    { thread_id: 'thread-1', run_type: 'next_phase', request_id: 'request-a' },
    { threadId: 'thread-1', runType: 'next_phase', requestId: 'request-b' },
  ),
  false,
);
```

- [ ] Assert the tracker rejects the same `(thread_id, event_id)` replay even after local request state changes.
- [ ] Assert `reconcileSseCursor()` returns `null` when `request_id` changes inside the same thread.
- [ ] Run:

```text
node frontend/tests/runEvents.test.mjs
node frontend/tests/sseCursor.test.mjs
```

- [ ] Expected before implementation: at least the changed-request replay assertion fails.

### Task 4: Enforce Server-Supplied Run Identity in `useSSE`

**Files:**
- Modify: `frontend/src/types/api.ts`
- Modify: `frontend/src/lib/runEvents.ts`
- Modify: `frontend/src/lib/sseCursor.ts`
- Modify: `frontend/src/hooks/useSSE.ts`
- Test: `frontend/tests/runEvents.test.mjs`
- Test: `frontend/tests/sseCursor.test.mjs`

- [ ] Add a shared event metadata type:

```typescript
export interface AgentRunEventMeta {
  thread_id: string;
  run_type: 'initial' | 'next_phase';
  request_id: string;
  state_version: number;
}
```

- [ ] Parse payload JSON before accepting `plan_ready`, `done`, and `agent_error`.
- [ ] Compare payload identity with the active run. Never construct event identity from local `phaseRequestId`.
- [ ] At the beginning of every EventSource callback, reject a handler belonging to an old source:

```typescript
if (!isMounted || eventSourceRef.current !== es) return;
```

- [ ] Scope `lastEventIdRef` by `(threadId, runType, requestId)` and clear it whenever any part changes.
- [ ] Use the run-scoped `events_url` returned by the next-phase endpoint, or construct exactly the same query parameters.
- [ ] Only update the cursor after the event passes both source and run-identity checks.
- [ ] Pass the verified event metadata into `finishAgentRun()`; do not call it with implicit store identity.
- [ ] Run:

```text
node frontend/tests/runEvents.test.mjs
node frontend/tests/sseCursor.test.mjs
```

- [ ] Expected: stale handlers and mismatched events are ignored without changing app state.

### Task 5: Add a Shared Snapshot Latest-Request Gate

**Files:**
- Create: `frontend/src/store/snapshotRequestGate.ts`
- Create: `frontend/tests/snapshotRequestGate.test.mjs`
- Modify: `frontend/src/store/useAppStore.ts`

- [ ] Implement one gate shared by `alignState()` and `loadProjectSnapshot()`:

```typescript
export function createLatestRequestGate() {
  let latest = 0;
  return {
    begin() {
      const sequence = ++latest;
      return () => sequence === latest;
    },
    invalidate() {
      latest += 1;
    },
  };
}
```

- [ ] At the start of each snapshot request, call `begin()` and retain the returned `isCurrent` function.
- [ ] Before every `set()`, localStorage mutation, authentication recovery, or error write caused by that response, return early when `isCurrent()` is false.
- [ ] Invalidate the gate when switching projects, logging out, starting a new intent, or deleting the active project.
- [ ] Add a deterministic test:
  - begin request A
  - begin request B
  - assert A cannot write
  - assert B can write
  - invalidate
  - assert B can no longer write
- [ ] Run: `node frontend/tests/snapshotRequestGate.test.mjs`
- [ ] Expected: only the latest snapshot request is eligible to mutate state.

### Task 6: Prevent Late Phase1 Snapshots from Overwriting Phase2

**Files:**
- Modify: `frontend/src/store/useAppStore.ts`
- Modify: `frontend/tests/stateRestoration.test.mjs`

- [ ] Route both `alignState()` and `loadProjectSnapshot()` through the shared request gate.
- [ ] Add a regression test with deferred fetch promises:
  - request A starts and will return phase1
  - request B starts later and returns phase2 first
  - request B writes phase2
  - request A resolves last
  - committed state remains phase2
- [ ] Assert the late request cannot alter:
  - `committedTaskTree`
  - `previewTaskTree`
  - `previewMode`
  - `phaseRequestId`
  - `appState`
  - `view`
- [ ] Run: `node frontend/tests/stateRestoration.test.mjs`
- [ ] Expected: phase2 remains committed after the phase1 response arrives.

### Task 7: Make `finishAgentRun()` Require Commit Proof

**Files:**
- Modify: `frontend/src/store/useAppStore.ts`
- Modify: `frontend/tests/generationRun.test.mjs`

- [ ] Change the action signature to accept verified server metadata:

```typescript
finishAgentRun: (event: AgentRunEventMeta) => Promise<void>;
```

- [ ] For `next_phase`, return without clearing preview unless all conditions hold:
  - `event.run_type === 'next_phase'`
  - `event.request_id === phaseRequestId`
  - the latest snapshot envelope has `type === 'phase_generation_state'`
  - snapshot envelope `request_id === phaseRequestId`
  - snapshot envelope `status === 'confirmed'`
  - snapshot current phase differs from the phase recorded when generation started
  - planned tasks contain at least one AI task for the new current `phase_id`
- [ ] Record the base phase ID when next-phase generation starts and persist it with the existing phase run context so refresh during confirmation can perform the same check.
- [ ] Keep `previewMode`, `phaseRequestId`, and `previewTaskTree` intact while proof is incomplete.
- [ ] If commit proof remains incomplete after a bounded re-alignment attempt, show an actionable synchronization error and keep “返回当前计划 / 重试同步” available. Do not report success.
- [ ] Add tests for:
  - old request `done` does nothing
  - matching request with unconfirmed snapshot does not clear preview
  - matching confirmed snapshot that did not advance phase does not clear preview
  - matching confirmed snapshot with advanced phase but no phase tasks does not clear preview
  - matching confirmed snapshot with advanced phase and phase tasks commits phase2 and clears preview
- [ ] Run: `node frontend/tests/generationRun.test.mjs`
- [ ] Expected: preview is cleared only in the final proven-success case.

### Task 8: Add the End-to-End Regression Scenario

**Files:**
- Test: `tests/test_agent_routes_integration.py`
- Test: `frontend/tests/generationRun.test.mjs`
- Test: `frontend/tests/stateRestoration.test.mjs`

- [ ] Lock this sequence into automated evidence:

```text
phase1 run emits done
-> same thread starts next-phase request B
-> client connects without a usable cursor
-> request B emits plan_ready
-> user confirms request B
-> request B emits done
-> phase2 snapshot is loaded
-> an older phase1 snapshot resolves late
-> phase2 remains visible and Unlock Phase 2 does not reappear
```

- [ ] Assert no event from request A is accepted while request B is active.
- [ ] Assert phase2 tasks are present after confirmation.
- [ ] Assert preview clears only after the confirmed phase2 snapshot and tasks are visible.

### Task 9: Final Verification

**Files:**
- No additional source files

- [ ] Run backend targeted tests:

```text
python -m pytest tests/test_agent_runtime.py tests/test_agent_routes_integration.py tests/test_thread_repository.py tests/test_task_persistence.py -q
```

- [ ] Run the full backend suite:

```text
python -m pytest tests -q
```

- [ ] Run frontend state tests:

```text
node frontend/tests/runEvents.test.mjs
node frontend/tests/sseCursor.test.mjs
node frontend/tests/snapshotRequestGate.test.mjs
node frontend/tests/generationRun.test.mjs
node frontend/tests/stateRestoration.test.mjs
node frontend/tests/phaseStore.test.mjs
```

- [ ] Run frontend quality gates:

```text
cd frontend
npm run build
npm run lint
```

- [ ] Run: `git diff --check`
- [ ] Manual acceptance:
  - complete phase1
  - unlock phase2 on the same project page
  - disconnect/reconnect SSE once during generation
  - confirm phase2
  - verify phase2 appears and remains after refresh
  - verify phase1 never appears as the active phase again
  - verify `Unlock Phase 2` does not reappear

## Ownership

### Backend

- Tasks 1-2 and backend portion of Task 8
- Deliverable: run-scoped SSE contract and cross-run replay tests
- Must not change task persistence semantics that already passed strict next-phase tests

### Frontend

- Tasks 3-7 and frontend portion of Task 8
- Deliverable: server-owned run identity, stale-source rejection, snapshot write fencing, and commit-proof completion
- Must preserve inline next-phase loading and preview behavior

### Reviewer

- Reproduce the original `received_count=2 / saw_old_done=True / saw_phase2_plan_ready=False` probe and require the opposite result
- Review the full cross-run sequence, not only isolated store helpers
- Confirm there is no local fallback that relabels an event with the active request ID
- Confirm both snapshot functions share the same latest-request gate
- Confirm preview is not cleared on unproven completion

## RC Exit Gate

RC may proceed only when all are true:

- old-run terminal events cannot enter or terminate a new run stream
- all next-phase terminal and preview events carry authentic server request identity
- stale EventSource callbacks cannot mutate state
- stale snapshot responses cannot overwrite newer project state
- `finishAgentRun()` requires matching confirmed state, phase advancement, and visible phase tasks
- the cross-run integration scenario is automated
- backend/full frontend gates and manual refresh verification pass
