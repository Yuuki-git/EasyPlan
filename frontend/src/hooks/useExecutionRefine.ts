import { useEffect, useRef } from 'react';
import { useAppStore } from '../store/useAppStore';
import { getFriendlyErrorMessage } from '../lib/errorHelper';
import { SSEEventEnvelope } from '../types/api';

const STALL_THRESHOLD_MS = 15000;

export const useExecutionRefine = () => {
  const {
    token,
    selectedProjectId,
    executionRefineActiveRequestId,
    setExecutionRefineStatus,
    setExecutionRefineStage,
    setExecutionRefineProposal,
    setExecutionRefineErrorCode,
    setExecutionRefineErrorMessage,
    addExecutionRefineLog,
    isExecutionRefinePanelOpen
  } = useAppStore();

  const eventSourceRef = useRef<EventSource | null>(null);
  const lastEventIdRef = useRef<string | null>(null);
  const reconnectTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const stallTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const isMountedRef = useRef<boolean>(true);
  const processedEventIdsRef = useRef<Set<string>>(new Set());

  useEffect(() => {
    processedEventIdsRef.current.clear();
  }, [selectedProjectId, executionRefineActiveRequestId]);

  const resetStallTimer = () => {
    if (stallTimerRef.current) {
      clearTimeout(stallTimerRef.current);
    }
    stallTimerRef.current = setTimeout(() => {
      addExecutionRefineLog('连接正在保持，后台微调较慢，请耐心等待...');
    }, STALL_THRESHOLD_MS);
  };

  const clearStallTimer = () => {
    if (stallTimerRef.current) {
      clearTimeout(stallTimerRef.current);
      stallTimerRef.current = null;
    }
  };

  useEffect(() => {
    isMountedRef.current = true;
    return () => {
      isMountedRef.current = false;
    };
  }, []);

  useEffect(() => {
    // Clean up EventSource if panel closed or no active request
    if (!isExecutionRefinePanelOpen || !selectedProjectId || !executionRefineActiveRequestId) {
      if (eventSourceRef.current) {
        eventSourceRef.current.close();
        eventSourceRef.current = null;
      }
      clearStallTimer();
      if (reconnectTimerRef.current) {
        clearTimeout(reconnectTimerRef.current);
        reconnectTimerRef.current = null;
      }
      lastEventIdRef.current = null;
      return;
    }

    const threadId = selectedProjectId;
    const requestId = executionRefineActiveRequestId;

    const scheduleReconnect = (delayMs: number) => {
      if (!isMountedRef.current) return;
      if (reconnectTimerRef.current) {
        clearTimeout(reconnectTimerRef.current);
      }
      reconnectTimerRef.current = setTimeout(() => {
        reconnectTimerRef.current = null;
        if (isMountedRef.current) connect();
      }, delayMs);
    };

    function connect() {
      if (eventSourceRef.current) {
        eventSourceRef.current.close();
        eventSourceRef.current = null;
      }

      if (!isMountedRef.current) return;

      resetStallTimer();

      const url = new URL(`/api/threads/${threadId}/refine-diffs/${requestId}/events`, window.location.origin);
      if (lastEventIdRef.current) {
        url.searchParams.set('last_event_id', lastEventIdRef.current);
      }
      if (token) {
        url.searchParams.set('token', token);
      }

      const es = new EventSource(url.toString());
      eventSourceRef.current = es;

      const processEvent = (e: MessageEvent) => {
        const storeState = useAppStore.getState();
        if (
          !storeState.isExecutionRefinePanelOpen ||
          storeState.executionRefineActiveRequestId !== requestId ||
          storeState.selectedProjectId !== threadId
        ) {
          es.close();
          if (eventSourceRef.current === es) {
            eventSourceRef.current = null;
          }
          return null;
        }

        if (!isMountedRef.current || eventSourceRef.current !== es) return null;

        let envelope: SSEEventEnvelope | null = null;
        try {
          envelope = JSON.parse(e.data);
        } catch {
          console.error("Failed to parse execution refine event JSON");
          return null;
        }

        if (!envelope) return null;

        const EXECUTION_REFINE_EVENT_ALLOWLIST = new Set([
          'run_started',
          'execution_context_ready',
          'refine_generation_started',
          'refine_validation_started',
          'repair_started',
          'still_running',
          'diff_ready',
          'done',
          'agent_error',
          'snapshot_required'
        ]);

        if (!EXECUTION_REFINE_EVENT_ALLOWLIST.has(envelope.event_type)) {
          return null;
        }

        const thread_id = envelope.thread_id || envelope.payload?.thread_id;
        const request_id = envelope.request_id || envelope.payload?.request_id;
        const run_type = (envelope.run_type || envelope.payload?.run_type) as string;

        if (
          thread_id !== threadId ||
          request_id !== requestId ||
          run_type !== 'execution_refine'
        ) {
          return null;
        }

        if (envelope.event_id) {
          if (processedEventIdsRef.current.has(envelope.event_id)) {
            return null;
          }
          processedEventIdsRef.current.add(envelope.event_id);
        }

        resetStallTimer();
        if (e.lastEventId) {
          lastEventIdRef.current = e.lastEventId;
        }

        return envelope;
      };

      const stages = [
        'run_started',
        'execution_context_ready',
        'refine_generation_started',
        'refine_validation_started',
        'repair_started',
        'still_running'
      ];

      stages.forEach(evtType => {
        es.addEventListener(evtType, (e) => {
          const envelope = processEvent(e);
          if (!envelope) return;

          setExecutionRefineStage(envelope.payload?.stage || envelope.event_type);

          let logMessage = '';
          if (evtType === 'run_started') logMessage = '已启动计划执行调整引擎...';
          else if (evtType === 'execution_context_ready') logMessage = '执行上下文环境加载就绪...';
          else if (evtType === 'refine_generation_started') logMessage = 'AI 正在生成计划微调调整方案...';
          else if (evtType === 'refine_validation_started') logMessage = '方案已生成，正在执行多重校验校验...';
          else if (evtType === 'repair_started') logMessage = '方案校验不通过，正在由 AI 自动自我修复...';
          else if (evtType === 'still_running') logMessage = '正在进行深度调优计算...';

          if (logMessage) {
            addExecutionRefineLog(logMessage);
          }
        });
      });

      es.addEventListener('diff_ready', (e) => {
        const envelope = processEvent(e);
        if (!envelope) return;

        const proposal = envelope.payload?.proposal;
        setExecutionRefineStatus('ready');
        setExecutionRefineStage('ready');
        setExecutionRefineProposal(proposal || null);
        addExecutionRefineLog('微调建议已成功生成。');
        clearStallTimer();
      });

      es.addEventListener('done', (e) => {
        const envelope = processEvent(e);
        if (!envelope) return;

        es.close();
        if (eventSourceRef.current === es) {
          eventSourceRef.current = null;
        }
        clearStallTimer();
      });

      es.addEventListener('agent_error', (e) => {
        const envelope = processEvent(e);
        if (!envelope) return;

        clearStallTimer();
        setExecutionRefineStatus('failed');
        setExecutionRefineStage('failed');

        const rawMsg = envelope.payload?.message || envelope.payload?.code || 'An error occurred';
        const code = envelope.payload?.code || 'EXECUTION_REFINE_FAILED';
        const friendlyMsg = getFriendlyErrorMessage(rawMsg);

        setExecutionRefineErrorCode(code);
        setExecutionRefineErrorMessage(friendlyMsg);
        addExecutionRefineLog(`发生错误：${friendlyMsg}`);

        es.close();
        if (eventSourceRef.current === es) {
          eventSourceRef.current = null;
        }
      });

      es.addEventListener('snapshot_required', async (e) => {
        const envelope = processEvent(e);
        if (!envelope) return;

        try {
          // Reset the heart-beat event cursor
          lastEventIdRef.current = null;
          // Pull durable snapshot
          const { fetchExecutionRefineSnapshot } = useAppStore.getState();
          const snapshot = await fetchExecutionRefineSnapshot(requestId);
          addExecutionRefineLog('检测到服务器端状态更新，已同步最新调整状态。');

          const terminalStatuses = new Set(['ready', 'failed', 'applied', 'cancelled', 'expired']);
          if (snapshot && terminalStatuses.has(snapshot.status)) {
            es.close();
            if (eventSourceRef.current === es) {
              eventSourceRef.current = null;
            }
            clearStallTimer();
          }
        } catch (err) {
          console.error("Failed to load snapshot on snapshot_required", err);
        }
      });

      es.addEventListener('error', () => {
        if (!isMountedRef.current || eventSourceRef.current !== es) return;

        es.close();
        if (eventSourceRef.current === es) {
          eventSourceRef.current = null;
        }
        clearStallTimer();
        addExecutionRefineLog('服务器连接断开，正在自动尝试重新建立连接...');
        scheduleReconnect(3000);
      });
    }

    connect();

    return () => {
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
    token,
    selectedProjectId,
    executionRefineActiveRequestId,
    isExecutionRefinePanelOpen,
    setExecutionRefineStatus,
    setExecutionRefineStage,
    setExecutionRefineProposal,
    setExecutionRefineErrorCode,
    setExecutionRefineErrorMessage,
    addExecutionRefineLog
  ]);
};
