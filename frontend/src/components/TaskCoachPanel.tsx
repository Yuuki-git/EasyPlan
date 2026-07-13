import React, { useState, useEffect } from 'react';
import { useAppStore } from '../store/useAppStore';
import { useTaskAssist } from '../hooks/useTaskAssist';
import { TaskAssistProposal } from './TaskAssistProposal';
import { getTaskAssistPlaceholder, getTaskAssistModeLabel } from '../lib/taskAssist';
import { TaskAssistMode } from '../types/api';
import {
  X,
  Sparkles,
  Loader2,
  CheckCircle,
  AlertOctagon,
  ChevronDown,
  ChevronUp
} from 'lucide-react';
import { clsx } from 'clsx';

export const TaskCoachPanel: React.FC = () => {
  // 1. Subscribe to the task assist EventSource hook
  useTaskAssist();

  const {
    boardTasks,
    taskAssistActiveTaskId,
    taskAssistActiveRequestId,
    taskAssistStatus,
    taskAssistProposal,
    taskAssistErrorCode,
    taskAssistErrorMessage,
    taskAssistLogs,
    isTaskAssistPanelOpen,
    setTaskAssistPanelOpen,
    startTaskAssist,
    cancelTaskAssist,
    applyTaskAssist,
    resetTaskAssist
  } = useAppStore();

  const [mode, setMode] = useState<TaskAssistMode>(
    () => (localStorage.getItem('easyplan_task_assist_mode') as TaskAssistMode) || 'start'
  );
  const [userContext, setUserContext] = useState('');
  const [selectedOptionId, setSelectedOptionId] = useState<string | null>(null);
  const [isApplying, setIsApplying] = useState(false);
  const [applyError, setApplyError] = useState<string | null>(null);
  const [showAllLogs, setShowAllLogs] = useState(false);
  const [isCancelling, setIsCancelling] = useState(false);

  // Find target task info
  const targetTask = boardTasks?.find(t => t.id === taskAssistActiveTaskId);

  // Sync mode reset when task changes
  useEffect(() => {
    if (isTaskAssistPanelOpen) {
      if (useAppStore.getState().taskAssistStatus === null) {
        const savedMode = localStorage.getItem('easyplan_task_assist_mode') as TaskAssistMode | null;
        setMode(savedMode || 'start');
      }
      setUserContext('');
      setSelectedOptionId(null);
      setApplyError(null);
      setIsApplying(false);
    }
  }, [taskAssistActiveTaskId, isTaskAssistPanelOpen]);

  // Sync selectedOptionId default from recommended option if unstick proposal loads
  useEffect(() => {
    if (taskAssistProposal) {
      setMode(taskAssistProposal.proposal_type);
      if (taskAssistProposal.proposal_type === 'unstick') {
        setSelectedOptionId(taskAssistProposal.recommended_option_id);
      } else {
        setSelectedOptionId(null);
      }
    }
  }, [taskAssistProposal]);

  if (!isTaskAssistPanelOpen || !taskAssistActiveTaskId) return null;

  const handleStart = async () => {
    setApplyError(null);
    try {
      await startTaskAssist(taskAssistActiveTaskId, mode, userContext);
    } catch (err) {
      // Handled in store
    }
  };

  const handleCancel = async () => {
    if (isCancelling) return;
    if (!taskAssistActiveTaskId || !taskAssistActiveRequestId) return;

    setIsCancelling(true);
    setApplyError(null);
    try {
      await cancelTaskAssist(taskAssistActiveTaskId, taskAssistActiveRequestId);
      resetTaskAssist();
      setTaskAssistPanelOpen(false);
    } catch (err) {
      setApplyError((err as Error).message || '取消生成失败，请重试');
    } finally {
      setIsCancelling(false);
    }
  };

  const handleApply = async () => {
    if (!taskAssistActiveRequestId) return;
    if (mode === 'unstick' && !selectedOptionId) {
      setApplyError('请先选择一个行动方案');
      return;
    }

    setIsApplying(true);
    setApplyError(null);
    try {
      await applyTaskAssist(taskAssistActiveTaskId, taskAssistActiveRequestId, selectedOptionId);
      // Success auto-closes or shows success view
    } catch (err) {
      setApplyError((err as Error).message || '同步方案失败，请重试');
    } finally {
      setIsApplying(false);
    }
  };

  const handleClose = async () => {
    if (isCancelling) return;
    if (taskAssistStatus === 'running') {
      if (taskAssistActiveTaskId && taskAssistActiveRequestId) {
        setIsCancelling(true);
        setApplyError(null);
        try {
          await cancelTaskAssist(taskAssistActiveTaskId, taskAssistActiveRequestId);
        } catch (err) {
          setApplyError('取消服务失败，请重试');
          setIsCancelling(false);
          return;
        }
        setIsCancelling(false);
      }
    }
    resetTaskAssist();
    setTaskAssistPanelOpen(false);
  };

  const isIdle = taskAssistStatus === null;
  const isRunning = taskAssistStatus === 'running';
  const isReady = taskAssistStatus === 'ready';
  const isApplied = taskAssistStatus === 'applied';
  const isFailed = taskAssistStatus === 'failed';

  const getApplyButtonCopy = () => {
    if (isApplying) return '正在应用中...';
    switch (mode) {
      case 'start':
        return '保存为开始提示';
      case 'unstick':
        return '使用这个降级动作';
      case 'decompose':
        return '确认拆分任务';
      default:
        return '确认应用';
    }
  };

  return (
    <>
      {/* Drawer Overlay for Mobile backdrop */}
      <div
        className={clsx(
          "fixed inset-0 bg-background/40 backdrop-blur-sm z-40 transition-opacity md:hidden",
          isTaskAssistPanelOpen ? "opacity-100" : "opacity-0 pointer-events-none"
        )}
        onClick={handleClose}
      />

      {/* Drawer Main Body */}
      <div
        className={clsx(
          "fixed inset-y-0 right-0 z-50 w-full max-w-md bg-background border-l border-muted/30 shadow-2xl flex flex-col transition-transform duration-300 ease-out transform",
          isTaskAssistPanelOpen ? "translate-x-0" : "translate-x-full"
        )}
      >
        {/* Header Section */}
        <div className="p-5 border-b border-muted/20 flex items-start justify-between gap-4 shrink-0 bg-foreground/[0.02]">
          <div className="space-y-1">
            <div className="flex items-center gap-1.5 text-blue-500 font-semibold text-sm">
              <Sparkles size={16} />
              <span>Action Coach / 行动教练</span>
            </div>
            <h3 className="text-base font-semibold text-foreground/90 line-clamp-2 pr-2" title={targetTask?.title}>
              目标任务：{targetTask?.title || '未知任务'}
            </h3>
          </div>
          <button
            onClick={handleClose}
            className="p-1.5 text-muted-foreground hover:text-foreground rounded-lg hover:bg-muted/15 transition-colors shrink-0"
          >
            <X size={18} />
          </button>
        </div>

        {/* Content Section */}
        <div className="flex-1 overflow-y-auto p-5 space-y-6">
          {/* 1. Mode selector and user input (Visible in Idle / Failed / Stale state) */}
          {(isIdle || isFailed) && (
            <div className="space-y-5 animate-fade-in">
              {/* Segmented control */}
              <div className="space-y-2">
                <span className="text-[10px] font-semibold text-muted-foreground/50 tracking-wider uppercase block">
                  选择辅助模式 / Select Assist Mode
                </span>
                <div className="flex p-0.5 rounded-lg bg-foreground/5 border border-muted/30">
                  {(['start', 'unstick', 'decompose'] as TaskAssistMode[]).map((m) => (
                    <button
                      key={m}
                      onClick={() => setMode(m)}
                      className={clsx(
                        "flex-1 py-1.5 text-xs font-medium rounded-md transition-all",
                        mode === m
                          ? "bg-background text-foreground shadow-sm font-semibold"
                          : "text-muted-foreground hover:text-foreground/80"
                      )}
                    >
                      {getTaskAssistModeLabel(m)}
                    </button>
                  ))}
                </div>
              </div>

              {/* User Context Input */}
              <div className="space-y-2">
                <span className="text-[10px] font-semibold text-muted-foreground/50 tracking-wider uppercase block">
                  补充信息（可选）/ Context Input
                </span>
                <textarea
                  value={userContext}
                  onChange={(e) => setUserContext(e.target.value)}
                  placeholder={getTaskAssistPlaceholder(mode)}
                  maxLength={1000}
                  rows={4}
                  className="w-full text-xs bg-foreground/[0.02] border border-muted/50 focus:border-muted focus:ring-1 focus:ring-foreground/20 rounded-xl px-3 py-2.5 resize-none placeholder:text-muted-foreground/30 focus:outline-none"
                />
                <div className="text-right text-[10px] text-muted-foreground/40 pr-1">
                  {userContext.length}/1000 字
                </div>
              </div>

              {/* Start Button */}
              <button
                onClick={handleStart}
                className="w-full py-2.5 rounded-xl bg-blue-500 hover:bg-blue-600 text-white font-semibold text-xs tracking-wider flex items-center justify-center gap-1.5 shadow-sm transition-all"
              >
                <Sparkles size={14} />
                <span>召唤教练辅助方案</span>
              </button>
            </div>
          )}

          {/* 2. Generating / Loading SSE Stream log box */}
          {isRunning && (
            <div className="space-y-5 py-8 animate-fade-in text-center">
              <Loader2 size={32} className="text-blue-500 animate-spin mx-auto" />
              
              <div className="space-y-1">
                <h5 className="text-sm font-medium text-foreground/80">
                  教练正在深度构思中...
                </h5>
                <p className="text-xs text-muted-foreground/60">
                  通过 DeepSeek 构造结构化建议方案
                </p>
              </div>

              {/* Terminal Logs */}
              {taskAssistLogs.length > 0 && (
                <div className="p-3.5 rounded-xl bg-foreground/[0.03] border border-muted/40 text-left font-mono text-[10px] text-muted-foreground/80 space-y-1.5 max-w-sm mx-auto">
                  <div className="flex items-center justify-between border-b border-muted/20 pb-1.5 mb-1.5 shrink-0">
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
                      {taskAssistLogs.map((log, idx) => (
                        <div key={idx} className="leading-relaxed border-l-2 border-blue-500/30 pl-2">
                          {log}
                        </div>
                      ))}
                    </div>
                  ) : (
                    <div className="leading-relaxed border-l-2 border-blue-500 pl-2 animate-pulse text-foreground/70">
                      {taskAssistLogs[taskAssistLogs.length - 1]}
                    </div>
                  )}
                </div>
              )}

              {/* Error boundary on Cancel */}
              {applyError && (
                <div className="p-3 rounded-lg bg-red-500/10 border border-red-500/20 text-xs text-red-500 font-light flex gap-2 max-w-sm mx-auto text-left">
                  <AlertOctagon size={14} className="shrink-0 mt-0.5" />
                  <p>{applyError}</p>
                </div>
              )}

              {/* Cancel Button */}
              <button
                disabled={isCancelling}
                onClick={handleCancel}
                className={clsx(
                  "px-4 py-2 border border-muted/60 text-muted-foreground hover:text-foreground hover:bg-muted/10 rounded-xl text-xs transition-all pointer-events-auto flex items-center gap-1.5 mx-auto",
                  isCancelling && "opacity-50 cursor-wait"
                )}
              >
                {isCancelling && <Loader2 size={12} className="animate-spin" />}
                <span>{isCancelling ? '正在取消...' : '取消生成'}</span>
              </button>
            </div>
          )}

          {/* 3. Ready Preview and Apply Proposal Card */}
          {isReady && taskAssistProposal && (
            <div className="space-y-6 animate-fade-in">
              <TaskAssistProposal
                proposal={taskAssistProposal}
                selectedOptionId={selectedOptionId}
                onSelectOption={setSelectedOptionId}
              />

              {/* Error boundary on Apply */}
              {applyError && (
                <div className="p-3 rounded-lg bg-red-500/10 border border-red-500/20 text-xs text-red-500 font-light flex gap-2">
                  <AlertOctagon size={14} className="shrink-0 mt-0.5" />
                  <p>{applyError}</p>
                </div>
              )}

              <div className="flex gap-3 pt-2 border-t border-muted/20 shrink-0">
                <button
                  onClick={handleClose}
                  className="flex-1 py-2.5 border border-muted/60 hover:bg-muted/10 text-muted-foreground hover:text-foreground font-semibold rounded-xl text-xs transition-colors"
                >
                  放弃建议
                </button>
                <button
                  disabled={isApplying || (mode === 'unstick' && !selectedOptionId)}
                  onClick={handleApply}
                  className={clsx(
                    "flex-1 py-2.5 rounded-xl font-semibold text-xs transition-all flex items-center justify-center gap-1.5 shadow-sm text-white",
                    isApplying || (mode === 'unstick' && !selectedOptionId)
                      ? "bg-blue-500/40 cursor-wait"
                      : "bg-blue-500 hover:bg-blue-600"
                  )}
                >
                  {isApplying && <Loader2 size={12} className="animate-spin" />}
                  <span>{getApplyButtonCopy()}</span>
                </button>
              </div>
            </div>
          )}

          {/* 4. Applied Done View */}
          {isApplied && (
            <div className="space-y-6 py-12 animate-fade-in text-center">
              <div className="w-16 h-16 rounded-full bg-green-500/10 border border-green-500/20 text-green-500 flex items-center justify-center mx-auto shadow-sm">
                <CheckCircle size={32} />
              </div>

              <div className="space-y-1.5 max-w-xs mx-auto">
                <h4 className="text-base font-semibold text-foreground/90">
                  方案已成功应用！
                </h4>
                <p className="text-xs text-muted-foreground/60 font-light leading-relaxed">
                  {mode === 'start' && '开始提示步骤已附加至任务。您可以按照提示立即开始。'}
                  {mode === 'unstick' && '备份行动方案已附加至任务。做不动时可以采取该退避动作。'}
                  {mode === 'decompose' && '任务已成功拆分为独立子任务，并已绑定进度自动归纳机制。'}
                </p>
              </div>

              <button
                onClick={handleClose}
                className="w-full max-w-xs py-2.5 rounded-xl bg-muted hover:bg-muted/80 text-foreground font-semibold text-xs tracking-wider transition-colors shadow-sm"
              >
                关闭面板
              </button>
            </div>
          )}

          {/* 5. Failed Error View */}
          {isFailed && (
            <div className="space-y-6 py-8 animate-fade-in text-center">
              <div className="w-16 h-16 rounded-full bg-red-500/10 border border-red-500/20 text-red-500 flex items-center justify-center mx-auto shadow-sm">
                <AlertOctagon size={32} />
              </div>

              <div className="space-y-1.5 max-w-xs mx-auto">
                <h4 className="text-base font-semibold text-foreground/90">
                  教练方案生成失败
                </h4>
                <p className="text-xs text-red-500 font-light leading-relaxed break-words">
                  {taskAssistErrorCode === 'TASK_ASSIST_CONTEXT_STALE' 
                    ? '任务已变化，请重新生成建议。' 
                    : (taskAssistErrorMessage || '未知生成错误，请稍后重试')}
                </p>
              </div>

              <div className="flex gap-3 max-w-xs mx-auto">
                <button
                  onClick={handleClose}
                  className="flex-1 py-2 border border-muted/60 text-muted-foreground hover:text-foreground hover:bg-muted/10 rounded-xl text-xs transition-colors"
                >
                  关闭
                </button>
                <button
                  onClick={handleStart}
                  className="flex-1 py-2 bg-blue-500 hover:bg-blue-600 text-white rounded-xl text-xs font-semibold transition-colors"
                >
                  {taskAssistErrorCode === 'TASK_ASSIST_CONTEXT_STALE' ? '重新生成建议' : '重试本次辅助'}
                </button>
              </div>
            </div>
          )}
        </div>
      </div>
    </>
  );
};
