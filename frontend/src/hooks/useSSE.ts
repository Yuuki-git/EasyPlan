import { useEffect, useRef } from 'react';
import { useAppStore } from '../store/useAppStore';

export const useSSE = () => {
  const { 
    threadId, 
    addReasoningLog, 
    setTaskTree, 
    setAppState, 
    setError, 
    setNodeStatus,
    alignState,
    token,
    setView
  } = useAppStore();
  const eventSourceRef = useRef<EventSource | null>(null);
  const lastEventIdRef = useRef<string | null>(null);
  const prevThreadIdRef = useRef<string | null>(null);
  const reconnectTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    if (threadId !== prevThreadIdRef.current) {
      lastEventIdRef.current = null;
      prevThreadIdRef.current = threadId;
    }

    if (!threadId) {
      if (eventSourceRef.current) {
        eventSourceRef.current.close();
        eventSourceRef.current = null;
      }
      return;
    }

    const activeThreadId = threadId;
    let isMounted = true;

    const scheduleReconnect = (delayMs: number) => {
      if (reconnectTimerRef.current) {
        clearTimeout(reconnectTimerRef.current);
      }
      reconnectTimerRef.current = setTimeout(() => {
        reconnectTimerRef.current = null;
        if (isMounted) connect();
      }, delayMs);
    };

    async function connect() {
      if (eventSourceRef.current) {
        eventSourceRef.current.close();
        eventSourceRef.current = null;
      }

      // 1. Align State first to ensure UI is in sync
      await alignState(activeThreadId);

      if (!isMounted) return;

      // 2. Setup EventSource with Last-Event-ID for recovery
      const url = new URL(`/api/threads/${activeThreadId}/events`, window.location.origin);
      if (lastEventIdRef.current) {
        url.searchParams.set('last_event_id', lastEventIdRef.current);
      }
      if (token) {
        url.searchParams.set('token', token);
      }
      
      const es = new EventSource(url.toString());
      eventSourceRef.current = es;

      es.addEventListener('reasoning', (e) => {
        lastEventIdRef.current = e.lastEventId;
        try {
          const data = JSON.parse(e.data);
          addReasoningLog(data.message || JSON.stringify(data));
        } catch {
          addReasoningLog(e.data);
        }
      });

      es.addEventListener('plan_ready', (e) => {
        lastEventIdRef.current = e.lastEventId;
        const data = JSON.parse(e.data);
        setTaskTree(data.task_tree);
        setAppState('PENDING');
      });

      es.addEventListener('sync_status', (e) => {
        lastEventIdRef.current = e.lastEventId;
        const { node_id, status } = JSON.parse(e.data);
        setNodeStatus(node_id, status);
      });

      es.addEventListener('sync_complete', (e) => {
        lastEventIdRef.current = e.lastEventId;
        const { status } = JSON.parse(e.data);
        setAppState(status === 'success' ? 'SUCCESS' : 'PARTIAL_ERROR');
      });

      es.addEventListener('done', (e) => {
        lastEventIdRef.current = e.lastEventId;
        setAppState('SUCCESS');
        setView('board');
        es.close();
        if (eventSourceRef.current === es) {
          eventSourceRef.current = null;
        }
      });

      es.addEventListener('snapshot_required', async () => {
        console.warn('SSE Snapshot Required. Re-aligning state and reconnecting...');
        lastEventIdRef.current = null;
        es.close();
        if (eventSourceRef.current === es) {
          eventSourceRef.current = null;
        }
        await alignState(activeThreadId);
        scheduleReconnect(250);
      });

      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      es.addEventListener('agent_error', (e: any) => {
        lastEventIdRef.current = e.lastEventId;
        try {
          const data = JSON.parse(e.data);
          setError(data.message || data.code || 'An error occurred');
        } catch {
          setError(e.data);
        }
        es.close();
        if (eventSourceRef.current === es) {
          eventSourceRef.current = null;
        }
      });

      es.addEventListener('error', () => {
        if (eventSourceRef.current !== es) {
          return;
        }
        console.warn('SSE Disconnected. Attempting to align and reconnect...');
        es.close();
        eventSourceRef.current = null;
        scheduleReconnect(3000); // Exponential backoff could be better
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
    };
  }, [threadId, addReasoningLog, setTaskTree, setAppState, setError, setNodeStatus, alignState, token, setView]);
};
