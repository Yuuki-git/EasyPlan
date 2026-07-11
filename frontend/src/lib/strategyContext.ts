import { StrategyContext, DeliveryStrategyContext, DecisionStrategyContext, TaskNode } from '../types/api';
import { parseExplorationSummary } from './explorationHelper';

/**
 * Type guard for Delivery Strategy Context
 */
export function isDeliveryContext(ctx: StrategyContext | null | undefined): ctx is DeliveryStrategyContext {
  if (ctx?.strategy_type !== 'delivery') return false;
  const d = ctx as Partial<DeliveryStrategyContext>;
  return !!(
    d.deliverable?.title &&
    d.deliverable?.format &&
    Array.isArray(d.deliverable?.quality_bar) &&
    d.deadline?.text &&
    d.time_plan &&
    typeof d.time_plan.planned_minutes === 'number' &&
    typeof d.time_plan.buffer_minutes === 'number' &&
    d.scope &&
    Array.isArray(d.scope.must_have) &&
    Array.isArray(d.workstreams) &&
    Array.isArray(d.critical_path_client_node_ids)
  );
}

/**
 * Type guard for Decision Strategy Context
 */
export function isDecisionContext(ctx: StrategyContext | null | undefined): ctx is DecisionStrategyContext {
  if (ctx?.strategy_type !== 'decision') return false;
  const d = ctx as Partial<DecisionStrategyContext>;
  return !!(
    d.question &&
    Array.isArray(d.options) &&
    d.current_judgment?.direction &&
    d.current_judgment?.statement &&
    d.current_judgment?.confidence &&
    Array.isArray(d.basis) &&
    Array.isArray(d.missing_information) &&
    Array.isArray(d.experiments) &&
    d.decision_gate?.review_after &&
    Array.isArray(d.decision_gate?.proceed_if) &&
    Array.isArray(d.decision_gate?.stop_if)
  );
}

/**
 * Formats minutes to rounded hours (e.g. 195 minutes -> 3.5 hours)
 */
export function formatPlannedTime(minutes: number): string {
  if (minutes <= 0) return '0 分钟';
  if (minutes < 30) {
    return `${minutes} 分钟`;
  }
  const hours = minutes / 60;
  // Round to nearest 0.5 hours
  const roundedHours = Math.round(hours * 2) / 2;
  if (roundedHours === 0) {
    return `${minutes} 分钟`;
  }
  return `${roundedHours} 小时`;
}

/**
 * Recursively searches a TaskNode tree for a specific client_node_id
 */
export function findNodeById(root: TaskNode, nodeId: string): TaskNode | null {
  if (root.client_node_id === nodeId) {
    return root;
  }
  if (root.children && root.children.length > 0) {
    for (const child of root.children) {
      const found = findNodeById(child, nodeId);
      if (found) return found;
    }
  }
  return null;
}

export interface ResolvedNodeRef {
  nodeId: string;
  title: string;
  exists: boolean;
}

export interface ResolveReferencesResult {
  references: ResolvedNodeRef[];
  diagnostics: {
    missingNodeIds: string[];
  };
}

/**
 * Resolves a list of task node references from the tree
 */
export function resolveNodeReferences(root: TaskNode, nodeIds: string[]): ResolveReferencesResult {
  if (!root || !nodeIds) {
    return {
      references: [],
      diagnostics: { missingNodeIds: [] }
    };
  }
  const missingNodeIds: string[] = [];
  const references = nodeIds.map(id => {
    const node = findNodeById(root, id);
    if (!node) {
      missingNodeIds.push(id);
      return {
        nodeId: id,
        title: id,
        exists: false
      };
    }
    return {
      nodeId: id,
      title: node.title,
      exists: true
    };
  });

  return {
    references,
    diagnostics: {
      missingNodeIds
    }
  };
}

export interface LegacyExplorationSummary {
  judgment: string;
  basis: string;
  exploration: string;
}

/**
 * Parses and returns the legacy exploration summary if applicable
 */
export function getLegacyExplorationSummary(summary: string | null | undefined): LegacyExplorationSummary | null {
  if (!summary) return null;
  const hasKeywords = /当前判断|判断依据|下一步探索|探索路线|探索建议/i.test(summary);
  if (!hasKeywords) return null;
  try {
    const parsed = parseExplorationSummary(summary);
    if (parsed && parsed.judgment && parsed.basis && parsed.exploration) {
      return parsed;
    }
  } catch {
    // ignore
  }
  return null;
}
