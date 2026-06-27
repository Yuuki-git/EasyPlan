export function createRunEventTracker() {
  const seen = new Set<string>();
  return {
    accept(eventId: string | null | undefined, threadId: string | null | undefined, requestId: string | null | undefined) {
      if (!eventId) return true;
      const key = `${eventId}:${threadId || ''}:${requestId || ''}`;
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
