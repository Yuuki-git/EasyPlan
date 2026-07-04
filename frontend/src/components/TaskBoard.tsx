import React, { useMemo, useEffect } from 'react';
import { motion } from 'framer-motion';
import { useAppStore } from '../store/useAppStore';
import { Sun, Calendar, Menu, Plus, CheckCircle2, Circle, Pencil, Trash2, Folder, ChevronDown } from 'lucide-react';
import { clsx } from 'clsx';
import { TaskNode, TaskResponse } from '../types/api';
import { PlanningOverview } from './PlanningOverview';
import { PortfolioOverview } from './PortfolioOverview';
import { selectPlanningView } from '../store/planningState';

const Sidebar: React.FC<{ isOpen: boolean; toggle: () => void }> = ({ isOpen }) => {
  const { currentViewBucket, setCurrentViewBucket, boardTasks, selectedProjectId, setSelectedProjectId } = useAppStore();

  const projects = useMemo(() => {
    if (!boardTasks) return [];
    const projectMap = new Map<string, { id: string; title: string; source?: string }>();
    boardTasks.forEach(task => {
      if (task.parent_task_id === null && task.thread_id) {
        const existing = projectMap.get(task.thread_id);
        if (!existing || (existing.source === 'manual' && task.source === 'ai')) {
          projectMap.set(task.thread_id, {
            id: task.thread_id,
            title: task.title,
            source: task.source
          });
        }
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
            <span>我的一天</span>
          </button>
          <button
            onClick={() => {
              setCurrentViewBucket('planned');
              setSelectedProjectId(null); // All planned
            }}
            className={clsx(
              "w-full flex items-center gap-3 px-3 py-2 rounded-lg transition-colors",
              currentViewBucket === 'planned' && selectedProjectId === null ? "bg-accent/20 text-accent-foreground font-medium" : "text-muted-foreground hover:bg-muted/30"
            )}
          >
            <Calendar size={16} className={currentViewBucket === 'planned' && selectedProjectId === null ? "text-blue-500" : ""} />
            <span>想法画布</span>
          </button>        </div>

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

interface TreeNode extends TaskResponse {
  children?: TreeNode[];
}

function buildPreviewTree(node: TaskNode, threadId: string, sortOrder = 0, parentTaskId: string | null = null): TreeNode {
  const previewId = `preview:${node.client_node_id}`;
  return {
    id: previewId,
    user_id: '',
    thread_id: threadId,
    parent_task_id: parentTaskId,
    client_node_id: node.client_node_id,
    title: node.title,
    description: node.description ?? null,
    node_type: node.node_type,
    status: 'active',
    view_bucket: 'planned',
    estimated_minutes: node.estimated_minutes,
    sort_order: sortOrder,
    is_in_my_day: false,
    done_criteria: node.done_criteria ?? null,
    start_hint: node.start_hint ?? null,
    fallback_action: node.fallback_action ?? null,
    source: 'ai',
    phase_id: null,
    phase_order: null,
    children: (node.children || []).map((child, index) => buildPreviewTree(child, threadId, index, previewId)),
  };
}

const BoardTaskNode: React.FC<{ node: TreeNode; depth?: number; interactive?: boolean }> = ({
  node,
  depth = 0,
  interactive = true,
}) => {
  const { updateTaskStatus, toggleTaskInMyDay, updateTaskDetails, deleteTask } = useAppStore();
  const isGroup = node.node_type === 'group';
  const hasChildren = node.children && node.children.length > 0;

  const [localCompleted, setLocalCompleted] = React.useState(node.status === 'completed');
  const [isToggling, setIsToggling] = React.useState(false);

  // Inline editing state
  const [isEditing, setIsEditing] = React.useState(false);
  const [editTitle, setEditTitle] = React.useState(node.title);
  const [editDescription, setEditDescription] = React.useState(node.description || '');
  const [editMinutes, setEditMinutes] = React.useState(node.estimated_minutes?.toString() || '');
  const editTitleRef = React.useRef<HTMLInputElement>(null);
  const editContainerRef = React.useRef<HTMLDivElement>(null);

  React.useEffect(() => {
    setLocalCompleted(node.status === 'completed');
  }, [node.status]);

  React.useEffect(() => {
    setEditTitle(node.title);
    setEditDescription(node.description || '');
    setEditMinutes(node.estimated_minutes?.toString() || '');
  }, [node.title, node.description, node.estimated_minutes]);

  React.useEffect(() => {
    if (isEditing && editTitleRef.current) {
      editTitleRef.current.focus();
    }
  }, [isEditing]);

  const handleToggle = async (e: React.MouseEvent) => {
    e.stopPropagation();
    if (!interactive || isGroup || isEditing) return;
    if (isToggling) return;

    const nextCompleted = !localCompleted;
    const nextStatus = nextCompleted ? 'completed' : 'active';

    setIsToggling(true);
    setLocalCompleted(nextCompleted);

    try {
      await updateTaskStatus(node.id, nextStatus);
    } catch {
      setLocalCompleted(!nextCompleted);
    } finally {
      setIsToggling(false);
    }
  };

  const handleToggleMyDay = async (e: React.MouseEvent) => {
    e.stopPropagation();
    if (!interactive) return;
    try {
      await toggleTaskInMyDay(node.id, !!node.is_in_my_day);
    } catch (err) {
      // Error handled in store
    }
  };

  const handleDelete = async (e: React.MouseEvent) => {
    e.stopPropagation();
    if (!interactive) return;
    try {
      await deleteTask(node.id);
    } catch (err) {
      // Error handled in store
    }
  };

  const handleDoubleClick = (e: React.MouseEvent) => {
    e.stopPropagation();
    if (!interactive || localCompleted || isGroup) return; // Prevent editing completed or group tasks for now
    setIsEditing(true);
  };

  const handleEditSubmit = async () => {
    if (!editTitle.trim()) {
      setIsEditing(false);
      setEditTitle(node.title);
      return;
    }

    const updates: { title?: string; description?: string | null; estimated_minutes?: number | null } = {};
    if (editTitle.trim() !== node.title) updates.title = editTitle.trim();
    const nextDescription = editDescription.trim() || null;
    if (nextDescription !== (node.description || null)) updates.description = nextDescription;

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
        setEditDescription(node.description || '');
        setEditMinutes(node.estimated_minutes?.toString() || '');
      }
    }
  };

  const handleEditKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleEditSubmit();
    } else if (e.key === 'Escape') {
      setIsEditing(false);
      setEditTitle(node.title);
      setEditDescription(node.description || '');
      setEditMinutes(node.estimated_minutes?.toString() || '');
    }
  };

  const handleEditBlur = (e: React.FocusEvent<HTMLDivElement>) => {
    const nextFocusedElement = e.relatedTarget;
    if (nextFocusedElement instanceof Node && editContainerRef.current?.contains(nextFocusedElement)) {
      return;
    }
    handleEditSubmit();
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
              <BoardTaskNode
                key={child.id}
                node={child}
                depth={node.title === 'root_dummy' ? depth : depth + 1}
                interactive={interactive}
              />
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
        !interactive && "cursor-default",
        localCompleted
          ? "bg-muted/10 border-transparent"
          : "bg-background border-muted/50 hover:border-muted hover:shadow-sm",
        isEditing && "cursor-default",
        isToggling && "cursor-wait",
        depth > 0 && "ml-4"
      )}
      onClick={interactive ? handleToggle : undefined}
      onDoubleClick={interactive ? handleDoubleClick : undefined}
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
      <div className="flex-1 pr-24">
        {isEditing ? (
          <div
            ref={editContainerRef}
            onBlur={handleEditBlur}
            className="flex flex-col gap-1 pointer-events-auto mt-[-2px]"
          >
            <input
              ref={editTitleRef}
              type="text"
              value={editTitle}
              onChange={(e) => setEditTitle(e.target.value)}
              onKeyDown={handleEditKeyDown}
              className="text-base font-medium bg-transparent border-none focus:outline-none focus:ring-0 p-0 w-full text-foreground/90 placeholder:text-muted-foreground/30"
              placeholder="任务标题..."
            />
            <textarea
              value={editDescription}
              onChange={(e) => setEditDescription(e.target.value)}
              onKeyDown={handleEditKeyDown}
              rows={2}
              className="text-xs bg-muted/10 text-muted-foreground/80 border-none focus:outline-none focus:ring-1 focus:ring-foreground/20 focus:bg-muted/20 rounded-md px-2 py-1 w-full resize-none placeholder:text-muted-foreground/30"
              placeholder="任务描述..."
            />
            <div className="flex items-center gap-1 mt-1">
              <input
                type="number"
                value={editMinutes}
                onChange={(e) => setEditMinutes(e.target.value)}
                onKeyDown={handleEditKeyDown}
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
            {node.done_criteria && (
              <div className={clsx(
                "text-xs mt-2 transition-colors font-medium break-words",
                localCompleted ? "text-muted-foreground/30 line-through" : "text-foreground/70"
              )}>
                完成标准：{node.done_criteria}
              </div>
            )}
            {(node.start_hint || node.fallback_action) && (
              <details
                className="mt-3 text-xs group/details outline-none"
                onClick={(e) => e.stopPropagation()}
              >
                <summary className={clsx(
                  "cursor-pointer select-none transition-colors outline-none",
                  localCompleted ? "text-muted-foreground/30" : "text-muted-foreground/70 hover:text-foreground/90"
                )}>
                  执行提示
                </summary>
                <div className={clsx(
                  "mt-2 pl-3 border-l border-muted/30 space-y-1.5 break-words",
                  localCompleted ? "text-muted-foreground/30" : "text-muted-foreground/80"
                )}>
                  {node.start_hint && <div><span className="font-medium text-foreground/70">如何开始：</span>{node.start_hint}</div>}
                  {node.fallback_action && <div><span className="font-medium text-foreground/70">做不动时：</span>{node.fallback_action}</div>}
                </div>
              </details>
            )}
          </>
        )}
      </div>

      {interactive && (
        <button
        onClick={handleToggleMyDay}
        className={clsx(
          "absolute right-3 top-3 p-1.5 rounded-md pointer-events-auto transition-colors",
          node.is_in_my_day
            ? "text-amber-500 bg-amber-500/10 opacity-100"
            : "text-muted-foreground/40 opacity-0 group-hover:opacity-100 hover:text-amber-500 hover:bg-amber-500/10"
        )}
        title={node.is_in_my_day ? "移出我的一天" : "加入我的一天"}
      >
        <Sun size={16} />
        </button>
      )}

      {interactive && !localCompleted && !isEditing && (
        <div className="absolute right-12 top-3 opacity-0 group-hover:opacity-100 transition-opacity flex items-center gap-1">
          <button
            onClick={(e) => {
              e.stopPropagation();
              setIsEditing(true);
            }}
            className="p-1.5 text-muted-foreground hover:text-blue-500 hover:bg-blue-500/10 rounded-md pointer-events-auto transition-colors"
            title="编辑"
          >
            <Pencil size={16} />
          </button>
          <button
            onClick={handleDelete}
            className="p-1.5 text-muted-foreground hover:text-red-500 hover:bg-red-500/10 rounded-md pointer-events-auto transition-colors"
            title="删除任务"
          >
            <Trash2 size={16} />
          </button>
        </div>
      )}
    </motion.div>
  );
};

const InlineTaskInput: React.FC = () => {
  const { createManualTask, selectedProjectId } = useAppStore();
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
      await createManualTask(taskTitle, { thread_id: selectedProjectId });
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

  if (!selectedProjectId) {
    return (
      <div className="mt-8 text-center py-6 border border-dashed border-muted/30 rounded-xl bg-muted/5">
        <span className="text-xs font-light text-muted-foreground/60">请先在侧边栏选择具体项目，以添加任务。</span>
      </div>
    );
  }

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
  const {
    currentViewBucket,
    selectedProjectId,
    boardTasks,
    boardError,
    fetchTasks,
    appState,
    committedTaskTree,
    previewTaskTree,
    loadProjectSnapshot,
    previewMode
  } = useAppStore();

  useEffect(() => {
    if (boardTasks === null) {
      const bootstrap = async () => {
        try {
          if (selectedProjectId === null) {
            useAppStore.setState({ committedTaskTree: null, previewTaskTree: null });
            await fetchTasks('planned');
          } else {
            useAppStore.setState({ committedTaskTree: null, previewTaskTree: null });
            await loadProjectSnapshot(selectedProjectId);
            await fetchTasks('planned');
          }
        } catch (err) {
          const errMsg = (err as Error).message;
          const boardError = errMsg === 'Failed to load project snapshot' || errMsg === 'Failed to load task board'
            ? '加载项目看板失败，请重试'
            : errMsg;
          useAppStore.setState({ boardError });
        }
      };
      bootstrap();
    }
  }, [boardTasks, selectedProjectId, fetchTasks, loadProjectSnapshot]);
  const [sidebarOpen, setSidebarOpen] = React.useState(true);

  const isGenerating = appState === 'THINKING' || appState === 'PENDING' || appState === 'SYNCING';

  const projects = useMemo(() => {
    if (!boardTasks) return [];
    const projectMap = new Map<string, { id: string; title: string; source?: string }>();
    boardTasks.forEach(task => {
      if (task.parent_task_id === null && task.thread_id) {
        const existing = projectMap.get(task.thread_id);
        if (!existing || (existing.source === 'manual' && task.source === 'ai')) {
          projectMap.set(task.thread_id, {
            id: task.thread_id,
            title: task.title,
            source: task.source
          });
        }
      }
    });
    return Array.from(projectMap.values());
  }, [boardTasks]);

  const planningView = useMemo(() => {
    if (import.meta.env.VITE_PHASE_PLANNING_ENABLED === 'false') return null;
    if (currentViewBucket !== 'planned' || !selectedProjectId) return null;
    return selectPlanningView(committedTaskTree, boardTasks || [], selectedProjectId);
  }, [committedTaskTree, boardTasks, currentViewBucket, selectedProjectId]);

  const previewTree = useMemo(() => {
    if (currentViewBucket !== 'planned' || previewMode !== 'next_phase' || !selectedProjectId || !previewTaskTree?.root) {
      return null;
    }
    return buildPreviewTree(previewTaskTree.root, selectedProjectId);
  }, [currentViewBucket, previewMode, selectedProjectId, previewTaskTree]);

  const displayTree = useMemo(() => {
    if (!boardTasks) return null;

    if (previewTree) {
      return previewTree;
    }

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
        is_in_my_day: false,
        children: [...boardTasks].sort((a, b) => a.sort_order - b.sort_order).map(t => ({ ...t }))
      };
      return root;
    } else {
      // Planned: Reconstruct tree
      let tasksToRender = boardTasks;
      if (planningView) {
        tasksToRender = planningView.currentTasks;
      }

      const taskMap = new Map<string, TreeNode>();

      tasksToRender.forEach(t => {
        taskMap.set(t.id, { ...t, children: [] });
      });

      const rootChildren: TreeNode[] = [];

      [...tasksToRender].sort((a, b) => a.sort_order - b.sort_order).forEach(t => {
        const node = taskMap.get(t.id)!;
        if (t.parent_task_id && taskMap.has(t.parent_task_id)) {
          taskMap.get(t.parent_task_id)!.children!.push(node);
        } else {
          if (!selectedProjectId || node.thread_id === selectedProjectId) {
            rootChildren.push(node);
          }
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
        is_in_my_day: false,
        children: rootChildren
      };
      return root;
    }
  }, [boardTasks, currentViewBucket, selectedProjectId, planningView, previewTree]);

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
          onClick={() => useAppStore.setState({ boardError: null, boardTasks: null })}
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
    useAppStore.getState().startNewIntent();
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
              {isGenerating ? '返回当前意图' : '🌱 播种新想法'}
            </button>
          </div>
        </header>

        <main className="flex-1 overflow-y-auto p-8 lg:px-24">
          <div className="max-w-3xl mx-auto pb-32">
            {currentViewBucket === 'planned' && selectedProjectId === null ? (
              <PortfolioOverview projects={projects} tasks={boardTasks ?? []} />
            ) : (
              <>
                {currentViewBucket === 'planned' && selectedProjectId && (
                  <PlanningOverview />
                )}

                {isEmpty ? (
                  <div className="flex flex-col items-center justify-center h-64 text-center space-y-4">
                    <p className="text-muted-foreground/60 text-lg">
                      {currentViewBucket === 'planned'
                        ? "您的专属 space 空空如也。点击右上角，让 AI 为您分忧。"
                        : "今天的事情都搞定啦！去喝杯茶，享受生活吧 ☕️"}
                    </p>
                    {currentViewBucket === 'planned' && (
                      <button
                        onClick={handleNewPlan}
                        className="px-4 py-2 border border-muted/50 rounded-lg text-sm text-foreground/70 hover:bg-muted/10 transition-colors"
                      >
                        {isGenerating ? '返回当前意图' : '🌱 播种新想法'}
                      </button>
                    )}
                  </div>
                ) : (
                  <BoardTaskNode node={displayTree} interactive={!previewTree} />
                )}

                {planningView && planningView.historicalPhases.length > 0 && previewMode !== 'next_phase' && (
                  <div className="mt-12 space-y-4">
                    <h3 className="text-sm font-semibold text-muted-foreground uppercase tracking-wider mb-2">Phase History</h3>
                    {planningView.historicalPhases.map((hist, index) => (
                      <details key={hist.phase.phase_id} className="border border-muted/50 rounded-xl overflow-hidden bg-background/50 group">
                        <summary className="px-4 py-3 bg-muted/10 text-muted-foreground hover:text-foreground font-medium cursor-pointer select-none outline-none flex items-center justify-between transition-colors">
                          <span>Phase {index + 1}: {hist.phase.title}</span>
                          <ChevronDown size={16} className="opacity-50 group-open:rotate-180 transition-transform" />
                        </summary>
                        <div className="p-4 space-y-2 border-t border-muted/30">
                          {hist.tasks.map(task => (
                            <div key={task.id} className="flex items-center gap-3 p-3 rounded-lg bg-muted/10 border border-transparent opacity-70">
                              <CheckCircle2 size={16} className="text-muted-foreground" />
                              <span className="text-sm text-muted-foreground line-through">{task.title}</span>
                            </div>
                          ))}
                          {hist.tasks.length === 0 && (
                            <div className="text-sm text-muted-foreground/50 py-2">No tasks found for this phase.</div>
                          )}
                        </div>
                      </details>
                    ))}
                  </div>
                )}

                {previewMode !== 'next_phase' && <InlineTaskInput />}
              </>
            )}

          </div>
        </main>
      </div>
    </motion.div>
  );
};



