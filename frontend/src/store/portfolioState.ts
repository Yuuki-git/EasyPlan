import { selectPlanningView } from './planningState';
import { ThreadSnapshot, TaskResponse } from '../types/api';

export interface PortfolioProject {
  id: string;
  title: string;
  source?: string;
}

export interface PortfolioCardView {
  projectId: string;
  title: string;
  typeLabel: string;
  currentPhaseLabel: string;
  progressLabel: string | null;
  nextActionLabel: string;
  snapshotAvailable: boolean;
}

export function selectPortfolioCard(
  project: PortfolioProject,
  snapshot: ThreadSnapshot | undefined,
  tasks: TaskResponse[],
): PortfolioCardView {
  const planningView = selectPlanningView(snapshot?.task_tree ?? null, tasks, project.id, snapshot?.long_term_execution);
  const snapshotAvailable = !!snapshot;

  let typeLabel = '直接计划';
  if (planningView?.context) {
    if (planningView.context.intent_type === 'long_term_growth') {
      typeLabel = '长期成长';
    } else if (planningView.context.intent_type === 'exploration_decision') {
      typeLabel = '探索决策';
    }
  } else if (project.source === 'manual') {
    typeLabel = '手动计划';
  }

  let currentPhaseLabel = '尚未建立阶段';
  if (planningView?.context.current_phase?.title) {
    currentPhaseLabel = planningView.context.current_phase.title;
  } else if (planningView?.isGoalComplete) {
    currentPhaseLabel = '已全部完成';
  }

  let progressLabel: string | null = null;
  if (planningView && planningView.totalAiActions > 0) {
    progressLabel = `${planningView.completedAiActions} / ${planningView.totalAiActions}`;
  }

  if (snapshot?.long_term_execution?.loops?.length) {
    const loops = snapshot.long_term_execution.loops;
    const completed = loops.reduce((sum, loop) => sum + loop.total_completed, 0);
    const required = loops.reduce((sum, loop) => sum + loop.required_completions, 0);
    progressLabel = `练习 ${completed} / ${required}`;
  } else if (planningView?.isGoalComplete) {
    progressLabel = '100%';
  }

  let nextActionLabel = '暂无下一步动作';
  if (planningView) {
    if (planningView.nextAction?.title) {
      nextActionLabel = planningView.nextAction.title;
    } else if (planningView.canUnlock) {
      nextActionLabel = '当前阶段已完成';
    } else if (planningView.isGoalComplete) {
      nextActionLabel = '无（所有任务已完成）';
    }
  }

  return {
    projectId: project.id,
    title: project.title,
    typeLabel,
    currentPhaseLabel,
    progressLabel,
    nextActionLabel,
    snapshotAvailable
  };
}
