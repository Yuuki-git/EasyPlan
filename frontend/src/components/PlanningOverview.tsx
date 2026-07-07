import React from 'react';
import { useAppStore } from '../store/useAppStore';
import { selectPlanningView } from '../store/planningState';
import { selectLongTermExecutionView } from '../store/longTermExecution';
import { PracticeLoopPanel } from './PracticeLoopPanel';
import { PhaseReviewPanel } from './PhaseReviewPanel';
import { PhaseRecords } from './PhaseRecords';
import { CheckCircle2, Circle, Lock, Unlock, ArrowRight } from 'lucide-react';
import { motion } from 'framer-motion';
import { RoadmapPhase, TaskNode, ThreadSnapshot } from '../types/api';

export const PlanningOverview: React.FC = () => {
  const {
    committedTaskTree,
    previewTaskTree,
    boardTasks,
    selectedProjectId,
    generateNextPhasePlan,
    isPhaseRequestPending,
    intent,
    previewMode,
    appState,
    isRunStalled,
    setRunStalled,
    cancelPlanPreview,
    confirmPlan,
    reasoningLogs,
    returnToCommittedPlan,
    isCancelPending,
    collapsePlanningPanel,
    reconnectActiveRun,
    longTermExecution,
    practiceError,
    isPracticeRequestPending,
    schedulePracticeToday,
    savePhaseReview,
    decidePhaseReview
  } = useAppStore();

  if (import.meta.env.VITE_PHASE_PLANNING_ENABLED === 'false') {
    return null;
  }

  if (!committedTaskTree || !boardTasks || !selectedProjectId) {
    return null;
  }

  const planningView = selectPlanningView(committedTaskTree, boardTasks, selectedProjectId, longTermExecution);
  if (!planningView) {
    return null;
  }

  const threadSnapshot: ThreadSnapshot = {
    thread_id: selectedProjectId,
    status: 'succeeded',
    state_version: 1,
    last_event_id: null,
    server_time: new Date().toISOString(),
    intent_text: intent,
    task_tree: committedTaskTree,
    long_term_execution: longTermExecution
  };
  const longTermView = selectLongTermExecutionView(threadSnapshot);

  const { nextAction, canUnlock, totalAiActions, completedAiActions, context, currentTasks } = planningView;
  const roadmap = context.roadmap;
  const currentPhase = roadmap.find((p: RoadmapPhase) => p.phase_id === context.current_phase?.phase_id);
  const currentPhaseIndex = roadmap.findIndex((p: RoadmapPhase) => p.phase_id === currentPhase?.phase_id);
  const nextPhaseNumber = currentPhaseIndex >= 0 ? currentPhaseIndex + 2 : roadmap.length + 1;

  return (
    <div className="w-full max-w-4xl mx-auto mb-8 bg-background/50 border border-muted/50 rounded-xl p-6 backdrop-blur-sm">
      <div className="flex flex-col gap-6">

        {/* Target & Progress */}
        <div className="flex justify-between items-start gap-4">
          <div className="flex-1">
            <h2 className="text-sm font-medium text-muted-foreground uppercase tracking-wider mb-1">Target</h2>
            <p className="text-lg text-foreground font-medium">{intent}</p>
          </div>
          <div className="flex flex-col items-end gap-2">
            <span className="text-sm text-muted-foreground">
              {completedAiActions} / {totalAiActions} Tasks
            </span>
            <div className="w-32 h-1.5 bg-muted rounded-full overflow-hidden">
              <motion.div
                className="h-full bg-foreground/70"
                initial={{ width: 0 }}
                animate={{ width: `${totalAiActions > 0 ? (completedAiActions / totalAiActions) * 100 : 0}%` }}
                transition={{ duration: 0.5, ease: "easeOut" }}
              />
            </div>
          </div>
        </div>

        {/* Roadmap Timeline */}
        <div className="py-4 border-y border-muted/30">
          <h3 className="text-xs font-semibold text-muted-foreground uppercase tracking-widest mb-4">Roadmap</h3>
          <div className="flex items-center gap-2 overflow-x-auto pb-2 scrollbar-hide">
            {roadmap.map((phase: RoadmapPhase, idx: number) => {
              const isCompleted = phase.status === 'completed';
              const isCurrent = phase.phase_id === currentPhase?.phase_id;

              return (
                <React.Fragment key={phase.phase_id}>
                  <div className={`flex items-center gap-2 px-3 py-1.5 rounded-full border ${isCurrent ? 'bg-foreground/5 border-foreground/20' : 'border-transparent'}`}>
                    {isCompleted ? (
                      <CheckCircle2 size={14} className="text-foreground/50" />
                    ) : isCurrent ? (
                      <Circle size={14} className="text-foreground" />
                    ) : (
                      <Circle size={14} className="text-muted-foreground/30" />
                    )}
                    <span className={`text-sm whitespace-nowrap ${isCurrent ? 'text-foreground font-medium' : isCompleted ? 'text-foreground/60' : 'text-muted-foreground'}`}>
                      Phase {idx + 1}: {phase.title}
                    </span>
                  </div>
                  {idx < roadmap.length - 1 && (
                    <ArrowRight size={14} className="text-muted-foreground/30 shrink-0" />
                  )}
                </React.Fragment>
              );
            })}
          </div>
        </div>

        {/* Current Phase & Next Action */}
        {previewMode === 'next_phase' ? (
          <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
            <div className="flex flex-col gap-3">
              <h3 className="text-xs font-semibold text-muted-foreground uppercase tracking-widest">
                Next Phase (Inline Preview)
              </h3>
              {isRunStalled ? (
                <div className="p-4 rounded-lg border border-amber-500/30 bg-amber-500/5 h-full flex flex-col justify-center items-center text-center">
                  <div className="text-amber-500 font-medium mb-1 animate-pulse">生成响应较慢，可能已卡住</div>
                  <p className="text-xs text-muted-foreground mb-4">您可以选择继续等待，或者尝试重新连接。</p>
                  <div className="flex flex-wrap items-center justify-center gap-2">
                    <button
                      onClick={() => setRunStalled(false)}
                      className="px-3 py-1.5 bg-foreground text-background hover:bg-foreground/90 rounded-lg text-xs font-medium transition-colors"
                    >
                      继续等待
                    </button>
                    <button
                      onClick={() => reconnectActiveRun()}
                      className="px-3 py-1.5 bg-secondary text-secondary-foreground hover:opacity-90 rounded-lg text-xs font-medium transition-colors"
                    >
                      重新连接
                    </button>
                    <button
                      onClick={() => returnToCommittedPlan()}
                      className="px-3 py-1.5 border border-muted hover:border-foreground/30 text-xs text-muted-foreground hover:text-foreground rounded-lg transition-colors"
                    >
                      返回当前计划
                    </button>
                  </div>
                </div>
              ) : appState === 'THINKING' ? (
                <div className="p-4 rounded-lg bg-foreground/5 border border-foreground/10 h-full flex flex-col justify-center items-center text-center min-h-[140px]">
                  <div className="flex items-center gap-2 text-foreground font-medium mb-2">
                    <svg className="animate-spin h-4 w-4 text-foreground" fill="none" viewBox="0 0 24 24">
                      <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                      <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z" />
                    </svg>
                    <span>正在规划下一阶段...</span>
                  </div>
                  {reasoningLogs.length > 0 && (
                    <p className="text-xs text-muted-foreground line-clamp-2 mt-1 animate-pulse px-4 max-w-[300px]">
                      AI: {reasoningLogs[reasoningLogs.length - 1]}
                    </p>
                  )}
                  <button
                    onClick={() => cancelPlanPreview()}
                    disabled={isCancelPending}
                    className="mt-4 px-3 py-1.5 border border-muted hover:border-foreground/30 text-xs text-muted-foreground hover:text-foreground rounded-full transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
                  >
                    {isCancelPending ? '正在取消...' : '放弃等待'}
                  </button>
                </div>
              ) : appState === 'SYNCING' ? (
                <div className="p-4 rounded-lg bg-foreground/5 border border-foreground/10 h-full flex flex-col justify-center items-center text-center min-h-[140px]">
                  <div className="flex items-center gap-2 text-foreground font-medium mb-2">
                    <svg className="animate-spin h-4 w-4 text-foreground" fill="none" viewBox="0 0 24 24">
                      <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                      <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z" />
                    </svg>
                    <span>正在将新阶段同步至看板...</span>
                  </div>
                  <button
                    onClick={() => collapsePlanningPanel()}
                    className="mt-4 px-3 py-1.5 border border-muted hover:border-foreground/30 text-xs text-muted-foreground hover:text-foreground rounded-full transition-colors"
                  >
                    返回当前计划
                  </button>
                </div>
              ) : appState === 'PENDING' ? (
                <div className="p-4 rounded-lg bg-amber-500/5 border border-amber-500/20 h-full flex flex-col justify-between min-h-[160px]">
                  <div>
                    <div className="flex items-center gap-2 mb-2">
                      <span className="text-[10px] font-semibold px-2 py-0.5 bg-amber-500/20 text-amber-600 dark:text-amber-400 rounded-full uppercase tracking-wider">下一阶段预览</span>
                      <h4 className="font-semibold text-foreground text-sm line-clamp-1">{previewTaskTree?.planning_context?.current_phase?.title || '新阶段'}</h4>
                    </div>
                    <p className="text-xs text-muted-foreground line-clamp-2 mb-3">{previewTaskTree?.planning_context?.current_phase?.objective || '阶段规划已生成'}</p>
                    {previewTaskTree?.root && (
                      <div className="mb-1">
                        <h5 className="text-[10px] font-semibold text-muted-foreground uppercase tracking-wider mb-1.5">新增任务列表：</h5>
                        <ul className="text-[11px] text-foreground/80 space-y-0.5 max-h-24 overflow-y-auto pl-4 list-disc custom-scrollbar">
                          {(() => {
                            const collectTaskNodes = (node: TaskNode): TaskNode[] => {
                              const res: TaskNode[] = [];
                              if (node.node_type === 'action') res.push(node);
                              if (node.children) {
                                for (const c of node.children) res.push(...collectTaskNodes(c));
                              }
                              return res;
                            };
                            return collectTaskNodes(previewTaskTree.root).map((node, i) => (
                              <li key={node.client_node_id || i} className="line-clamp-1">{node.title}</li>
                            ));
                          })()}
                        </ul>
                      </div>
                    )}
                  </div>
                  <div className="flex items-center gap-2 mt-4">
                    <button
                      onClick={() => confirmPlan()}
                      className="flex-1 px-3 py-1.5 bg-foreground text-background hover:bg-foreground/90 rounded-lg text-xs font-semibold transition-colors shadow-sm"
                    >
                      追加到当前计划
                    </button>
                    <button
                      onClick={() => cancelPlanPreview()}
                      disabled={isCancelPending}
                      className="px-3 py-1.5 border border-muted hover:border-foreground/30 text-xs text-muted-foreground hover:text-foreground rounded-lg transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
                    >
                      {isCancelPending ? '正在取消...' : '放弃此计划'}
                    </button>
                  </div>
                </div>
              ) : (
                <div className="p-4 rounded-lg bg-muted/20 border border-muted/50 h-full flex items-center justify-center text-sm text-muted-foreground">
                  等待加载中...
                </div>
              )}
            </div>

            <div className="flex flex-col gap-3">
              <h3 className="text-xs font-semibold text-muted-foreground uppercase tracking-widest">Next Action</h3>
              {nextAction ? (
                <div className="p-4 rounded-lg bg-foreground/5 border border-foreground/10 h-full flex flex-col justify-center">
                  <div className="flex items-start gap-3">
                    <div className="mt-0.5">
                      <Circle size={16} className="text-foreground/60" />
                    </div>
                    <div>
                      <h4 className="font-medium text-foreground text-sm leading-tight mb-1">{nextAction.title}</h4>
                      {nextAction.description && (
                        <p className="text-xs text-muted-foreground line-clamp-1">{nextAction.description}</p>
                      )}
                    </div>
                  </div>
                </div>
              ) : (
                <div className="p-4 rounded-lg bg-muted/20 border border-muted/50 h-full flex items-center justify-center text-sm text-muted-foreground">
                  No immediate action
                </div>
              )}
            </div>
          </div>
        ) : longTermView ? (
          // Schema V2 Layout
          <div className="flex flex-col gap-6 py-4 border-t border-muted/20 mt-4">
            {/* 本阶段任务 */}
            <div className="flex flex-col gap-2">
              <h3 className="text-xs font-semibold text-muted-foreground uppercase tracking-wider">本阶段任务</h3>
              <div className="flex flex-col gap-2 p-4 rounded-xl border border-border/40 bg-card">
                {currentTasks.filter(t => t.node_type === 'action' && t.source !== 'practice_loop').length > 0 ? (
                  <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
                    {currentTasks.filter(t => t.node_type === 'action' && t.source !== 'practice_loop').map(task => (
                      <div key={task.id} className="flex items-center gap-2 text-xs">
                        {task.status === 'completed' ? (
                          <span className="text-emerald-500"><CheckCircle2 size={14} /></span>
                        ) : (
                          <span className="text-muted-foreground"><Circle size={14} /></span>
                        )}
                        <span className={task.status === 'completed' ? 'line-through text-muted-foreground' : 'text-foreground/80'}>{task.title}</span>
                      </div>
                    ))}
                  </div>
                ) : (
                  <span className="text-xs text-muted-foreground">本阶段无其他单次待办任务。</span>
                )}
              </div>
            </div>

            {/* 循环练习 */}
            <PracticeLoopPanel
              loops={longTermView.loops}
              onSchedule={schedulePracticeToday}
              isPending={isPracticeRequestPending}
              practiceError={practiceError}
            />

            {/* 阶段验证与复盘 */}
            <PhaseReviewPanel
              phaseId={longTermView.phaseId}
              checkpoints={context.outcome_checkpoints || []}
              activeReview={longTermView.activeReview}
              recommendation={longTermView.recommendation}
              reviewAvailable={longTermView.canReview}
              oneOffReady={longTermView.oneOffReady}
              processReady={longTermView.processReady}
              outcomeReady={longTermView.outcomeReady}
              onSave={(payload) => savePhaseReview(longTermView.phaseId, payload)}
              onDecide={(payload) => decidePhaseReview(longTermView.phaseId, payload)}
              isPending={isPracticeRequestPending}
              practiceError={practiceError}
              loops={longTermView.loops}
            />

            {/* 历史记录 */}
            <PhaseRecords
              reviewHistory={longTermView.reviewHistory}
              roadmap={roadmap}
            />
          </div>
        ) : (
          // Schema V1 Layout (Roadmap detail & Next Action)
          <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
            <div className="flex flex-col gap-3">
              <h3 className="text-xs font-semibold text-muted-foreground uppercase tracking-widest">
                Current Phase
              </h3>
              {currentPhase ? (
                <div className="p-4 rounded-lg bg-foreground/5 border border-foreground/10 h-full flex flex-col justify-center">
                  <h4 className="font-medium text-foreground mb-2">{currentPhase.title}</h4>
                  <p className="text-sm text-muted-foreground line-clamp-2">{currentPhase.objective}</p>
                </div>
              ) : (
                <div className="p-4 rounded-lg bg-muted/20 border border-muted/50 h-full flex items-center justify-center text-sm text-muted-foreground">
                  All phases completed
                </div>
              )}
            </div>

            <div className="flex flex-col gap-3">
              <h3 className="text-xs font-semibold text-muted-foreground uppercase tracking-widest">Next Action</h3>
              {nextAction ? (
                <div className="p-4 rounded-lg bg-foreground/5 border border-foreground/10 h-full flex flex-col justify-center">
                  <div className="flex items-start gap-3">
                    <div className="mt-0.5">
                      <Circle size={16} className="text-foreground/60" />
                    </div>
                    <div>
                      <h4 className="font-medium text-foreground text-sm leading-tight mb-1">{nextAction.title}</h4>
                      {nextAction.description && (
                        <p className="text-xs text-muted-foreground line-clamp-1">{nextAction.description}</p>
                      )}
                    </div>
                  </div>
                </div>
              ) : (
                <div className="p-4 rounded-lg bg-muted/20 border border-muted/50 h-full flex items-center justify-center text-sm text-muted-foreground">
                  No immediate action
                </div>
              )}
            </div>
          </div>
        )}
        {/* Unlock Button */}
        {previewMode !== 'next_phase' && (
          <div className="flex justify-end mt-2">
            <button
              onClick={generateNextPhasePlan}
              disabled={!canUnlock || isPhaseRequestPending}
              className={`flex items-center gap-2 px-4 py-2 rounded-lg text-sm font-medium transition-all ${
                canUnlock && !isPhaseRequestPending
                  ? 'bg-foreground text-background hover:bg-foreground/90 shadow-sm cursor-pointer'
                  : 'bg-muted text-muted-foreground cursor-not-allowed opacity-50'
              }`}
            >
              {canUnlock && !isPhaseRequestPending ? <Unlock size={16} /> : <Lock size={16} />}
              {isPhaseRequestPending ? 'Generating...' : `Unlock Phase ${nextPhaseNumber}`}
            </button>
          </div>
        )}

      </div>
    </div>
  );
};
