import React from 'react';
import { DecisionStrategyContext, TaskNode } from '../types/api';
import { resolveNodeReferences } from '../lib/strategyContext';
import { HelpCircle, Award, CheckCircle2, FlaskConical, Milestone, AlertTriangle } from 'lucide-react';

interface DecisionCardProps {
  context: DecisionStrategyContext;
  rootNode: TaskNode;
}

export const DecisionCard: React.FC<DecisionCardProps> = ({ context, rootNode }) => {
  const { question, options, current_judgment, basis, missing_information, experiments, decision_gate } = context;

  // Confidence styling mapping
  const getConfidenceBadge = (confidence: 'low' | 'medium' | 'high') => {
    switch (confidence) {
      case 'low':
        return { text: '参考有限', className: 'bg-orange-500/10 text-orange-400 border-orange-500/20' };
      case 'medium':
        return { text: '有一定依据', className: 'bg-blue-500/10 text-blue-400 border-blue-500/20' };
      case 'high':
        return { text: '依据较充分', className: 'bg-emerald-500/10 text-emerald-400 border-emerald-500/20' };
      default:
        return { text: confidence, className: 'bg-muted text-muted-foreground border-muted' };
    }
  };

  // Basis type label mapping
  const getBasisTypeLabel = (type: 'user_context' | 'known_constraint' | 'working_assumption') => {
    switch (type) {
      case 'user_context':
        return { text: '用户背景', isAssumption: false };
      case 'known_constraint':
        return { text: '已知约束', isAssumption: false };
      case 'working_assumption':
        return { text: '工作假设（需验证）', isAssumption: true };
      default:
        return { text: type, isAssumption: false };
    }
  };

  const getEffortLabel = (level: 'low' | 'medium' | 'high') => {
    switch (level) {
      case 'low':
        return '低成本';
      case 'medium':
        return '中等投入';
      case 'high':
        return '较高投入';
      default:
        return level;
    }
  };

  const confidenceBadge = getConfidenceBadge(current_judgment.confidence);

  return (
    <div className="w-full max-w-xl px-4 py-6 mb-8 rounded-2xl border border-muted bg-background/30 backdrop-blur-md space-y-6">
      {/* Target Question */}
      <div className="space-y-1">
        <span className="text-[10px] font-semibold text-muted-foreground/50 tracking-wider uppercase block">核心决策议题 / Question</span>
        <h4 className="text-sm font-semibold text-foreground/80 leading-relaxed">
          {question}
        </h4>
        {options && options.length > 0 && (
          <div className="flex flex-wrap gap-1.5 pt-1">
            {options.map((opt, idx) => (
              <span key={idx} className="text-[10px] text-muted-foreground/75 px-2 py-0.5 rounded-md bg-foreground/5 border border-muted/30">
                选项 {idx + 1}: {opt}
              </span>
            ))}
          </div>
        )}
      </div>

      {/* Current Judgment */}
      <div className="p-4 rounded-xl border border-amber-500/20 bg-amber-500/5 space-y-2">
        <div className="flex items-center justify-between gap-3">
          <div className="flex items-center gap-1.5 text-xs font-semibold text-amber-500">
            <Award size={14} />
            <span>当前判断 / Judgment</span>
          </div>
          <span className={`text-[9px] font-medium px-2 py-0.5 rounded-full border ${confidenceBadge.className}`}>
            置信度：{confidenceBadge.text}
          </span>
        </div>
        <p className="text-base font-semibold text-foreground leading-snug">
          {current_judgment.statement}
        </p>
      </div>

      {/* Decision Basis */}
      <div className="space-y-2 pt-2 border-t border-muted/20">
        <span className="text-[10px] font-semibold text-muted-foreground/50 tracking-wider uppercase block">判断依据 / Basis</span>
        <div className="space-y-2">
          {basis.map((item, idx) => {
            const basisMeta = getBasisTypeLabel(item.basis_type);
            return (
              <div key={idx} className="text-xs space-y-0.5 leading-relaxed">
                <div className="flex items-center gap-1.5">
                  <span className={`text-[9px] px-1.5 py-0.2 rounded font-medium border ${
                    basisMeta.isAssumption 
                      ? 'border-orange-500/30 bg-orange-500/10 text-orange-400' 
                      : 'border-muted bg-foreground/5 text-muted-foreground'
                  }`}>
                    {basisMeta.text}
                  </span>
                </div>
                <p className="text-muted-foreground font-light pl-1">
                  {item.statement}
                </p>
              </div>
            );
          })}
        </div>
      </div>

      {/* Missing Information */}
      <div className="space-y-2 pt-2 border-t border-muted/20">
        <div className="flex items-center gap-1.5">
          <HelpCircle size={12} className="text-muted-foreground/60" />
          <span className="text-[10px] font-semibold text-muted-foreground/50 tracking-wider uppercase block">仍需确认 / Missing Info</span>
        </div>
        <ul className="space-y-1.5 pl-1">
          {missing_information.map((item, idx) => (
            <li key={idx} className="text-xs text-muted-foreground flex items-start gap-2 leading-relaxed">
              <span className="w-1.5 h-1.5 rounded-full bg-muted-foreground/40 mt-1.5 shrink-0" />
              <span>{item}</span>
            </li>
          ))}
        </ul>
      </div>

      {/* Decision Experiments */}
      {experiments && experiments.length > 0 && (
        <div className="space-y-3 pt-4 border-t border-muted/20">
          <div className="flex items-center gap-1.5">
            <FlaskConical size={12} className="text-blue-400" />
            <span className="text-[10px] font-semibold text-muted-foreground/50 tracking-wider uppercase block">低成本验证 / Verification</span>
          </div>
          <div className="space-y-3">
            {experiments.map((exp) => {
              const { references: resolvedTasks } = resolveNodeReferences(rootNode, exp.task_client_node_ids);
              return (
                <div key={exp.experiment_id} className="p-3.5 rounded-xl border border-muted/40 bg-foreground/5 space-y-2">
                  <div className="flex items-center justify-between gap-3">
                    <span className="text-xs font-semibold text-foreground/80">{exp.title}</span>
                    <span className="text-[9px] font-medium px-2 py-0.5 rounded bg-blue-500/10 text-blue-400 border border-blue-500/20">
                      {getEffortLabel(exp.effort_level)}
                    </span>
                  </div>
                  <div className="text-xs space-y-1 leading-relaxed">
                    <p className="text-muted-foreground font-light">
                      验证假设：<span className="text-foreground/70 font-normal">{exp.hypothesis}</span>
                    </p>
                    <p className="text-muted-foreground font-light">
                      成功信号：<span className="text-foreground/70 font-normal">{exp.success_signal}</span>
                    </p>
                  </div>
                  {resolvedTasks.length > 0 && (
                    <div className="flex flex-wrap gap-1.5 pt-1 border-t border-muted/10">
                      {resolvedTasks.map((t) => (
                        <span 
                          key={t.nodeId} 
                          className={`inline-flex items-center gap-1 text-[10px] px-2 py-0.5 rounded border ${
                            t.exists 
                              ? 'border-muted bg-background/50 text-muted-foreground' 
                              : 'border-red-500/30 bg-red-500/5 text-red-400 font-mono'
                          }`}
                        >
                          {t.exists && <CheckCircle2 size={10} className="text-muted-foreground/60" />}
                          {t.title}
                        </span>
                      ))}
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        </div>
      )}

      {/* Decision Gate */}
      <div className="p-4 rounded-xl border border-muted/50 bg-background/20 space-y-3 pt-4 border-t border-muted/20">
        <div className="flex items-center gap-1.5">
          <Milestone size={14} className="text-muted-foreground" />
          <span className="text-[10px] font-semibold text-muted-foreground/50 tracking-wider uppercase block">复盘条件 / Decision Gate</span>
        </div>
        <div className="space-y-2.5 text-xs">
          <p className="text-muted-foreground font-light leading-relaxed">
            复盘时刻：<span className="text-foreground font-semibold">{decision_gate.review_after}</span>
          </p>

          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3 pt-2 border-t border-muted/20">
            {/* Proceed branch */}
            <div className="space-y-1.5">
              <span className="text-[10px] font-medium text-emerald-400 flex items-center gap-1">
                <CheckCircle2 size={10} /> 继续推进条件
              </span>
              <ul className="space-y-1 pl-0.5">
                {decision_gate.proceed_if.map((cond, idx) => (
                  <li key={idx} className="text-muted-foreground font-light leading-relaxed flex items-start gap-1">
                    <span className="text-[10px] text-emerald-500 shrink-0 select-none">•</span>
                    <span>{cond}</span>
                  </li>
                ))}
              </ul>
            </div>

            {/* Stop branch */}
            <div className="space-y-1.5">
              <span className="text-[10px] font-medium text-orange-400 flex items-center gap-1">
                <AlertTriangle size={10} /> 触发终止/调整条件
              </span>
              <ul className="space-y-1 pl-0.5">
                {decision_gate.stop_if.map((cond, idx) => (
                  <li key={idx} className="text-muted-foreground font-light leading-relaxed flex items-start gap-1">
                    <span className="text-[10px] text-orange-500 shrink-0 select-none">•</span>
                    <span>{cond}</span>
                  </li>
                ))}
              </ul>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
};
