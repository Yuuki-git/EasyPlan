import codecs
import re

file_path = 'frontend/src/components/TaskBoard.tsx'

with codecs.open(file_path, 'r', 'utf-8', errors='ignore') as f:
    content = f.read()

new_sidebar = """const Sidebar: React.FC<{ isOpen: boolean; toggle: () => void }> = ({ isOpen }) => {
  const { currentViewBucket, setCurrentViewBucket, boardTasks, selectedProjectId, setSelectedProjectId } = useAppStore();

  const projects = useMemo(() => {
    if (!boardTasks) return [];
    const projectMap = new Map<string, { id: string; title: string }>();
    boardTasks.forEach(task => {
      if (task.parent_task_id === null && task.thread_id) {
        projectMap.set(task.thread_id, {
          id: task.thread_id,
          title: task.title
        });
      }
    });
    return Array.from(projectMap.values());
  }, [boardTasks]);

  return (
    <motion.div
      initial={{ width: 240 }}
      animate={{ width: isOpen ? 240 : 0, opacity: isOpen ? 1 : 0 }}
      className="h-full bg-background/50 border-r border-muted/30 backdrop-blur-md overflow-hidden shrink-0 flex flex-col"
    >
      <div className="w-[240px] p-4 flex flex-col h-full">
        <div className="flex items-center justify-between mb-8">
          <span className="font-medium text-foreground/80 tracking-wide px-2">我的手帐</span>
        </div>
        
        <div className="space-y-1 mb-6">
          <button 
            onClick={() => {
              setCurrentViewBucket('my_day');
            }}
            className={clsx(
              "w-full flex items-center gap-3 px-3 py-2 rounded-lg font-medium transition-colors",
              currentViewBucket === 'my_day' ? "bg-accent/20 text-accent-foreground" : "text-muted-foreground hover:bg-muted/30"
            )}
          >
            <Sun size={16} className={currentViewBucket === 'my_day' ? "text-amber-500" : ""} />
            <span>我的一天 (My Day)</span>
          </button>
          <button 
            onClick={() => {
              setCurrentViewBucket('planned');
              setSelectedProjectId(null);
            }}
            className={clsx(
              "w-full flex items-center gap-3 px-3 py-2 rounded-lg transition-colors",
              currentViewBucket === 'planned' && selectedProjectId === null ? "bg-accent/20 text-accent-foreground font-medium" : "text-muted-foreground hover:bg-muted/30"
            )}
          >
            <Calendar size={16} className={currentViewBucket === 'planned' && selectedProjectId === null ? "text-blue-500" : ""} />
            <span>全部计划 (All Planned)</span>
          </button>
        </div>

        {projects.length > 0 && (
          <>
            <div className="h-px bg-muted/30 mx-2 mb-4" />
            <div className="px-2 mb-2 text-xs font-semibold text-muted-foreground/50 tracking-wider uppercase">
              项目
            </div>
            <div className="space-y-1 flex-1 overflow-y-auto overflow-x-hidden pr-2 custom-scrollbar">
              {projects.map(project => (
                <button
                  key={project.id}
                  onClick={() => {
                    setCurrentViewBucket('planned');
                    setSelectedProjectId(project.id);
                  }}
                  className={clsx(
                    "w-full flex items-center gap-3 px-3 py-2 rounded-lg transition-colors text-left",
                    currentViewBucket === 'planned' && selectedProjectId === project.id ? "bg-accent/20 text-accent-foreground font-medium" : "text-muted-foreground hover:bg-muted/30"
                  )}
                  title={project.title}
                >
                  <Folder size={14} className="shrink-0 opacity-70" />
                  <span className="truncate text-sm">{project.title}</span>
                </button>
              ))}
            </div>
          </>
        )}
      </div>
    </motion.div>
  );
};
"""

content = re.sub(
    r'const Sidebar: React\.FC<\{ isOpen: boolean; toggle: \(\) => void \}> = \(\{ isOpen \}\) => \{.*?</motion\.div>\n\s*};\n'
    , new_sidebar, content, flags=re.DOTALL)

with codecs.open(file_path, 'w', 'utf-8') as f:
    f.write(content)
print('Sidebar replaced using regex')
