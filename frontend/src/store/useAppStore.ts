import { create } from 'zustand';
import { TaskTree } from '../types/api';

export type AppState = 
  | 'INITIAL' 
  | 'THINKING' 
  | 'PENDING' 
  | 'SYNCING' 
  | 'SUCCESS' 
  | 'PARTIAL_ERROR' 
  | 'ERROR';

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

  // Actions
  setIntent: (intent: string) => void;
  setPreferredProvider: (provider: string) => void;
  setAppState: (state: AppState) => void;
  setThreadId: (id: string | null) => void;
  setToken: (token: string | null) => void;
  generateSyncId: () => void;
  addReasoningLog: (log: string) => void;
  setTaskTree: (tree: TaskTree | null) => void;
  setNodeStatus: (nodeId: string, status: 'pending' | 'syncing' | 'success' | 'error') => void;
  setError: (error: string | null) => void;
  reset: () => void;

  // Actions
  alignState: (threadId: string) => Promise<void>;
  retryNode: (nodeId: string) => Promise<void>;
}

export const useAppStore = create<AppStore>((set, get) => ({
  intent: '',
  appState: 'INITIAL',
  threadId: null,
  syncRequestId: null,
  reasoningLogs: [],
  taskTree: null,
  nodeStatuses: {},
  preferredProvider: 'todoist',
  isIntegrated: false,
  error: null,
  token: localStorage.getItem('auth_token'),

  setIntent: (intent) => set({ intent }),
  setPreferredProvider: (preferredProvider) => set({ preferredProvider }),
  setAppState: (appState) => set({ appState }),
  setThreadId: (threadId) => set({ threadId }),
  setToken: (token) => {
    if (token) {
      localStorage.setItem('auth_token', token);
    } else {
      localStorage.removeItem('auth_token');
    }
    set({ token });
  },
  
  generateSyncId: () => set({ syncRequestId: crypto.randomUUID() }),
  
  addReasoningLog: (log) => set((state) => ({ 
    reasoningLogs: [...state.reasoningLogs, log] 
  })),
  
  setTaskTree: (taskTree) => set({ taskTree }),

  setNodeStatus: (nodeId, status) => set((state) => ({
    nodeStatuses: { ...state.nodeStatuses, [nodeId]: status }
  })),
  
  setError: (error) => set({ error, appState: error ? 'ERROR' : 'INITIAL' }),
  
  reset: () => set({
    intent: '',
    appState: 'INITIAL',
    threadId: null,
    syncRequestId: null,
    reasoningLogs: [],
    taskTree: null,
    nodeStatuses: {},
    error: null,
  }),

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
        appState: snapshot.status === 'interrupt' ? 'PENDING' : 'THINKING'
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
  }
}));

