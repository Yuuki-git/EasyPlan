import { useEffect, useRef } from 'react';
import { useAppStore, PreviewMode } from '../store/useAppStore';
import { createRunEventTracker, matchesRunIdentity, matchesActiveRun } from '../lib/runEvents';
import { getFriendlyErrorMessage } from '../lib/errorHelper';
import { reconcileSseCursor } from '../lib/sseCursor';
import { TaskTree, AgentRunEventMeta } from '../types/api';

export const useSSE = () => {
  const {
    addReasoningLog,
    setPreviewTaskTree,
    setAppState,
    setError,
    setNodeStatus,
    alignState,
    token,
    setView,
    finishAgentRun,
    setRunStalled,
    activeRun,
    sseReconnectNonce
  } = useAppStore();
  const eventSourceRef = useRef<EventSource | null>(null);
  const lastEventIdRef = useRef<Record<string, string | null>>({});
  const prevCursorKeyRef = useRef<string | null>(null);
  const reconnectTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const trackerRef = useRef(createRunEventTracker());
  const prevRequestIdRef = useRef<string | null>(null);
  const stallTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const activeRequestId = activeRun?.requestId || '';

  if (activeRequestId && activeRequestId !== prevRequestIdRef.current) {
    trackerRef.current.reset();
    prevRequestIdRef.current = activeRequestId;
  }

  const resetStallTimer = () => {
    if (stallTimerRef.current) {
      clearTimeout(stallTimerRef.current);
    }
    const currentStore = useAppStore.getState();
    if (!currentStore.activeRun) return;
    const { appState } = currentStore;
    if (appState === 'THINKING') {
      stallTimerRef.current = setTimeout(() => {
        setRunStalled(true);
      }, 10000);
    }
  };

  const clearStallTimer = () => {
    if (stallTimerRef.current) {
      clearTimeout(stallTimerRef.current);
      stallTimerRef.current = null;
    }
  };

  const handleEventActivity = () => {
    setRunStalled(false);
    resetStallTimer();
  };

  const activeThreadId = activeRun?.threadId || '';
  const activeRunType = activeRun?.runType || 'initial';

  const cursorKey = `${activeThreadId}:${activeRunType}:${activeRequestId}`;

  useEffect(() => {
    if (!activeRun) {
      if (eventSourceRef.current) {
        eventSourceRef.current.close();
        eventSourceRef.current = null;
      }
      clearStallTimer();
      if (reconnectTimerRef.current) {
        clearTimeout(reconnectTimerRef.current);
        reconnectTimerRef.current = null;
      }
      return;
    }

    const prevCursorKey = prevCursorKeyRef.current;
    if (cursorKey !== prevCursorKey) {
      const prevCursor = prevCursorKey ? (lastEventIdRef.current[prevCursorKey] || null) : null;

      const parsedPrev = prevCursorKey ? prevCursorKey.split(':') : [null, null, null];
      const prevThread = parsedPrev[0];
      const prevType = parsedPrev[1] as PreviewMode;
      const prevReq = parsedPrev[2];

      const nextCursor = reconcileSseCursor({
        previousThreadId: prevThread,
        nextThreadId: activeThreadId,
        previousRunType: prevType,
        nextRunType: activeRunType,
        previousRequestId: prevReq,
        nextRequestId: activeRequestId,
        currentLastEventId: prevCursor,
      });

      lastEventIdRef.current[cursorKey] = nextCursor;
      prevCursorKeyRef.current = cursorKey;
    }

    let isMounted = true;

    const scheduleReconnect = (delayMs: number) => {
      const currentStore = useAppStore.getState();
      if (currentStore.activeRun === null) return;
      if (reconnectTimerRef.current) {
        clearTimeout(reconnectTimerRef.current);
      }
      reconnectTimerRef.current = setTimeout(() => {
        reconnectTimerRef.current = null;
        if (isMounted) connect();
      }, delayMs);
    };

    const capturedRun = activeRun ? { ...activeRun } : null;

    async function connect() {
      if (eventSourceRef.current) {
        eventSourceRef.current.close();
        eventSourceRef.current = null;
      }

      if (!capturedRun) return;

      // 1. Align State first to ensure UI is in sync
      await alignState(activeThreadId);

      if (!isMounted) return;
      const currentStore = useAppStore.getState();
      if (!currentStore.activeRun || !matchesActiveRun(currentStore.activeRun, capturedRun)) {
        return;
      }

      handleEventActivity();

      // 2. Setup EventSource with Last-Event-ID for recovery
      const url = new URL(`/api/threads/${activeThreadId}/events`, window.location.origin);
      const cursor = lastEventIdRef.current[cursorKey] || null;
      if (cursor) {
        url.searchParams.set('last_event_id', cursor);
      }
      if (token) {
        url.searchParams.set('token', token);
      }
      url.searchParams.set('run_type', activeRunType);
      url.searchParams.set('request_id', activeRequestId);

      const es = new EventSource(url.toString());
      eventSourceRef.current = es;

      es.addEventListener('reasoning', (e) => {
        if (!isMounted || eventSourceRef.current !== es) return;
        if (!matchesActiveRun(useAppStore.getState().activeRun, capturedRun)) return;

        let parsedData: {
          thread_id?: string;
          run_type?: 'initial' | 'next_phase';
          request_id?: string;
          message?: string;
        } | null = null;
        try {
          parsedData = JSON.parse(e.data);
        } catch {
          // ignore parsing error
        }

        if (parsedData && parsedData.thread_id) {
          if (!matchesRunIdentity(
            {
              thread_id: parsedData.thread_id,
              run_type: parsedData.run_type || '',
              request_id: parsedData.request_id || ''
            },
            { threadId: activeThreadId, runType: activeRunType, requestId: activeRequestId }
          )) {
            return;
          }
        }

        if (!trackerRef.current.accept(e.lastEventId, activeThreadId)) {
          return;
        }
        handleEventActivity();
        lastEventIdRef.current[cursorKey] = e.lastEventId;
        try {
          const data = parsedData || JSON.parse(e.data);
          addReasoningLog(data.message || JSON.stringify(data));
        } catch {
          addReasoningLog(e.data);
        }
      });

      es.addEventListener('plan_ready', (e) => {
        if (!isMounted || eventSourceRef.current !== es) return;
        if (!matchesActiveRun(useAppStore.getState().activeRun, capturedRun)) return;

        let parsedData: { thread_id?: string; run_type?: 'initial' | 'next_phase'; request_id?: string; task_tree?: TaskTree } | null = null;
        try {
          parsedData = JSON.parse(e.data);
        } catch {
          // ignore parsing error
        }

        if (
          !parsedData ||
          !matchesRunIdentity(
            { thread_id: parsedData.thread_id || '', run_type: parsedData.run_type || '', request_id: parsedData.request_id || '' },
            { threadId: activeThreadId, runType: activeRunType, requestId: activeRequestId }
          )
        ) {
          return;
        }

        if (!trackerRef.current.accept(e.lastEventId, activeThreadId)) {
          return;
        }
        handleEventActivity();
        lastEventIdRef.current[cursorKey] = e.lastEventId;
        if (parsedData.task_tree) {
          setPreviewTaskTree(parsedData.task_tree);
        }
        setAppState('PENDING');
        clearStallTimer();
      });

      es.addEventListener('sync_status', (e) => {
        if (!isMounted || eventSourceRef.current !== es) return;
        if (!matchesActiveRun(useAppStore.getState().activeRun, capturedRun)) return;

        if (!trackerRef.current.accept(e.lastEventId, activeThreadId)) {
          return;
        }
        handleEventActivity();
        lastEventIdRef.current[cursorKey] = e.lastEventId;
        const { node_id, status } = JSON.parse(e.data);
        setNodeStatus(node_id, status);
      });

      es.addEventListener('sync_complete', (e) => {
        if (!isMounted || eventSourceRef.current !== es) return;
        if (!matchesActiveRun(useAppStore.getState().activeRun, capturedRun)) return;

        if (!trackerRef.current.accept(e.lastEventId, activeThreadId)) {
          return;
        }
        handleEventActivity();
        lastEventIdRef.current[cursorKey] = e.lastEventId;
        const { status } = JSON.parse(e.data);
        setAppState(status === 'success' ? 'SUCCESS' : 'PARTIAL_ERROR');
        clearStallTimer();
      });

      es.addEventListener('done', async (e) => {
        if (!isMounted || eventSourceRef.current !== es) return;
        if (!matchesActiveRun(useAppStore.getState().activeRun, capturedRun)) return;

        let parsedData: AgentRunEventMeta | null = null;
        try {
          parsedData = JSON.parse(e.data);
        } catch {
          // ignore parsing error
        }

        if (!parsedData || !matchesRunIdentity(parsedData, { threadId: activeThreadId, runType: activeRunType, requestId: activeRequestId })) {
          return;
        }

        if (!trackerRef.current.accept(e.lastEventId, activeThreadId)) {
          return;
        }
        clearStallTimer();
        lastEventIdRef.current[cursorKey] = e.lastEventId;

        await finishAgentRun(parsedData);

        const currentStore = useAppStore.getState();
        if (!currentStore.previewMode) {
          setAppState('SUCCESS');
          es.close();
          if (eventSourceRef.current === es) {
            eventSourceRef.current = null;
          }
        }
      });

      es.addEventListener('snapshot_required', async () => {
        if (!isMounted || eventSourceRef.current !== es) return;
        if (!matchesActiveRun(useAppStore.getState().activeRun, capturedRun)) return;

        console.warn('SSE Snapshot Required. Re-aligning state and reconnecting...');
        lastEventIdRef.current[cursorKey] = null;
        es.close();
        if (eventSourceRef.current === es) {
          eventSourceRef.current = null;
        }
        clearStallTimer();
        await alignState(activeThreadId);
        scheduleReconnect(250);
      });

      es.addEventListener('agent_error', (e) => {
        if (!isMounted || eventSourceRef.current !== es) return;
        if (!matchesActiveRun(useAppStore.getState().activeRun, capturedRun)) return;

        let parsedData: { thread_id?: string; run_type?: 'initial' | 'next_phase'; request_id?: string; message?: string; code?: string } | null = null;
        try {
          parsedData = JSON.parse(e.data);
        } catch {
          // ignore parsing error
        }

        if (
          !parsedData ||
          !matchesRunIdentity(
            { thread_id: parsedData.thread_id || '', run_type: parsedData.run_type || '', request_id: parsedData.request_id || '' },
            { threadId: activeThreadId, runType: activeRunType, requestId: activeRequestId }
          )
        ) {
          return;
        }

        if (!trackerRef.current.accept(e.lastEventId, activeThreadId)) {
          return;
        }
        clearStallTimer();
        lastEventIdRef.current[cursorKey] = e.lastEventId;
        setAppState('ERROR');
        try {
          const rawMsg = parsedData.message || parsedData.code || 'An error occurred';
          setError(getFriendlyErrorMessage(rawMsg));
        } catch {
          setError(getFriendlyErrorMessage(e.data));
        }
        es.close();
        if (eventSourceRef.current === es) {
          eventSourceRef.current = null;
        }
      });

      es.addEventListener('error', () => {
        if (!isMounted || eventSourceRef.current !== es) return;
        if (!matchesActiveRun(useAppStore.getState().activeRun, capturedRun)) return;

        console.warn('SSE Disconnected. Attempting to align and reconnect...');
        es.close();
        if (eventSourceRef.current === es) {
          eventSourceRef.current = null;
        }
        clearStallTimer();
        scheduleReconnect(3000);
      });
    }

    connect();

    return () => {
      isMounted = false;
      if (eventSourceRef.current) {
        eventSourceRef.current.close();
        eventSourceRef.current = null;
      }
      if (reconnectTimerRef.current) {
        clearTimeout(reconnectTimerRef.current);
        reconnectTimerRef.current = null;
      }
      clearStallTimer();
    };
  }, [
    activeThreadId,
    activeRunType,
    activeRequestId,
    cursorKey,
    addReasoningLog,
    setPreviewTaskTree,
    setAppState,
    setError,
    setNodeStatus,
    alignState,
    token,
    setView,
    finishAgentRun,
    setRunStalled,
    activeRun,
    sseReconnectNonce
  ]);
};
