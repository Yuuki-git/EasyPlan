import { create } from 'zustand';
import {
  TaskTree,
  TaskResponse,
  ThreadSnapshot,
  NextPhaseCommitReceipt,
  AgentRunEventMeta,
  ActiveRun,
  LongTermExecutionSnapshot,
  PhaseReviewUpdateRequest,
  PhaseReviewDecisionRequest
} from '../types/api';
import { buildAuthRecoveryState, isUnauthorizedResponse } from './authRecovery';
import { buildIntentRequest, resolvePlannerProvider } from './intentRequest';
import { createLatestRequestGate } from './snapshotRequestGate';

const snapshotGate = createLatestRequestGate();

const clearActiveRunStorage = () => {
  localStorage.removeItem('easyplan_active_run');
};

const generateUUID = () => {
  if (typeof crypto !== 'undefined' && 'randomUUID' in crypto) {
    return crypto.randomUUID();
  }
  return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, function(c) {
    const r = Math.random() * 16 | 0, v = c === 'x' ? r : (r & 0x3 | 0x8);
    return v.toString(16);
  });
};

type MutableTaskStatus = 'completed' | 'active';

const TASK_STATUS_OVERRIDE_TTL_MS = 10000;
const taskStatusOverrides = new Map<string, { status: MutableTaskStatus; pending: boolean; updatedAt: number }>();

const applyTaskStatusOverrides = (tasks: TaskResponse[], fetchStartedAt: number) => {
  const now = Date.now();

  return tasks.map((task) => {
    const override = taskStatusOverrides.get(task.id);
    if (!override) return task;

    if (task.status === override.status) {
      taskStatusOverrides.delete(task.id);
      return task;
    }

    const isRecent = now - override.updatedAt < TASK_STATUS_OVERRIDE_TTL_MS;
    if (override.pending || override.updatedAt >= fetchStartedAt || isRecent) {
      return { ...task, status: override.status };
    }

    taskStatusOverrides.delete(task.id);
    return task;
  });
};

export type AppState =
  | 'INITIAL'
  | 'THINKING'
  | 'PENDING'
  | 'SYNCING'
  | 'SUCCESS'
  | 'PARTIAL_ERROR'
  | 'ERROR';

export type ThemeType = 'parchment' | 'void';

export type PreviewMode = 'initial' | 'next_phase' | null;

interface AppStore {
  // Data
  intent: string;
  appState: AppState;
  threadId: string | null;
  syncRequestId: string | null;
  reasoningLogs: string[];
  committedTaskTree: TaskTree | null;
  previewTaskTree: TaskTree | null;
  nodeStatuses: Record<string, 'pending' | 'syncing' | 'success' | 'error'>;
  preferredProvider: string; // 'todoist' or 'microsoft_todo'
  isIntegrated: boolean;
  error: string | null;
  token: string | null;
  showAuthModal: boolean;
  pendingIntent: string | null;
  theme: ThemeType;
  view: 'input' | 'board';
  currentViewBucket: 'planned' | 'my_day';
  selectedProjectId: string | null;
  boardTasks: TaskResponse[] | null;
  boardError: string | null;
  previewMode: PreviewMode;
  phaseRequestId: string | null;
  basePhaseId: string | null;
  isPhaseRequestPending: boolean;
  isRunStalled: boolean;
  isCancelPending: boolean;
  projectSnapshots: Record<string, ThreadSnapshot>;
  highlightedProjectId: string | null;
  lastDoneEvent: AgentRunEventMeta | null;
  activeRun: ActiveRun | null;
  sseReconnectNonce: number;
  longTermExecution: LongTermExecutionSnapshot | null;
  practiceError: string | null;
  isPracticeRequestPending: boolean;

  // Actions
  setActiveRun: (run: ActiveRun | null) => void;
  clearActiveRun: () => void;
  setIntent: (intent: string) => void;
  setPreferredProvider: (provider: string) => void;
  setAppState: (state: AppState) => void;
  setThreadId: (id: string | null) => void;
  setToken: (token: string | null, isExplicitLogout?: boolean) => void;
  setShowAuthModal: (show: boolean) => void;
  setPendingIntent: (intent: string | null) => void;
  setTheme: (theme: ThemeType) => void;
  setView: (view: 'input' | 'board') => void;
  setCurrentViewBucket: (bucket: 'planned' | 'my_day') => void;
  setSelectedProjectId: (projectId: string | null) => void;
  generateSyncId: () => void;
  addReasoningLog: (log: string) => void;
  setPreviewTaskTree: (tree: TaskTree | null) => void;
  setCommittedTaskTree: (tree: TaskTree | null) => void;
  setNodeStatus: (nodeId: string, status: 'pending' | 'syncing' | 'success' | 'error') => void;
  setError: (error: string | null) => void;
  setRunStalled: (stalled: boolean) => void;
  setHighlightedProjectId: (id: string | null) => void;
  fetchProjectSnapshots: () => Promise<void>;
  reset: () => void;
  reconnectActiveRun: () => void;
  dismissInitialSync: () => void;

  // Actions
  alignState: (threadId: string) => Promise<void>;
  retryNode: (nodeId: string) => Promise<void>;
  submitIntent: (intentText: string) => Promise<void>;
  confirmPlan: () => Promise<void>;
  collapsePlanningPanel: () => void;
  refinePlan: (feedback: string) => Promise<void>;
  fetchTasks: (bucket?: 'planned' | 'my_day') => Promise<void>;
  updateTaskStatus: (taskId: string, status: 'completed' | 'active') => Promise<void>;
  updateTaskDetails: (taskId: string, updates: { title?: string; description?: string | null; estimated_minutes?: number | null }) => Promise<void>;
  createManualTask: (title: string, options?: { thread_id?: string | null }) => Promise<void>;
  toggleTaskInMyDay: (taskId: string, currentState: boolean) => Promise<void>;
  generateNextPhasePlan: () => Promise<void>;
  deleteTask: (taskId: string) => Promise<void>;
  startNewIntent: () => void;
  deleteThread: (threadId: string) => Promise<void>;
  loadProjectSnapshot: (threadId: string) => Promise<void>;
  cancelPlanPreview: () => Promise<void>;
  finishAgentRun: (event: AgentRunEventMeta) => Promise<void>;
  returnToCommittedPlan: () => Promise<void>;
  schedulePracticeToday: (loopId: string) => Promise<void>;
  savePhaseReview: (phaseId: string, payload: PhaseReviewUpdateRequest) => Promise<void>;
  decidePhaseReview: (phaseId: string, payload: PhaseReviewDecisionRequest) => Promise<void>;
}

type AppStoreSet = (partial: Partial<AppStore> | ((state: AppStore) => Partial<AppStore>)) => void;
type AppStoreGet = () => AppStore;

const clearRecoveredThreadContext = (set: AppStoreSet, get: AppStoreGet, staleThreadId: string) => {
  const state = get();
  const shouldClearSelectedProject = state.selectedProjectId === staleThreadId;
  const shouldClearThread = state.threadId === staleThreadId;

  snapshotGate.invalidate();

  set({
    selectedProjectId: shouldClearSelectedProject ? null : state.selectedProjectId,
    threadId: shouldClearThread ? null : state.threadId,
    committedTaskTree: null,
    previewTaskTree: null,
    previewMode: null,
    phaseRequestId: null,
    basePhaseId: null,
    appState: 'INITIAL',
    error: null,
    boardError: null,
    currentViewBucket: 'planned',
    isPhaseRequestPending: false,
    isRunStalled: false,
    reasoningLogs: [],
    nodeStatuses: {},
    activeRun: null,
    longTermExecution: null,
    practiceError: null,
    isPracticeRequestPending: false,
  });

  if (shouldClearSelectedProject) {
    localStorage.removeItem('easyplan_selected_project_id');
  }
  if (shouldClearThread) {
    localStorage.removeItem('easyplan_thread_id');
  }
  localStorage.removeItem('easyplan_preview_mode');
  localStorage.removeItem('easyplan_phase_request_id');
  localStorage.removeItem('easyplan_base_phase_id');
  clearActiveRunStorage();
};

export const useAppStore = create<AppStore>((set, get) => ({
  intent: '',
  appState: 'INITIAL',
  threadId: localStorage.getItem('easyplan_thread_id') || null,
  syncRequestId: null,
  reasoningLogs: [],
  committedTaskTree: null,
  previewTaskTree: null,
  nodeStatuses: {},
  preferredProvider: 'microsoft_todo',
  isIntegrated: false,
  error: null,
  token: localStorage.getItem('auth_token'),
  showAuthModal: false,
  pendingIntent: null,
  theme: (localStorage.getItem('app_theme') as ThemeType) || 'parchment', // using parchment since zen was removed, wait, let me check what it currently is
  view: (localStorage.getItem('easyplan_view') as 'input' | 'board') || 'input',
  currentViewBucket: 'planned', // Default to planned after transition
  selectedProjectId: localStorage.getItem('easyplan_selected_project_id') || null,
  boardTasks: null,
  boardError: null,
  previewMode: (localStorage.getItem('easyplan_preview_mode') as PreviewMode) || null,
  phaseRequestId: localStorage.getItem('easyplan_phase_request_id') || null,
  basePhaseId: localStorage.getItem('easyplan_base_phase_id') || null,
  isPhaseRequestPending: false,
  isRunStalled: false,
  isCancelPending: false,
  projectSnapshots: {},
  highlightedProjectId: null,
  lastDoneEvent: null,
  activeRun: (() => {
    try {
      const stored = localStorage.getItem('easyplan_active_run');
      if (stored) {
        const parsed = JSON.parse(stored);
        if (
          parsed &&
          typeof parsed.threadId === 'string' &&
          (parsed.runType === 'initial' || parsed.runType === 'next_phase') &&
          typeof parsed.requestId === 'string'
        ) {
          return parsed as ActiveRun;
        }
      }
    } catch {
      // ignore
    }
    return null;
  })(),
  sseReconnectNonce: 0,
  longTermExecution: null,
  practiceError: null,
  isPracticeRequestPending: false,

  setActiveRun: (run) => {
    set({ activeRun: run });
    if (run) {
      localStorage.setItem('easyplan_active_run', JSON.stringify(run));
    } else {
      localStorage.removeItem('easyplan_active_run');
    }
  },
  clearActiveRun: () => {
    set({ activeRun: null });
    localStorage.removeItem('easyplan_active_run');
  },

  setIntent: (intent) => set({ intent }),
  setRunStalled: (stalled) => set({ isRunStalled: stalled }),
  setHighlightedProjectId: (highlightedProjectId) => set({ highlightedProjectId }),
  setPreferredProvider: (preferredProvider) => set({ preferredProvider }),
  setAppState: (appState) => set({ appState }),
  setThreadId: (threadId) => {
    set({ threadId });
    if (threadId) {
      localStorage.setItem('easyplan_thread_id', threadId);
    } else {
      localStorage.removeItem('easyplan_thread_id');
    }
  },
  setToken: (token, isExplicitLogout = false) => {
    if (token) {
      localStorage.setItem('auth_token', token);
      set({ token });

      // Auto-restore board after successful login
      const { view, selectedProjectId, fetchTasks, loadProjectSnapshot } = get();
      if (view === 'board') {
        set({ boardError: null, boardTasks: null }); // Clear error and show loading state
        if (selectedProjectId === null) {
          fetchTasks('planned');
        } else {
          loadProjectSnapshot(selectedProjectId)
            .then(() => fetchTasks('planned'))
            .catch(err => {
              set({ boardError: err.message || '恢复项目失败，请重试' });
            });
        }
      }
    } else {
      localStorage.removeItem('auth_token');
      set({ token: null });
      if (isExplicitLogout) {
        // P0 Fix: Force memory cleanup on logout to prevent privacy leaks
        get().reset();
      }
    }
  },
  setShowAuthModal: (showAuthModal) => set({ showAuthModal }),
  setPendingIntent: (pendingIntent) => set({ pendingIntent }),
  setTheme: (theme) => {
    localStorage.setItem('app_theme', theme);
    set({ theme });
  },
  setView: (view) => {
    set({ view });
    localStorage.setItem('easyplan_view', view);
    if (view === 'board') {
      const { selectedProjectId } = get();
      if (!selectedProjectId) {
        set({
          currentViewBucket: 'planned',
          selectedProjectId: null,
          committedTaskTree: null,
          previewTaskTree: null,
          boardTasks: null,
          activeRun: null,
        });
        clearActiveRunStorage();
        get().fetchTasks('planned');
      } else {
        set({ currentViewBucket: 'planned', previewTaskTree: null, boardTasks: null });
        get().fetchTasks('planned');
      }
    }
  },
  setCurrentViewBucket: (bucket) => {
    set({ currentViewBucket: bucket });
    get().fetchTasks(bucket);
  },
  setSelectedProjectId: (projectId) => {
    snapshotGate.invalidate();
    set({ selectedProjectId: projectId });
    if (projectId) {
      localStorage.setItem('easyplan_selected_project_id', projectId);
      get().alignState(projectId);
    } else {
      localStorage.removeItem('easyplan_selected_project_id');
      set({
        threadId: null,
        committedTaskTree: null,
        previewTaskTree: null,
        previewMode: null,
        phaseRequestId: null,
        basePhaseId: null,
        appState: 'INITIAL',
        activeRun: null,
      });
      localStorage.removeItem('easyplan_thread_id');
      localStorage.removeItem('easyplan_preview_mode');
      localStorage.removeItem('easyplan_phase_request_id');
      localStorage.removeItem('easyplan_base_phase_id');
      clearActiveRunStorage();
    }
  },

  generateSyncId: () => set({ syncRequestId: generateUUID() }),

  addReasoningLog: (log) => set((state) => ({
    reasoningLogs: [...state.reasoningLogs, log]
  })),

  setPreviewTaskTree: (previewTaskTree) => set({ previewTaskTree }),
  setCommittedTaskTree: (committedTaskTree) => set({ committedTaskTree }),

  setNodeStatus: (nodeId, status) => set((state) => ({
    nodeStatuses: { ...state.nodeStatuses, [nodeId]: status }
  })),

  setError: (error) => set({ error, appState: error ? 'ERROR' : 'INITIAL' }),

  reset: () => {
    taskStatusOverrides.clear();
    snapshotGate.invalidate();
    set({
      intent: '',
      appState: 'INITIAL',
      threadId: null,
      reasoningLogs: [],
      error: null,
      view: 'input',
      previewMode: null,
      phaseRequestId: null,
      basePhaseId: null,
      isRunStalled: false,
      isCancelPending: false,
      projectSnapshots: {},
      highlightedProjectId: null,
      selectedProjectId: null,
      boardTasks: null,
      boardError: null,
      committedTaskTree: null,
      previewTaskTree: null,
      showAuthModal: false,
      pendingIntent: null,
      isPhaseRequestPending: false,
      activeRun: null,
      sseReconnectNonce: 0,
    });
    localStorage.setItem('easyplan_view', 'input');
    localStorage.removeItem('easyplan_selected_project_id');
    localStorage.removeItem('easyplan_thread_id');
    localStorage.removeItem('easyplan_preview_mode');
    localStorage.removeItem('easyplan_phase_request_id');
    localStorage.removeItem('easyplan_base_phase_id');
    localStorage.removeItem('easyplan_active_run');
  },

  reconnectActiveRun: () => {
    if (!get().activeRun) return;
    set((state) => ({
      sseReconnectNonce: state.sseReconnectNonce + 1,
      isRunStalled: false,
      error: null,
    }));
  },

  dismissInitialSync: () => {
    const { activeRun: run, selectedProjectId } = get();
    if (!run || run.runType !== 'initial') return;

    set({
      view: 'board',
      currentViewBucket: 'planned',
      previewMode: null,
      appState: 'INITIAL',
      error: null,
      isRunStalled: false,
    });

    localStorage.setItem('easyplan_view', 'board');
    localStorage.removeItem('easyplan_preview_mode');
    if (selectedProjectId) {
      void get().loadProjectSnapshot(selectedProjectId);
    } else {
      localStorage.removeItem('easyplan_selected_project_id');
    }
    void get().fetchTasks('planned');
  },

  finishAgentRun: async (event: AgentRunEventMeta) => {
    const isCurrent = snapshotGate.begin();
    const { selectedProjectId, threadId, fetchTasks, activeRun } = get();

    if (
      !activeRun ||
      activeRun.threadId !== event.thread_id ||
      activeRun.runType !== event.run_type ||
      activeRun.requestId !== event.request_id
    ) {
      if (!(globalThis as { __test__?: boolean }).__test__) {
        console.warn('finishAgentRun run check failed: mismatched run or no active run');
      }
      return;
    }

    if (activeRun?.runType === 'next_phase' && selectedProjectId) {
      const { token } = get();
      const headers: Record<string, string> = {
        'X-User-Timezone': Intl.DateTimeFormat().resolvedOptions().timeZone
      };
      if (token) headers['Authorization'] = `Bearer ${token}`;

      try {
        const receiptUrl =
          `/api/threads/${selectedProjectId}/phases/next/commit`
          + `?request_id=${encodeURIComponent(event.request_id)}`;
        const response = await fetch(receiptUrl, {
          headers,
          cache: 'no-store'
        });
        if (isUnauthorizedResponse(response)) {
          if (!isCurrent()) return;
          get().setToken(null, false);
          set({
            showAuthModal: true,
            error: '登录已失效，请重新登录',
            appState: 'ERROR',
            lastDoneEvent: event
          });
          return;
        }
        if (!response.ok) {
          if (!isCurrent()) return;
          set({
            error: '读取下一阶段提交回执失败，请重试同步。',
            appState: 'ERROR',
            lastDoneEvent: event
          });
          return;
        }
        let receipt = await response.json() as NextPhaseCommitReceipt;
        if (!isCurrent()) return;
        if (
          receipt.thread_id !== selectedProjectId ||
          receipt.request_id !== event.request_id
        ) {
          set({
            error: '下一阶段提交回执与当前请求不匹配，请重试同步。',
            appState: 'ERROR',
            lastDoneEvent: event
          });
          return;
        }
        if (receipt.status === 'confirming') {
          set({ error: null, appState: 'SYNCING' });
          const retryResponse = await fetch(`/api/threads/${selectedProjectId}/confirm`, {
            method: 'POST',
            headers: {
              ...headers,
              'Content-Type': 'application/json'
            },
            body: JSON.stringify({
              request_id: event.request_id,
              action: 'approve'
            })
          });
          if (isUnauthorizedResponse(retryResponse)) {
            if (!isCurrent()) return;
            get().setToken(null, false);
            set({
              showAuthModal: true,
              error: '登录已失效，请重新登录',
              appState: 'ERROR',
              lastDoneEvent: event
            });
            return;
          }
          if (!retryResponse.ok && retryResponse.status !== 409) {
            if (!isCurrent()) return;
            set({
              error: '重新提交下一阶段失败，请返回当前计划后重试。',
              appState: 'ERROR',
              lastDoneEvent: event
            });
            return;
          }

          for (let attempt = 0; attempt < 20; attempt += 1) {
            const retryReceiptResponse = await fetch(receiptUrl, {
              headers,
              cache: 'no-store'
            });
            if (!retryReceiptResponse.ok) break;
            receipt = await retryReceiptResponse.json() as NextPhaseCommitReceipt;
            if (!isCurrent()) return;
            if (
              receipt.thread_id !== selectedProjectId ||
              receipt.request_id !== event.request_id ||
              receipt.status !== 'confirming'
            ) {
              break;
            }
            await new Promise((resolve) => setTimeout(resolve, 100));
          }
        }
        if (receipt.status !== 'confirmed' || !receipt.task_tree) {
          const error =
            receipt.status === 'incomplete'
              ? '后端未完整提交下一阶段任务，请返回当前计划后重新生成。'
              : receipt.status === 'cancelled'
                ? '本次下一阶段生成已取消。'
                : receipt.status === 'failed'
                  ? '下一阶段提交失败，请重新生成。'
                  : '下一阶段仍在提交中，请稍后重试同步。';
          set({ error, appState: 'ERROR', lastDoneEvent: event });
          return;
        }

        set({
          view: 'board',
          currentViewBucket: 'planned',
          committedTaskTree: receipt.task_tree,
          boardTasks: receipt.tasks,
          previewMode: null,
          phaseRequestId: null,
          basePhaseId: null,
          previewTaskTree: null,
          error: null,
          lastDoneEvent: null,
          isRunStalled: false,
          appState: 'INITIAL', // Reset appState to INITIAL on success
          activeRun: null
        });
        localStorage.setItem('easyplan_view', 'board');
        localStorage.removeItem('easyplan_preview_mode');
        localStorage.removeItem('easyplan_phase_request_id');
        localStorage.removeItem('easyplan_base_phase_id');
        localStorage.removeItem('easyplan_active_run');
      } catch (err) {
        if (!isCurrent()) return;
        set({
          error: '同步过程发生网络异常，请重试。',
          appState: 'ERROR',
          lastDoneEvent: event
        });
      }
    } else {
      if (!isCurrent()) return;
      set({
        view: 'board',
        previewMode: null,
        phaseRequestId: null,
        basePhaseId: null,
        currentViewBucket: 'planned',
        selectedProjectId: null,
        highlightedProjectId: threadId,
        committedTaskTree: null,
        previewTaskTree: null,
        lastDoneEvent: null,
        activeRun: null
      });
      localStorage.setItem('easyplan_view', 'board');
      localStorage.removeItem('easyplan_selected_project_id');
      localStorage.removeItem('easyplan_preview_mode');
      localStorage.removeItem('easyplan_phase_request_id');
      localStorage.removeItem('easyplan_base_phase_id');
      localStorage.removeItem('easyplan_active_run');
      await fetchTasks('planned');
    }
  },

  loadProjectSnapshot: async (threadId: string) => {
    const isCurrent = snapshotGate.begin();
    try {
      const { token } = get();
      if (!token) {
        if (!isCurrent()) return;
        set({ showAuthModal: true });
        throw new Error('请先登录以查看项目看板');
      }

      const headers: Record<string, string> = {
        'X-User-Timezone': Intl.DateTimeFormat().resolvedOptions().timeZone,
        'Authorization': `Bearer ${token}`
      };

      const response = await fetch(`/api/threads/${threadId}`, {
        headers,
        cache: 'no-store'
      });

      if (isUnauthorizedResponse(response)) {
        if (!isCurrent()) return;
        get().setToken(null, false);
        set({ showAuthModal: true });
        throw new Error('请先登录以查看项目看板');
      }

      if (response.status === 404) {
        if (!isCurrent()) return;
        const { selectedProjectId, threadId: activeThreadId } = get();
        if (selectedProjectId === threadId || activeThreadId === threadId) {
          clearRecoveredThreadContext(set, get, threadId);
          return;
        }
      }

      if (!response.ok) throw new Error('Failed to load project snapshot');
      const snapshot = await response.json();

      if (!isCurrent()) return;
      set({
        committedTaskTree: snapshot.task_tree,
        longTermExecution: snapshot.long_term_execution ?? null
      });
    } catch (err) {
      if (!isCurrent()) return;
      console.error('loadProjectSnapshot failed', err);
      throw err;
    }
  },

  cancelPlanPreview: async () => {
    const { token, selectedProjectId, activeRun, isCancelPending } = get();
    if (
      isCancelPending
      || !token
      || !selectedProjectId
      || !activeRun
      || activeRun.runType !== "next_phase"
      || activeRun.threadId !== selectedProjectId
    ) {
      return;
    }

    set({ isCancelPending: true, error: null });
    try {
      const url =
        `/api/threads/${selectedProjectId}/phases/next/cancel`
        + `?request_id=${encodeURIComponent(activeRun.requestId)}`;

      const response = await fetch(url, {
        method: 'DELETE',
        headers: {
          'Authorization': `Bearer ${token}`
        }
      });

      if (isUnauthorizedResponse(response)) {
        get().setToken(null);
        set({ showAuthModal: true, error: '登录已失效，请重新登录' });
        return;
      }

      if (response.status === 409) {
        await get().alignState(selectedProjectId);
        if (get().previewMode !== null) {
          set({ error: '当前生成状态已变化，请重试。', appState: 'PENDING' });
        }
        return;
      }

      if (!response.ok) {
        let msg = '取消下一阶段预览失败';
        try {
          const errData = await response.json();
          if (errData.detail) msg = errData.detail;
        } catch {
          // ignore JSON parse error
        }
        throw new Error(msg);
      }

      const snapshot = await response.json();

      set({
        view: 'board',
        previewMode: null,
        phaseRequestId: null,
        basePhaseId: null,
        appState: 'INITIAL',
        committedTaskTree: snapshot.task_tree || null,
        previewTaskTree: null,
        threadId: snapshot.thread_id,
        intent: snapshot.intent_text,
        activeRun: null,
      });
      localStorage.setItem('easyplan_view', 'board');
      localStorage.removeItem('easyplan_preview_mode');
      localStorage.removeItem('easyplan_phase_request_id');
      localStorage.removeItem('easyplan_base_phase_id');
      clearActiveRunStorage();
      get().fetchTasks();
    } catch (err) {
      console.error('cancelPlanPreview error:', err);
      set({ error: (err as Error).message });
    } finally {
      set({ isCancelPending: false });
    }
  },

  fetchProjectSnapshots: async () => {
    const { token, boardTasks } = get();
    if (!token || !boardTasks) return;

    const projectMap = new Map<string, { id: string; title: string; source?: string }>();
    boardTasks.forEach(task => {
      if (task.parent_task_id === null && task.thread_id) {
        const existing = projectMap.get(task.thread_id);
        if (!existing || (existing.source === 'manual' && task.source === 'ai')) {
          projectMap.set(task.thread_id, {
            id: task.thread_id,
            title: task.title,
            source: task.source
          });
        }
      }
    });
    const projects = Array.from(projectMap.values());
    const snapshots = { ...get().projectSnapshots };

    await Promise.all(
      projects.map(async (project) => {
        try {
          const headers: Record<string, string> = {
            'X-User-Timezone': Intl.DateTimeFormat().resolvedOptions().timeZone
          };
          if (token) headers['Authorization'] = `Bearer ${token}`;
          const response = await fetch(`/api/threads/${project.id}`, { headers });
          if (response.ok) {
            snapshots[project.id] = await response.json();
          }
        } catch (err) {
          console.error(`Failed to fetch snapshot for project ${project.id}`, err);
        }
      })
    );

    set({ projectSnapshots: snapshots });
  },

  returnToCommittedPlan: async () => {
    const { selectedProjectId } = get();
    if (selectedProjectId) {
      set({
        view: 'board',
        previewMode: null,
        phaseRequestId: null,
        appState: 'INITIAL',
        error: null,
        isRunStalled: false,
        activeRun: null,
      });
      localStorage.setItem('easyplan_view', 'board');
      localStorage.removeItem('easyplan_preview_mode');
      localStorage.removeItem('easyplan_phase_request_id');
      clearActiveRunStorage();
      await get().loadProjectSnapshot(selectedProjectId);
      await get().fetchTasks();
    } else {
      set({
        view: 'input',
        previewMode: null,
        phaseRequestId: null,
        appState: 'INITIAL',
        error: null,
        isRunStalled: false,
        threadId: null,
        intent: '',
        committedTaskTree: null,
        previewTaskTree: null,
        activeRun: null,
      });
      localStorage.setItem('easyplan_view', 'input');
      localStorage.removeItem('easyplan_thread_id');
      localStorage.removeItem('easyplan_preview_mode');
      localStorage.removeItem('easyplan_phase_request_id');
      clearActiveRunStorage();
    }
  },

  schedulePracticeToday: async (loopId: string) => {
    const { token, selectedProjectId } = get();
    if (!token || !selectedProjectId) return;

    set({ isPracticeRequestPending: true, practiceError: null });
    try {
      const response = await fetch(`/api/threads/${selectedProjectId}/practice-loops/${loopId}/schedule-today`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'X-User-Timezone': Intl.DateTimeFormat().resolvedOptions().timeZone,
          'Authorization': `Bearer ${token}`
        }
      });

      if (isUnauthorizedResponse(response)) {
        get().setToken(null, false);
        set({ showAuthModal: true, practiceError: '登录已失效，请重新登录', isPracticeRequestPending: false });
        return;
      }

      if (response.status === 409) {
        const errorData = await response.json();
        set({ practiceError: errorData.detail?.message || errorData.detail || '安排练习任务冲突或次数已满。', isPracticeRequestPending: false });
        return;
      }

      if (!response.ok) {
        throw new Error('Failed to schedule practice today');
      }

      const newTask = await response.json();

      const currentTasks = get().boardTasks || [];
      const updatedTasks = currentTasks.filter(t => t.id !== newTask.id);
      set({
        boardTasks: [...updatedTasks, newTask],
        isPracticeRequestPending: false
      });

      await get().loadProjectSnapshot(selectedProjectId);
      await get().fetchTasks(get().currentViewBucket);
    } catch (err) {
      console.error(err);
      set({ practiceError: '安排今日练习失败，请重试。', isPracticeRequestPending: false });
    }
  },

  savePhaseReview: async (phaseId: string, payload: PhaseReviewUpdateRequest) => {
    const { token, selectedProjectId } = get();
    if (!token || !selectedProjectId) return;

    set({ isPracticeRequestPending: true, practiceError: null });
    try {
      const response = await fetch(`/api/threads/${selectedProjectId}/phases/${phaseId}/review`, {
        method: 'PUT',
        headers: {
          'Content-Type': 'application/json',
          'X-User-Timezone': Intl.DateTimeFormat().resolvedOptions().timeZone,
          'Authorization': `Bearer ${token}`
        },
        body: JSON.stringify(payload)
      });

      if (isUnauthorizedResponse(response)) {
        get().setToken(null, false);
        set({ showAuthModal: true, practiceError: '登录已失效，请重新登录', isPracticeRequestPending: false });
        return;
      }

      if (!response.ok) {
        throw new Error('Failed to save phase review');
      }

      await get().loadProjectSnapshot(selectedProjectId);
      set({ isPracticeRequestPending: false });
    } catch (err) {
      console.error(err);
      set({ practiceError: '保存复盘审计失败，请重试。', isPracticeRequestPending: false });
    }
  },

  decidePhaseReview: async (phaseId: string, payload: PhaseReviewDecisionRequest) => {
    const { token, selectedProjectId } = get();
    if (!token || !selectedProjectId) return;

    set({ isPracticeRequestPending: true, practiceError: null });
    try {
      const response = await fetch(`/api/threads/${selectedProjectId}/phases/${phaseId}/review/decision`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'X-User-Timezone': Intl.DateTimeFormat().resolvedOptions().timeZone,
          'Authorization': `Bearer ${token}`
        },
        body: JSON.stringify(payload)
      });

      if (isUnauthorizedResponse(response)) {
        get().setToken(null, false);
        set({ showAuthModal: true, practiceError: '登录已失效，请重新登录', isPracticeRequestPending: false });
        return;
      }

      if (!response.ok) {
        throw new Error('Failed to submit phase review decision');
      }

      await get().loadProjectSnapshot(selectedProjectId);
      await get().fetchTasks(get().currentViewBucket);
      set({ isPracticeRequestPending: false });
    } catch (err) {
      console.error(err);
      set({ practiceError: '提交复盘决策失败，请重试。', isPracticeRequestPending: false });
    }
  },

  fetchTasks: async (bucket) => {
    const targetBucket = bucket || get().currentViewBucket;
    const { token } = get();
    if (!token) {
      set({ showAuthModal: true, boardError: '请先登录以查看看板任务' });
      return;
    }

    set({ boardError: null });
    try {
      const fetchStartedAt = Date.now();
      const headers: Record<string, string> = {
        'Authorization': `Bearer ${token}`
      };
      const response = await fetch(`/api/tasks?view_bucket=${targetBucket}`, { headers });

      // P1 Fix: Global Auth Recovery
      if (isUnauthorizedResponse(response)) {
        get().setToken(null);
        set({ showAuthModal: true });
        return;
      }

      if (!response.ok) throw new Error('Failed to fetch tasks');
      const tasks = await response.json();
      set({ boardTasks: applyTaskStatusOverrides(tasks, fetchStartedAt) });
    } catch (err) {
      console.error("Fetch tasks failed", err);
      set({ boardError: "获取任务失败，请重试" });
    }
  },

  updateTaskStatus: async (taskId: string, status: 'completed' | 'active') => {
    const { token, boardTasks } = get();
    if (!token) {
      set({ showAuthModal: true });
      throw new Error('Authentication required');
    }

    const taskToRollback = boardTasks?.find(t => t.id === taskId);
    const originalStatus = taskToRollback ? taskToRollback.status : (status === 'completed' ? 'active' : 'completed');
    taskStatusOverrides.set(taskId, { status, pending: true, updatedAt: Date.now() });

    // Optimistic UI sync (task level)
    set({
      boardTasks: (boardTasks || []).map(t => t.id === taskId ? { ...t, status } : t)
    });

    const rollback = () => {
      taskStatusOverrides.delete(taskId);
      set((state) => ({
        boardTasks: (state.boardTasks || []).map(t => t.id === taskId ? { ...t, status: originalStatus } : t),
        boardError: '任务状态同步失败，请稍后重试'
      }));
    };

    try {
      const headers: Record<string, string> = {
        'Content-Type': 'application/json',
        'Authorization': `Bearer ${token}`
      };
      const response = await fetch(`/api/tasks/${taskId}`, {
        method: 'PATCH',
        headers,
        body: JSON.stringify({ status })
      });

      if (isUnauthorizedResponse(response)) {
        taskStatusOverrides.delete(taskId);
        get().setToken(null);
        set({ showAuthModal: true });
        throw new Error('Authentication required');
      }

      if (!response.ok) throw new Error('Failed to update task status');

      const updatedTask = await response.json();
      const serverStatus = updatedTask.status === 'completed' || updatedTask.status === 'active' ? updatedTask.status : status;
      taskStatusOverrides.set(taskId, { status: serverStatus, pending: false, updatedAt: Date.now() });
      set((state) => ({
        boardTasks: (state.boardTasks || []).map(t => t.id === taskId ? { ...t, ...updatedTask } : t)
      }));

      if (
        (updatedTask.source === 'ai' && updatedTask.phase_id) ||
        (updatedTask.practice_loop_id && updatedTask.thread_id === get().selectedProjectId)
      ) {
        await get().loadProjectSnapshot(updatedTask.thread_id);
        await get().fetchTasks(get().currentViewBucket);
      }
    } catch (err) {
      if ((err as Error).message !== 'Authentication required') {
        console.error("Update task status failed", err);
        rollback();
      }
      throw err;
    }
  },

  updateTaskDetails: async (taskId: string, updates: { title?: string; description?: string | null; estimated_minutes?: number | null }) => {
    const { token, boardTasks } = get();
    if (!token) return;

    // Optimistic UI sync
    const originalTasks = boardTasks ? [...boardTasks] : [];
    set({
      boardTasks: (boardTasks || []).map(t => t.id === taskId ? { ...t, ...updates } : t)
    });

    try {
      const headers: Record<string, string> = {
        'Content-Type': 'application/json',
        'Authorization': `Bearer ${token}`
      };
      const response = await fetch(`/api/tasks/${taskId}`, {
        method: 'PATCH',
        headers,
        body: JSON.stringify(updates)
      });

      // P1 Fix: Global Auth Recovery
      if (isUnauthorizedResponse(response)) {
        get().setToken(null);
        set({ showAuthModal: true });
        return;
      }

      if (!response.ok) {
        throw new Error('Failed to update task details');
      }

      const updatedTask = await response.json();

      // Update with server truth
      set({
        boardTasks: (get().boardTasks || []).map(t => t.id === taskId ? { ...t, ...updatedTask } : t)
      });

      if (updatedTask.source === 'ai' && updatedTask.phase_id) {
        get().loadProjectSnapshot(updatedTask.thread_id);
      }
    } catch (err) {
      console.error("Update task details failed", err);
      // Revert optimistic update on error
      set({ boardTasks: originalTasks });
      throw err;
    }
  },

  createManualTask: async (title: string, options?: { thread_id?: string | null }) => {
    const { token, currentViewBucket, boardTasks, selectedProjectId } = get();
    if (!token) return;
    const shouldAddToMyDay = currentViewBucket === 'my_day';
    const targetThreadId = options?.thread_id !== undefined ? options.thread_id : selectedProjectId;

    if (!targetThreadId) {
      console.warn("createManualTask skipped: targetThreadId is null");
      return;
    }

    try {
      const headers: Record<string, string> = {
        'Content-Type': 'application/json',
        'Authorization': `Bearer ${token}`
      };
      const response = await fetch('/api/tasks', {
        method: 'POST',
        headers,
        body: JSON.stringify({
          title,
          view_bucket: shouldAddToMyDay ? 'planned' : currentViewBucket,
          is_in_my_day: shouldAddToMyDay,
          thread_id: targetThreadId
        })
      });

      // P1 Fix: Global Auth Recovery
      if (isUnauthorizedResponse(response)) {
        get().setToken(null);
        set({ showAuthModal: true });
        return;
      }

      if (!response.ok) throw new Error('Failed to create task');

      const newTask = await response.json();

      // Optimistically append to boardTasks to save a network request
      set({
        boardTasks: [...(boardTasks || []), newTask]
      });
    } catch (err) {
      console.error("Create manual task failed", err);
      throw err;
    }
  },

  toggleTaskInMyDay: async (taskId: string, currentState: boolean) => {
    const { token, boardTasks, currentViewBucket } = get();
    if (!token) return;

    const nextState = !currentState;
    const originalTasks = boardTasks ? [...boardTasks] : [];

    if (currentViewBucket === 'my_day' && !nextState) {
      set({
        boardTasks: (boardTasks || []).filter(t => t.id !== taskId)
      });
    } else {
      set({
        boardTasks: (boardTasks || []).map(t => t.id === taskId ? { ...t, is_in_my_day: nextState } : t)
      });
    }

    try {
      const headers: Record<string, string> = {
        'Content-Type': 'application/json',
        'Authorization': `Bearer ${token}`
      };
      const response = await fetch(`/api/tasks/${taskId}`, {
        method: 'PATCH',
        headers,
        body: JSON.stringify({ is_in_my_day: nextState })
      });

      if (isUnauthorizedResponse(response)) {
        get().setToken(null);
        set({ showAuthModal: true });
        set({ boardTasks: originalTasks });
        return;
      }

      if (!response.ok) throw new Error('Failed to toggle task in my day');
    } catch (err) {
      console.error("Toggle task in my day failed", err);
      set({ boardTasks: originalTasks });
      throw err;
    }
  },

  deleteTask: async (taskId: string) => {
    const { token, boardTasks } = get();
    if (!token) return;

    const taskToDelete = boardTasks?.find(t => t.id === taskId);
    // Optimistic UI sync
    const originalTasks = boardTasks ? [...boardTasks] : [];
    set({
      boardTasks: (boardTasks || []).filter(t => t.id !== taskId)
    });

    try {
      const headers: Record<string, string> = {
        'Authorization': `Bearer ${token}`
      };
      const response = await fetch(`/api/tasks/${taskId}`, {
        method: 'DELETE',
        headers
      });

      // P1 Fix: Global Auth Recovery
      if (isUnauthorizedResponse(response)) {
        get().setToken(null);
        set({ showAuthModal: true });
        return;
      }

      if (!response.ok) {
        throw new Error('Failed to delete task');
      }

      if (taskToDelete?.source === 'ai' && taskToDelete?.phase_id) {
        get().loadProjectSnapshot(taskToDelete.thread_id);
      }
    } catch (err) {
      console.error("Delete task failed", err);
      // Revert optimistic update on error
      set({ boardTasks: originalTasks });
      throw err;
    }
  },

  startNewIntent: () => {
    snapshotGate.invalidate();
    set({
      appState: 'INITIAL',
      view: 'input',
      intent: '',
      threadId: null,
      syncRequestId: null,
      reasoningLogs: [],
      error: null,
      pendingIntent: null,
      selectedProjectId: null,
      previewMode: null,
      phaseRequestId: null,
      basePhaseId: null,
      boardTasks: null,
      committedTaskTree: null,
      previewTaskTree: null,
      nodeStatuses: {},
      isRunStalled: false,
      activeRun: null,
    });
    localStorage.setItem('easyplan_view', 'input');
    localStorage.removeItem('easyplan_selected_project_id');
    localStorage.removeItem('easyplan_thread_id');
    localStorage.removeItem('easyplan_preview_mode');
    localStorage.removeItem('easyplan_phase_request_id');
    localStorage.removeItem('easyplan_base_phase_id');
    clearActiveRunStorage();
  },

  deleteThread: async (threadId: string) => {
    const { token, boardTasks, selectedProjectId } = get();
    if (!token) return;
    try {
      const response = await fetch(`/api/threads/${threadId}`, {
        method: 'DELETE',
        headers: { 'Authorization': `Bearer ${token}` }
      });

      if (isUnauthorizedResponse(response)) {
        get().setToken(null);
        set({ showAuthModal: true });
        return;
      }

      if (!response.ok) throw new Error('Failed to delete plan');

      const isCurrentProject = selectedProjectId === threadId;
      const isActiveRunThread = get().activeRun?.threadId === threadId;
      if (isCurrentProject || isActiveRunThread) {
        snapshotGate.invalidate();
        localStorage.removeItem('easyplan_selected_project_id');
        localStorage.removeItem('easyplan_preview_mode');
        localStorage.removeItem('easyplan_phase_request_id');
        localStorage.removeItem('easyplan_base_phase_id');
        clearActiveRunStorage();
      }
      if (threadId === get().threadId) {
        localStorage.removeItem('easyplan_thread_id');
      }

      set({
        boardTasks: (boardTasks || []).filter(t => t.thread_id !== threadId),
        selectedProjectId: isCurrentProject ? null : selectedProjectId,
        threadId: threadId === get().threadId ? null : get().threadId,
        previewMode: isCurrentProject ? null : get().previewMode,
        phaseRequestId: isCurrentProject ? null : get().phaseRequestId,
        basePhaseId: isCurrentProject ? null : get().basePhaseId,
        activeRun: (isCurrentProject || isActiveRunThread) ? null : get().activeRun,
      });
    } catch (err) {
      console.error("Delete plan failed", err);
      throw err;
    }
  },

  generateNextPhasePlan: async () => {
    const { token, selectedProjectId, committedTaskTree, isPhaseRequestPending, boardTasks } = get();
    if (!token || !selectedProjectId || !committedTaskTree?.planning_context || isPhaseRequestPending) return;
    if (import.meta.env.VITE_PHASE_PLANNING_ENABLED === 'false') return;

    // Use dynamic import or alternative to avoid circular deps if they occur
    const { selectPlanningView } = await import('./planningState');
    const planningView = selectPlanningView(committedTaskTree, boardTasks || [], selectedProjectId);
    if (!planningView?.canUnlock) return;

    const requestId = generateUUID();
    const basePhaseId = committedTaskTree.planning_context.current_phase?.phase_id || null;
    get().setActiveRun({
      threadId: selectedProjectId,
      runType: 'next_phase',
      requestId: requestId,
    });
    set({
      isPhaseRequestPending: true,
      isRunStalled: false,
      reasoningLogs: [],
      nodeStatuses: {},
      error: null,
      phaseRequestId: requestId,
      basePhaseId: basePhaseId,
      previewMode: 'next_phase',
      appState: 'THINKING',
      previewTaskTree: null
    });
    localStorage.setItem('easyplan_view', 'board');
    localStorage.setItem('easyplan_thread_id', selectedProjectId);
    localStorage.setItem('easyplan_preview_mode', 'next_phase');
    localStorage.setItem('easyplan_phase_request_id', requestId);
    if (basePhaseId) {
      localStorage.setItem('easyplan_base_phase_id', basePhaseId);
    } else {
      localStorage.removeItem('easyplan_base_phase_id');
    }

    try {
      const response = await fetch(`/api/threads/${selectedProjectId}/phases/next`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'X-User-Timezone': Intl.DateTimeFormat().resolvedOptions().timeZone,
          'Authorization': `Bearer ${token}`,
        },
        body: JSON.stringify({ request_id: requestId }),
      });

      if (isUnauthorizedResponse(response)) {
        get().setToken(null);
        set({
          showAuthModal: true,
          isPhaseRequestPending: false,
          previewMode: null,
          appState: 'INITIAL',
          activeRun: null,
        });
        localStorage.removeItem('easyplan_preview_mode');
        localStorage.removeItem('easyplan_phase_request_id');
        localStorage.removeItem('easyplan_base_phase_id');
        clearActiveRunStorage();
        return;
      }

      if (!response.ok) throw new Error('Failed to generate next phase plan');

      set({
        threadId: selectedProjectId,
      });
    } catch (err) {
      console.error("Generate next phase plan failed", err);
      set({
        error: (err as Error).message,
        appState: 'ERROR',
        previewMode: null,
        activeRun: null,
      });
      localStorage.removeItem('easyplan_preview_mode');
      localStorage.removeItem('easyplan_phase_request_id');
      localStorage.removeItem('easyplan_base_phase_id');
      clearActiveRunStorage();
    } finally {
      set({ isPhaseRequestPending: false });
    }
  },

  submitIntent: async (intentText: string) => {
    const { token, preferredProvider } = get();
    if (!token) {
      set({ showAuthModal: true, pendingIntent: intentText });
      return;
    }

    try {
      set({
        appState: 'THINKING',
        error: null,
        isRunStalled: false,
        reasoningLogs: [],
        committedTaskTree: null,
        previewTaskTree: null,
        nodeStatuses: {}
      });
      const headers: Record<string, string> = {
        'Content-Type': 'application/json',
        'X-User-Timezone': Intl.DateTimeFormat().resolvedOptions().timeZone,
        'Authorization': `Bearer ${token}`
      };

      const response = await fetch('/api/intents', {
        method: 'POST',
        headers,
        body: JSON.stringify(buildIntentRequest({
          intentText,
          preferredProvider,
          plannerProvider: resolvePlannerProvider(import.meta.env),
        }))
      });

      if (isUnauthorizedResponse(response)) {
        localStorage.removeItem('auth_token');
        set(buildAuthRecoveryState(intentText));
        return;
      }

      if (!response.ok) throw new Error('Failed to submit intent');

      const data = await response.json();
      set({
        intent: intentText,
        threadId: data.thread_id,
        pendingIntent: null,
        syncRequestId: data.request_id || null,
      });
      if (data.thread_id) {
        localStorage.setItem('easyplan_thread_id', data.thread_id);
      }
      if (data.thread_id && data.request_id) {
        get().setActiveRun({
          threadId: data.thread_id,
          runType: 'initial',
          requestId: data.request_id,
        });
      }
    } catch (err) {
      set({ error: (err as Error).message, appState: 'ERROR' });
    }
  },

  alignState: async (threadId: string) => {
    const isCurrent = snapshotGate.begin();
    try {
      const { token } = get();
      const headers: Record<string, string> = {
        'X-User-Timezone': Intl.DateTimeFormat().resolvedOptions().timeZone
      };
      if (token) headers['Authorization'] = `Bearer ${token}`;

      const response = await fetch(`/api/threads/${threadId}`, {
        headers,
        cache: 'no-store'
      });
      if (response.status === 404) {
        if (!isCurrent()) return;
        clearRecoveredThreadContext(set, get, threadId);
        return;
      }
      if (!response.ok) throw new Error('Failed to align state');
      const snapshot = await response.json();

      if (!isCurrent()) return;

      const localPreviewMode = localStorage.getItem('easyplan_preview_mode') as PreviewMode;
      const localPhaseRequestId = localStorage.getItem('easyplan_phase_request_id');
      const localBasePhaseId = localStorage.getItem('easyplan_base_phase_id');

      const envelope = snapshot.interrupt_payload;
      const isMatchingRequest = envelope && envelope.request_id === localPhaseRequestId;
      const isPending = snapshot.status === 'awaiting_confirmation';
      const isNextPhaseConfirming =
        envelope?.type === 'next_phase_review'
        && envelope.status === 'confirming'
        && (!localPhaseRequestId || isMatchingRequest);
      const isNextPhasePreview =
        envelope?.type === 'next_phase_review'
        && (isPending || isNextPhaseConfirming);
      const phaseGenerationStatus =
        (envelope?.type === 'phase_generation_state' && (localPreviewMode !== 'next_phase' || isMatchingRequest))
          ? envelope.status
          : null;

      let alignedTasks: TaskResponse[] | null = null;
      let alignedCommittedTaskTree: TaskTree | null = null;
      let hasTerminalPhaseGenerationState = false;
      if (phaseGenerationStatus === 'confirmed') {
        if (localPhaseRequestId) {
          try {
            const receiptUrl =
              `/api/threads/${threadId}/phases/next/commit`
              + `?request_id=${encodeURIComponent(localPhaseRequestId)}`;
            const receiptResponse = await fetch(receiptUrl, {
              headers,
              cache: 'no-store'
            });
            if (!isCurrent()) return;
            if (receiptResponse.ok) {
              const receipt = await receiptResponse.json() as NextPhaseCommitReceipt;
              if (!isCurrent()) return;
              if (
                receipt.thread_id === threadId &&
                receipt.request_id === localPhaseRequestId &&
                receipt.status === 'confirmed' &&
                receipt.task_tree
              ) {
                alignedTasks = receipt.tasks;
                alignedCommittedTaskTree = receipt.task_tree;
                hasTerminalPhaseGenerationState = true;
              }
            }
          } catch (e) {
            console.error('alignState commit receipt failed:', e);
          }
        } else {
          hasTerminalPhaseGenerationState = true;
        }
      } else if (phaseGenerationStatus === 'cancelled' || phaseGenerationStatus === 'failed') {
        hasTerminalPhaseGenerationState = true;
      }

      const isNextPhaseRunning = snapshot.status === 'running' && localPreviewMode === 'next_phase';
      const isStalled = snapshot.status === 'stalled';
      const shouldPreserveLocalNextPhase =
        localPreviewMode === 'next_phase' &&
        !!localPhaseRequestId &&
        !hasTerminalPhaseGenerationState &&
        get().selectedProjectId === threadId;

      const currentActiveRun = get().activeRun;
      const preserveInitialRunning =
        snapshot.status === 'running'
        && !!currentActiveRun
        && currentActiveRun.threadId === snapshot.thread_id
        && currentActiveRun.runType === 'initial'
        && currentActiveRun.requestId.length > 0;

      let savedPreviewMode: PreviewMode = null;
      let recoveredPhaseRequestId: string | null = null;
      let recoveredBasePhaseId: string | null = null;
      let recoveredActiveRun: ActiveRun | null = null;

      if (isPending || isNextPhaseConfirming) {
        if (isNextPhasePreview) {
          savedPreviewMode = 'next_phase';
          localStorage.setItem('easyplan_preview_mode', 'next_phase');
          recoveredPhaseRequestId = snapshot.interrupt_payload?.request_id || null;
          recoveredBasePhaseId = localBasePhaseId;
          recoveredActiveRun = {
            threadId: snapshot.thread_id,
            runType: 'next_phase',
            requestId: recoveredPhaseRequestId || '',
          };
        } else {
          savedPreviewMode = 'initial';
          localStorage.setItem('easyplan_preview_mode', 'initial');
          const initialReqId = snapshot.interrupt_payload?.request_id || snapshot.interrupt_payload?.phase_request_id || '';
          recoveredActiveRun = {
            threadId: snapshot.thread_id,
            runType: 'initial',
            requestId: initialReqId,
          };
        }
      } else if (isNextPhaseRunning || (isStalled && localPreviewMode === 'next_phase')) {
        savedPreviewMode = 'next_phase';
        recoveredPhaseRequestId = localPhaseRequestId;
        recoveredBasePhaseId = localBasePhaseId;
        recoveredActiveRun = {
          threadId: snapshot.thread_id,
          runType: 'next_phase',
          requestId: localPhaseRequestId || '',
        };
      } else if (shouldPreserveLocalNextPhase) {
        savedPreviewMode = 'next_phase';
        recoveredPhaseRequestId = localPhaseRequestId;
        recoveredBasePhaseId = localBasePhaseId;
        recoveredActiveRun = {
          threadId: snapshot.thread_id,
          runType: 'next_phase',
          requestId: localPhaseRequestId || '',
        };
      } else if (preserveInitialRunning) {
        savedPreviewMode = 'initial';
        recoveredActiveRun = currentActiveRun;
      } else {
        localStorage.removeItem('easyplan_preview_mode');
        localStorage.removeItem('easyplan_phase_request_id');
        localStorage.removeItem('easyplan_base_phase_id');
        savedPreviewMode = null;
      }

      if (recoveredPhaseRequestId) {
        localStorage.setItem('easyplan_phase_request_id', recoveredPhaseRequestId);
      }
      if (recoveredBasePhaseId) {
        localStorage.setItem('easyplan_base_phase_id', recoveredBasePhaseId);
      }

      const isActiveRunUnchanged =
        (currentActiveRun === null && recoveredActiveRun === null) ||
        (currentActiveRun !== null &&
          recoveredActiveRun !== null &&
          currentActiveRun.threadId === recoveredActiveRun.threadId &&
          currentActiveRun.runType === recoveredActiveRun.runType &&
          currentActiveRun.requestId === recoveredActiveRun.requestId);

      const finalActiveRun = isActiveRunUnchanged ? currentActiveRun : recoveredActiveRun;

      if (finalActiveRun) {
        localStorage.setItem('easyplan_active_run', JSON.stringify(finalActiveRun));
      } else {
        localStorage.removeItem('easyplan_active_run');
      }

      if (snapshot.thread_id) {
        localStorage.setItem('easyplan_thread_id', snapshot.thread_id);
      }

      let committedTaskTree = null;
      let previewTaskTree = null;
      let targetAppState: AppState = 'INITIAL';
      let targetView = get().view;

      if (isPending || isNextPhaseConfirming) {
        if (isNextPhasePreview) {
          committedTaskTree = alignedCommittedTaskTree || snapshot.task_tree || null;
          previewTaskTree = snapshot.interrupt_payload?.task_tree || null;
        } else {
          committedTaskTree = null;
          previewTaskTree = snapshot.interrupt_payload?.task_tree || null;
        }
        targetAppState = isNextPhaseConfirming ? 'SYNCING' : 'PENDING';
        targetView = get().selectedProjectId ? 'board' : get().view;
      } else if (isNextPhaseRunning || isStalled || shouldPreserveLocalNextPhase || preserveInitialRunning) {
        committedTaskTree = alignedCommittedTaskTree || snapshot.task_tree || null;
        previewTaskTree = null;
        targetAppState = 'THINKING';
        targetView = get().selectedProjectId ? 'board' : get().view;
      } else {
        committedTaskTree = alignedCommittedTaskTree || snapshot.task_tree || null;
        previewTaskTree = null;
        targetAppState = committedTaskTree ? 'INITIAL' : 'THINKING';
        targetView = (committedTaskTree && get().selectedProjectId) ? 'board' : get().view;
      }

      const nextBoardTasks = alignedTasks ? alignedTasks : get().boardTasks;

      set({
        threadId: snapshot.thread_id,
        intent: snapshot.intent_text,
        committedTaskTree,
        previewTaskTree,
        appState: targetAppState,
        view: targetView,
        previewMode: savedPreviewMode,
        phaseRequestId: recoveredPhaseRequestId || localStorage.getItem('easyplan_phase_request_id') || null,
        basePhaseId: recoveredBasePhaseId || localStorage.getItem('easyplan_base_phase_id') || null,
        isRunStalled: isStalled,
        boardTasks: nextBoardTasks,
        activeRun: finalActiveRun,
      });
      localStorage.setItem('easyplan_view', targetView);
      if (isNextPhaseConfirming && recoveredPhaseRequestId) {
        await get().finishAgentRun({
          thread_id: snapshot.thread_id,
          run_type: 'next_phase',
          request_id: recoveredPhaseRequestId,
          state_version: snapshot.state_version || 0
        });
      }
    } catch (err) {
      if (!isCurrent()) return;
      set({ error: (err as Error).message, appState: 'ERROR' });
    }
  },

  retryNode: async (nodeId: string) => {
    const { threadId, token } = get();
    if (!threadId) return;

    const requestId = generateUUID();
    set({
      syncRequestId: requestId,
      isRunStalled: false,
      reasoningLogs: [],
      previewTaskTree: null,
      nodeStatuses: { [nodeId]: 'syncing' },
      error: null
    });

    try {
      const headers: Record<string, string> = {
        'Content-Type': 'application/json',
        'X-User-Timezone': Intl.DateTimeFormat().resolvedOptions().timeZone
      };
      if (token) headers['Authorization'] = `Bearer ${token}`;

      const response = await fetch(`/api/threads/${threadId}/confirm`, {
        method: 'POST',
        headers,
        body: JSON.stringify({
          request_id: requestId,
          action: 'approve',
          // We can send specific nodes to retry if the API supports it,
          // but usually the backend knows what failed.
        })
      });

      if (!response.ok) throw new Error('Retry failed');
      // Status updates will come through SSE event: sync_status
    } catch (err) {
      set((state) => ({
        nodeStatuses: { ...state.nodeStatuses, [nodeId]: 'error' }
      }));
    }
  },

  confirmPlan: async () => {
    const { threadId, token } = get();
    if (!threadId) return;

    const { appState } = get();
    if (appState === 'SYNCING') return;

    set({ appState: 'SYNCING', error: null });

    const run = get().activeRun;
    if (!run || run.threadId !== threadId) {
      set({
        appState: 'ERROR',
        error: '当前规划会话已失效，请重新生成。',
      });
      return;
    }
    const requestId = run.requestId;

    try {
      const headers: Record<string, string> = {
        'Content-Type': 'application/json',
        'X-User-Timezone': Intl.DateTimeFormat().resolvedOptions().timeZone
      };
      if (token) headers['Authorization'] = `Bearer ${token}`;

      const response = await fetch(`/api/threads/${threadId}/confirm`, {
        method: 'POST',
        headers,
        body: JSON.stringify({
          request_id: requestId,
          action: 'approve'
        })
      });

      if (isUnauthorizedResponse(response)) {
        get().setToken(null);
        set({ showAuthModal: true, appState: 'PENDING', error: '登录已失效，请重新登录' });
        return;
      }

      if (response.status === 409) {
        set({
          error: '预览已过期/请求不匹配，请重新生成下一阶段',
          appState: 'PENDING'
        });
        return;
      }

      if (!response.ok) {
        let msg = 'Failed to confirm plan';
        try {
          const errData = await response.json();
          if (errData.detail) msg = errData.detail;
        } catch {
          // ignore JSON parse error
        }
        throw new Error(msg);
      }

      // Success transition is now handled by SSE 'done' event
    } catch (err) {
      set({ error: (err as Error).message, appState: 'ERROR' });
    }
  },

  collapsePlanningPanel: () => {
    set({
      previewMode: null,
      appState: 'INITIAL'
    });
    localStorage.removeItem('easyplan_preview_mode');
  },

  refinePlan: async (feedback: string) => {
    const { threadId, token } = get();
    if (!threadId) return;

    set({
      appState: 'THINKING',
      error: null,
      isRunStalled: false,
      reasoningLogs: [],
      committedTaskTree: null,
      previewTaskTree: null,
      nodeStatuses: {}
    });

    try {
      const headers: Record<string, string> = {
        'Content-Type': 'application/json',
        'X-User-Timezone': Intl.DateTimeFormat().resolvedOptions().timeZone
      };
      if (token) headers['Authorization'] = `Bearer ${token}`;

      const requestId = generateUUID();
      get().setActiveRun({
        threadId,
        runType: 'initial',
        requestId,
      });
      set({ syncRequestId: requestId });

      const response = await fetch(`/api/threads/${threadId}/confirm`, {
        method: 'POST',
        headers,
        body: JSON.stringify({
          request_id: requestId,
          action: 'refine',
          feedback
        })
      });

      if (isUnauthorizedResponse(response)) {
        get().setToken(null);
        set({ showAuthModal: true, appState: 'PENDING', error: '登录已失效，请重新登录' });
        return;
      }

      if (!response.ok) {
        let msg = 'Failed to refine plan';
        try {
          const errData = await response.json();
          if (errData.detail) msg = errData.detail;
        } catch {
          // ignore JSON parse error
        }
        throw new Error(msg);
      }
    } catch (err) {
      set({ error: (err as Error).message, appState: 'ERROR' });
    }
  }
}));
