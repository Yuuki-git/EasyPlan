import React from 'react';
import { ExecutionRefineProposal, TaskResponse } from '../types/api';
import { useAppStore } from '../store/useAppStore';
import {
  Sparkles,
  ArrowRight,
  Plus,
  Sun,
  AlertTriangle,
  CheckCircle2,
  PlayCircle,
  EyeOff
} from 'lucide-react';
import { clsx } from 'clsx';

interface ExecutionDiffPreviewProps {
  proposal: ExecutionRefineProposal;
}

export const ExecutionDiffPreview: React.FC<ExecutionDiffPreviewProps> = ({ proposal }) => {
  const { boardTasks } = useAppStore();

  const getTaskTitle = (id: string) => {
    return boardTasks?.find(t => t.id === id)?.title || '任务暂不可用';
  };

  const getTaskObject = (id: string): TaskResponse | undefined => {
    return boardTasks?.find(t => t.id === id);
  };

  const {
    summary,
    user_facing_reasons,
    preserved_constraints,
    operations,
    focus_task_ids,
    estimated_focus_minutes,
    buffer_minutes,
    warnings
  } = proposal;

  const updateOps = operations.filter(op => op.operation_type === 'update_task');
  const addOps = operations.filter(op => op.operation_type === 'add_task');
  const reorderOps = operations.filter(op => op.operation_type === 'reorder_siblings');
  const myDayOps = operations.filter(op => op.operation_type === 'set_my_day');

  return (
    <div className="space-y-6 animate-fade-in text-left">
      {/* Summary Alert */}
      <div className="p-4 rounded-xl border border-blue-500/10 bg-blue-500/5 text-xs text-blue-400 font-light leading-relaxed flex gap-2">
        <Sparkles size={14} className="shrink-0 mt-0.5" />
        <div>
          <span className="font-semibold uppercase text-[9px] tracking-wider block mb-1">调整方案摘要</span>
          <p className="font-medium text-foreground">{summary}</p>
        </div>
      </div>

      {/* Focus & Capacity Stats */}
      {(focus_task_ids.length > 0 || estimated_focus_minutes > 0) && (
        <div className="p-4 rounded-xl border border-muted/40 bg-background/30 space-y-3.5">
          <span className="text-[10px] font-semibold text-muted-foreground/50 tracking-wider uppercase block">
            今日聚焦与容量核算 / Focus & Capacity
          </span>
          <div className="flex items-center justify-between text-xs text-muted-foreground font-light border-b border-muted/20 pb-3">
            <div>
              聚焦任务预估：<span className="font-semibold text-foreground">{estimated_focus_minutes} min</span>
            </div>
            <div>
              安全缓冲时间：<span className="font-semibold text-foreground">{buffer_minutes} min</span>
            </div>
          </div>
          {focus_task_ids.length > 0 && (
            <div className="space-y-2">
              <span className="text-[10px] text-muted-foreground/75 block">聚焦执行的任务：</span>
              <ul className="space-y-1.5 pl-1.5">
                {focus_task_ids.map(id => (
                  <li key={id} className="text-xs text-foreground/80 font-light flex items-center gap-1.5">
                    <PlayCircle size={12} className="text-blue-500" />
                    <span>{getTaskTitle(id)}</span>
                  </li>
                ))}
              </ul>
            </div>
          )}
        </div>
      )}

      {/* 1. Updated Tasks */}
      {updateOps.length > 0 && (
        <div className="space-y-3">
          <span className="text-[10px] font-semibold text-muted-foreground/50 tracking-wider uppercase block">
            修改任务属性 / Updated Tasks ({updateOps.length})
          </span>
          <div className="space-y-3">
            {updateOps.map((op, idx) => {
              if (op.operation_type !== 'update_task') return null;
              const task = getTaskObject(op.task_id);
              return (
                <div key={idx} className="p-3.5 rounded-xl border border-muted/50 bg-background/50 space-y-3">
                  <div className="flex items-center justify-between border-b border-muted/20 pb-2">
                    <h5 className="font-semibold text-foreground/90 text-xs">
                      {task?.title || getTaskTitle(op.task_id)}
                    </h5>
                    <span className="text-[10px] text-muted-foreground bg-muted/20 px-2 py-0.5 rounded-full font-mono">
                      更新属性
                    </span>
                  </div>
                  <div className="space-y-2 text-xs font-light text-muted-foreground">
                    {op.changes.title && (
                      <div className="flex items-center gap-1 flex-wrap">
                        <span className="text-foreground/70 font-medium">任务标题：</span>
                        <span className="line-through">{task?.title || '无'}</span>
                        <ArrowRight size={10} className="text-muted-foreground/50" />
                        <span className="text-foreground font-normal">{op.changes.title}</span>
                      </div>
                    )}
                    {op.changes.description !== undefined && (
                      <div className="flex items-center gap-1 flex-wrap">
                        <span className="text-foreground/70 font-medium">任务描述：</span>
                        <span className="line-through max-w-[150px] truncate">{task?.description || '无'}</span>
                        <ArrowRight size={10} className="text-muted-foreground/50" />
                        <span className="text-foreground font-normal max-w-[150px] truncate">{op.changes.description || '无'}</span>
                      </div>
                    )}
                    {op.changes.estimated_minutes !== undefined && (
                      <div className="flex items-center gap-1 flex-wrap">
                        <span className="text-foreground/70 font-medium">预估时间：</span>
                        <span className="line-through">{task?.estimated_minutes ?? '无'} min</span>
                        <ArrowRight size={10} className="text-muted-foreground/50" />
                        <span className="text-foreground font-normal">{op.changes.estimated_minutes} min</span>
                      </div>
                    )}
                    {op.changes.done_criteria && (
                      <div className="flex items-center gap-1 flex-wrap">
                        <span className="text-foreground/70 font-medium">完成标准：</span>
                        <span className="line-through max-w-[150px] truncate">{task?.done_criteria || '无'}</span>
                        <ArrowRight size={10} className="text-muted-foreground/50" />
                        <span className="text-foreground font-normal max-w-[150px] truncate">{op.changes.done_criteria}</span>
                      </div>
                    )}
                    {op.changes.start_hint !== undefined && (
                      <div className="flex items-center gap-1 flex-wrap">
                        <span className="text-foreground/70 font-medium">开始提示：</span>
                        <span className="line-through max-w-[150px] truncate">{task?.start_hint || '无'}</span>
                        <ArrowRight size={10} className="text-muted-foreground/50" />
                        <span className="text-foreground font-normal max-w-[150px] truncate">{op.changes.start_hint || '无'}</span>
                      </div>
                    )}
                    {op.changes.fallback_action !== undefined && (
                      <div className="flex items-center gap-1 flex-wrap">
                        <span className="text-foreground/70 font-medium">退避行动：</span>
                        <span className="line-through max-w-[150px] truncate">{task?.fallback_action || '无'}</span>
                        <ArrowRight size={10} className="text-muted-foreground/50" />
                        <span className="text-foreground font-normal max-w-[150px] truncate">{op.changes.fallback_action || '无'}</span>
                      </div>
                    )}
                  </div>
                  <div className="text-[10px] text-muted-foreground/60 leading-relaxed italic bg-muted/10 p-2 rounded-lg">
                    变更原因：{op.reason}
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      )}

      {/* 2. Added Tasks */}
      {addOps.length > 0 && (
        <div className="space-y-3">
          <span className="text-[10px] font-semibold text-muted-foreground/50 tracking-wider uppercase block">
            新增行动任务 / Added Tasks ({addOps.length})
          </span>
          <div className="space-y-3">
            {addOps.map((op, idx) => {
              if (op.operation_type !== 'add_task') return null;
              return (
                <div key={idx} className="p-3.5 rounded-xl border border-muted/50 bg-background/50 space-y-3">
                  <div className="flex items-start justify-between gap-3">
                    <div className="space-y-1">
                      <h5 className="font-semibold text-foreground/90 text-sm">
                        {op.title}
                      </h5>
                      {op.parent_task_id && (
                        <span className="text-[10px] text-muted-foreground block font-light">
                          父任务：{getTaskTitle(op.parent_task_id)}
                        </span>
                      )}
                    </div>
                    <div className="flex items-center gap-1 text-[10px] font-mono text-muted-foreground bg-muted/20 px-2 py-0.5 rounded-full shrink-0">
                      <Plus size={10} className="text-blue-500" />
                      <span>{op.estimated_minutes} min</span>
                    </div>
                  </div>
                  {op.description && (
                    <p className="text-xs text-muted-foreground/80 leading-relaxed font-light pl-2.5 border-l border-muted/20">
                      {op.description}
                    </p>
                  )}
                  <div className="space-y-1.5 text-xs text-muted-foreground font-light">
                    <div>
                      <span className="font-medium text-foreground/70">完成标准：</span>
                      <span>{op.done_criteria}</span>
                    </div>
                    {op.start_hint && (
                      <div>
                        <span className="font-medium text-foreground/70">开始提示：</span>
                        <span>{op.start_hint}</span>
                      </div>
                    )}
                    {op.depends_on_refs.length > 0 && (
                      <div>
                        <span className="font-medium text-foreground/70">依赖草稿/节点：</span>
                        <span>{op.depends_on_refs.join(', ')}</span>
                      </div>
                    )}
                  </div>
                  <div className="text-[10px] text-muted-foreground/60 leading-relaxed italic bg-muted/10 p-2 rounded-lg">
                    新增原因：{op.reason}
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      )}

      {/* 3. Reorder Siblings */}
      {reorderOps.length > 0 && (
        <div className="space-y-3">
          <span className="text-[10px] font-semibold text-muted-foreground/50 tracking-wider uppercase block">
            调整执行顺序 / Order Changes ({reorderOps.length})
          </span>
          <div className="space-y-3">
            {reorderOps.map((op, idx) => {
              if (op.operation_type !== 'reorder_siblings') return null;
              return (
                <div key={idx} className="p-3.5 rounded-xl border border-muted/50 bg-background/50 space-y-3.5">
                  <div className="flex items-center justify-between border-b border-muted/20 pb-2">
                    <span className="text-[10px] text-muted-foreground font-light">
                      重排位置：{op.parent_task_id ? getTaskTitle(op.parent_task_id) : '根层级 (项目大盘)'}
                    </span>
                    <span className="text-[10px] text-muted-foreground bg-muted/20 px-2 py-0.5 rounded-full font-mono">
                      重新排序
                    </span>
                  </div>
                  <div className="space-y-2">
                    <span className="text-[10px] text-muted-foreground/75 block">调整后的顺序如下：</span>
                    <ol className="space-y-1.5 pl-4 list-decimal text-xs font-light text-foreground/80">
                      {op.ordered_task_ids.map(id => (
                        <li key={id}>{getTaskTitle(id)}</li>
                      ))}
                    </ol>
                  </div>
                  <div className="text-[10px] text-muted-foreground/60 leading-relaxed italic bg-muted/10 p-2 rounded-lg">
                    重排原因：{op.reason}
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      )}

      {/* 4. Set My Day */}
      {myDayOps.length > 0 && (
        <div className="space-y-3">
          <span className="text-[10px] font-semibold text-muted-foreground/50 tracking-wider uppercase block">
            “我的一天”勾选变更 / My Day Changes ({myDayOps.length})
          </span>
          <div className="space-y-3">
            {myDayOps.map((op, idx) => {
              if (op.operation_type !== 'set_my_day') return null;
              return (
                <div key={idx} className="p-3.5 rounded-xl border border-muted/50 bg-background/50 space-y-2.5">
                  <div className="flex items-center justify-between">
                    <h5 className="font-semibold text-foreground/90 text-xs">
                      {getTaskTitle(op.task_id)}
                    </h5>
                    <span className={clsx(
                      "text-[9px] font-medium px-1.5 py-0.5 rounded border flex items-center gap-1",
                      op.is_in_my_day
                        ? "bg-amber-500/10 text-amber-500 border-amber-500/20"
                        : "bg-muted text-muted-foreground border-muted-foreground/20"
                    )}>
                      {op.is_in_my_day ? <Sun size={9} /> : <EyeOff size={9} />}
                      <span>{op.is_in_my_day ? '加入我的一天' : '移出我的一天'}</span>
                    </span>
                  </div>
                  <div className="text-[10px] text-muted-foreground/60 leading-relaxed italic bg-muted/10 p-2 rounded-lg">
                    变更原因：{op.reason}
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      )}

      {/* reasons / user_facing_reasons */}
      {user_facing_reasons.length > 0 && (
        <div className="space-y-2.5">
          <span className="text-[10px] font-semibold text-muted-foreground/50 tracking-wider uppercase block">
            调整依据与推论 / Reasoning
          </span>
          <ul className="space-y-1.5 pl-1.5">
            {user_facing_reasons.map((reason, idx) => (
              <li key={idx} className="text-xs text-muted-foreground font-light leading-relaxed flex items-start gap-2">
                <CheckCircle2 size={12} className="text-blue-500 shrink-0 mt-0.5" />
                <span>{reason}</span>
              </li>
            ))}
          </ul>
        </div>
      )}

      {/* preserved_constraints */}
      {preserved_constraints.length > 0 && (
        <div className="space-y-2.5">
          <span className="text-[10px] font-semibold text-muted-foreground/50 tracking-wider uppercase block">
            已遵循的规划约束 / Preserved Constraints
          </span>
          <ul className="space-y-1.5 pl-1.5">
            {preserved_constraints.map((constraint, idx) => (
              <li key={idx} className="text-xs text-muted-foreground font-light leading-relaxed flex items-start gap-2">
                <CheckCircle2 size={12} className="text-green-500 shrink-0 mt-0.5" />
                <span>{constraint}</span>
              </li>
            ))}
          </ul>
        </div>
      )}

      {/* warnings */}
      {warnings.length > 0 && (
        <div className="space-y-2.5">
          <span className="text-[10px] font-semibold text-muted-foreground/50 tracking-wider uppercase block">
            规划调整警告 / Warnings
          </span>
          <div className="space-y-2">
            {warnings.map((warning, idx) => (
              <div key={idx} className="p-3 rounded-lg bg-amber-500/10 border border-amber-500/20 text-xs text-amber-500 font-light flex gap-2">
                <AlertTriangle size={14} className="shrink-0 mt-0.5" />
                <p>{warning}</p>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
};
