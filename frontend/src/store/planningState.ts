import { TaskTree, TaskResponse, PlanningContext, RoadmapPhase, TaskNode } from '../types/api';

export interface PlanningView {
  context: PlanningContext;
  currentTasks: TaskResponse[];
  historicalPhases: Array<{ phase: RoadmapPhase; tasks: TaskResponse[] }>;
  nextAction: TaskResponse | null;
  totalAiActions: number;
  completedAiActions: number;
  canUnlock: boolean;
  isGoalComplete: boolean;
}

export function selectPlanningView(
  taskTree: TaskTree | null,
  tasks: TaskResponse[],
  threadId: string | null,
): PlanningView | null {
  const context = taskTree?.planning_context;
  if (!context || !threadId) return null;
  
  const threadTasks = tasks.filter((task) => task.thread_id === threadId);
  
  // Sort tasks by phase_order, sort_order, then id
  const sortedTasks = [...threadTasks].sort((a, b) => {
    if (a.phase_order !== b.phase_order) {
      return (a.phase_order ?? Number.MAX_SAFE_INTEGER) - (b.phase_order ?? Number.MAX_SAFE_INTEGER);
    }
    if (a.sort_order !== b.sort_order) {
      return a.sort_order - b.sort_order;
    }
    return a.id.localeCompare(b.id);
  });

  const phaseId = context.current_phase?.phase_id ?? null;
  
  const currentTasks = sortedTasks.filter(
    (task) => (phaseId !== null && task.phase_id === phaseId) || task.source === 'manual',
  );
  
  const aiActions = currentTasks.filter(
    (task) => task.source === 'ai' && task.phase_id === phaseId && task.node_type === 'action',
  );
  
  const completedAiActions = aiActions.filter((task) => task.status === 'completed').length;
  
  const nextAction = sortedTasks.find(
    (task) => task.client_node_id === context.next_action_client_node_id && task.status !== 'completed',
  ) ?? null;
  
  const completedPhases = context.roadmap.filter((phase) => phase.status === 'completed');
  
  // Sort completed phases by order
  completedPhases.sort((a, b) => a.order - b.order);

  return {
    context,
    currentTasks,
    historicalPhases: completedPhases.map((phase) => ({
      phase,
      tasks: sortedTasks.filter((task) => task.phase_id === phase.phase_id),
    })),
    nextAction,
    totalAiActions: aiActions.length,
    completedAiActions,
    canUnlock: context.current_phase !== null && aiActions.length > 0 && completedAiActions === aiActions.length,
    isGoalComplete: context.current_phase === null && context.roadmap.every((phase) => phase.status === 'completed'),
  };
}

export function buildTaskTree(tasks: TaskResponse[]): TaskNode[] {
  // Simple deterministic tree reconstruction based on sort_order & parent_task_id
  const taskMap = new Map<string, TaskNode & { dbId: string }>();
  const roots: (TaskNode & { dbId: string })[] = [];

  const sortedTasks = [...tasks].sort((a, b) => a.sort_order - b.sort_order);

  for (const task of sortedTasks) {
    taskMap.set(task.id, {
      client_node_id: task.client_node_id,
      title: task.title,
      description: task.description,
      verb: '', // Reconstructed from DB, might be empty
      estimated_minutes: task.estimated_minutes ?? 0,
      node_type: task.node_type,
      done_criteria: task.done_criteria,
      start_hint: task.start_hint,
      fallback_action: task.fallback_action,
      children: [],
      dbId: task.id,
    });
  }

  for (const task of sortedTasks) {
    const node = taskMap.get(task.id)!;
    if (task.parent_task_id && taskMap.has(task.parent_task_id)) {
      const parent = taskMap.get(task.parent_task_id)!;
      parent.children!.push(node);
    } else {
      roots.push(node);
    }
  }

  return roots;
}
