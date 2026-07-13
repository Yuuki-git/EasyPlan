import { useEffect, useRef } from 'react';
import { useAppStore } from '../store/useAppStore';
import { getFriendlyErrorMessage } from '../lib/errorHelper';
import { SSEEventEnvelope, TaskAssistStage } from '../types/api';

const STALL_THRESHOLD_MS = 15000;

export const useTaskAssist = () => {
  const {
    token,
    boardTasks,
    taskAssistActiveTaskId,
    taskAssistActiveRequestId,
    setTaskAssistStatus,
    setTaskAssistStage,
    setTaskAssistProposal,
    setTaskAssistErrorCode,
    setTaskAssistErrorMessage,
    addTaskAssistLog,
    isTaskAssistPanelOpen
  } = useAppStore();

  const eventSourceRef = useRef<EventSource | null>(null);
  const lastEventIdRef = useRef<string | null>(null);
  const reconnectTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const stallTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const isMountedRef = useRef<boolean>(true);
  const processedEventIdsRef = useRef<Set<string>>(new Set());

  useEffect(() => {
    processedEventIdsRef.current.clear();
  }, [taskAssistActiveTaskId, taskAssistActiveRequestId]);

  const resetStallTimer = () => {
    if (stallTimerRef.current) {
      clearTimeout(stallTimerRef.current);
    }
    stallTimerRef.current = setTimeout(() => {
      addTaskAssistLog('连接似乎有些缓慢，AI 仍在努力思考中，请耐心等待...');
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
    // If panel is closed or there is no active assist run, clean up EventSource
    if (!isTaskAssistPanelOpen || !taskAssistActiveTaskId || !taskAssistActiveRequestId) {
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

    const taskId = taskAssistActiveTaskId;
    const requestId = taskAssistActiveRequestId;

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

      const url = new URL(`/api/tasks/${taskId}/assist/${requestId}/events`, window.location.origin);
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
          !storeState.isTaskAssistPanelOpen ||
          storeState.taskAssistActiveRequestId !== requestId ||
          storeState.taskAssistActiveTaskId !== taskId
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
          console.error("Failed to parse task assist event JSON");
          return null;
        }

        if (!envelope) return null;

        const TASK_ASSIST_EVENT_ALLOWLIST = new Set([
          'run_started',
          'task_context_ready',
          'assist_generation_started',
          'assist_validation_started',
          'still_running',
          'assist_ready',
          'done',
          'agent_error'
        ]);

        if (!TASK_ASSIST_EVENT_ALLOWLIST.has(envelope.event_type)) {
          return null;
        }

        const thread_id = envelope.thread_id || envelope.payload?.thread_id;
        const task_id = envelope.payload?.task_id;
        const request_id = envelope.request_id || envelope.payload?.request_id;
        const run_type = envelope.run_type || envelope.payload?.run_type;

        const targetTask = boardTasks?.find(t => t.id === taskId);
        const expectedThreadId = targetTask?.thread_id;

        if (task_id && task_id !== taskId) {
          return null;
        }
        if (
          (expectedThreadId && thread_id !== expectedThreadId) ||
          request_id !== requestId ||
          run_type !== 'task_assist'
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

      // Add event listeners for all expected stages
      const stages = [
        'run_started',
        'task_context_ready',
        'assist_generation_started',
        'assist_validation_started',
        'still_running'
      ];

      stages.forEach(evtType => {
        es.addEventListener(evtType, (e) => {
          const envelope = processEvent(e);
          if (!envelope) return;

          setTaskAssistStage((envelope.payload?.stage as TaskAssistStage) || null);
          
          let logMessage = '';
          if (evtType === 'run_started') logMessage = '已启动辅导任务分析...';
          else if (evtType === 'task_context_ready') logMessage = '上下文数据已加载完毕，正在构建问题...';
          else if (evtType === 'assist_generation_started') logMessage = 'AI 行动教练正在生成量身定制的具体执行方案...';
          else if (evtType === 'assist_validation_started') logMessage = '方案已生成，正在进行可用性与依赖校验...';
          else if (evtType === 'still_running') logMessage = '生成耗时较长，AI 仍在微调方案中...';

          if (logMessage) {
            addTaskAssistLog(logMessage);
          }
        });
      });

      es.addEventListener('assist_ready', (e) => {
        const envelope = processEvent(e);
        if (!envelope) return;

        const proposal = envelope.payload?.proposal;
        setTaskAssistStatus('ready');
        setTaskAssistStage('ready');
        setTaskAssistProposal(proposal || null);
        addTaskAssistLog('方案生成成功！建议已准备就绪。');
        clearStallTimer();
      });

      es.addEventListener('done', (e) => {
        const envelope = processEvent(e);
        if (!envelope) return;

        // If it was already applied or set to ready, just wrap up
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
        setTaskAssistStatus('failed');
        setTaskAssistStage('failed');

        const rawMsg = envelope.payload?.message || envelope.payload?.code || 'An error occurred';
        const code = envelope.payload?.code || 'TASK_ASSIST_FAILED';
        const friendlyMsg = getFriendlyErrorMessage(rawMsg);

        setTaskAssistErrorCode(code);
        setTaskAssistErrorMessage(friendlyMsg);
        addTaskAssistLog(`发生错误：${friendlyMsg}`);

        es.close();
        if (eventSourceRef.current === es) {
          eventSourceRef.current = null;
        }
      });

      es.addEventListener('error', () => {
        if (!isMountedRef.current || eventSourceRef.current !== es) return;

        // On disconnect, attempt reconnect after delay
        es.close();
        if (eventSourceRef.current === es) {
          eventSourceRef.current = null;
        }
        clearStallTimer();
        addTaskAssistLog('与服务器连接断开，正在尝试重连恢复...');
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
    boardTasks,
    taskAssistActiveTaskId,
    taskAssistActiveRequestId,
    isTaskAssistPanelOpen,
    setTaskAssistStatus,
    setTaskAssistStage,
    setTaskAssistProposal,
    setTaskAssistErrorCode,
    setTaskAssistErrorMessage,
    addTaskAssistLog
  ]);
};
