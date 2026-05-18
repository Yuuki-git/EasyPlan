import codecs
import re

app_store_path = 'frontend/src/store/useAppStore.ts'
with codecs.open(app_store_path, 'r', 'utf-8') as f:
    app_content = f.read()

# 1. Update createManualTask interface, add startNewIntent, deleteThread
app_content = app_content.replace(
    'createManualTask: (title: string) => Promise<void>;',
    'createManualTask: (title: string, options?: { thread_id?: string | null }) => Promise<void>;\n  startNewIntent: () => void;\n  deleteThread: (threadId: string) => Promise<void>;'
)

# 2. Implementation of createManualTask
old_create = r"  createManualTask: async \(title: string\) => \{\s*const \{ token, currentViewBucket, boardTasks \} = get\(\);\s*if \(!token\) return;\s*const shouldAddToMyDay = currentViewBucket === 'my_day';\s*try \{\s*const headers: Record<string, string> = \{\s*'Content-Type': 'application/json',\s*'Authorization': `Bearer \$\{token\}`\s*\};\s*const response = await fetch\('/api/tasks', \{\s*method: 'POST',\s*headers,\s*body: JSON\.stringify\(\{\s*title,\s*view_bucket: shouldAddToMyDay \? 'planned' : currentViewBucket,\s*is_in_my_day: shouldAddToMyDay\s*\}\)\s*\}\);"

new_create = """  createManualTask: async (title: string, options?: { thread_id?: string | null }) => {
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
      });"""

app_content = re.sub(old_create, new_create, app_content)

# 3. Add startNewIntent and deleteThread
new_methods = """  startNewIntent: () => {
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
  },"""

app_content = app_content.replace('  generateNextPhasePlan: async () => {', new_methods + '\n\n  generateNextPhasePlan: async () => {')

# 4. Make updateTaskStatus optimistic
old_update = r"  updateTaskStatus: async \(taskId: string, status: 'completed' \| 'active'\) => \{.*?\} catch \(err\) \{\s*console\.error\(\"Update task status failed\", err\);\s*throw err; // allow component to revert visual state\s*\}\s*\},"

new_update = """  updateTaskStatus: async (taskId: string, status: 'completed' | 'active') => {
    const { token, boardTasks } = get();
    if (!token) return;

    const originalTasks = boardTasks ? [...boardTasks] : [];
    // Optimistic UI sync
    set({
      boardTasks: (boardTasks || []).map(t => t.id === taskId ? { ...t, status } : t)
    });

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
        get().setToken(null);
        set({ showAuthModal: true });
        return;
      }

      if (!response.ok) throw new Error('Failed to update task status');
    } catch (err) {
      console.error("Update task status failed", err);
      set({ boardTasks: originalTasks });
      throw err;
    }
  },"""

app_content = re.sub(old_update, new_update, app_content, flags=re.DOTALL)

with codecs.open(app_store_path, 'w', 'utf-8') as f:
    f.write(app_content)
