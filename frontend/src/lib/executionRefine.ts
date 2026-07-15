import { TaskResponse, ExecutionRefineMode } from '../types/api';

/**
 * Merges applied execution refine tasks into the flat tasks list
 */
export function mergeApplyReceipt(
  currentTasks: TaskResponse[],
  updatedOrCreatedTasks: TaskResponse[]
): TaskResponse[] {
  if (!updatedOrCreatedTasks || updatedOrCreatedTasks.length === 0) {
    return currentTasks;
  }
  const updatedMap = new Map(updatedOrCreatedTasks.map(t => [t.id, t]));
  let updated = currentTasks.map(t => {
    const fresh = updatedMap.get(t.id);
    return fresh ? { ...t, ...fresh } : t;
  });

  // Append any tasks that are in updatedOrCreatedTasks but not in currentTasks (newly created tasks)
  const existingIds = new Set(currentTasks.map(t => t.id));
  const newTasks = updatedOrCreatedTasks.filter(t => !existingIds.has(t.id));
  if (newTasks.length > 0) {
    updated = [...updated, ...newTasks];
  }
  return updated;
}

/**
 * Formats execution refine mode to user-friendly label
 */
export function getExecutionRefineModeLabel(mode: ExecutionRefineMode): string {
  switch (mode) {
    case 'time_budget':
      return '时间预算';
    case 'progress_recovery':
      return '进度恢复';
    case 'context_change':
      return '条件变更';
    default:
      return '调整计划';
  }
}
