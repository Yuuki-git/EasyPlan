import React from 'react';
import {
  TaskAssistProposal as TaskAssistProposalType,
  RescueOption,
  AssistTaskDraft
} from '../types/api';
import {
  isStartProposal,
  isUnstickProposal,
  isDecomposeProposal
} from '../lib/taskAssist';
import {
  Sparkles,
  HelpCircle,
  Clock,
  CheckCircle2,
  AlertTriangle,
  GitBranch,
  ShieldCheck,
  Zap
} from 'lucide-react';
import { clsx } from 'clsx';

interface TaskAssistProposalProps {
  proposal: TaskAssistProposalType;
  selectedOptionId: string | null;
  onSelectOption: (id: string) => void;
}

export const TaskAssistProposal: React.FC<TaskAssistProposalProps> = ({
  proposal,
  selectedOptionId,
  onSelectOption
}) => {
  // 1. Start Proposal Rendering
  if (isStartProposal(proposal)) {
    const { summary, starter_step } = proposal;
    return (
      <div className="space-y-5 animate-fade-in">
        <div className="p-4 rounded-xl border border-blue-500/10 bg-blue-500/5 text-xs text-blue-400 font-light leading-relaxed flex gap-2">
          <Sparkles size={14} className="shrink-0 mt-0.5" />
          <p>{summary}</p>
        </div>

        <div className="p-4 rounded-xl border border-muted/60 bg-background/50 space-y-4">
          <div className="flex items-start justify-between gap-3">
            <div className="space-y-1">
              <span className="text-[10px] font-semibold text-blue-500/70 tracking-wider uppercase block">
                快速启动任务 / Starter Step
              </span>
              <h5 className="font-semibold text-foreground/90 text-sm">
                {starter_step.title}
              </h5>
            </div>
            <div className="flex items-center gap-1 text-[10px] font-mono text-muted-foreground bg-muted/20 px-2 py-0.5 rounded-full shrink-0">
              <Clock size={10} />
              <span>{starter_step.estimated_minutes} 分钟</span>
            </div>
          </div>

          {starter_step.description && (
            <p className="text-xs text-muted-foreground/80 leading-relaxed pl-3 border-l border-muted/30 font-light">
              {starter_step.description}
            </p>
          )}

          <div className="space-y-2 text-xs">
            <div className="flex items-start gap-2 text-muted-foreground leading-relaxed">
              <ShieldCheck size={12} className="text-green-500 shrink-0 mt-0.5" />
              <div>
                <span className="font-medium text-foreground/70">完成标准：</span>
                <span className="font-light">{starter_step.done_criteria}</span>
              </div>
            </div>

            {starter_step.start_hint && (
              <div className="flex items-start gap-2 text-muted-foreground leading-relaxed">
                <Zap size={12} className="text-amber-500 shrink-0 mt-0.5" />
                <div>
                  <span className="font-medium text-foreground/70">如何开始：</span>
                  <span className="font-light">{starter_step.start_hint}</span>
                </div>
              </div>
            )}
          </div>
        </div>
      </div>
    );
  }

  // 2. Unstick Proposal Rendering
  if (isUnstickProposal(proposal)) {
    const { obstacle_summary, recommended_option_id, options } = proposal;
    return (
      <div className="space-y-5 animate-fade-in">
        <div className="p-4 rounded-xl border border-amber-500/10 bg-amber-500/5 text-xs text-amber-500/80 font-light leading-relaxed flex gap-2">
          <AlertTriangle size={14} className="shrink-0 mt-0.5 text-amber-500" />
          <div>
            <span className="font-semibold uppercase text-[9px] tracking-wider block mb-0.5">阻碍分析 / Obstacle</span>
            <p>{obstacle_summary}</p>
          </div>
        </div>

        <div className="space-y-3">
          <span className="text-[10px] font-semibold text-muted-foreground/50 tracking-wider uppercase block">
            请选择拯救行动方案 / Options
          </span>
          <div className="space-y-3">
            {options.map((opt: RescueOption) => {
              const isRecommended = opt.option_id === recommended_option_id;
              const isSelected = opt.option_id === selectedOptionId;

              return (
                <div
                  key={opt.option_id}
                  onClick={() => onSelectOption(opt.option_id)}
                  className={clsx(
                    "p-4 rounded-xl border transition-all cursor-pointer relative space-y-3.5 hover:shadow-sm",
                    isSelected
                      ? "border-blue-500 bg-blue-500/5 hover:border-blue-500"
                      : "border-muted/60 bg-background/50 hover:border-muted"
                  )}
                >
                  <div className="flex items-start justify-between gap-3 pr-14">
                    <h5 className="font-semibold text-foreground/90 text-sm">
                      {opt.title}
                    </h5>
                    <div className="flex items-center gap-1.5 shrink-0">
                      {isRecommended && (
                        <span className="text-[9px] font-medium px-1.5 py-0.5 rounded bg-blue-500/10 text-blue-400 border border-blue-500/20">
                          推荐
                        </span>
                      )}
                      <span className="flex items-center gap-1 text-[10px] font-mono text-muted-foreground bg-muted/20 px-2 py-0.5 rounded-full">
                        <Clock size={10} />
                        <span>{opt.estimated_minutes} min</span>
                      </span>
                    </div>
                  </div>

                  <p className="text-xs text-muted-foreground leading-relaxed font-light">
                    行动建议：<span className="text-foreground/80 font-normal">{opt.action}</span>
                  </p>

                  <div className="text-[11px] text-muted-foreground/70 pl-2.5 border-l-2 border-muted/30 leading-relaxed font-light">
                    代价与折舍：{opt.tradeoff}
                  </div>

                  <div className="absolute right-4 top-1/2 -translate-y-1/2">
                    <div className={clsx(
                      "w-4 h-4 rounded-full border flex items-center justify-center transition-colors",
                      isSelected ? "border-blue-500 bg-blue-500" : "border-muted-foreground/30"
                    )}>
                      {isSelected && <div className="w-1.5 h-1.5 rounded-full bg-background" />}
                    </div>
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      </div>
    );
  }

  // 3. Decompose Proposal Rendering
  if (isDecomposeProposal(proposal)) {
    const { summary, subtasks, dependencies } = proposal;

    // Helper to find dependencies for a task draft
    const getSubtaskDependencies = (draftId: string) => {
      const deps = dependencies
        .filter(d => d.task_draft_id === draftId)
        .map(d => {
          const idx = subtasks.findIndex(s => s.draft_id === d.depends_on_draft_id);
          return idx >= 0 ? `#${idx + 1}` : null;
        })
        .filter(Boolean);
      return deps.length > 0 ? deps.join(', ') : null;
    };

    return (
      <div className="space-y-5 animate-fade-in">
        <div className="p-4 rounded-xl border border-blue-500/10 bg-blue-500/5 text-xs text-blue-400 font-light leading-relaxed flex gap-2">
          <Sparkles size={14} className="shrink-0 mt-0.5" />
          <p>{summary}</p>
        </div>

        <div className="space-y-4">
          <span className="text-[10px] font-semibold text-muted-foreground/50 tracking-wider uppercase block">
            拆分步骤预览 / Subtasks Preview
          </span>
          <div className="space-y-3.5 relative pl-4 border-l border-muted/30 ml-2.5">
            {subtasks.map((task: AssistTaskDraft, index: number) => {
              const depText = getSubtaskDependencies(task.draft_id);
              
              return (
                <div key={task.draft_id} className="relative space-y-2">
                  {/* Step bullet indicator */}
                  <div className="absolute -left-[27px] top-1 w-5 h-5 rounded-full bg-background border border-muted/80 text-[10px] font-semibold text-muted-foreground flex items-center justify-center shadow-sm">
                    {index + 1}
                  </div>

                  <div className="p-3.5 rounded-xl border border-muted/40 bg-background/30 space-y-2.5">
                    <div className="flex items-start justify-between gap-3">
                      <h5 className="font-semibold text-foreground/90 text-sm">
                        {task.title}
                      </h5>
                      <div className="flex items-center gap-1.5 shrink-0">
                        {depText && (
                          <span className="flex items-center gap-1 text-[9px] font-medium px-1.5 py-0.5 rounded bg-amber-500/10 text-amber-500 border border-amber-500/20">
                            <GitBranch size={9} />
                            <span>依赖 {depText}</span>
                          </span>
                        )}
                        <span className="flex items-center gap-1 text-[10px] font-mono text-muted-foreground bg-muted/20 px-2 py-0.5 rounded-full">
                          <Clock size={10} />
                          <span>{task.estimated_minutes} min</span>
                        </span>
                      </div>
                    </div>

                    {task.description && (
                      <p className="text-xs text-muted-foreground/80 leading-relaxed font-light pl-2.5 border-l border-muted/20">
                        {task.description}
                      </p>
                    )}

                    <div className="flex items-start gap-1.5 text-xs text-muted-foreground leading-relaxed pl-1">
                      <CheckCircle2 size={12} className="text-green-500 shrink-0 mt-0.5" />
                      <div>
                        <span className="font-medium text-foreground/70">标准：</span>
                        <span className="font-light">{task.done_criteria}</span>
                      </div>
                    </div>
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      </div>
    );
  }

  // Fallback rendering
  return (
    <div className="p-8 text-center text-xs text-muted-foreground/60 space-y-2">
      <HelpCircle size={32} className="mx-auto text-muted-foreground/30" />
      <p>暂无有效的 AI 建议方案或内容异常，请重试。</p>
    </div>
  );
};
