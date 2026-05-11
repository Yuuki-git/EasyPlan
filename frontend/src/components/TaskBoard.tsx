import React, { useMemo } from 'react';
import { motion } from 'framer-motion';
import { useAppStore } from '../store/useAppStore';
import { Sun, Calendar, Menu, Plus, CheckCircle2, Circle } from 'lucide-react';
import { clsx } from 'clsx';
import { TaskResponse } from '../types/api';

const Sidebar: React.FC<{ isOpen: boolean; toggle: () => void }> = ({ isOpen }) => {
  const { currentViewBucket, setCurrentViewBucket } = useAppStore();

  return (
    <motion.div
      initial={{ width: 240 }}
      animate={{ width: isOpen ? 240 : 0, opacity: isOpen ? 1 : 0 }}
      className="h-full bg-background/50 border-r border-muted/30 backdrop-blur-md overflow-hidden shrink-0"
    >
      <div className="w-[240px] p-4 flex flex-col h-full">
        <div className="flex items-center justify-between mb-8">
          <span className="font-medium text-foreground/80 tracking-wide px-2">Views</span>
        </div>
        
        <div className="space-y-1 flex-1">
          <button 
            onClick={() => setCurrentViewBucket('my_day')}
            className={clsx(
              "w-full flex items-center gap-3 px-3 py-2 rounded-lg font-medium transition-colors",
              currentViewBucket === 'my_day' ? "bg-accent/20 text-accent-foreground" : "text-muted-foreground hover:bg-muted/30"
            )}
          >
            <Sun size={16} className={currentViewBucket === 'my_day' ? "text-amber-500" : ""} />
            <span>我的一天 (My Day)</span>
          </button>
          <button 
            onClick={() => setCurrentViewBucket('planned')}
            className={clsx(
              "w-full flex items-center gap-3 px-3 py-2 rounded-lg transition-colors",
              currentViewBucket === 'planned' ? "bg-accent/20 text-accent-foreground font-medium" : "text-muted-foreground hover:bg-muted/30"
            )}
          >
            <Calendar size={16} className={currentViewBucket === 'planned' ? "text-blue-500" : ""} />
            <span>计划中 (Planned)</span>
          </button>
        </div>
      </div>
    </motion.div>
  );
};

interface TreeNode extends TaskResponse {
  children?: TreeNode[];
}

const BoardTaskNode: React.FC<{ node: TreeNode; depth?: number }> = ({ node, depth = 0 }) => {
  const { updateTaskStatus, moveTaskToMyDay, currentViewBucket } = useAppStore();
  const isGroup = node.node_type === 'group';
  const hasChildren = node.children && node.children.length > 0;
  const isCompleted = node.status === 'completed';

  const handleToggle = async (e: React.MouseEvent) => {
    e.stopPropagation();
    if (isGroup) return;
    
    // Optistic update handled by store, but we call the api here
    const newStatus = isCompleted ? 'active' : 'completed';
    try {
      await updateTaskStatus(node.id, newStatus);
    } catch (err) {
      // Error is handled/logged in store
    }
  };

  const handleMoveToMyDay = async (e: React.MouseEvent) => {
    e.stopPropagation();
    try {
      await moveTaskToMyDay(node.id);
    } catch (err) {
      // Error handled in store
    }
  };

  if (isGroup) {
    return (
      <div className={clsx(node.title !== 'root_dummy' && "mb-8")}>
        {node.title !== 'root_dummy' && (
          <h2 className="text-lg font-semibold text-foreground/90 tracking-tight mt-6 mb-3 pb-2 border-b border-muted/60">
            {node.title}
          </h2>
        )}
        {hasChildren && (
          <div className="flex flex-col space-y-2">
            {node.children!.map(child => (
              <BoardTaskNode key={child.id} node={child} depth={node.title === 'root_dummy' ? depth : depth + 1} />
            ))}
          </div>
        )}
      </div>
    );
  }

  // Action Node
  return (
    <motion.div 
      layout
      initial={{ opacity: 0, y: 10 }}
      animate={{ opacity: 1, y: 0 }}
      exit={{ opacity: 0, scale: 0.95, transition: { duration: 0.2 } }}
      className={clsx(
        "group flex items-start gap-3 p-3 rounded-xl border transition-all cursor-pointer relative",
        isCompleted 
          ? "bg-muted/10 border-transparent" 
          : "bg-background border-muted/50 hover:border-muted hover:shadow-sm",
        depth > 0 && "ml-4"
      )}
      onClick={handleToggle}
    >
      <div className="mt-0.5 shrink-0">
        {isCompleted ? (
          <CheckCircle2 size={18} className="text-green-500" />
        ) : (
          <Circle size={18} className="text-muted-foreground/30 group-hover:text-foreground/50 transition-colors" />
        )}
      </div>
      <div className="flex-1 pr-8">
        <h4 className={clsx(
          "text-base transition-colors",
          isCompleted ? "text-muted-foreground/50 line-through decoration-muted-foreground/30" : "text-foreground/90 font-medium"
        )}>
          {node.title}
        </h4>
        {node.description && (
          <p className={clsx(
            "text-xs mt-1 transition-colors",
            isCompleted ? "text-muted-foreground/30 line-through" : "text-muted-foreground/60"
          )}>
            {node.description}
          </p>
        )}
        {!isCompleted && node.estimated_minutes != null && (
          <div className="flex items-center gap-2 mt-2">
            <span className="text-[10px] font-mono text-muted-foreground/50 bg-muted/20 px-2 py-0.5 rounded-full">
              {node.estimated_minutes} min
            </span>
          </div>
        )}
      </div>
      
      {currentViewBucket === 'planned' && !isCompleted && (
        <button
          onClick={handleMoveToMyDay}
          className="absolute right-3 top-3 opacity-0 group-hover:opacity-100 transition-opacity p-1.5 text-muted-foreground hover:text-amber-500 hover:bg-amber-500/10 rounded-md"
          title="加入我的一天"
        >
          <Sun size={16} />
        </button>
      )}
    </motion.div>
  );
};

const InlineTaskInput: React.FC = () => {
  const { createManualTask } = useAppStore();
  const [isAdding, setIsAdding] = React.useState(false);
  const [title, setTitle] = React.useState('');
  const inputRef = React.useRef<HTMLInputElement>(null);

  React.useEffect(() => {
    if (isAdding && inputRef.current) {
      inputRef.current.focus();
    }
  }, [isAdding]);

  const handleSubmit = async () => {
    if (!title.trim()) {
      setIsAdding(false);
      return;
    }
    
    const taskTitle = title.trim();
    setTitle(''); // Clear immediately for UX
    try {
      await createManualTask(taskTitle);
      // Keep input open to add more
    } catch (err) {
      // Error is handled/logged in store
    }
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter') {
      handleSubmit();
    } else if (e.key === 'Escape') {
      setIsAdding(false);
      setTitle('');
    }
  };

  if (!isAdding) {
    return (
      <button 
        onClick={() => setIsAdding(true)}
        className="mt-8 flex items-center gap-2 text-muted-foreground/50 hover:text-foreground/80 transition-colors py-2 group w-full"
      >
        <div className="p-1 rounded-full group-hover:bg-muted/20 transition-colors">
          <Plus size={16} />
        </div>
        <span className="text-sm">添加任务...</span>
      </button>
    );
  }

  return (
    <motion.div 
      initial={{ opacity: 0, y: -10 }}
      animate={{ opacity: 1, y: 0 }}
      className="mt-8 flex items-center gap-3 p-2 rounded-xl border border-muted/50 bg-background focus-within:border-foreground/30 focus-within:ring-1 focus-within:ring-foreground/10 transition-all"
    >
      <Circle size={18} className="text-muted-foreground/30 ml-1" />
      <input
        ref={inputRef}
        type="text"
        value={title}
        onChange={(e) => setTitle(e.target.value)}
        onKeyDown={handleKeyDown}
        onBlur={() => {
          if (!title.trim()) setIsAdding(false);
        }}
        placeholder="输入任务名称，按回车保存"
        className="flex-1 bg-transparent border-none focus:outline-none text-base text-foreground/90 placeholder:text-muted-foreground/40"
      />
    </motion.div>
  );
};

export const TaskBoard: React.FC = () => {
  const { currentViewBucket, boardTasks, boardError, reset, setView, fetchTasks } = useAppStore();
  const [sidebarOpen, setSidebarOpen] = React.useState(true);

  const displayTree = useMemo(() => {
    if (!boardTasks) return null;

    if (currentViewBucket === 'my_day') {
      // Flat list
      const root: TreeNode = {
        id: 'root',
        title: 'root_dummy',
        node_type: 'group',
        status: 'active',
        user_id: '',
        thread_id: '',
        parent_task_id: null,
        client_node_id: 'root',
        description: null,
        view_bucket: 'my_day',
        estimated_minutes: null,
        sort_order: 0,
        children: boardTasks.sort((a, b) => a.sort_order - b.sort_order).map(t => ({ ...t }))
      };
      return root;
    } else {
      // Planned: Reconstruct tree
      const taskMap = new Map<string, TreeNode>();
      
      boardTasks.forEach(t => {
        taskMap.set(t.id, { ...t, children: [] });
      });

      const rootChildren: TreeNode[] = [];

      boardTasks.sort((a, b) => a.sort_order - b.sort_order).forEach(t => {
        const node = taskMap.get(t.id)!;
        if (t.parent_task_id && taskMap.has(t.parent_task_id)) {
          taskMap.get(t.parent_task_id)!.children!.push(node);
        } else {
          rootChildren.push(node);
        }
      });

      const root: TreeNode = {
        id: 'root',
        title: 'root_dummy',
        node_type: 'group',
        status: 'active',
        user_id: '',
        thread_id: '',
        parent_task_id: null,
        client_node_id: 'root',
        description: null,
        view_bucket: 'planned',
        estimated_minutes: null,
        sort_order: 0,
        children: rootChildren
      };
      return root;
    }
  }, [boardTasks, currentViewBucket]);

  if (boardError) {
    return (
      <motion.div 
        initial={{ opacity: 0, x: 20 }}
        animate={{ opacity: 1, x: 0 }}
        exit={{ opacity: 0, x: 20 }}
        transition={{ duration: 0.5, ease: [0.22, 1, 0.36, 1] }}
        className="fixed inset-0 bg-background flex flex-col items-center justify-center gap-4 z-40"
      >
        <p className="text-destructive font-medium">{boardError}</p>
        <button 
          onClick={() => fetchTasks()} 
          className="px-4 py-2 bg-muted hover:bg-muted/80 rounded-md transition-colors"
        >
          重新加载
        </button>
      </motion.div>
    );
  }

  if (!displayTree) {
    return (
      <motion.div 
        initial={{ opacity: 0, x: 20 }}
        animate={{ opacity: 1, x: 0 }}
        exit={{ opacity: 0, x: 20 }}
        transition={{ duration: 0.5, ease: [0.22, 1, 0.36, 1] }}
        className="fixed inset-0 bg-background flex items-center justify-center z-40"
      >
        <p className="text-muted-foreground animate-pulse">Loading tasks...</p>
      </motion.div>
    );
  }

  const isEmpty = !displayTree.children || displayTree.children.length === 0;

  const handleNewPlan = () => {
    setView('input');
    useAppStore.getState().setAppState('INITIAL');
    setTimeout(() => reset(), 500);
  };

  return (
    <motion.div 
      initial={{ opacity: 0, x: 20 }}
      animate={{ opacity: 1, x: 0 }}
      exit={{ opacity: 0, x: 20 }}
      transition={{ duration: 0.5, ease: [0.22, 1, 0.36, 1] }}
      className="fixed inset-0 bg-background flex z-40"
    >
      <Sidebar isOpen={sidebarOpen} toggle={() => setSidebarOpen(!sidebarOpen)} />
      
      <div className="flex-1 flex flex-col h-full overflow-hidden">
        <header className="h-16 border-b border-muted/20 flex items-center px-4 shrink-0 bg-background/80 backdrop-blur-sm z-10 justify-between">
          <div className="flex items-center gap-4">
            <button 
              onClick={() => setSidebarOpen(!sidebarOpen)}
              className="text-muted-foreground hover:text-foreground transition-colors p-2 rounded-lg hover:bg-muted/20"
            >
              <Menu size={20} />
            </button>
            <h1 className="text-xl font-medium tracking-tight text-foreground">
              {currentViewBucket === 'my_day' ? '☀️ 我的一天' : '📅 计划中'}
            </h1>
          </div>
          
          <div className="flex items-center gap-4">
            <button 
              onClick={handleNewPlan}
              className="text-xs text-muted-foreground hover:text-foreground transition-colors px-3 py-1.5 rounded-full hover:bg-muted/20"
            >
              新计划
            </button>
          </div>
        </header>
        
        <main className="flex-1 overflow-y-auto p-8 lg:px-24">
          <div className="max-w-3xl mx-auto pb-32">
            {isEmpty ? (
              <div className="flex flex-col items-center justify-center h-64 text-center space-y-4">
                <p className="text-muted-foreground/60 text-lg">
                  {currentViewBucket === 'planned' 
                    ? "您的计划库空空如也。点击右上角新建意图，让 AI 为您分忧。"
                    : "今天的事情都搞定啦！去喝杯茶，享受生活吧 ☕️"}
                </p>
                {currentViewBucket === 'planned' && (
                  <button 
                    onClick={handleNewPlan}
                    className="px-4 py-2 border border-muted/50 rounded-lg text-sm text-foreground/70 hover:bg-muted/10 transition-colors"
                  >
                    新建意图
                  </button>
                )}
              </div>
            ) : (
              <BoardTaskNode node={displayTree} />
            )}
            
            <InlineTaskInput />
          </div>
        </main>
      </div>
    </motion.div>
  );
};

