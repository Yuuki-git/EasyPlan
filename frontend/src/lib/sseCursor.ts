export interface SseCursorScope {
  previousThreadId: string | null;
  nextThreadId: string | null;
  previousRunType: 'initial' | 'next_phase' | null;
  nextRunType: 'initial' | 'next_phase' | null;
  previousRequestId: string | null;
  nextRequestId: string | null;
  currentLastEventId: string | null;
}

export function reconcileSseCursor({
  previousThreadId,
  nextThreadId,
  previousRunType,
  nextRunType,
  previousRequestId,
  nextRequestId,
  currentLastEventId,
}: SseCursorScope): string | null {
  if (
    !previousThreadId ||
    previousThreadId !== nextThreadId ||
    previousRunType !== nextRunType ||
    previousRequestId !== nextRequestId
  ) {
    return null;
  }
  return currentLastEventId;
}
