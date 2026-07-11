import React from 'react';
import { DeliveryStrategyContext, TaskNode } from '../types/api';
import { formatPlannedTime, resolveNodeReferences } from '../lib/strategyContext';
import { Calendar, Clock, Target, CheckCircle2, GitBranch, KeyRound } from 'lucide-react';

interface DeliverySummaryProps {
  context: DeliveryStrategyContext;
  rootNode: TaskNode;
}

export const DeliverySummary: React.FC<DeliverySummaryProps> = ({ context, rootNode }) => {
  const { deliverable, deadline, time_plan, scope, workstreams, critical_path_client_node_ids } = context;

  // Resolve task references for workstreams and critical path
  const { references: resolvedCriticalPath } = resolveNodeReferences(rootNode, critical_path_client_node_ids);

  return (
    <div className="w-full max-w-xl px-4 py-6 mb-8 rounded-2xl border border-muted bg-background/30 backdrop-blur-md space-y-6">
      {/* Header section: Deliverable definition */}
      <div className="flex items-start gap-4">
        <div className="p-3 rounded-xl bg-blue-500/10 border border-blue-500/20 text-blue-400 shrink-0">
          <Target size={20} />
        </div>
        <div className="space-y-1">
          <span className="text-[10px] font-semibold text-blue-400 tracking-wider uppercase block">交付目标 / Deliverable</span>
          <h3 className="text-lg font-bold text-foreground leading-snug">
            {deliverable.title}
          </h3>
          <p className="text-xs text-muted-foreground">
            输出格式：<span className="font-medium text-foreground">{deliverable.format}</span>
          </p>
          {deliverable.quality_bar && deliverable.quality_bar.length > 0 && (
            <div className="mt-3 space-y-1.5">
              <span className="text-[10px] font-medium text-muted-foreground/60 block">质量底线：</span>
              <ul className="space-y-1">
                {deliverable.quality_bar.map((q, idx) => (
                  <li key={idx} className="text-xs text-muted-foreground flex items-center gap-1.5">
                    <span className="w-1 h-1 rounded-full bg-blue-500/60 shrink-0" />
                    <span>{q}</span>
                  </li>
                ))}
              </ul>
            </div>
          )}
        </div>
      </div>

      <div className="grid grid-cols-1 sm:grid-cols-2 gap-4 pt-2 border-t border-muted/20">
        {/* Deadline section */}
        <div className="flex items-start gap-3">
          <div className="p-2 rounded-lg bg-foreground/5 text-muted-foreground mt-0.5">
            <Calendar size={16} />
          </div>
          <div className="space-y-0.5">
            <span className="text-[10px] font-medium text-muted-foreground tracking-wide block">截止约束 / Deadline</span>
            <p className="text-sm font-semibold text-foreground">
              {deadline.text}
            </p>
            <p className="text-[10px] text-muted-foreground/60">
              {deadline.is_explicit ? '（用户指定截止）' : '（建议合理时间段）'}
            </p>
          </div>
        </div>

        {/* Time Plan section */}
        <div className="flex items-start gap-3">
          <div className="p-2 rounded-lg bg-foreground/5 text-muted-foreground mt-0.5">
            <Clock size={16} />
          </div>
          <div className="space-y-0.5">
            <span className="text-[10px] font-medium text-muted-foreground tracking-wide block">时间与缓冲 / Time & Buffer</span>
            <p className="text-sm font-semibold text-foreground">
              预计耗时: {formatPlannedTime(time_plan.planned_minutes)}
            </p>
            <p className="text-xs text-emerald-400 font-medium">
              安全缓冲: {formatPlannedTime(time_plan.buffer_minutes)}
            </p>
            {time_plan.available_minutes !== undefined && time_plan.available_minutes !== null && (
              <p className="text-[10px] text-muted-foreground/80">
                可用时间预算: {formatPlannedTime(time_plan.available_minutes)}
              </p>
            )}
          </div>
        </div>
      </div>

      {/* Scope definition with priority headings */}
      <div className="space-y-3 pt-4 border-t border-muted/20">
        <div className="space-y-2">
          <span className="text-[10px] font-semibold text-muted-foreground/50 tracking-wider uppercase block">必须完成 / Must Have</span>
          <div className="flex flex-wrap gap-2">
            {scope.must_have.map((item, idx) => (
              <span key={idx} className="px-2.5 py-1 text-xs rounded-lg border border-red-500/20 bg-red-500/5 text-red-400 font-medium">
                {item}
              </span>
            ))}
          </div>
        </div>

        {scope.should_have && scope.should_have.length > 0 && (
          <div className="space-y-2">
            <span className="text-[10px] font-semibold text-muted-foreground/50 tracking-wider uppercase block">建议包含 / Should Have</span>
            <div className="flex flex-wrap gap-2">
              {scope.should_have.map((item, idx) => (
                <span key={idx} className="px-2.5 py-1 text-xs rounded-lg border border-muted bg-foreground/5 text-foreground/80">
                  {item}
                </span>
              ))}
            </div>
          </div>
        )}

        {scope.can_cut && scope.can_cut.length > 0 && (
          <div className="space-y-2">
            <span className="text-[10px] font-semibold text-muted-foreground/50 tracking-wider uppercase block">时间不足时可舍弃 / Can Cut</span>
            <div className="flex flex-wrap gap-2">
              {scope.can_cut.map((item, idx) => (
                <span key={idx} className="px-2.5 py-1 text-xs rounded-lg border border-dashed border-muted bg-transparent text-muted-foreground">
                  {item}
                </span>
              ))}
            </div>
          </div>
        )}
      </div>

      {/* Workstreams definition */}
      {workstreams && workstreams.length > 0 && (
        <div className="space-y-3 pt-4 border-t border-muted/20">
          <span className="text-[10px] font-semibold text-muted-foreground/50 tracking-wider uppercase block">执行工作流 / Workstreams</span>
          <div className="space-y-2.5">
            {workstreams.map((stream) => {
              const { references: resolvedTasks } = resolveNodeReferences(rootNode, stream.task_client_node_ids);
              return (
                <div key={stream.workstream_id} className="p-3.5 rounded-xl border border-muted/40 bg-foreground/5 space-y-1.5">
                  <div className="flex items-center gap-1.5 text-xs font-semibold text-foreground/80">
                    <GitBranch size={13} className="text-muted-foreground" />
                    <span>{stream.title}</span>
                  </div>
                  <p className="text-xs text-muted-foreground font-light leading-relaxed">
                    预期输出：<span className="text-foreground/80 font-normal">{stream.output}</span>
                  </p>
                  {resolvedTasks.length > 0 && (
                    <div className="flex flex-wrap gap-1.5 pt-1">
                      {resolvedTasks.map((t) => (
                        <span 
                          key={t.nodeId} 
                          title={t.exists ? '关联任务已在树中定义' : `[错误] 丢失任务引用: ${t.nodeId}`}
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

      {/* Critical Path definition */}
      {resolvedCriticalPath.length > 0 && (
        <div className="space-y-2 pt-4 border-t border-muted/20">
          <div className="flex items-center gap-1.5">
            <KeyRound size={12} className="text-amber-400" />
            <span className="text-[10px] font-semibold text-muted-foreground/50 tracking-wider uppercase block">关键路径 / Critical Path</span>
          </div>
          <div className="flex flex-wrap gap-1.5">
            {resolvedCriticalPath.map((t) => (
              <span 
                key={t.nodeId} 
                className={`text-[10px] px-2 py-0.5 rounded border font-medium ${
                  t.exists
                    ? 'border-amber-500/20 bg-amber-500/5 text-amber-400'
                    : 'border-red-500/30 bg-red-500/5 text-red-400 font-mono'
                }`}
              >
                {t.title}
              </span>
            ))}
          </div>
        </div>
      )}
    </div>
  );
};
