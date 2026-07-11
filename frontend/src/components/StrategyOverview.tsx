import React from 'react';
import { TaskTree } from '../types/api';
import { isDeliveryContext, isDecisionContext, getLegacyExplorationSummary } from '../lib/strategyContext';
import { DeliverySummary } from './DeliverySummary';
import { DecisionCard } from './DecisionCard';

interface StrategyOverviewProps {
  taskTree: TaskTree;
}

export const StrategyOverview: React.FC<StrategyOverviewProps> = ({ taskTree }) => {
  if (!taskTree) return null;

  const { strategy_context, planning_context, summary } = taskTree;

  // 1. Check for structured Delivery context
  if (isDeliveryContext(strategy_context)) {
    return <DeliverySummary context={strategy_context} rootNode={taskTree.root} />;
  }

  // 2. Check for structured Decision context
  if (isDecisionContext(strategy_context)) {
    return <DecisionCard context={strategy_context} rootNode={taskTree.root} />;
  }

  // 3. Fallback for legacy exploration decision plans lacking strategy_context
  const isExploration = planning_context?.intent_type === 'exploration_decision';
  if (isExploration) {
    const legacy = getLegacyExplorationSummary(summary);
    if (legacy) {
      return (
        <div className="w-full max-w-xl px-4 py-5 mb-8 rounded-2xl border border-amber-500/20 bg-amber-500/5 backdrop-blur-md space-y-4">
          <div>
            <h3 className="text-xs font-semibold text-amber-500 tracking-wider uppercase mb-1">当前判断 / Judgment</h3>
            <p className="text-base font-semibold text-foreground leading-snug">
              {legacy.judgment}
            </p>
          </div>

          <div className="space-y-1 pt-2 border-t border-muted/20">
            <h3 className="text-xs font-semibold text-muted-foreground/50 tracking-wider uppercase">判断依据 / Basis</h3>
            <p className="text-sm font-light text-muted-foreground leading-relaxed">
              {legacy.basis}
            </p>
          </div>

          <div className="space-y-1 pt-2 border-t border-muted/20">
            <h3 className="text-xs font-semibold text-muted-foreground/50 tracking-wider uppercase">下一步探索 / Next Steps</h3>
            <p className="text-sm font-light text-muted-foreground leading-relaxed">
              {legacy.exploration}
            </p>
          </div>
        </div>
      );
    }
  }

  // 4. Default standard fallback summary card
  return (
    <div className="w-full max-w-xl px-2 mb-8">
      <h3 className="text-xs font-mono text-muted-foreground/40 tracking-widest mb-2">
        建议行动计划
      </h3>
      <p className="text-lg font-light text-foreground/80 leading-snug">
        {summary}
      </p>
    </div>
  );
};
export default StrategyOverview;
