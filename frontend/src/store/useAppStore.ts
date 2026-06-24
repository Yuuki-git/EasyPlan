import { create } from 'zustand';
import { TaskTree, TaskResponse } from '../types/api';
import { buildAuthRecoveryState, isUnauthorizedResponse } from './authRecovery';
import { buildIntentRequest, resolvePlannerProvider } from './intentRequest';

const generateUUID = () => {
  if (typeof crypto !== 'undefined' && 'randomUUID' in crypto) {
    return crypto.randomUUID();
  }
  return Math.random().toString(36).substring(2, 15) + Math.random().toString(36).substring(2, 15);
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
}

export const useAppStore = create<AppStore>((set, get) => ({
  intent: '',
  appState: 'INITIAL',
  threadId: null,
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
  selectedProjectId: null,
  boardTasks: null,
  boardError: null,

  setIntent: (intent) => set({ intent }),
  setPreferredProvider: (preferredProvider) => set({ preferredProvider }),
  setAppState: (appState) => {
    set({ appState });
    if (appState === 'SUCCESS' && get().view === 'board') {
      set({ currentViewBucket: 'planned', selectedProjectId: null });
      get().fetchTasks('planned');
    }
  },
  setThreadId: (threadId) => set({ threadId }),
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
      set({ currentViewBucket: 'planned', selectedProjectId: null });
      get().fetchTasks('planned');
    }
  },
  setCurrentViewBucket: (bucket) => {
    set({ currentViewBucket: bucket });
    get().fetchTasks(bucket);
  },
  setSelectedProjectId: (projectId) => set({ selectedProjectId: projectId }),
  
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
      boardError: null
    });
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
    });
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
      set({
        boardTasks: (boardTasks || []).filter(t => t.thread_id !== threadId),
        selectedProjectId: selectedProjectId === threadId ? null : selectedProjectId
      });
    } catch (err) {
      console.error("Delete plan failed", err);
      throw err;
    }
  },

  generateNextPhasePlan: async () => {
    const { boardTasks, currentViewBucket } = get();
    const tasks = boardTasks || [];
    const plannedTasks = tasks.filter((task) => task.view_bucket === currentViewBucket);
    const completedCount = plannedTasks.filter((task) => task.status === 'completed').length;
    const taskSnapshot = plannedTasks
      .slice(0, 20)
      .map((task, index) => {
        const statusLabel = task.status === 'completed' ? '已完成' : '未完成';
        const estimate = task.estimated_minutes != null ? `${task.estimated_minutes}分钟` : '未估时';
        const description = task.description ? `：${task.description}` : '';
        return `${index + 1}. [${statusLabel}] ${task.title}${description} (${estimate})`;
      })
      .join('\n');

    const intentText = [
      '请基于我当前 EasyPlan 原生任务看板中已经完成的 Phase 1，生成下一阶段 Phase 2 的计划。',
      '保持 Fog of War Lite：只解锁下一阶段，不要排完整长期周期。',
      '请保持原始意图类型，不要重新解释为短期交付或情境清单；本次只是为同一目标解锁下一阶段。',
      `当前视图：${currentViewBucket}`,
      `当前任务数量：${plannedTasks.length}，已完成：${completedCount}`,
      taskSnapshot ? `当前任务快照：\n${taskSnapshot}` : '当前任务快照为空，请给出一个保守的下一阶段启动计划。',
    ].join('\n');

    set({
      view: 'input',
      appState: 'THINKING',
      error: null,
      reasoningLogs: [],
      taskTree: null,
      nodeStatuses: {},
    });
    await get().submitIntent(intentText);
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
      
      set({
        threadId: snapshot.thread_id,
        intent: snapshot.intent_text,
        taskTree: snapshot.task_tree,
        appState: snapshot.status === 'awaiting_confirmation' ? 'PENDING' : 'THINKING'
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
    const { threadId, token, syncRequestId } = get();
    if (!threadId) return;

    set({ appState: 'SYNCING', error: null });
    
    // Generate request ID if it doesn't exist yet
    const requestId = syncRequestId || generateUUID();
    if (!syncRequestId) {
      set({ syncRequestId: requestId });
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

      if (!response.ok) throw new Error('Failed to confirm plan');
      
      // Success transition is now handled by SSE 'done' event
    } catch (err) {
      set({ error: (err as Error).message, appState: 'ERROR' });
    }
  }
}));

