import { create } from 'zustand';
import { TaskTree, TaskResponse, ThreadSnapshot } from '../types/api';
import { buildAuthRecoveryState, isUnauthorizedResponse } from './authRecovery';
import { buildIntentRequest, resolvePlannerProvider } from './intentRequest';

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
  taskTree: TaskTree | null;
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
  isPhaseRequestPending: boolean;
  isRunStalled: boolean;
  projectSnapshots: Record<string, ThreadSnapshot>;
  highlightedProjectId: string | null;

  // Actions
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
  setTaskTree: (tree: TaskTree | null) => void;
  setNodeStatus: (nodeId: string, status: 'pending' | 'syncing' | 'success' | 'error') => void;
  setError: (error: string | null) => void;
  setRunStalled: (stalled: boolean) => void;
  setHighlightedProjectId: (id: string | null) => void;
  fetchProjectSnapshots: () => Promise<void>;
  reset: () => void;

  // Actions
  alignState: (threadId: string) => Promise<void>;
  retryNode: (nodeId: string) => Promise<void>;
  submitIntent: (intentText: string) => Promise<void>;
  confirmPlan: () => Promise<void>;
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
  finishAgentRun: () => Promise<void>;
  returnToCommittedPlan: () => Promise<void>;
}

export const useAppStore = create<AppStore>((set, get) => ({
  intent: '',
  appState: 'INITIAL',
  threadId: localStorage.getItem('easyplan_thread_id') || null,
  syncRequestId: null,
  reasoningLogs: [],
  taskTree: null,
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
  isPhaseRequestPending: false,
  isRunStalled: false,
  projectSnapshots: {},
  highlightedProjectId: null,

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
        set({ currentViewBucket: 'planned', selectedProjectId: null, taskTree: null, boardTasks: null });
        get().fetchTasks('planned');
      } else {
        set({ currentViewBucket: 'planned', taskTree: null, boardTasks: null });
        get().fetchTasks('planned');
      }
    }
  },
  setCurrentViewBucket: (bucket) => {
    set({ currentViewBucket: bucket });
    get().fetchTasks(bucket);
  },
  setSelectedProjectId: (projectId) => {
    set({ selectedProjectId: projectId });
    if (projectId) {
      localStorage.setItem('easyplan_selected_project_id', projectId);
      get().alignState(projectId);
    } else {
      localStorage.removeItem('easyplan_selected_project_id');
      set({
        threadId: null,
        taskTree: null,
        previewMode: null,
        phaseRequestId: null,
        appState: 'INITIAL',
      });
      localStorage.removeItem('easyplan_thread_id');
      localStorage.removeItem('easyplan_preview_mode');
      localStorage.removeItem('easyplan_phase_request_id');
    }
  },

  generateSyncId: () => set({ syncRequestId: generateUUID() }),

  addReasoningLog: (log) => set((state) => ({
    reasoningLogs: [...state.reasoningLogs, log]
  })),

  setTaskTree: (taskTree) => set({ taskTree }),

  setNodeStatus: (nodeId, status) => set((state) => ({
    nodeStatuses: { ...state.nodeStatuses, [nodeId]: status }
  })),

  setError: (error) => set({ error, appState: error ? 'ERROR' : 'INITIAL' }),

  reset: () => {
    taskStatusOverrides.clear();
    set({
      intent: '',
      appState: 'INITIAL',
      threadId: null,
      reasoningLogs: [],
      error: null,
      view: 'input',
      previewMode: null,
      phaseRequestId: null,
      isRunStalled: false,
      projectSnapshots: {},
      highlightedProjectId: null,
      selectedProjectId: null,
      boardTasks: null,
      boardError: null,
      taskTree: null,
      showAuthModal: false,
      pendingIntent: null,
      isPhaseRequestPending: false,
    });
    localStorage.setItem('easyplan_view', 'input');
    localStorage.removeItem('easyplan_selected_project_id');
    localStorage.removeItem('easyplan_thread_id');
    localStorage.removeItem('easyplan_preview_mode');
    localStorage.removeItem('easyplan_phase_request_id');
  },

  finishAgentRun: async () => {
    const { previewMode, selectedProjectId, threadId, fetchTasks, loadProjectSnapshot } = get();
    if (previewMode === 'next_phase' && selectedProjectId) {
      set({
        view: 'board',
        previewMode: null,
        phaseRequestId: null,
        currentViewBucket: 'planned'
      });
      localStorage.setItem('easyplan_view', 'board');
      localStorage.removeItem('easyplan_preview_mode');
      localStorage.removeItem('easyplan_phase_request_id');
      await fetchTasks('planned');
      await loadProjectSnapshot(selectedProjectId);
    } else {
      set({
        view: 'board',
        previewMode: null,
        phaseRequestId: null,
        currentViewBucket: 'planned',
        selectedProjectId: null,
        highlightedProjectId: threadId
      });
      localStorage.setItem('easyplan_view', 'board');
      localStorage.removeItem('easyplan_selected_project_id');
      localStorage.removeItem('easyplan_preview_mode');
      localStorage.removeItem('easyplan_phase_request_id');
      await fetchTasks('planned');
    }
  },

  loadProjectSnapshot: async (threadId: string) => {
    try {
      const { token } = get();
      if (!token) {
        set({ showAuthModal: true });
        throw new Error('请先登录以查看项目看板');
      }

      const headers: Record<string, string> = {
        'X-User-Timezone': Intl.DateTimeFormat().resolvedOptions().timeZone,
        'Authorization': `Bearer ${token}`
      };

      const response = await fetch(`/api/threads/${threadId}`, { headers });

      if (isUnauthorizedResponse(response)) {
        get().setToken(null, false);
        set({ showAuthModal: true });
        throw new Error('请先登录以查看项目看板');
      }

      if (!response.ok) throw new Error('Failed to load project snapshot');
      const snapshot = await response.json();

      set({
        taskTree: snapshot.task_tree
      });
    } catch (err) {
      console.error('loadProjectSnapshot failed', err);
      throw err;
    }
  },

  cancelPlanPreview: async () => {
    const { token, selectedProjectId } = get();
    if (!token || !selectedProjectId) return;

    set({ error: null });
    try {
      const response = await fetch(`/api/threads/${selectedProjectId}/phases/next/cancel`, {
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
        appState: 'INITIAL',
        taskTree: snapshot.task_tree || null,
        threadId: snapshot.thread_id,
        intent: snapshot.intent_text,
      });
      localStorage.setItem('easyplan_view', 'board');
      localStorage.removeItem('easyplan_preview_mode');
      localStorage.removeItem('easyplan_phase_request_id');
      get().fetchTasks();
    } catch (err) {
      console.error('cancelPlanPreview error:', err);
      set({ error: (err as Error).message });
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
        isRunStalled: false
      });
      localStorage.setItem('easyplan_view', 'board');
      localStorage.removeItem('easyplan_preview_mode');
      localStorage.removeItem('easyplan_phase_request_id');
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
        taskTree: null
      });
      localStorage.setItem('easyplan_view', 'input');
      localStorage.removeItem('easyplan_thread_id');
      localStorage.removeItem('easyplan_preview_mode');
      localStorage.removeItem('easyplan_phase_request_id');
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

      if (updatedTask.source === 'ai' && updatedTask.phase_id) {
        get().loadProjectSnapshot(updatedTask.thread_id);
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
      boardTasks: null,
      taskTree: null,
      nodeStatuses: {},
      isRunStalled: false
    });
    localStorage.setItem('easyplan_view', 'input');
    localStorage.removeItem('easyplan_selected_project_id');
    localStorage.removeItem('easyplan_thread_id');
    localStorage.removeItem('easyplan_preview_mode');
    localStorage.removeItem('easyplan_phase_request_id');
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
      if (isCurrentProject) {
        localStorage.removeItem('easyplan_selected_project_id');
        localStorage.removeItem('easyplan_preview_mode');
        localStorage.removeItem('easyplan_phase_request_id');
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
      });
    } catch (err) {
      console.error("Delete plan failed", err);
      throw err;
    }
  },

  generateNextPhasePlan: async () => {
    const { token, selectedProjectId, taskTree, isPhaseRequestPending, boardTasks } = get();
    if (!token || !selectedProjectId || !taskTree?.planning_context || isPhaseRequestPending) return;
    if (import.meta.env.VITE_PHASE_PLANNING_ENABLED === 'false') return;

    // Use dynamic import or alternative to avoid circular deps if they occur
    const { selectPlanningView } = await import('./planningState');
    const planningView = selectPlanningView(taskTree, boardTasks || [], selectedProjectId);
    if (!planningView?.canUnlock) return;

    const requestId = generateUUID();
    set({
      isPhaseRequestPending: true,
      isRunStalled: false,
      reasoningLogs: [],
      taskTree: null,
      nodeStatuses: {},
      error: null,
      phaseRequestId: requestId
    });

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
        set({ showAuthModal: true, isPhaseRequestPending: false });
        return;
      }

      if (!response.ok) throw new Error('Failed to generate next phase plan');

      set({
        threadId: selectedProjectId,
        previewMode: 'next_phase',
        phaseRequestId: requestId,
        view: 'input',
        appState: 'THINKING'
      });
      localStorage.setItem('easyplan_view', 'input');
      localStorage.setItem('easyplan_thread_id', selectedProjectId);
      localStorage.setItem('easyplan_preview_mode', 'next_phase');
      localStorage.setItem('easyplan_phase_request_id', requestId);
    } catch (err) {
      console.error("Generate next phase plan failed", err);
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
        taskTree: null,
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
      set({ intent: intentText, threadId: data.thread_id, pendingIntent: null });
      if (data.thread_id) {
        localStorage.setItem('easyplan_thread_id', data.thread_id);
      }
    } catch (err) {
      set({ error: (err as Error).message, appState: 'ERROR' });
    }
  },

  alignState: async (threadId: string) => {
    try {
      const { token } = get();
      const headers: Record<string, string> = {
        'X-User-Timezone': Intl.DateTimeFormat().resolvedOptions().timeZone
      };
      if (token) headers['Authorization'] = `Bearer ${token}`;

      const response = await fetch(`/api/threads/${threadId}`, { headers });
      if (!response.ok) throw new Error('Failed to align state');
      const snapshot = await response.json();

      const isPending = snapshot.status === 'awaiting_confirmation';
      const isNextPhasePreview = isPending && snapshot.interrupt_payload?.type === 'next_phase_review';

      const localPreviewMode = localStorage.getItem('easyplan_preview_mode') as PreviewMode;
      const isNextPhaseRunning = snapshot.status === 'running' && localPreviewMode === 'next_phase';
      const isStalled = snapshot.status === 'stalled';

      let savedPreviewMode: PreviewMode = null;
      let recoveredPhaseRequestId: string | null = null;

      if (isPending) {
        if (isNextPhasePreview) {
          savedPreviewMode = 'next_phase';
          localStorage.setItem('easyplan_preview_mode', 'next_phase');
          recoveredPhaseRequestId = snapshot.interrupt_payload?.request_id || null;
        } else {
          savedPreviewMode = 'initial';
          localStorage.setItem('easyplan_preview_mode', 'initial');
        }
      } else if (isNextPhaseRunning || (isStalled && localPreviewMode === 'next_phase')) {
        savedPreviewMode = 'next_phase';
        recoveredPhaseRequestId = localStorage.getItem('easyplan_phase_request_id');
      } else {
        localStorage.removeItem('easyplan_preview_mode');
        localStorage.removeItem('easyplan_phase_request_id');
        savedPreviewMode = null;
      }

      if (recoveredPhaseRequestId) {
        localStorage.setItem('easyplan_phase_request_id', recoveredPhaseRequestId);
      }

      if (snapshot.thread_id) {
        localStorage.setItem('easyplan_thread_id', snapshot.thread_id);
      }

      let taskTree = null;
      let targetAppState: AppState = 'INITIAL';
      let targetView = get().view;

      if (isPending) {
        taskTree = snapshot.interrupt_payload?.task_tree || null;
        targetAppState = 'PENDING';
        targetView = get().selectedProjectId ? 'board' : get().view;
      } else if (isNextPhaseRunning || isStalled) {
        taskTree = null;
        targetAppState = 'THINKING';
        targetView = get().selectedProjectId ? 'board' : get().view;
      } else {
        taskTree = snapshot.task_tree || null;
        targetAppState = taskTree ? 'INITIAL' : 'THINKING';
        targetView = (taskTree && get().selectedProjectId) ? 'board' : get().view;
      }

      set({
        threadId: snapshot.thread_id,
        intent: snapshot.intent_text,
        taskTree: taskTree,
        appState: targetAppState,
        view: targetView,
        previewMode: savedPreviewMode,
        phaseRequestId: recoveredPhaseRequestId || localStorage.getItem('easyplan_phase_request_id') || null,
        isRunStalled: isStalled,
      });
      localStorage.setItem('easyplan_view', targetView);
    } catch (err) {
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
      taskTree: null,
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
    const { threadId, token, syncRequestId, phaseRequestId, previewMode } = get();
    if (!threadId) return;

    const { appState } = get();
    if (appState === 'SYNCING') return;

    set({ appState: 'SYNCING', error: null });

    let requestId: string;
    if (previewMode === 'next_phase') {
      if (!phaseRequestId) {
        set({ error: '预览已过期/请求不匹配，请重新生成下一阶段', appState: 'ERROR' });
        return;
      }
      requestId = phaseRequestId;
    } else {
      requestId = syncRequestId || generateUUID();
      if (!syncRequestId) {
        set({ syncRequestId: requestId });
      }
    }

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

  refinePlan: async (feedback: string) => {
    const { threadId, token } = get();
    if (!threadId) return;

    set({
      appState: 'THINKING',
      error: null,
      isRunStalled: false,
      reasoningLogs: [],
      taskTree: null,
      nodeStatuses: {}
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
          request_id: generateUUID(),
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

