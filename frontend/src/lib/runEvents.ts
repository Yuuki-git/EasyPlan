export interface RunIdentityScope {
  threadId: string | null | undefined;
  runType: 'initial' | 'next_phase' | null | undefined;
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

export function isRunStalled(lastActivityAt: number | null, now: number, thresholdMs = 10_000) {
  return lastActivityAt !== null && now - lastActivityAt >= thresholdMs;
}
