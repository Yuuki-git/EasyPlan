import React from 'react';
import { CalendarPlus } from 'lucide-react';

interface PracticeLoopPanelProps {
  loops: Array<{
    loopId: string;
    loopKey: string;
    title: string;
    doneCriteria: string;
    targetPerWeek: number;
    currentWeekCompleted: number;
    totalCompleted: number;
    requiredCompletions: number;
    estimatedEnd: string;
    status: 'active' | 'paused' | 'completed' | 'superseded';
    canScheduleToday: boolean;
    activeOccurrenceTaskId: string | null;
    weeklyLabel: string;
    totalLabel: string;
  }>;
  onSchedule: (loopId: string) => Promise<void>;
  isPending: boolean;
  practiceError: string | null;
}

export const PracticeLoopPanel: React.FC<PracticeLoopPanelProps> = ({
  loops,
  onSchedule,
  isPending,
  practiceError
}) => {
  if (!loops || loops.length === 0) return null;

  return (
    <div className="w-full flex flex-col gap-4 mt-4 select-none">
      <div className="flex flex-col gap-1.5 border-b border-border/40 pb-2">
        <h3 className="text-sm font-semibold tracking-wide text-foreground/80 uppercase">
          循环练习
        </h3>
        <p className="text-xs text-muted-foreground">
          在本阶段需要高频重复磨炼的循环习惯，帮助您通过微习惯达成大目标。
        </p>
      </div>

      {practiceError && (
        <div className="text-xs py-2 px-3 rounded-lg border border-red-500/20 bg-red-500/5 text-red-500 animate-pulse">
          {practiceError}
        </div>
      )}

      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        {loops.map((loop) => {
          const isActive = loop.status === 'active';
          
          // Determine button disabled reason explanation
          let disabledReason = '';
          if (!isActive) {
            disabledReason = '该练习已不处于活跃状态';
          } else if (loop.activeOccurrenceTaskId) {
            disabledReason = '今天已生成待办任务，请先完成';
          } else if (!loop.canScheduleToday) {
            disabledReason = '今天已完成该练习或本周次数已达上限';
          }

          return (
            <div
              key={loop.loopId}
              className="group relative flex flex-col justify-between p-4 rounded-xl border border-border/50 bg-card hover:bg-accent/5 hover:border-accent/40 transition-all duration-300 shadow-sm"
            >
              <div className="flex flex-col gap-2">
                <div className="flex items-start justify-between gap-3">
                  <h4 className="text-sm font-medium text-foreground group-hover:text-primary transition-colors line-clamp-1">
                    {loop.title}
                  </h4>
                  <span
                    className={`text-[10px] px-2 py-0.5 rounded-full font-medium ${
                      isActive
                        ? 'bg-blue-500/10 text-blue-500 border border-blue-500/20'
                        : 'bg-muted-foreground/10 text-muted-foreground border border-muted-foreground/20'
                    }`}
                  >
                    {isActive ? '进行中' : '已归档'}
                  </span>
                </div>

                <p className="text-xs text-muted-foreground line-clamp-2 min-h-[2rem]">
                  <strong className="text-foreground/75">验收指标：</strong>
                  {loop.doneCriteria}
                </p>

                <div className="flex flex-wrap gap-x-4 gap-y-2 mt-2 pt-2 border-t border-border/30 text-[11px] text-muted-foreground">
                  <div className="flex items-center gap-1">
                    <span className="font-semibold text-foreground/80">{loop.weeklyLabel}</span>
                  </div>
                  <div className="flex items-center gap-1">
                    <span className="font-semibold text-foreground/80">{loop.totalLabel}</span>
                  </div>
                  <div className="flex items-center gap-1 ml-auto">
                    <span>结束日期: {loop.estimatedEnd}</span>
                  </div>
                </div>
              </div>

              {isActive && (
                <div className="mt-4 flex items-center justify-between gap-2 pt-2 border-t border-border/20">
                  <span className="text-[10px] text-muted-foreground line-clamp-1 max-w-[65%]">
                    {disabledReason}
                  </span>
                  <button
                    onClick={() => loop.canScheduleToday && !loop.activeOccurrenceTaskId && onSchedule(loop.loopId)}
                    disabled={isPending || !loop.canScheduleToday || !!loop.activeOccurrenceTaskId}
                    className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium bg-primary text-primary-foreground hover:opacity-90 active:scale-95 disabled:pointer-events-none disabled:bg-muted disabled:text-muted-foreground transition-all ml-auto cursor-pointer"
                    title={disabledReason || '安排到今天的任务列表中'}
                    aria-label="安排到今天"
                  >
                    <CalendarPlus size={13} />
                    <span>安排到今天</span>
                  </button>
                </div>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
};
