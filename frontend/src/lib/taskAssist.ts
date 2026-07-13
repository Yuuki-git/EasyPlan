import {
  TaskResponse,
  TaskAssistProposal,
  StartAssistProposal,
  UnstickAssistProposal,
  DecomposeAssistProposal,
  TaskAssistMode
} from '../types/api';

/**
 * Type guard for Start Assist Proposal
 */
export function isStartProposal(proposal: TaskAssistProposal | null | undefined): proposal is StartAssistProposal {
  if (!proposal || proposal.proposal_type !== 'start') return false;
  const p = proposal as Partial<StartAssistProposal>;
  return !!(
    p.summary &&
    p.starter_step?.draft_id &&
    p.starter_step?.title &&
    typeof p.starter_step?.estimated_minutes === 'number' &&
    p.starter_step?.done_criteria
  );
}

/**
 * Type guard for Unstick Assist Proposal
 */
export function isUnstickProposal(proposal: TaskAssistProposal | null | undefined): proposal is UnstickAssistProposal {
  if (!proposal || proposal.proposal_type !== 'unstick') return false;
  const p = proposal as Partial<UnstickAssistProposal>;
  return !!(
    p.obstacle_summary &&
    p.recommended_option_id &&
    Array.isArray(p.options) &&
    p.options.length >= 2 &&
    p.options.every(o => o.option_id && o.title && o.action && typeof o.estimated_minutes === 'number' && o.tradeoff)
  );
}

/**
 * Type guard for Decompose Assist Proposal
 */
export function isDecomposeProposal(proposal: TaskAssistProposal | null | undefined): proposal is DecomposeAssistProposal {
  if (!proposal || proposal.proposal_type !== 'decompose') return false;
  const p = proposal as Partial<DecomposeAssistProposal>;
  return !!(
    p.summary &&
    p.completion_rule === 'all_subtasks_completed' &&
    Array.isArray(p.subtasks) &&
    p.subtasks.length >= 2 &&
    p.subtasks.every(s => s.draft_id && s.title && typeof s.estimated_minutes === 'number' && s.done_criteria) &&
    Array.isArray(p.dependencies)
  );
}

/**
 * Merges applied parent and created children into flat tasks list in a transaction-safe manner
 */
export function mergeApplyReceipt(
  currentTasks: TaskResponse[],
  appliedParent: TaskResponse,
  createdChildren: TaskResponse[]
): TaskResponse[] {
  let updated = currentTasks.map(t => t.id === appliedParent.id ? { ...t, ...appliedParent } : t);
  if (createdChildren && createdChildren.length > 0) {
    const newIds = new Set(createdChildren.map(c => c.id));
    // Filter duplicates
    updated = updated.filter(t => !newIds.has(t.id));
    // Append children
    updated = [...updated, ...createdChildren];
  }
  return updated;
}

/**
 * Formats task assist mode to user-friendly label
 */
export function getTaskAssistModeLabel(mode: TaskAssistMode): string {
  switch (mode) {
    case 'start':
      return '帮我开始';
    case 'unstick':
      return '我卡住了';
    case 'decompose':
      return '拆得更细';
    default:
      return 'AI 辅助';
  }
}

/**
 * Formats task assist mode to placeholder user context helper text
 */
export function getTaskAssistPlaceholder(mode: TaskAssistMode): string {
  switch (mode) {
    case 'start':
      return '可以补充您当前的实际情况，例如：“我现在只有10分钟，且有点累。”（可选）';
    case 'unstick':
      return '可以补充具体卡在哪里，例如：“找了半天没找到官方API，也没有找到教程。”（可选）';
    case 'decompose':
      return '可以补充拆分偏好，例如：“希望前两个步骤尽量简单，后几个步骤更偏落地。”（可选）';
    default:
      return '输入补充信息（可选）...';
  }
}
