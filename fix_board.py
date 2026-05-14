import codecs

file_path = 'frontend/src/components/TaskBoard.tsx'

with codecs.open(file_path, 'r', 'utf-8', errors='ignore') as f:
    content = f.read()

# Replace destructuring
content = content.replace(
    'const { updateTaskStatus, moveTaskToMyDay, currentViewBucket, updateTaskDetails, deleteTask } = useAppStore();',
    'const { updateTaskStatus, toggleTaskInMyDay, currentViewBucket, updateTaskDetails, deleteTask } = useAppStore();'
)

# Replace handle method
old_handle = """  const handleMoveToMyDay = async (e: React.MouseEvent) => {
    e.stopPropagation();
    try {
      await moveTaskToMyDay(node.id);
    } catch (err) {
      // Error handled in store
    }
  };"""

new_handle = """  const handleToggleMyDay = async (e: React.MouseEvent) => {
    e.stopPropagation();
    try {
      await toggleTaskInMyDay(node.id, !!node.is_in_my_day);
    } catch (err) {
      // Error handled in store
    }
  };"""

content = content.replace(old_handle, new_handle)

# Replace Sun button logic
old_sun = """          {currentViewBucket === 'planned' && (
            <button
              onClick={handleMoveToMyDay}
              className="p-1.5 text-muted-foreground hover:text-amber-500 hover:bg-amber-500/10 rounded-md pointer-events-auto transition-colors"
              title="加入我的一天"
            >
              <Sun size={16} />
            </button>
          )}"""

new_sun = """          {currentViewBucket === 'planned' && (
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

content = content.replace(old_sun, new_sun)

with codecs.open(file_path, 'w', 'utf-8') as f:
    f.write(content)
print('Applied TaskBoard changes via Python')
