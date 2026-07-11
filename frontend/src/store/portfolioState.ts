import { selectPlanningView } from './planningState';
import { ThreadSnapshot, TaskResponse } from '../types/api';
import { getLegacyExplorationSummary, isDeliveryContext, isDecisionContext } from '../lib/strategyContext';

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
  const strategyContext = snapshot?.task_tree?.strategy_context;

  const isDelivery = isDeliveryContext(strategyContext);
  const isDecision = isDecisionContext(strategyContext);

  let typeLabel = '直接计划';
  if (isDelivery) {
    typeLabel = '短期交付';
  } else if (isDecision) {
    typeLabel = '探索决策';
  } else if (planningView?.context) {
    if (planningView.context.intent_type === 'long_term_growth') {
      typeLabel = '长期成长';
    } else if (planningView.context.intent_type === 'exploration_decision') {
      typeLabel = '探索决策';
    }
  } else if (project.source === 'manual') {
    typeLabel = '手动计划';
  }

  let currentPhaseLabel = '尚未建立阶段';
  if (isDelivery && strategyContext) {
    currentPhaseLabel = `交付目标: ${strategyContext.deliverable.title}`;
  } else if (isDecision && strategyContext) {
    currentPhaseLabel = `当前判断: ${strategyContext.current_judgment.statement}`;
  } else {
    // Check legacy exploration fallback
    const isExploration = planningView?.context?.intent_type === 'exploration_decision';
    const legacy = isExploration ? getLegacyExplorationSummary(snapshot?.task_tree?.summary) : null;
    if (legacy) {
      currentPhaseLabel = `当前判断: ${legacy.judgment}`;
    } else if (planningView?.context.current_phase?.title) {
      currentPhaseLabel = planningView.context.current_phase.title;
    } else if (planningView?.isGoalComplete) {
      currentPhaseLabel = '已全部完成';
    }
  }

  // Calculate progress label
  let progressLabel: string | null = null;
  const threadTasks = tasks.filter((task) => task.thread_id === project.id);
  const aiActions = threadTasks.filter((task) => task.source === 'ai' && task.node_type === 'action');
  const completedAiActionsCount = aiActions.filter((task) => task.status === 'completed').length;

  if (planningView && planningView.totalAiActions > 0) {
    progressLabel = `${planningView.completedAiActions} / ${planningView.totalAiActions}`;
  } else if (aiActions.length > 0) {
    progressLabel = `${completedAiActionsCount} / ${aiActions.length}`;
  }

  if (snapshot?.long_term_execution?.loops?.length) {
    const loops = snapshot.long_term_execution.loops;
    const completed = loops.reduce((sum, loop) => sum + loop.total_completed, 0);
    const required = loops.reduce((sum, loop) => sum + loop.required_completions, 0);
    progressLabel = `练习 ${completed} / ${required}`;
  } else if (planningView?.isGoalComplete || (!planningView && aiActions.length > 0 && completedAiActionsCount === aiActions.length)) {
    progressLabel = '100%';
  }

  // Calculate next action label
  let nextActionLabel = '暂无下一步动作';
  if (planningView) {
    if (planningView.nextAction?.title) {
      nextActionLabel = planningView.nextAction.title;
    } else if (planningView.canUnlock) {
      nextActionLabel = '当前阶段已完成';
    } else if (planningView.isGoalComplete) {
      nextActionLabel = '无（所有任务已完成）';
    }
  } else if (threadTasks.length > 0) {
    const sortedThreadTasks = [...threadTasks].sort((a, b) => a.sort_order - b.sort_order);
    const nextAction = sortedThreadTasks.find((task) => task.status !== 'completed' && task.node_type === 'action');
    if (nextAction) {
      nextActionLabel = nextAction.title;
    } else if (aiActions.length > 0 && completedAiActionsCount === aiActions.length) {
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
