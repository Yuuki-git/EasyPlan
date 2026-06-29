# EasyPlan Next-Phase Inline Preview Bugfix Design

Status: approved for implementation
Date: 2026-06-29
Target: v1.2.5.x bugfix line before v1.2.6
Primary provider: DeepSeek

## Summary

This bugfix changes the "Unlock Next Phase" experience from a full generation-page transition into an inline continuation flow inside the current project board.

The user should remain inside the current project. When they click "Unlock Next Phase", the current phase panel is replaced in-place by a lightweight loading state. When generation completes, that same area becomes a next-phase preview. The user can then append the preview to the current plan or cancel it.

## Problem

Today the next-phase flow incorrectly reuses the first-plan generation surface:

- the store sets `view = 'input'` when next-phase generation starts
- the app forces `previewMode === 'next_phase'` back into the input/generation surface
- the user feels like they left the project and started a new planning run

This conflicts with the product meaning of next-phase planning:

- it is same-thread continuation
- it should preserve committed board state
- it should feel like continuing the current project, not regenerating the whole plan

## Product Decision

Approved interaction:

1. User clicks `Unlock Phase N`
2. Page stays on the current project board
3. `Current Phase` panel switches in-place to a lightweight loading state
4. No full-screen reasoning surface is shown
5. When preview is ready, that same panel becomes `Next Phase Preview`
6. User chooses:
   - `Append to Current Plan`
   - `Cancel`
7. Confirming appends the next phase into the same thread and keeps the user on the board
8. Canceling restores the committed project state in place

## Explicit UX Rules

### 1. Stay on board

Next-phase generation must not switch `view` from `board` to `input`.

### 2. Replace only the phase panel

The following project context must remain visible:

- sidebar
- roadmap
- project task area
- other board scaffolding

Only the current phase area is replaced.

### 3. Use lightweight loading

During generation, show only:

- a short loading message
- cancel
- optional return-to-committed-plan if still meaningful in the local UI structure

Do not show the full initial-plan reasoning stream.

### 4. Treat preview as server-owned

The server-owned next-phase preview model remains unchanged:

- preview lives in `interrupt_payload`
- committed plan remains the board truth until confirmation

This bugfix changes rendering location, not preview ownership.

### 5. Refresh recovery stays inline

If the user refreshes while next-phase generation is running or awaiting confirmation:

- the app must restore the board
- the current phase area must return to the loading or preview state
- the app must not fall back to the input/generation page

## Non-goals

This bugfix does not include:

- adding user-supplied checkpoint inputs before next-phase generation
- redesigning roadmap strategy
- changing backend request/response contract
- introducing new preview schema
- changing initial-plan generation flow

Those belong to later product upgrades.

## State Model

For this bugfix, next-phase should be interpreted as:

- same board view
- temporary inline replacement inside planning overview
- committed tasks still preserved underneath until confirmation

Recommended frontend interpretation:

- `view` remains `board`
- `previewMode === 'next_phase'` means "board-scoped preview state", not "switch to input surface"
- `appState === 'THINKING'` with next-phase preview means inline loading
- `appState === 'PENDING'` with next-phase preview means inline preview awaiting confirmation

## Main File Boundaries

- `frontend/src/App.tsx`
  - stop forcing next-phase preview into the input page
- `frontend/src/store/useAppStore.ts`
  - keep board view during next-phase generation
  - preserve committed board state and restore inline preview state on refresh
- `frontend/src/components/PlanningOverview.tsx`
  - render current-phase card, inline loading card, and inline preview card
- `frontend/src/components/ActionLayer.tsx`
  - avoid treating next-phase as a full-screen generation flow
- `frontend/tests/stateRestoration.test.mjs`
  - add refresh recovery coverage for inline next-phase board state
- `frontend/tests/generationRun.test.mjs`
  - add store-level next-phase board-state assertions

## Acceptance Criteria

1. Clicking `Unlock Phase N` does not navigate away from the project board.
2. The current phase panel changes in place to a loading state.
3. The full initial generation page is not shown.
4. When preview is ready, the next-phase preview appears in the current phase area.
5. `Append to Current Plan` updates the same project in place.
6. `Cancel` restores committed board state in place.
7. Refresh during running preview restores the board and the inline loading state.
8. Refresh during pending preview restores the board and the inline preview state.
