import { useEffect, useRef } from 'react';
import { useAppStore, PreviewMode } from '../store/useAppStore';
import { RUN_STALL_THRESHOLD_MS, createRunEventTracker, matchesRunIdentity, matchesActiveRun } from '../lib/runEvents';
import { getFriendlyErrorMessage } from '../lib/errorHelper';
import { reconcileSseCursor } from '../lib/sseCursor';
import { AgentRunEventMeta } from '../types/api';

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
      }, RUN_STALL_THRESHOLD_MS);
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

      const processSSEEvent = (e: MessageEvent) => {
        if (!isMounted || eventSourceRef.current !== es) return null;
        if (!matchesActiveRun(useAppStore.getState().activeRun, capturedRun)) return null;

        let envelope: any = null; // eslint-disable-line @typescript-eslint/no-explicit-any
        try {
          envelope = JSON.parse(e.data);
        } catch {
          console.error("Failed to parse SSE event data");
          return null;
        }

        if (!envelope) return null;

        // Backward compatibility mapping: check both root and payload
        const thread_id = envelope.thread_id || envelope.payload?.thread_id || '';
        const run_type = envelope.run_type || envelope.payload?.run_type || '';
        const request_id = envelope.request_id || envelope.payload?.request_id || '';

        if (!matchesRunIdentity(
          { thread_id, run_type, request_id },
          { threadId: activeThreadId, runType: activeRunType, requestId: activeRequestId }
        )) {
          return null;
        }

        if (!trackerRef.current.accept(e.lastEventId, activeThreadId)) {
          return null;
        }

        handleEventActivity();
        lastEventIdRef.current[cursorKey] = e.lastEventId;

        return envelope;
      };

      // 1. Stage Events (run_started, intent_profile_started, intent_profile_completed, strategy_selected, planning_started, validation_started, repair_started, persistence_started, still_running)
      const stageEvents = [
        'run_started',
        'intent_profile_started',
        'intent_profile_completed',
        'strategy_selected',
        'planning_started',
        'validation_started',
        'repair_started',
        'persistence_started',
        'still_running'
      ];
      stageEvents.forEach(evtType => {
        es.addEventListener(evtType, (e) => {
          const envelope = processSSEEvent(e);
          if (!envelope) return;

          const label = envelope.payload?.label || envelope.payload?.stage || evtType;
          useAppStore.setState((state) => {
            const alreadyExists = state.recentEvents.includes(label);
            const newEvents = alreadyExists ? state.recentEvents : [...state.recentEvents, label];
            return {
              currentStage: label,
              recentEvents: newEvents,
              reasoningLogs: [...state.reasoningLogs, label]
            };
          });
        });
      });

      // Backward compatibility for old reasoning event
      es.addEventListener('reasoning', (e) => {
        const envelope = processSSEEvent(e);
        if (!envelope) return;

        const message = envelope.payload?.message || envelope.message || e.data;
        useAppStore.setState((state) => ({
          currentStage: message,
          recentEvents: state.recentEvents.includes(message) ? state.recentEvents : [...state.recentEvents, message],
          reasoningLogs: [...state.reasoningLogs, message]
        }));
      });

      es.addEventListener('plan_ready', (e) => {
        const envelope = processSSEEvent(e);
        if (!envelope) return;

        const task_tree = envelope.payload?.task_tree !== undefined ? envelope.payload.task_tree : envelope.task_tree;
        if (task_tree) {
          setPreviewTaskTree(task_tree);
        }
        setAppState('PENDING');
        clearStallTimer();

        const label = envelope.payload?.label || '已生成预览计划';
        useAppStore.setState((state) => ({
          currentStage: label,
          recentEvents: state.recentEvents.includes(label) ? state.recentEvents : [...state.recentEvents, label],
          reasoningLogs: [...state.reasoningLogs, label],
          isProcessPanelExpanded: false,
        }));
      });

      es.addEventListener('sync_status', (e) => {
        const envelope = processSSEEvent(e);
        if (!envelope) return;

        const node_id = envelope.payload?.node_id !== undefined ? envelope.payload.node_id : envelope.node_id;
        const status = envelope.payload?.status !== undefined ? envelope.payload.status : envelope.status;
        if (node_id !== undefined && status !== undefined) {
          setNodeStatus(node_id, status);
        }

        const label = envelope.payload?.label || envelope.payload?.stage || '正在同步计划...';
        useAppStore.setState((state) => {
          const alreadyExists = state.recentEvents.includes(label);
          const newEvents = alreadyExists ? state.recentEvents : [...state.recentEvents, label];
          return {
            currentStage: label,
            recentEvents: newEvents,
            reasoningLogs: [...state.reasoningLogs, label]
          };
        });
      });

      es.addEventListener('sync_complete', (e) => {
        const envelope = processSSEEvent(e);
        if (!envelope) return;

        const status = envelope.payload?.status !== undefined ? envelope.payload.status : envelope.status;
        const isSuccess = status === undefined || status === 'success';
        setAppState(isSuccess ? 'SUCCESS' : 'PARTIAL_ERROR');
        clearStallTimer();

        const label = envelope.payload?.label || envelope.payload?.stage || (isSuccess ? '已完成计划同步' : '同步出现部分错误');
        useAppStore.setState((state) => ({
          currentStage: label,
          recentEvents: state.recentEvents.includes(label) ? state.recentEvents : [...state.recentEvents, label],
          reasoningLogs: [...state.reasoningLogs, label]
        }));
      });

      es.addEventListener('done', async (e) => {
        const envelope = processSSEEvent(e);
        if (!envelope) return;

        const state_version = envelope.state_version !== undefined ? envelope.state_version : (envelope.payload?.state_version !== undefined ? envelope.payload.state_version : 0);
        const parsedData: AgentRunEventMeta = {
          thread_id: envelope.thread_id || envelope.payload?.thread_id || '',
          run_type: (envelope.run_type || envelope.payload?.run_type || '') as 'initial' | 'next_phase' | 'refine',
          request_id: envelope.request_id || envelope.payload?.request_id || '',
          state_version: state_version
        };

        clearStallTimer();
        await finishAgentRun(parsedData);

        const currentStore = useAppStore.getState();
        if (!currentStore.previewMode) {
          setAppState('SUCCESS');
          es.close();
          if (eventSourceRef.current === es) {
            eventSourceRef.current = null;
          }
        }

        const label = '规划已结束';
        useAppStore.setState((state) => ({
          currentStage: label,
          recentEvents: state.recentEvents.includes(label) ? state.recentEvents : [...state.recentEvents, label],
          reasoningLogs: [...state.reasoningLogs, label],
          isProcessPanelExpanded: false,
        }));
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
        const envelope = processSSEEvent(e);
        if (!envelope) return;

        clearStallTimer();
        setAppState('ERROR');
        const rawMsg = envelope.payload?.message || envelope.message || envelope.payload?.code || envelope.code || 'An error occurred';
        const friendlyMsg = getFriendlyErrorMessage(rawMsg);
        setError(friendlyMsg);
        useAppStore.setState({ lastRunErrorSummary: friendlyMsg });

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
