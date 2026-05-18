import codecs
import re

board_path = 'frontend/src/components/TaskBoard.tsx'
with codecs.open(board_path, 'r', 'utf-8') as f:
    board_content = f.read()

# 1. Update InlineTaskInput to use selectedProjectId
old_inline = r'const InlineTaskInput: React\.FC = \(\) => \{\s*const \{ createManualTask \} = useAppStore\(\);'
new_inline = '''const InlineTaskInput: React.FC = () => {
  const { createManualTask, selectedProjectId } = useAppStore();'''
board_content = re.sub(old_inline, new_inline, board_content)

old_submit = r'await createManualTask\(taskTitle\);'
new_submit = 'await createManualTask(taskTitle, { thread_id: selectedProjectId });'
board_content = board_content.replace(old_submit, new_submit)

# 2. Update handleNewPlan to use startNewIntent
old_new_plan = r"  const handleNewPlan = \(\) => \{\s*if \(isGenerating\) \{\s*setView\('input'\);\s*\} else \{\s*setView\('input'\);\s*useAppStore\.getState\(\)\.setAppState\('INITIAL'\);\s*setTimeout\(\(\) => reset\(\), 500\);\s*\}\s*\};"
new_new_plan = '''  const handleNewPlan = () => {
    useAppStore.getState().startNewIntent();
  };'''
board_content = re.sub(old_new_plan, new_new_plan, board_content)

# 3. Add delete plan button in Sidebar
old_sidebar_button = r'                  <span className=\"truncate text-sm\">\{project\.title\}</span>\n                </button>'
new_sidebar_button = '''                  <span className="truncate text-sm">{project.title}</span>
                </button>
                {currentViewBucket === 'planned' && selectedProjectId === project.id && (
                  <button
                    onClick={(e) => {
                      e.stopPropagation();
                      if (window.confirm('确定删除这个计划吗？其中的任务也会被删除，此操作不可恢复。')) {
                        useAppStore.getState().deleteThread(project.id).then(() => {
                          useAppStore.getState().setSelectedProjectId(null);
                        });
                      }
                    }}
                    className="absolute right-2 p-1.5 text-muted-foreground hover:text-red-500 hover:bg-red-500/10 rounded-md transition-colors opacity-0 group-hover:opacity-100"
                    title="删除计划"
                  >
                    <Trash2 size={14} />
                  </button>
                )}'''
board_content = board_content.replace(old_sidebar_button, new_sidebar_button)

# Also add "group relative" to the Sidebar button wrapper.
# Find the map block in Sidebar:
old_map_block = r'              \{projects\.map\(project => \(\s*<button\n\s*key=\{project\.id\}'
new_map_block = '''              {projects.map(project => (
                <div key={project.id} className="group relative w-full flex items-center">
                <button'''
board_content = re.sub(old_map_block, new_map_block, board_content)

# And add the closing </div>
old_button_close = r'                  title=\{project\.title\}\n                >\n                  <Folder size=\{14\} className=\"shrink-0 opacity-70\" />\n                  <span className=\"truncate text-sm\">\{project\.title\}</span>\n                </button>\n              \)\)\}'
new_button_close = '''                  title={project.title}
                >
                  <Folder size={14} className="shrink-0 opacity-70" />
                  <span className="truncate text-sm">{project.title}</span>
                </button>
                {currentViewBucket === 'planned' && selectedProjectId === project.id && (
                  <button
                    onClick={(e) => {
                      e.stopPropagation();
                      if (window.confirm('确定删除这个计划吗？其中的任务也会被删除，此操作不可恢复。')) {
                        useAppStore.getState().deleteThread(project.id).then(() => {
                          useAppStore.getState().setSelectedProjectId(null);
                        });
                      }
                    }}
                    className="absolute right-2 p-1.5 text-muted-foreground hover:text-red-500 hover:bg-red-500/10 rounded-md transition-colors opacity-0 group-hover:opacity-100"
                    title="删除计划"
                  >
                    <Trash2 size={14} />
                  </button>
                )}
                </div>
              ))}'''
board_content = re.sub(old_button_close, new_button_close, board_content)


# 4. Fix handleToggle in BoardTaskNode
old_handle_toggle = r"    if \(!localCompleted\) \{\s*// Complete with ritual delay\s*setLocalCompleted\(true\);\s*timeoutRef\.current = setTimeout\(async \(\) => \{\s*try \{\s*await updateTaskStatus\(node\.id, 'completed'\);\s*\} catch \(err\) \{\s*setLocalCompleted\(false\);\s*\} finally \{\s*timeoutRef\.current = null;\s*\}\s*\}, 2000\);\s*\} else \{\s*// Uncheck instantly\s*setLocalCompleted\(false\);\s*try \{\s*await updateTaskStatus\(node\.id, 'active'\);\s*\} catch \(err\) \{\s*setLocalCompleted\(true\);\s*\}\s*\}"
new_handle_toggle = """    if (!localCompleted) {
      setLocalCompleted(true);
      // Fire backend request immediately for instant sync
      updateTaskStatus(node.id, 'completed').catch(() => setLocalCompleted(false));
      
      // Use timeout to block spam clicking
      timeoutRef.current = setTimeout(() => {
        timeoutRef.current = null;
      }, 2000);
    } else {
      setLocalCompleted(false);
      updateTaskStatus(node.id, 'active').catch(() => setLocalCompleted(true));
    }"""
board_content = re.sub(old_handle_toggle, new_handle_toggle, board_content)

# 5. Hide Fog of War Next Phase
old_fog_of_war = r'\{showFogOfWar && \(\s*<motion\.div'
new_fog_of_war = '''{false && showFogOfWar && (
              <motion.div'''
board_content = re.sub(old_fog_of_war, new_fog_of_war, board_content)

with codecs.open(board_path, 'w', 'utf-8') as f:
    f.write(board_content)

print("TaskBoard.tsx fixes applied via Python")
