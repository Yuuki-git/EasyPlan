import codecs
import re

# 1. Update useAppStore.ts
app_store_path = 'frontend/src/store/useAppStore.ts'
with codecs.open(app_store_path, 'r', 'utf-8') as f:
    app_content = f.read()

# Replace interface definition
app_content = re.sub(
    r'moveTaskToMyDay:\s*\(taskId:\s*string\)\s*=>\s*Promise<void>;',
    r'toggleTaskInMyDay: (taskId: string, currentState: boolean) => Promise<void>;',
    app_content
)

# Replace implementation
old_impl_pattern = r'  moveTaskToMyDay:\s*async\s*\(taskId:\s*string\)\s*=>\s*\{.*?\n  \},'
new_impl = """  toggleTaskInMyDay: async (taskId: string, currentState: boolean) => {
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
  },"""

app_content = re.sub(old_impl_pattern, new_impl, app_content, flags=re.DOTALL)

with codecs.open(app_store_path, 'w', 'utf-8') as f:
    f.write(app_content)

# 2. Update TaskBoard.tsx
board_path = 'frontend/src/components/TaskBoard.tsx'
with codecs.open(board_path, 'r', 'utf-8') as f:
    board_content = f.read()

# Fix dummy root nodes
board_content = re.sub(
    r'sort_order: 0,\n\s*children:',
    r'sort_order: 0,\n        is_in_my_day: false,\n        children:',
    board_content
)

# Fix handleMoveToMyDay
old_handle_pattern = r'  const handleMoveToMyDay\s*=\s*async\s*\(e:\s*React\.MouseEvent\)\s*=>\s*\{.*?\n  \};\n'
new_handle = """  const handleToggleMyDay = async (e: React.MouseEvent) => {
    e.stopPropagation();
    try {
      await toggleTaskInMyDay(node.id, !!node.is_in_my_day);
    } catch (err) {
      // Error handled in store
    }
  };\n"""

board_content = re.sub(old_handle_pattern, new_handle, board_content, flags=re.DOTALL)

# Fix onClick={handleMoveToMyDay} to onClick={handleToggleMyDay} and update class/title
old_sun_pattern = r'\{currentViewBucket === \'planned\' && \(\n\s*<button\n\s*onClick=\{handleMoveToMyDay\}\n\s*className="p-1\.5 text-muted-foreground hover:text-amber-500 hover:bg-amber-500/10 rounded-md pointer-events-auto transition-colors"\n\s*title="加入我的一天"\n\s*>\n\s*<Sun size=\{16\} />\n\s*</button>\n\s*\)\}'

new_sun = """{currentViewBucket === 'planned' && (
            <button
              onClick={handleToggleMyDay}
              className={`p-1.5 rounded-md pointer-events-auto transition-colors ${
                node.is_in_my_day
                  ? "text-amber-500 bg-amber-500/10 hover:text-muted-foreground hover:bg-transparent"
                  : "text-muted-foreground hover:text-amber-500 hover:bg-amber-500/10"
              }`}
              title={node.is_in_my_day ? "移出我的一天" : "加入我的一天"}
            >
              <Sun size={16} />
            </button>
          )}"""

board_content = re.sub(old_sun_pattern, new_sun, board_content)

# Fix any remaining handleMoveToMyDay
board_content = board_content.replace('handleMoveToMyDay', 'handleToggleMyDay')

with codecs.open(board_path, 'w', 'utf-8') as f:
    f.write(board_content)

print('TaskBoard and useAppStore fixed via Python')
