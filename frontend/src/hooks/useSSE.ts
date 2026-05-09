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
    token
  } = useAppStore();
  const eventSourceRef = useRef<EventSource | null>(null);
  const lastEventIdRef = useRef<string | null>(null);

  useEffect(() => {
    if (!threadId) {
      if (eventSourceRef.current) {
        eventSourceRef.current.close();
        eventSourceRef.current = null;
      }
      return;
    }

    let isMounted = true;

    const connect = async () => {
      // 1. Align State first to ensure UI is in sync
      await alignState(threadId);

      if (!isMounted) return;

      // 2. Setup EventSource with Last-Event-ID for recovery
      const url = new URL(`/api/threads/${threadId}/events`, window.location.origin);
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
        
        // If all nodes done, check for partial errors or success
        // This logic is usually driven by a final 'sync_complete' event
      });

      es.addEventListener('sync_complete', (e) => {
        lastEventIdRef.current = e.lastEventId;
        const { status } = JSON.parse(e.data);
        setAppState(status === 'success' ? 'SUCCESS' : 'PARTIAL_ERROR');
      });

      es.addEventListener('done', () => {
        setAppState('SUCCESS');
        es.close();
      });

      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      es.addEventListener('agent_error', (e: any) => {
        try {
          const data = JSON.parse(e.data);
          setError(data.message || data.code || 'An error occurred');
        } catch {
          setError(e.data);
        }
        es.close();
      });

      es.addEventListener('error', () => {
        console.warn('SSE Disconnected. Attempting to align and reconnect...');
        es.close();
        setTimeout(() => {
          if (isMounted) connect();
        }, 3000); // Exponential backoff could be better
      });
    };

    connect();

    return () => {
      isMounted = false;
      if (eventSourceRef.current) {
        eventSourceRef.current.close();
        eventSourceRef.current = null;
      }
    };
  }, [threadId, addReasoningLog, setTaskTree, setAppState, setError, setNodeStatus, alignState, token]);
};
