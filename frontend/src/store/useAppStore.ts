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
  isIntegrated: boolean;
  error: string | null;

  // Actions
  setIntent: (intent: string) => void;
  setAppState: (state: AppState) => void;
  setThreadId: (id: string | null) => void;
  generateSyncId: () => void;
  addReasoningLog: (log: string) => void;
  setTaskTree: (tree: TaskTree | null) => void;
  setError: (error: string | null) => void;
  reset: () => void;

  // SSE placeholder
  alignState: (threadId: string) => Promise<void>;
}

export const useAppStore = create<AppStore>((set) => ({
  intent: '',
  appState: 'INITIAL',
  threadId: null,
  syncRequestId: null,
  reasoningLogs: [],
  taskTree: null,
  isIntegrated: false,
  error: null,

  setIntent: (intent) => set({ intent }),
  setAppState: (appState) => set({ appState }),
  setThreadId: (threadId) => set({ threadId }),
  
  generateSyncId: () => set({ syncRequestId: crypto.randomUUID() }),
  
  addReasoningLog: (log) => set((state) => ({ 
    reasoningLogs: [...state.reasoningLogs, log] 
  })),
  
  setTaskTree: (taskTree) => set({ taskTree }),
  
  setError: (error) => set({ error, appState: error ? 'ERROR' : 'INITIAL' }),
  
  reset: () => set({
    intent: '',
    appState: 'INITIAL',
    threadId: null,
    syncRequestId: null,
    reasoningLogs: [],
    taskTree: null,
    error: null,
  }),

  alignState: async (threadId: string) => {
    try {
      const response = await fetch(`/api/threads/${threadId}`, {
        headers: {
          'X-User-Timezone': Intl.DateTimeFormat().resolvedOptions().timeZone
        }
      });
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
  }
}));
