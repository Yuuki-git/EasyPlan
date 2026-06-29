export interface SseCursorScope {
  previousThreadId: string | null;
  nextThreadId: string | null;
  currentLastEventId: string | null;
}

export function reconcileSseCursor({
  previousThreadId,
  nextThreadId,
  currentLastEventId,
}: SseCursorScope): string | null {
  if (!previousThreadId || previousThreadId !== nextThreadId) {
    return null;
  }
  return currentLastEventId;
}
