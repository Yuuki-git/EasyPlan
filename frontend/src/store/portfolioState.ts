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
  const planningView = selectPlanningView(snapshot?.task_tree ?? null, tasks, project.id);
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
  }

  let progressLabel: string | null = null;
  if (planningView && planningView.totalAiActions > 0) {
    progressLabel = `${planningView.completedAiActions} / ${planningView.totalAiActions}`;
  }

  let nextActionLabel = '暂无下一步动作';
  if (planningView) {
    if (planningView.nextAction?.title) {
      nextActionLabel = planningView.nextAction.title;
    } else if (planningView.canUnlock) {
      nextActionLabel = '当前阶段已完成';
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
