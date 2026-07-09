export interface RunIdentityScope {
  threadId: string | null | undefined;
  runType: 'initial' | 'next_phase' | 'refine' | null | undefined;
  requestId: string | null | undefined;
}

export function matchesRunIdentity(
  event: { thread_id: string; run_type: string; request_id: string } | null | undefined,
  scope: RunIdentityScope
): boolean {
  if (!event || !scope) return false;
  return (
    event.thread_id === scope.threadId &&
    event.run_type === scope.runType &&
    event.request_id === scope.requestId
  );
}

export function matchesActiveRun(
  run1: { threadId: string; runType: string; requestId: string } | null | undefined,
  run2: { threadId: string; runType: string; requestId: string } | null | undefined
): boolean {
  if (!run1 || !run2) return false;
  return (
    run1.threadId === run2.threadId &&
    run1.runType === run2.runType &&
    run1.requestId === run2.requestId
  );
}

export function createRunEventTracker() {
  const seen = new Set<string>();
  return {
    accept(eventId: string | null | undefined, threadId: string | null | undefined) {
      if (!eventId) return true;
      const key = `${eventId}:${threadId || ''}`;
      if (seen.has(key)) return false;
      seen.add(key);
      return true;
    },
    reset() {
      seen.clear();
    },
  };
}

export const RUN_STALL_THRESHOLD_MS = 30_000;

export function isRunStalled(lastActivityAt: number | null, now: number, thresholdMs = RUN_STALL_THRESHOLD_MS) {
  return lastActivityAt !== null && now - lastActivityAt >= thresholdMs;
}
