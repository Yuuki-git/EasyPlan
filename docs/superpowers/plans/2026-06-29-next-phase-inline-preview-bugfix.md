# Next-Phase Inline Preview Bugfix Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Keep next-phase generation inside the current project board by replacing the current phase panel in place instead of navigating to the full generation page.

**Architecture:** Reuse the existing same-thread preview contract and interrupt-payload ownership. Restrict the fix to frontend state and rendering so `previewMode === 'next_phase'` becomes a board-scoped inline state rather than an input-page transition.

**Tech Stack:** React, TypeScript, Zustand, Framer Motion, Node-based frontend tests

---

## File Map

- Modify: `frontend/src/App.tsx`
  - stop mapping next-phase preview to the input page
- Modify: `frontend/src/store/useAppStore.ts`
  - keep `view = 'board'` during next-phase generation
  - preserve inline next-phase restoration on refresh
- Modify: `frontend/src/components/PlanningOverview.tsx`
  - replace current phase card with inline loading or inline preview
- Modify: `frontend/src/components/ActionLayer.tsx`
  - prevent next-phase from using the full-screen generation controls as the main UI surface
- Test: `frontend/tests/generationRun.test.mjs`
  - assert next-phase keeps board view
- Test: `frontend/tests/stateRestoration.test.mjs`
  - assert refresh restores inline next-phase board state

## Task 1: Keep Next-Phase on the Board

**Files:**
- Modify: `frontend/src/store/useAppStore.ts`
- Modify: `frontend/src/App.tsx`
- Test: `frontend/tests/generationRun.test.mjs`

- [ ] Update `generateNextPhasePlan()` so starting next-phase generation no longer sets `view: 'input'`; keep the current board view and only mark next-phase preview state.
- [ ] Update `App.tsx` so `previewMode === 'next_phase'` does not force `currentView` back to the input surface.
- [ ] Add a regression test proving that unlocking next phase keeps `view === 'board'` while `previewMode === 'next_phase'`.
- [ ] Run: `node frontend/tests/generationRun.test.mjs`
- [ ] Expected: next-phase board-state assertions pass.

## Task 2: Render Inline Loading and Inline Preview

**Files:**
- Modify: `frontend/src/components/PlanningOverview.tsx`
- Modify: `frontend/src/components/ActionLayer.tsx`

- [ ] Add a dedicated inline next-phase loading card inside the current phase slot of `PlanningOverview`.
- [ ] Add an inline next-phase preview rendering path inside the same area once preview data exists.
- [ ] Keep roadmap, sidebar, and task list visible while next-phase is generating.
- [ ] Reduce `ActionLayer` responsibility for next-phase so it no longer behaves like the primary full-screen surface for that flow.
- [ ] Manually verify in browser: clicking `Unlock Phase N` leaves the user on the project page and swaps only the current phase area.

## Task 3: Preserve Cancel, Confirm, and Refresh Behavior

**Files:**
- Modify: `frontend/src/store/useAppStore.ts`
- Test: `frontend/tests/stateRestoration.test.mjs`

- [ ] Keep the committed board state intact while next-phase generation is running.
- [ ] Ensure `cancelPlanPreview()` restores the committed project board in place.
- [ ] Ensure `confirmPlan()` appends the preview and leaves the user on the same board.
- [ ] Add refresh recovery coverage for:
  - running next-phase generation on board
  - pending next-phase preview on board
- [ ] Run: `node frontend/tests/stateRestoration.test.mjs`
- [ ] Expected: both running and pending next-phase states restore back into board-scoped UI.

## Task 4: Final Verification

**Files:**
- No additional source files

- [ ] Run: `cd frontend && npm run build`
- [ ] Expected: build passes.
- [ ] Run: `cd frontend && npm run lint`
- [ ] Expected: lint passes.
- [ ] Manual acceptance:
  - click `Unlock Phase N`
  - confirm board does not navigate away
  - confirm current phase card becomes loading
  - confirm preview appears in place
  - confirm cancel restores committed board
  - confirm append updates the same project
  - confirm refresh during running preview returns to board, not input
  - confirm refresh during pending preview returns to board, not input

## Reviewer Checklist

- [ ] `Unlock Phase N` no longer routes to the full generation page
- [ ] `view` remains `board` throughout next-phase generation
- [ ] `Current Phase` is replaced in place by loading and then preview
- [ ] Sidebar, roadmap, and task list remain visible during next-phase generation
- [ ] Cancel restores the committed board in place
- [ ] Confirm appends into the same project and stays on board
- [ ] Refresh recovery for running and pending next-phase states stays inside board UI
