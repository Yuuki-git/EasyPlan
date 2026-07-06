import { ThreadSnapshot, LongTermExecutionSnapshot } from '../types/api';

export interface LongTermExecutionView {
  phaseId: string;
  recommendation: 'ready' | 'partial' | 'not_ready' | 'overridden';
  canReview: boolean;
  oneOffReady: boolean;
  processReady: boolean;
  outcomeReady: boolean;
  loops: Array<{
    loopId: string;
    loopKey: string;
    title: string;
    doneCriteria: string;
    targetPerWeek: number;
    currentWeekCompleted: number;
    totalCompleted: number;
    requiredCompletions: number;
    estimatedEnd: string;
    status: 'active' | 'paused' | 'completed' | 'superseded';
    canScheduleToday: boolean;
    activeOccurrenceTaskId: string | null;
    weeklyLabel: string;
    totalLabel: string;
  }>;
  activeReview: LongTermExecutionSnapshot['active_review'];
  latestFinalizedReview: LongTermExecutionSnapshot['latest_finalized_review'];
  reviewHistory: LongTermExecutionSnapshot['review_history'];
}

export function selectLongTermExecutionView(
  snapshot: ThreadSnapshot | null | undefined
): LongTermExecutionView | null {
  if (!snapshot || !snapshot.task_tree?.planning_context) return null;
  const context = snapshot.task_tree.planning_context;
  if (context.schema_version !== 2) return null;

  const exec = snapshot.long_term_execution;
  if (!exec) return null;

  return {
    phaseId: exec.phase_id,
    recommendation: exec.recommendation,
    canReview: exec.review_available,
    oneOffReady: exec.one_off_ready,
    processReady: exec.process_ready,
    outcomeReady: exec.outcome_ready,
    loops: (exec.loops || []).map(loop => ({
      loopId: loop.loop_id,
      loopKey: loop.loop_key,
      title: loop.title,
      doneCriteria: loop.done_criteria,
      targetPerWeek: loop.target_per_week,
      currentWeekCompleted: loop.current_week_completed,
      totalCompleted: loop.total_completed,
      requiredCompletions: loop.required_completions,
      estimatedEnd: loop.estimated_end,
      status: loop.status,
      canScheduleToday: loop.can_schedule_today,
      activeOccurrenceTaskId: loop.active_occurrence_task_id,
      weeklyLabel: `本周 ${loop.current_week_completed} / ${loop.target_per_week} 次`,
      totalLabel: `总计 ${loop.total_completed} / ${loop.required_completions} 次`
    })),
    activeReview: exec.active_review,
    latestFinalizedReview: exec.latest_finalized_review,
    reviewHistory: exec.review_history || []
  };
}
