import React, { useState, useEffect } from 'react';
import { useAppStore } from '../store/useAppStore';
import { useExecutionRefine } from '../hooks/useExecutionRefine';
import { ExecutionDiffPreview } from './ExecutionDiffPreview';
import { ExecutionRefineMode } from '../types/api';
import {
  X,
  Clock,
  Calendar,
  AlertOctagon,
  Loader2,
  CheckCircle2,
  ChevronDown,
  ChevronUp,
  AlertTriangle,
  Play
} from 'lucide-react';
import { clsx } from 'clsx';

export const ExecutionRefinePanel: React.FC = () => {
  const {
    selectedProjectId,
    boardTasks,
    executionRefineActiveRequestId,
    executionRefineStatus,
    executionRefineProposal,
    executionRefineScopeFingerprint,
    executionRefineErrorCode,
    executionRefineErrorMessage,
    executionRefineLogs,
    isExecutionRefinePanelOpen,
    setExecutionRefinePanelOpen,
    startExecutionRefine,
    fetchExecutionRefineSnapshot,
    cancelExecutionRefine,
    applyExecutionRefine,
    resetExecutionRefine
  } = useAppStore();

  // Bind the SSE listener hook
  useExecutionRefine();

  const [mode, setMode] = useState<ExecutionRefineMode>('time_budget');
  const [availableMinutes, setAvailableMinutes] = useState<number>(30);
  const [newDeadline, setNewDeadline] = useState<string>('');
  const [priorityTaskIds, setPriorityTaskIds] = useState<string[]>([]);
  const [blockedTaskIds, setBlockedTaskIds] = useState<string[]>([]);
  const [userContext, setUserContext] = useState<string>('');

  const [isApplying, setIsApplying] = useState(false);
  const [applyError, setApplyError] = useState<string | null>(null);
  const [showAllLogs, setShowAllLogs] = useState(false);
  const [isCancelling, setIsCancelling] = useState(false);

  const [showPriorityDropdown, setShowPriorityDropdown] = useState(false);
  const [showBlockedDropdown, setShowBlockedDropdown] = useState(false);

  // Recover state on mount/open
  useEffect(() => {
    if (isExecutionRefinePanelOpen && executionRefineActiveRequestId) {
      fetchExecutionRefineSnapshot(executionRefineActiveRequestId).catch(() => {
        // Ignore, handled by error boundaries
      });
      // Restore form inputs from localStorage if present
      const savedMode = localStorage.getItem('easyplan_execution_refine_mode');
      if (savedMode) setMode(savedMode as ExecutionRefineMode);

      const savedMins = localStorage.getItem('easyplan_execution_refine_available_minutes');
      if (savedMins) setAvailableMinutes(Number(savedMins));

      const savedCtx = localStorage.getItem('easyplan_execution_refine_user_context');
      if (savedCtx) setUserContext(savedCtx);

      const savedPriority = localStorage.getItem('easyplan_execution_refine_priority_task_ids');
      if (savedPriority) setPriorityTaskIds(JSON.parse(savedPriority));

      const savedBlocked = localStorage.getItem('easyplan_execution_refine_blocked_task_ids');
      if (savedBlocked) setBlockedTaskIds(JSON.parse(savedBlocked));

      const savedDeadline = localStorage.getItem('easyplan_execution_refine_new_deadline');
      if (savedDeadline) setNewDeadline(savedDeadline);
    }
  }, [isExecutionRefinePanelOpen, executionRefineActiveRequestId]);

  if (!isExecutionRefinePanelOpen) return null;

  // Filter eligible tasks: incomplete, belonging to current project, and not task_assist/practice
  const eligibleTasks = boardTasks?.filter(t =>
    t.thread_id === selectedProjectId &&
    t.status === 'active' &&
    t.source !== 'task_assist' &&
    t.source !== 'practice_loop'
  ) || [];

  const handleStart = async () => {
    setApplyError(null);
    try {
      await startExecutionRefine(mode, {
        available_minutes: mode === 'time_budget' ? availableMinutes : null,
        new_deadline: mode === 'context_change' && newDeadline ? newDeadline : null,
        priority_task_ids: mode === 'context_change' ? priorityTaskIds : [],
        blocked_task_ids: mode === 'context_change' ? blockedTaskIds : [],
        user_context: userContext || null
      });
    } catch (err) {
      // Handled in store
    }
  };

  const handleCancel = async () => {
    if (isCancelling) return;
    if (!executionRefineActiveRequestId) return;
    setIsCancelling(true);
    setApplyError(null);
    try {
      await cancelExecutionRefine(executionRefineActiveRequestId);
      resetExecutionRefine();
      setExecutionRefinePanelOpen(false);
    } catch (err) {
      setApplyError((err as Error).message || '取消生成失败，请重试');
    } finally {
      setIsCancelling(false);
    }
  };

  const handleApply = async () => {
    if (!executionRefineActiveRequestId) return;
    setIsApplying(true);
    setApplyError(null);
    try {
      await applyExecutionRefine(executionRefineActiveRequestId, executionRefineScopeFingerprint);
      // Success will reload board and show success screen
    } catch (err) {
      setApplyError((err as Error).message || '应用调整方案失败，请重试');
    } finally {
      setIsApplying(false);
    }
  };

  const handleClose = async () => {
    if (isCancelling) return;
    if (executionRefineStatus === 'running') {
      if (executionRefineActiveRequestId) {
        setIsCancelling(true);
        setApplyError(null);
        try {
          await cancelExecutionRefine(executionRefineActiveRequestId);
        } catch (err) {
          setApplyError('取消服务失败，请重试');
          setIsCancelling(false);
          return;
        }
        setIsCancelling(false);
      }
    }
    resetExecutionRefine();
    setExecutionRefinePanelOpen(false);
  };

  const isIdle = executionRefineStatus === null;
  const isRunning = executionRefineStatus === 'running';
  const isReady = executionRefineStatus === 'ready';
  const isApplied = executionRefineStatus === 'applied';
  const isFailed = executionRefineStatus === 'failed';

  const togglePriorityTask = (id: string) => {
    setPriorityTaskIds(prev =>
      prev.includes(id) ? prev.filter(x => x !== id) : [...prev, id].slice(0, 5)
    );
  };

  const toggleBlockedTask = (id: string) => {
    setBlockedTaskIds(prev =>
      prev.includes(id) ? prev.filter(x => x !== id) : [...prev, id].slice(0, 5)
    );
  };

  return (
    <div className="fixed inset-0 z-50 flex justify-end">
      {/* Backdrop */}
      <div
        className="absolute inset-0 bg-background/40 backdrop-blur-sm transition-opacity"
        onClick={handleClose}
      />

      {/* Drawer */}
      <div className="relative w-full max-w-lg h-full bg-background border-l border-muted/30 shadow-2xl flex flex-col z-10 animate-slide-in">
        {/* Header */}
        <header className="h-16 border-b border-muted/20 px-6 flex items-center justify-between shrink-0">
          <div className="flex items-center gap-2">
            <div className="w-2 h-2 rounded-full bg-blue-500 animate-pulse" />
            <h3 className="font-semibold text-foreground text-sm tracking-wide">
              调整当前计划 (Execution Refine)
            </h3>
          </div>
          <button
            onClick={handleClose}
            className="p-1.5 hover:bg-muted/20 text-muted-foreground hover:text-foreground rounded-lg transition-colors"
          >
            <X size={16} />
          </button>
        </header>

        {/* Content Area */}
        <div className="flex-1 overflow-y-auto px-6 py-6 space-y-6 select-none custom-scrollbar">
          {/* 1. Idle Form State */}
          {isIdle && (
            <div className="space-y-6">
              {/* Segmented Mode Selector */}
              <div className="space-y-2">
                <label className="text-[10px] font-semibold text-muted-foreground/50 tracking-wider uppercase">
                  调整模式 / Mode
                </label>
                <div className="flex p-1 rounded-xl bg-foreground/[0.03] border border-muted/30 gap-1">
                  {(['time_budget', 'progress_recovery', 'context_change'] as ExecutionRefineMode[]).map(m => (
                    <button
                      key={m}
                      onClick={() => setMode(m)}
                      className={clsx(
                        "flex-1 py-1.5 rounded-lg text-xs font-medium transition-all",
                        mode === m
                          ? "bg-background text-foreground shadow-sm font-semibold border border-muted/20"
                          : "text-muted-foreground/60 hover:text-foreground hover:bg-muted/10"
                      )}
                    >
                      {m === 'time_budget' && '时间预算'}
                      {m === 'progress_recovery' && '进度恢复'}
                      {m === 'context_change' && '条件变更'}
                    </button>
                  ))}
                </div>
              </div>

              {/* Mode Dependent Controls */}
              {mode === 'time_budget' && (
                <div className="space-y-2 animate-fade-in">
                  <label className="text-[10px] font-semibold text-muted-foreground/50 tracking-wider uppercase flex items-center gap-1">
                    <Clock size={11} />
                    <span>今日可支配时间容量 (available minutes)</span>
                  </label>
                  <div className="flex items-center gap-3">
                    <input
                      type="number"
                      min={10}
                      max={480}
                      value={availableMinutes}
                      onChange={e => setAvailableMinutes(Math.max(10, Math.min(480, Number(e.target.value))))}
                      className="flex-1 bg-muted/10 text-foreground border border-muted/30 focus:border-blue-500/50 rounded-xl px-3.5 py-2 text-sm focus:outline-none font-mono"
                    />
                    <span className="text-xs text-muted-foreground">分钟 (10-480)</span>
                  </div>
                </div>
              )}

              {mode === 'context_change' && (
                <div className="space-y-4 animate-fade-in">
                  {/* Deadline Datepicker */}
                  <div className="space-y-2">
                    <label className="text-[10px] font-semibold text-muted-foreground/50 tracking-wider uppercase flex items-center gap-1">
                      <Calendar size={11} />
                      <span>新截止日期 / New Deadline</span>
                    </label>
                    <input
                      type="datetime-local"
                      value={newDeadline}
                      onChange={e => setNewDeadline(e.target.value)}
                      className="w-full bg-muted/10 text-foreground border border-muted/30 focus:border-blue-500/50 rounded-xl px-3.5 py-2 text-sm focus:outline-none font-mono"
                    />
                  </div>

                  {/* Priority Task Picker */}
                  <div className="space-y-2 relative">
                    <label className="text-[10px] font-semibold text-muted-foreground/50 tracking-wider uppercase block">
                      高优先级任务 / Priority Tasks (最多 5 个)
                    </label>
                    <button
                      onClick={() => setShowPriorityDropdown(!showPriorityDropdown)}
                      className="w-full bg-muted/10 text-left text-foreground border border-muted/30 rounded-xl px-3.5 py-2 text-sm flex items-center justify-between"
                    >
                      <span className="truncate">
                        {priorityTaskIds.length === 0
                          ? '选择优先级任务...'
                          : `已选择 ${priorityTaskIds.length} 个任务`}
                      </span>
                      <ChevronDown size={14} className="text-muted-foreground" />
                    </button>
                    {showPriorityDropdown && (
                      <div className="absolute top-[68px] left-0 right-0 max-h-48 overflow-y-auto bg-background border border-muted/40 rounded-xl p-2 z-20 shadow-lg custom-scrollbar">
                        {eligibleTasks.length === 0 ? (
                          <div className="p-3 text-xs text-muted-foreground text-center">暂无可选择的活动任务</div>
                        ) : (
                          eligibleTasks.map(t => {
                            const isSelected = priorityTaskIds.includes(t.id);
                            return (
                              <div
                                key={t.id}
                                onClick={() => togglePriorityTask(t.id)}
                                className={clsx(
                                  "flex items-center gap-2 p-2 rounded-lg text-xs cursor-pointer hover:bg-muted/30 transition-colors",
                                  isSelected && "bg-blue-500/5 text-blue-400 font-semibold"
                                )}
                              >
                                <input
                                  type="checkbox"
                                  checked={isSelected}
                                  onChange={() => {}}
                                  className="rounded border-muted text-blue-500 focus:ring-blue-500/20"
                                />
                                <span className="truncate">{t.title}</span>
                              </div>
                            );
                          })
                        )}
                      </div>
                    )}
                  </div>

                  {/* Blocked Task Picker */}
                  <div className="space-y-2 relative">
                    <label className="text-[10px] font-semibold text-muted-foreground/50 tracking-wider uppercase block">
                      已被阻塞任务 / Blocked Tasks (最多 5 个)
                    </label>
                    <button
                      onClick={() => setShowBlockedDropdown(!showBlockedDropdown)}
                      className="w-full bg-muted/10 text-left text-foreground border border-muted/30 rounded-xl px-3.5 py-2 text-sm flex items-center justify-between"
                    >
                      <span className="truncate">
                        {blockedTaskIds.length === 0
                          ? '选择被阻塞的任务...'
                          : `已选择 ${blockedTaskIds.length} 个任务`}
                      </span>
                      <ChevronDown size={14} className="text-muted-foreground" />
                    </button>
                    {showBlockedDropdown && (
                      <div className="absolute top-[68px] left-0 right-0 max-h-48 overflow-y-auto bg-background border border-muted/40 rounded-xl p-2 z-20 shadow-lg custom-scrollbar">
                        {eligibleTasks.length === 0 ? (
                          <div className="p-3 text-xs text-muted-foreground text-center">暂无可选择的活动任务</div>
                        ) : (
                          eligibleTasks.map(t => {
                            const isSelected = blockedTaskIds.includes(t.id);
                            return (
                              <div
                                key={t.id}
                                onClick={() => toggleBlockedTask(t.id)}
                                className={clsx(
                                  "flex items-center gap-2 p-2 rounded-lg text-xs cursor-pointer hover:bg-muted/30 transition-colors",
                                  isSelected && "bg-blue-500/5 text-blue-400 font-semibold"
                                )}
                              >
                                <input
                                  type="checkbox"
                                  checked={isSelected}
                                  onChange={() => {}}
                                  className="rounded border-muted text-blue-500 focus:ring-blue-500/20"
                                />
                                <span className="truncate">{t.title}</span>
                              </div>
                            );
                          })
                        )}
                      </div>
                    )}
                  </div>
                </div>
              )}

              {/* Context Area */}
              <div className="space-y-2">
                <label className="text-[10px] font-semibold text-muted-foreground/50 tracking-wider uppercase">
                  变更说明与偏好备注 (user context)
                </label>
                <textarea
                  maxLength={1000}
                  value={userContext}
                  onChange={e => setUserContext(e.target.value)}
                  rows={4}
                  placeholder="补充您需要微调计划的背景信息，例如：“我今天下午有突发会议，因此只能处理紧急的任务。”（可选）"
                  className="w-full bg-muted/10 text-foreground border border-muted/30 focus:border-blue-500/50 rounded-xl px-3.5 py-2.5 text-xs focus:outline-none resize-none placeholder:text-muted-foreground/30 font-light"
                />
              </div>

              {/* Action Button */}
              <button
                onClick={handleStart}
                className="w-full py-3 bg-blue-500 hover:bg-blue-600 active:scale-[0.99] text-white font-semibold rounded-xl text-xs transition-all shadow-md shadow-blue-500/10 flex items-center justify-center gap-1.5"
              >
                <Play size={12} fill="white" />
                <span>生成调整方案</span>
              </button>
            </div>
          )}

          {/* 2. Generating Logs */}
          {isRunning && (
            <div className="space-y-5 py-8 animate-fade-in text-center">
              <Loader2 size={32} className="text-blue-500 animate-spin mx-auto" />
              <div className="space-y-1">
                <h5 className="text-sm font-medium text-foreground/80">
                  AI 正在精心微调计划中...
                </h5>
                <p className="text-xs text-muted-foreground/60">
                  根据当前任务与最新条件执行容量核算
                </p>
              </div>

              {/* logs console */}
              {executionRefineLogs.length > 0 && (
                <div className="p-3.5 rounded-xl bg-foreground/[0.03] border border-muted/40 text-left font-mono text-[10px] text-muted-foreground/80 space-y-1.5 max-w-sm mx-auto shadow-sm">
                  <div className="flex items-center justify-between border-b border-muted/20 pb-1.5 mb-1.5">
                    <span className="text-foreground/50 uppercase tracking-wider font-semibold">日志 / Execution Log</span>
                    <button
                      onClick={() => setShowAllLogs(!showAllLogs)}
                      className="hover:text-foreground transition-colors flex items-center gap-0.5"
                    >
                      <span>{showAllLogs ? '收起' : '展开全部'}</span>
                      {showAllLogs ? <ChevronUp size={10} /> : <ChevronDown size={10} />}
                    </button>
                  </div>
                  {showAllLogs ? (
                    <div className="space-y-1.5 max-h-32 overflow-y-auto pr-1">
                      {executionRefineLogs.map((log, idx) => (
                        <div key={idx} className="leading-relaxed border-l-2 border-blue-500/30 pl-2">
                          {log}
                        </div>
                      ))}
                    </div>
                  ) : (
                    <div className="leading-relaxed border-l-2 border-blue-500 pl-2 animate-pulse text-foreground/70">
                      {executionRefineLogs[executionRefineLogs.length - 1]}
                    </div>
                  )}
                </div>
              )}

              {/* Error Alert inside Loader */}
              {applyError && (
                <div className="p-3 rounded-lg bg-red-500/10 border border-red-500/20 text-xs text-red-500 font-light flex gap-2 max-w-sm mx-auto text-left">
                  <AlertOctagon size={14} className="shrink-0 mt-0.5" />
                  <p>{applyError}</p>
                </div>
              )}

              {/* Cancel Generation Button */}
              <button
                disabled={isCancelling}
                onClick={handleCancel}
                className={clsx(
                  "px-4 py-2 border border-muted/60 text-muted-foreground hover:text-foreground hover:bg-muted/10 rounded-xl text-xs transition-all flex items-center gap-1.5 mx-auto",
                  isCancelling && "opacity-50 cursor-not-allowed"
                )}
              >
                {isCancelling && <Loader2 size={12} className="animate-spin" />}
                <span>{isCancelling ? '正在取消...' : '取消生成'}</span>
              </button>
            </div>
          )}

          {/* 3. Ready Preview and Apply Proposal */}
          {isReady && executionRefineProposal && (
            <div className="space-y-6">
              <ExecutionDiffPreview proposal={executionRefineProposal} />

              {/* Error Banner on Apply */}
              {applyError && (
                <div className="p-3 rounded-lg bg-red-500/10 border border-red-500/20 text-xs text-red-500 font-light flex gap-2">
                  <AlertOctagon size={14} className="shrink-0 mt-0.5" />
                  <p>{applyError}</p>
                </div>
              )}

              {/* Apply / Close buttons */}
              <div className="flex gap-3 pt-4 border-t border-muted/20">
                <button
                  onClick={handleClose}
                  className="flex-1 py-2.5 border border-muted/60 hover:bg-muted/10 text-muted-foreground hover:text-foreground font-semibold rounded-xl text-xs transition-colors"
                >
                  放弃建议
                </button>
                <button
                  disabled={isApplying}
                  onClick={handleApply}
                  className={clsx(
                    "flex-1 py-2.5 rounded-xl font-semibold text-xs transition-all flex items-center justify-center gap-1.5 shadow-sm text-white",
                    isApplying
                      ? "bg-blue-500/40 cursor-wait"
                      : "bg-blue-500 hover:bg-blue-600"
                  )}
                >
                  {isApplying && <Loader2 size={12} className="animate-spin" />}
                  <span>应用本次调整</span>
                </button>
              </div>
            </div>
          )}

          {/* 4. Applied Done View */}
          {isApplied && (
            <div className="space-y-6 py-12 text-center animate-fade-in">
              <div className="w-16 h-16 rounded-full bg-green-500/10 border border-green-500/20 text-green-500 flex items-center justify-center mx-auto shadow-sm">
                <CheckCircle2 size={32} />
              </div>
              <div className="space-y-1.5 max-w-xs mx-auto">
                <h4 className="text-base font-semibold text-foreground/90">
                  执行计划已成功微调！
                </h4>
                <p className="text-xs text-muted-foreground/60 font-light leading-relaxed">
                  调整后的顺序、时间预算及 My Day 标记已完美应用至当前看板视图。
                </p>
              </div>
              <button
                onClick={handleClose}
                className="px-6 py-2.5 bg-muted/60 hover:bg-muted text-foreground font-semibold rounded-xl text-xs transition-colors mx-auto"
              >
                完成
              </button>
            </div>
          )}

          {/* 5. Failed View */}
          {isFailed && (
            <div className="space-y-6 py-8 text-center animate-fade-in">
              <div className="w-16 h-16 rounded-full bg-red-500/10 border border-red-500/20 text-red-500 flex items-center justify-center mx-auto shadow-sm">
                <AlertOctagon size={32} />
              </div>
              <div className="space-y-1.5 max-w-xs mx-auto">
                <h4 className="text-sm font-semibold text-foreground/90">
                  生成或应用调整方案失败
                </h4>
                <p className="text-xs text-red-500/80 font-light leading-relaxed">
                  {executionRefineErrorMessage || '发生了未知错误，请重试。'}
                </p>
              </div>
              {executionRefineErrorCode === 'EXECUTION_REFINE_CONTEXT_STALE' ? (
                <div className="space-y-3 pt-2">
                  <div className="p-3 bg-amber-500/10 border border-amber-500/20 rounded-xl text-xs text-amber-500 font-light leading-relaxed max-w-xs mx-auto flex gap-2">
                    <AlertTriangle size={14} className="shrink-0 mt-0.5" />
                    <p>任务已发生变化，请保留当前输入偏好并重新生成。</p>
                  </div>
                  <button
                    onClick={handleStart}
                    className="px-6 py-2.5 bg-blue-500 hover:bg-blue-600 text-white font-semibold rounded-xl text-xs transition-colors mx-auto flex items-center justify-center gap-1.5 active:scale-[0.98]"
                  >
                    <span>重新生成调整方案</span>
                  </button>
                </div>
              ) : (
                <button
                  onClick={() => {
                    resetExecutionRefine();
                  }}
                  className="px-6 py-2.5 bg-muted/60 hover:bg-muted text-foreground font-semibold rounded-xl text-xs transition-colors mx-auto"
                >
                  返回修改偏好
                </button>
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  );
};
