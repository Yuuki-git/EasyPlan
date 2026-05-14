import codecs
import re

app_store_path = 'frontend/src/store/useAppStore.ts'
with codecs.open(app_store_path, 'r', 'utf-8') as f:
    app_store_content = f.read()

# Update AppStore interface
app_store_content = app_store_content.replace(
    'moveTaskToMyDay: (taskId: string) => Promise<void>;',
    'toggleTaskInMyDay: (taskId: string, currentState: boolean) => Promise<void>;'
)

# Update implementation
old_move_impl = """  moveTaskToMyDay: async (taskId: string) => {
    const { token, boardTasks } = get();
    if (!token) return;

    // Optimistically remove from current planned view immediately for visual smoothness
    set({
      boardTasks: (boardTasks || []).filter(t => t.id !== taskId)
    });

    try {
      const headers: Record<string, string> = {
        'Content-Type': 'application/json',
        'Authorization': `Bearer ${token}`
      };
      const response = await fetch(`/api/tasks/${taskId}`, {
        method: 'PATCH',
        headers,
        body: JSON.stringify({ view_bucket: 'my_day' })
      });
      
      // P1 Fix: Global Auth Recovery
      if (isUnauthorizedResponse(response)) {
        get().setToken(null);
        set({ showAuthModal: true });
        return;
      }

      if (!response.ok) throw new Error('Failed to move task to my day');
    } catch (err) {
      console.error("Move task to my day failed", err);
      // Revert if failed by refetching
      get().fetchTasks();
      throw err;
    }
  },"""

new_toggle_impl = """  toggleTaskInMyDay: async (taskId: string, currentState: boolean) => {
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
        return;
      }

      if (!response.ok) throw new Error('Failed to toggle task in my day');
    } catch (err) {
      console.error("Toggle task in my day failed", err);
      // Revert on error
      set({ boardTasks: originalTasks });
      throw err;
    }
  },"""

app_store_content = app_store_content.replace(old_move_impl, new_toggle_impl)

with codecs.open(app_store_path, 'w', 'utf-8') as f:
    f.write(app_store_content)
print('Replaced moveTaskToMyDay in useAppStore')
