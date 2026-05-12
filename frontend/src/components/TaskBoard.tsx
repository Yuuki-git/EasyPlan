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
          <span className="font-medium text-foreground/80 tracking-wide px-2">我的手帐</span>
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
  const { updateTaskStatus, moveTaskToMyDay, currentViewBucket, updateTaskDetails } = useAppStore();
  const isGroup = node.node_type === 'group';
  const hasChildren = node.children && node.children.length > 0;
  
  const [localCompleted, setLocalCompleted] = React.useState(node.status === 'completed');
  const timeoutRef = React.useRef<ReturnType<typeof setTimeout> | null>(null);

  // Inline editing state
  const [isEditing, setIsEditing] = React.useState(false);
  const [editTitle, setEditTitle] = React.useState(node.title);
  const [editMinutes, setEditMinutes] = React.useState(node.estimated_minutes?.toString() || '');
  const editTitleRef = React.useRef<HTMLInputElement>(null);

  React.useEffect(() => {
    setLocalCompleted(node.status === 'completed');
  }, [node.status]);

  React.useEffect(() => {
    setEditTitle(node.title);
    setEditMinutes(node.estimated_minutes?.toString() || '');
  }, [node.title, node.estimated_minutes]);

  React.useEffect(() => {
    return () => {
      if (timeoutRef.current) {
        clearTimeout(timeoutRef.current);
      }
    };
  }, []);

  React.useEffect(() => {
    if (isEditing && editTitleRef.current) {
      editTitleRef.current.focus();
    }
  }, [isEditing]);

  const handleToggle = async (e: React.MouseEvent) => {
    e.stopPropagation();
    if (isGroup || isEditing) return;
    
    // Prevent double clicking during ritual
    if (timeoutRef.current) return;

    if (!localCompleted) {
      // Complete with ritual delay
      setLocalCompleted(true);
      timeoutRef.current = setTimeout(async () => {
        try {
          await updateTaskStatus(node.id, 'completed');
        } catch (err) {
          setLocalCompleted(false);
        } finally {
          timeoutRef.current = null;
        }
      }, 2000);
    } else {
      // Uncheck instantly
      setLocalCompleted(false);
      try {
        await updateTaskStatus(node.id, 'active');
      } catch (err) {
        setLocalCompleted(true);
      }
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

  const handleDoubleClick = (e: React.MouseEvent) => {
    e.stopPropagation();
    if (localCompleted || isGroup) return; // Prevent editing completed or group tasks for now
    setIsEditing(true);
  };

  const handleEditSubmit = async () => {
    if (!editTitle.trim()) {
      setIsEditing(false);
      setEditTitle(node.title);
      return;
    }

    const updates: { title?: string; estimated_minutes?: number | null } = {};
    if (editTitle.trim() !== node.title) updates.title = editTitle.trim();
    
    const minutesVal = parseInt(editMinutes);
    if (!isNaN(minutesVal) && minutesVal !== node.estimated_minutes) {
      updates.estimated_minutes = minutesVal;
    } else if (editMinutes.trim() === '' && node.estimated_minutes != null) {
      updates.estimated_minutes = null;
    }

    setIsEditing(false);

    if (Object.keys(updates).length > 0) {
      try {
        await updateTaskDetails(node.id, updates);
      } catch (err) {
        // Revert on error
        setEditTitle(node.title);
        setEditMinutes(node.estimated_minutes?.toString() || '');
      }
    }
  };

  const handleEditKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter') {
      handleEditSubmit();
    } else if (e.key === 'Escape') {
      setIsEditing(false);
      setEditTitle(node.title);
      setEditMinutes(node.estimated_minutes?.toString() || '');
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
        localCompleted 
          ? "bg-muted/10 border-transparent" 
          : "bg-background border-muted/50 hover:border-muted hover:shadow-sm",
        (timeoutRef.current || isEditing) && "pointer-events-none cursor-default",
        depth > 0 && "ml-4"
      )}
      onClick={handleToggle}
      onDoubleClick={handleDoubleClick}
    >
      <div className="mt-0.5 shrink-0">
        {localCompleted ? (
          <motion.div
            initial={{ scale: 0.5, opacity: 0 }}
            animate={{ scale: 1, opacity: 1 }}
            transition={{ type: "spring", stiffness: 300, damping: 20 }}
          >
            <CheckCircle2 size={18} className="text-green-500" />
          </motion.div>
        ) : (
          <Circle size={18} className={clsx(
            "text-muted-foreground/30 transition-colors",
            !isEditing && "group-hover:text-foreground/50"
          )} />
        )}
      </div>
      <div className="flex-1 pr-8">
        {isEditing ? (
          <div className="flex flex-col gap-1 pointer-events-auto mt-[-2px]">
            <input
              ref={editTitleRef}
              type="text"
              value={editTitle}
              onChange={(e) => setEditTitle(e.target.value)}
              onKeyDown={handleEditKeyDown}
              onBlur={handleEditSubmit}
              className="text-base font-medium bg-transparent border-none focus:outline-none focus:ring-0 p-0 w-full text-foreground/90 placeholder:text-muted-foreground/30"
              placeholder="任务标题..."
            />
            <div className="flex items-center gap-1 mt-1">
              <input
                type="number"
                value={editMinutes}
                onChange={(e) => setEditMinutes(e.target.value)}
                onKeyDown={handleEditKeyDown}
                onBlur={handleEditSubmit}
                placeholder="耗时"
                className="text-[10px] font-mono bg-muted/20 text-muted-foreground/80 border-none focus:outline-none focus:ring-1 focus:ring-foreground/20 focus:bg-muted/40 rounded-full px-2 py-0.5 w-16"
              />
              <span className="text-[10px] text-muted-foreground/50">min</span>
            </div>
          </div>
        ) : (
          <>
            <h4 className={clsx(
              "text-base transition-colors",
              localCompleted ? "text-muted-foreground/50 line-through decoration-muted-foreground/30" : "text-foreground/90 font-medium"
            )}>
              {node.title}
            </h4>
            {node.description && (
              <p className={clsx(
                "text-xs mt-1 transition-colors",
                localCompleted ? "text-muted-foreground/30 line-through" : "text-muted-foreground/60"
              )}>
                {node.description}
              </p>
            )}
            {!localCompleted && node.estimated_minutes != null && (
              <div className="flex items-center gap-2 mt-2">
                <span className="text-[10px] font-mono text-muted-foreground/50 bg-muted/20 px-2 py-0.5 rounded-full">
                  {node.estimated_minutes} min
                </span>
              </div>
            )}
          </>
        )}
      </div>
      
      {currentViewBucket === 'planned' && !localCompleted && !isEditing && (
        <button
          onClick={handleMoveToMyDay}
          className="absolute right-3 top-3 opacity-0 group-hover:opacity-100 transition-opacity p-1.5 text-muted-foreground hover:text-amber-500 hover:bg-amber-500/10 rounded-md pointer-events-auto"
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
  const { currentViewBucket, boardTasks, boardError, reset, setView, fetchTasks, appState } = useAppStore();
  const [sidebarOpen, setSidebarOpen] = React.useState(true);
  
  const isGenerating = appState === 'THINKING' || appState === 'PENDING' || appState === 'SYNCING';

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
    if (isGenerating) {
      setView('input');
    } else {
      setView('input');
      useAppStore.getState().setAppState('INITIAL');
      setTimeout(() => reset(), 500);
    }
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
              {isGenerating ? '返回当前意图' : '新计划'}
            </button>
          </div>
        </header>
        
        <main className="flex-1 overflow-y-auto p-8 lg:px-24">
          <div className="max-w-3xl mx-auto pb-32">
            {isEmpty ? (
              <div className="flex flex-col items-center justify-center h-64 text-center space-y-4">
                <p className="text-muted-foreground/60 text-lg">
                  {currentViewBucket === 'planned' 
                    ? "您的专属空间空空如也。点击右上角，让 AI 为您分忧。"
                    : "今天的事情都搞定啦！去喝杯茶，享受生活吧 ☕️"}
                </p>
                {currentViewBucket === 'planned' && (
                  <button 
                    onClick={handleNewPlan}
                    className="px-4 py-2 border border-muted/50 rounded-lg text-sm text-foreground/70 hover:bg-muted/10 transition-colors"
                  >
                    {isGenerating ? '返回当前意图' : '新建意图'}
                  </button>
                )}
              </div>
            ) : (
              <BoardTaskNode node={displayTree} />
            )}
            
            <InlineTaskInput />
            
            {currentViewBucket === 'planned' && !isEmpty && (
              <motion.div 
                initial={{ opacity: 0 }}
                animate={{ opacity: 1 }}
                className="mt-12 flex justify-center"
              >
                <button 
                  onClick={() => console.log('Coming soon in v1.3: Contextual Continuation')}
                  className="group relative px-6 py-3 rounded-full overflow-hidden transition-all hover:scale-105 active:scale-95"
                >
                  <div className="absolute inset-0 bg-foreground/5 opacity-50 group-hover:opacity-100 transition-opacity" />
                  <motion.div 
                    animate={{ 
                      boxShadow: ['0px 0px 0px 0px rgba(168, 85, 247, 0)', '0px 0px 20px 2px rgba(168, 85, 247, 0.3)', '0px 0px 0px 0px rgba(168, 85, 247, 0)'] 
                    }}
                    transition={{ duration: 3, repeat: Infinity, ease: "easeInOut" }}
                    className="absolute inset-0 rounded-full border border-purple-500/30" 
                  />
                  <span className="relative text-sm font-medium text-purple-500/80 group-hover:text-purple-400 transition-colors flex items-center gap-2">
                    <span className="text-lg">✨</span> 当前阶段已完成，让 AI 生成下一阶段计划
                  </span>
                </button>
              </motion.div>
            )}
          </div>
        </main>
      </div>
    </motion.div>
  );
};

