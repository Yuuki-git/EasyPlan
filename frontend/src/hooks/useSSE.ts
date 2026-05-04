import { useEffect, useRef } from 'react';
import { useAppStore } from '../store/useAppStore';

export const useSSE = () => {
  const { threadId, addReasoningLog, setTaskTree, setAppState, setError } = useAppStore();
  const eventSourceRef = useRef<EventSource | null>(null);

  useEffect(() => {
    if (!threadId) {
      if (eventSourceRef.current) {
        eventSourceRef.current.close();
        eventSourceRef.current = null;
      }
      return;
    }

    // Close existing connection if any
    if (eventSourceRef.current) {
      eventSourceRef.current.close();
    }

    const url = `/api/threads/${threadId}/events`;
    const es = new EventSource(url);
    eventSourceRef.current = es;

    es.addEventListener('reasoning', (e) => {
      try {
        const data = JSON.parse(e.data);
        addReasoningLog(data.content || data);
      } catch {
        addReasoningLog(e.data);
      }
    });

    es.addEventListener('plan_ready', (e) => {
      const data = JSON.parse(e.data);
      setTaskTree(data);
      setAppState('PENDING');
    });

    es.addEventListener('error', (e: any) => {
      console.error('SSE Error:', e);
      // Don't set global error yet, SSE often retries automatically
    });

    es.addEventListener('node_start', (e) => {
      const data = JSON.parse(e.data);
      addReasoningLog(`Step: ${data.node}`);
    });

    return () => {
      es.close();
      eventSourceRef.current = null;
    };
  }, [threadId, addReasoningLog, setTaskTree, setAppState, setError]);
};
