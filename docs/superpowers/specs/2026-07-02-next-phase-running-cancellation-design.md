# Next-Phase Running Cancellation Design

**Status:** Approved direction; confirmation boundary updated on 2026-07-02

**Release classification:** P1 RC blocker

**Goal:** Make “取消本次生成” work during next-phase generation without allowing a cancelled run to emit a usable preview, overwrite thread state, or persist tasks later.

## Context

The project page currently shows “取消本次生成” while the next-phase run is `THINKING` or `SYNCING`. The frontend calls:

```text
DELETE /api/threads/{thread_id}/phases/next/cancel
```

The backend only accepts an `awaiting_confirmation` `next_phase_review`. During generation, the thread holds a `phase_generation_state` with status `running`, so the visible cancellation action returns `409`.

The original Phase 2 rollback bug is already fixed. This design addresses only the remaining cancellation contract mismatch.

## Product Semantics

When the user cancels a running next-phase generation:

1. The user immediately returns to the committed project plan.
2. The current next-phase request becomes terminally `cancelled`.
3. The lease is released.
4. The frontend clears the matching `activeRun`, preview state, loading state, and persisted run context.
5. A late model result cannot become a preview, update the committed task tree, or persist tasks.
6. The same request may be cancelled repeatedly without producing an error.

Cancellation is request-scoped. It never means “cancel whichever run happens to be active.”

## Confirmation Boundary

Confirmation is the irreversible product boundary.

```text
THINKING -> cancellable generation
PENDING  -> cancellable preview
SYNCING  -> confirmed submission, not cancellable
```

After the user confirms a next-phase preview:

- the cancel action is hidden
- the status text becomes `正在追加到当前计划...`
- the user may choose `返回当前计划`
- returning dismisses only the waiting panel
- the confirmed run, `activeRun`, EventSource, and commit verification continue in the background
- when `done` arrives, the committed project updates normally

The existing `returnToCommittedPlan()` action must not be used for this purpose because it clears run identity and generation state. Add a dedicated UI-only action such as `dismissSyncingView()`.

## API Contract

Use the existing endpoint with an explicit request identity:

```text
DELETE /api/threads/{thread_id}/phases/next/cancel?request_id={request_id}
```

### Success

Return `200 ThreadSnapshot` when:

- the matching request is `phase_generation_state/running`
- the matching request is stalled
- the matching request is `next_phase_review/awaiting_confirmation`
- the matching request is already cancelled

The returned snapshot contains:

```json
{
  "status": "cancelled",
  "interrupt_payload": {
    "type": "phase_generation_state",
    "request_id": "request-id",
    "status": "cancelled",
    "history": {
      "request-id": {
        "status": "cancelled",
        "cancelled_at": "..."
      }
    }
  }
}
```

### Conflict

Return `409` when:

- `request_id` does not match the current or recorded request
- the request is already confirmed
- there is no cancellable next-phase lifecycle for that request

Return `404` when the thread does not belong to the current user.

## Backend State Transition

The repository performs cancellation inside one transaction while locking the thread row.

```text
phase_generation_state/running
        |
        v
phase_generation_state/cancelled

next_phase_review/awaiting_confirmation
        |
        v
phase_generation_state/cancelled
```

The transition also:

- sets the thread to its stable committed-plan state
- releases `lease_owner` and `lease_expires_at`
- records the cancelled request in history
- preserves the existing committed `task_tree`
- does not create, update, or delete tasks

Repeated cancellation of the same request returns the existing cancelled snapshot.

## Runtime Cancellation

Cancellation has two defenses.

### Fast In-Memory Stop

After the repository transaction succeeds, the route tells `AgentRuntime` to cancel the exact:

```text
(thread_id, run_type=next_phase, request_id)
```

The runtime marks that run cancelled, closes or detaches its subscribers, and ignores later non-terminal events for it.

This is an optimization for prompt user feedback and reduced event noise. It is not the source of truth because another worker may own the run.

### Authoritative Persistence Fence

Before persisting a next-phase interrupt or emitting `plan_ready`, the runtime checks the locked thread state.

Persistence is allowed only when:

- envelope request ID matches the run request ID
- envelope status is still `running`
- the request is not recorded as cancelled

If cancellation won the race:

- no preview is persisted
- no `plan_ready`, `done`, or `agent_error` is emitted for normal cancellation
- the graph result is discarded

This database fence protects multi-worker deployments, process restarts, and results that arrive after the in-memory cancellation signal.

## Frontend Behavior

`cancelPlanPreview()` sends the current `activeRun.requestId`.

It may call the endpoint only when:

- `activeRun.runType === "next_phase"`
- `activeRun.threadId === selectedProjectId`
- `appState === "THINKING"` or `appState === "PENDING"`

It must not call the endpoint when `appState === "SYNCING"`.

On `200`, the frontend atomically:

- restores `committedTaskTree` from the returned snapshot
- clears `activeRun`
- clears `previewMode`, `phaseRequestId`, `basePhaseId`, and `previewTaskTree`
- clears loading, stalled, and generation errors
- removes the matching localStorage keys
- remains on the selected project board

While the request is in flight, the cancel control is disabled to prevent duplicate clicks. Repeated server calls remain safe because cancellation is idempotent.

On `409`, the frontend re-aligns the thread snapshot:

- if confirmed, it loads the committed phase
- if cancelled, it performs normal cancellation cleanup
- otherwise it shows a concise state-conflict message and preserves the current project

### Dismissing the Confirmed Wait Panel

Add transient UI state:

```typescript
isSyncingViewDismissed: boolean;
```

`dismissSyncingView()` sets this value to `true` and does not modify:

- `activeRun`
- `previewMode`
- `phaseRequestId`
- `appState`
- `committedTaskTree`
- EventSource or cursor state

When dismissed, `PlanningOverview` renders the committed current-phase view instead of the SYNCING panel. The background run remains active.

Reset `isSyncingViewDismissed` to `false` when:

- a new generation starts
- a new preview becomes pending
- confirmation succeeds or fails
- cancellation succeeds
- the user starts a new intent
- the user changes projects

The dismissed flag does not need localStorage persistence. Refreshing during SYNCING may show the waiting panel again.

## Failure and Race Handling

### Cancellation Before Preview Persistence

The cancellation transaction wins. The persistence fence discards the late preview.

### Preview Persistence Before Cancellation

The thread is now `awaiting_confirmation`. The same endpoint cancels that preview and records the tombstone.

### Confirmation Before Cancellation

The frontend hides cancellation after confirmation. If a stale client still sends cancellation, the backend returns `409`; the frontend re-aligns and displays the confirmed state.

### Duplicate Cancellation

The same request returns `200` with the cancelled snapshot.

### Late SSE Event

The frontend has already cleared `activeRun`, so run-identity checks reject it. The backend runtime fence also suppresses normal late terminal events.

## Test Requirements

### Backend

- running next-phase cancellation returns `200`
- stalled next-phase cancellation returns `200`
- awaiting-confirmation cancellation remains supported
- duplicate cancellation is idempotent
- mismatched request returns `409`
- confirmed request cannot be cancelled
- cancellation preserves committed `task_tree` and tasks
- cancellation releases the lease
- late interrupt persistence after cancellation is rejected
- cancelled run does not emit `plan_ready`, `done`, or `agent_error`
- tenant ownership remains enforced
- OpenAPI requires `request_id`

### Frontend

- THINKING cancellation sends the active next-phase request ID
- PENDING cancellation sends the active next-phase request ID
- SYNCING does not render or invoke cancellation
- SYNCING renders `返回当前计划`
- dismissing SYNCING preserves `activeRun` and EventSource
- background completion still commits and displays Phase 2 after dismissal
- successful cancellation restores the committed board and clears all run state
- cancel control cannot double-submit
- `409` triggers state re-alignment
- a late event cannot mutate the board after cancellation
- the Hook-level test uses the real request-scoped endpoint contract rather than an unconditional `200` mock

### Manual

1. Complete Phase 1.
2. Unlock Phase 2.
3. Cancel while loading.
4. Confirm the current project remains visible.
5. Wait longer than the normal generation duration.
6. Confirm no preview appears and no new tasks are added.
7. Unlock Phase 2 again with a new request ID and confirm it works.
8. Confirm another Phase 2 preview, click `返回当前计划` during SYNCING, and verify Phase 2 appears after background completion.

## Non-Goals

- cancelling initial intent generation
- cancelling a next-phase request after confirmation
- forcibly terminating an in-flight provider HTTP request
- redesigning the next-phase preview
- changing Phase 2 task persistence
- adding user checkpoint input
- adding a general-purpose job scheduler

## RC Gate

The RC may proceed when:

- the original Phase 2 rollback regression remains green
- running cancellation returns `200` for the matching request
- a cancelled run cannot later persist or display a preview
- frontend and backend cancellation tests use the same request-scoped contract
- full backend, Hook, frontend Node, build, lint, and `git diff --check` gates pass
