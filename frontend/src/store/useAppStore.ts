import { create } from 'zustand';
import { TaskTree, TaskResponse } from '../types/api';
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

  // Actions
  setIntent: (intent: string) => void;
  setPreferredProvider: (provider: string) => void;
  setAppState: (state: AppState) => void;
  setThreadId: (id: string | null) => void;
  setToken: (token: string | null) => void;
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
  reset: () => void;

  // Actions
  alignState: (threadId: string) => Promise<void>;
  retryNode: (nodeId: string) => Promise<void>;
  submitIntent: (intentText: string) => Promise<void>;
  confirmPlan: () => Promise<void>;
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
  view: 'input',
  currentViewBucket: 'planned', // Default to planned after transition
  selectedProjectId: localStorage.getItem('easyplan_selected_project_id') || null,
  boardTasks: null,
  boardError: null,
  previewMode: (localStorage.getItem('easyplan_preview_mode') as PreviewMode) || null,
  phaseRequestId: localStorage.getItem('easyplan_phase_request_id') || null,
  isPhaseRequestPending: false,

  setIntent: (intent) => set({ intent }),
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
  setToken: (token) => {
    if (token) {
      localStorage.setItem('auth_token', token);
    } else {
      localStorage.removeItem('auth_token');
      // P0 Fix: Force memory cleanup on logout to prevent privacy leaks
      get().reset();
    }
    set({ token });
  },
  setShowAuthModal: (showAuthModal) => set({ showAuthModal }),
  setPendingIntent: (pendingIntent) => set({ pendingIntent }),
  setTheme: (theme) => {
    localStorage.setItem('app_theme', theme);
    set({ theme });
  },
  setView: (view) => {
    set({ view });
    if (view === 'board') {
      const { selectedProjectId } = get();
      if (!selectedProjectId) {
        set({ currentViewBucket: 'planned', selectedProjectId: null });
        get().fetchTasks('planned');
      } else {
        set({ currentViewBucket: 'planned' });
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
      syncRequestId: null,
      reasoningLogs: [],
      taskTree: null,
      nodeStatuses: {},
      error: null,
      showAuthModal: false,
      pendingIntent: null,
      view: 'input',
      boardTasks: null,
      boardError: null,
      previewMode: null,
      phaseRequestId: null,
    });
    localStorage.removeItem('easyplan_thread_id');
    localStorage.removeItem('easyplan_preview_mode');
    localStorage.removeItem('easyplan_phase_request_id');
  },

  finishAgentRun: async () => {
    const { view, previewMode, selectedProjectId, fetchTasks, loadProjectSnapshot } = get();
    if (previewMode === 'next_phase' && selectedProjectId) {
      set({
        view: 'board',
        previewMode: null,
        phaseRequestId: null,
        currentViewBucket: 'planned'
      });
      localStorage.removeItem('easyplan_preview_mode');
      localStorage.removeItem('easyplan_phase_request_id');
      await fetchTasks('planned');
      await loadProjectSnapshot(selectedProjectId);
    } else {
      if (view === 'board') {
        set({ currentViewBucket: 'planned', selectedProjectId: null });
        fetchTasks('planned');
      }
    }
  },

  loadProjectSnapshot: async (threadId: string) => {
    try {
      const { token } = get();
      const headers: Record<string, string> = {
        'X-User-Timezone': Intl.DateTimeFormat().resolvedOptions().timeZone
      };
      if (token) headers['Authorization'] = `Bearer ${token}`;

      const response = await fetch(`/api/threads/${threadId}`, { headers });
      if (!response.ok) throw new Error('Failed to load project snapshot');
      const snapshot = await response.json();

      set({
        taskTree: snapshot.task_tree
      });
    } catch (err) {
      console.error('loadProjectSnapshot failed', err);
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
      localStorage.removeItem('easyplan_preview_mode');
      localStorage.removeItem('easyplan_phase_request_id');
      get().fetchTasks();
    } catch (err) {
      console.error('cancelPlanPreview error:', err);
      set({ error: (err as Error).message });
    }
  },

  fetchTasks: async (bucket) => {
    const targetBucket = bucket || get().currentViewBucket;
    const { token } = get();
    if (!token) return;

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
    });
    localStorage.removeItem('easyplan_selected_project_id');
    localStorage.removeItem('easyplan_thread_id');
    localStorage.removeItem('easyplan_preview_mode');
    localStorage.removeItem('easyplan_phase_request_id');
    setTimeout(() => {
      set({ boardTasks: null, taskTree: null, nodeStatuses: {} });
    }, 500);
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
    const { token, selectedProjectId, taskTree, isPhaseRequestPending, phaseRequestId, boardTasks } = get();
    if (!token || !selectedProjectId || !taskTree?.planning_context || isPhaseRequestPending) return;
    if (import.meta.env.VITE_PHASE_PLANNING_ENABLED === 'false') return;

    // Use dynamic import or alternative to avoid circular deps if they occur
    const { selectPlanningView } = await import('./planningState');
    const planningView = selectPlanningView(taskTree, boardTasks || [], selectedProjectId);
    if (!planningView?.canUnlock) return;

    const requestId = phaseRequestId || generateUUID();
    set({ isPhaseRequestPending: true });

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
      set({ appState: 'THINKING', error: null });
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

      let savedPreviewMode = localStorage.getItem('easyplan_preview_mode') as PreviewMode;
      if (isPending) {
        if (isNextPhasePreview) {
          savedPreviewMode = 'next_phase';
          localStorage.setItem('easyplan_preview_mode', 'next_phase');
        } else if (!savedPreviewMode) {
          savedPreviewMode = 'initial';
          localStorage.setItem('easyplan_preview_mode', 'initial');
        }
      } else {
        localStorage.removeItem('easyplan_preview_mode');
        savedPreviewMode = null;
      }

      const recoveredPhaseRequestId = (isPending && isNextPhasePreview)
        ? (snapshot.interrupt_payload?.request_id || null)
        : null;

      if (recoveredPhaseRequestId) {
        localStorage.setItem('easyplan_phase_request_id', recoveredPhaseRequestId);
      } else if (!isPending) {
        localStorage.removeItem('easyplan_phase_request_id');
      }

      if (snapshot.thread_id) {
        localStorage.setItem('easyplan_thread_id', snapshot.thread_id);
      }

      const taskTree = isPending && snapshot.interrupt_payload?.task_tree
        ? snapshot.interrupt_payload.task_tree
        : (snapshot.task_tree || null);

      const targetAppState = isPending ? 'PENDING' : (taskTree ? 'INITIAL' : 'THINKING');
      const targetView = ((taskTree || isNextPhasePreview) && get().selectedProjectId) ? 'board' : get().view;

      set({
        threadId: snapshot.thread_id,
        intent: snapshot.intent_text,
        taskTree: taskTree,
        appState: targetAppState,
        view: targetView,
        previewMode: savedPreviewMode,
        phaseRequestId: recoveredPhaseRequestId || localStorage.getItem('easyplan_phase_request_id') || null,
      });
    } catch (err) {
      set({ error: (err as Error).message, appState: 'ERROR' });
    }
  },

  retryNode: async (nodeId: string) => {
    const { threadId, syncRequestId, token } = get();
    if (!threadId || !syncRequestId) return;

    set((state) => ({
      nodeStatuses: { ...state.nodeStatuses, [nodeId]: 'syncing' }
    }));

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
          request_id: syncRequestId,
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
  }
}));

