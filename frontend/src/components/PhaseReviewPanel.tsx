import React, { useState, useEffect } from 'react';
import { CheckCircle2, AlertCircle, Save, Check } from 'lucide-react';
import { OutcomeCheckpoint, PhaseReview, PhaseReviewUpdateRequest, PhaseReviewDecisionRequest } from '../types/api';

interface PhaseReviewPanelProps {
  phaseId: string;
  checkpoints: OutcomeCheckpoint[];
  activeReview: PhaseReview | null;
  recommendation: 'ready' | 'partial' | 'not_ready' | 'overridden';
  reviewAvailable: boolean;
  oneOffReady: boolean;
  processReady: boolean;
  outcomeReady: boolean;
  onSave: (payload: PhaseReviewUpdateRequest) => Promise<void>;
  onDecide: (payload: PhaseReviewDecisionRequest) => Promise<void>;
  isPending: boolean;
  practiceError: string | null;
  loops?: Array<{ loopId: string; title: string; targetPerWeek: number; doneCriteria: string }>;
}

export const PhaseReviewPanel: React.FC<PhaseReviewPanelProps> = ({
  phaseId,
  checkpoints,
  activeReview,
  recommendation,
  reviewAvailable,
  oneOffReady,
  processReady,
  outcomeReady,
  onSave,
  onDecide,
  isPending,
  practiceError,
  loops = []
}) => {
  // Local state for draft fields
  const [evidence, setEvidence] = useState<Record<string, Record<string, unknown>>>({});
  const [difficulty, setDifficulty] = useState<string>('');
  const [nextCapacity, setNextCapacity] = useState<string>('');
  const [earlyReviewRequested, setEarlyReviewRequested] = useState<boolean>(false);

  // Local state for decision
  const [decision, setDecision] = useState<'proceed' | 'extend' | 'adjust' | 'override'>('proceed');
  const [overrideReason, setOverrideReason] = useState<string>('');
  const [extensionWeeks, setExtensionWeeks] = useState<number>(1);
  const [adjustments, setAdjustments] = useState<Record<string, { target_per_week: number; title: string; done_criteria: string }>>({});

  // Sync draft fields when activeReview changes
  useEffect(() => {
    if (activeReview) {
      setEvidence(activeReview.evidence || {});
      setDifficulty(activeReview.difficulty || '');
      setNextCapacity(activeReview.next_capacity || '');
    } else {
      setEvidence({});
      setDifficulty('');
      setNextCapacity('');
    }
  }, [activeReview]);

  // Sync loops into adjustments state
  useEffect(() => {
    if (loops.length > 0) {
      const initialAdj: typeof adjustments = {};
      loops.forEach((loop) => {
        initialAdj[loop.loopId] = {
          target_per_week: loop.targetPerWeek,
          title: loop.title,
          done_criteria: loop.doneCriteria
        };
      });
      setAdjustments(initialAdj);
    }
  }, [loops]);

  const handleCheckpointChange = (checkpointId: string, value: unknown) => {
    setEvidence((prev) => ({
      ...prev,
      [checkpointId]: { value, url: typeof value === 'string' ? value : undefined }
    }));
  };

  const handleSaveDraft = async (e: React.FormEvent) => {
    e.preventDefault();
    await onSave({
      evidence,
      difficulty: difficulty || null,
      next_capacity: nextCapacity || null,
      early_review_requested: earlyReviewRequested
    });
  };

  const handleSubmitDecision = async (e: React.FormEvent) => {
    e.preventDefault();
    const payload: PhaseReviewDecisionRequest = { decision };

    if (decision === 'override') {
      if (!overrideReason.trim()) return;
      payload.override_reason = overrideReason;
    } else if (decision === 'extend') {
      payload.extension_weeks = extensionWeeks;
    } else if (decision === 'adjust') {
      payload.adjustments = Object.entries(adjustments).map(([loopId, adj]) => ({
        loop_id: loopId,
        title: adj.title,
        target_per_week: adj.target_per_week,
        done_criteria: adj.done_criteria
      }));
    }

    await onDecide(payload);
  };

  if (!reviewAvailable && !activeReview) {
    return (
      <div className="w-full p-4 rounded-xl border border-border/50 bg-muted/20 select-none">
        <div className="flex items-center gap-2.5 text-muted-foreground text-xs">
          <AlertCircle size={15} />
          <span>当前阶段条件尚未达成，且时间段尚未截止。请继续完成单次待办和循环练习。</span>
        </div>
      </div>
    );
  }

  const isRecommendProceed = recommendation === 'ready' || recommendation === 'overridden';
  const showOverrideWarning = decision === 'override' && !overrideReason.trim();

  return (
    <div
      className="w-full flex flex-col gap-6 mt-4 select-none"
      data-phase-id={phaseId}
    >
      <div className="flex flex-col gap-1.5 border-b border-border/40 pb-2">
        <h3 className="text-sm font-semibold tracking-wide text-foreground/80 uppercase">
          阶段验证与复盘
        </h3>
        <p className="text-xs text-muted-foreground">
          填写当前阶段的达成指标证据，并在完成阶段总结后提交进入下一阶段或调整计划的决策。
        </p>
      </div>

      {practiceError && (
        <div className="text-xs py-2 px-3 rounded-lg border border-red-500/20 bg-red-500/5 text-red-500 animate-pulse">
          {practiceError}
        </div>
      )}

      {/* System Facts - Read-only */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4 p-4 rounded-xl border border-border/40 bg-accent/5">
        <div className="flex flex-col gap-1">
          <span className="text-[10px] text-muted-foreground">单次待办状态</span>
          <div className="flex items-center gap-1.5 text-xs font-medium">
            {oneOffReady ? (
              <span className="text-emerald-500 flex items-center gap-1"><CheckCircle2 size={13} /> 已就绪</span>
            ) : (
              <span className="text-amber-500 flex items-center gap-1"><AlertCircle size={13} /> 未完成</span>
            )}
          </div>
        </div>

        <div className="flex flex-col gap-1">
          <span className="text-[10px] text-muted-foreground">循环练习达成率</span>
          <div className="flex items-center gap-1.5 text-xs font-medium">
            {processReady ? (
              <span className="text-emerald-500 flex items-center gap-1"><CheckCircle2 size={13} /> 已达标 (≥80%)</span>
            ) : (
              <span className="text-amber-500 flex items-center gap-1"><AlertCircle size={13} /> 未达标 (&lt;80%)</span>
            )}
          </div>
        </div>

        <div className="flex flex-col gap-1">
          <span className="text-[10px] text-muted-foreground">指标验证状态</span>
          <div className="flex items-center gap-1.5 text-xs font-medium">
            {outcomeReady ? (
              <span className="text-emerald-500 flex items-center gap-1"><CheckCircle2 size={13} /> 指标通过</span>
            ) : (
              <span className="text-amber-500 flex items-center gap-1"><AlertCircle size={13} /> 未通过</span>
            )}
          </div>
        </div>

        <div className="flex flex-col gap-1">
          <span className="text-[10px] text-muted-foreground">系统建议决策</span>
          <div className="flex items-center gap-1.5 text-xs font-semibold">
            {recommendation === 'ready' && <span className="text-emerald-500">建议进入下一阶段</span>}
            {recommendation === 'partial' && <span className="text-amber-500">建议延长或强解锁</span>}
            {recommendation === 'not_ready' && <span className="text-red-500">建议延长周期</span>}
            {recommendation === 'overridden' && <span className="text-purple-500">建议强解锁 (已批准)</span>}
          </div>
        </div>
      </div>

      {/* 1. Evidence Input Form (Drafting) */}
      <form onSubmit={handleSaveDraft} className="flex flex-col gap-4 border border-border/30 rounded-xl p-4 bg-card shadow-sm">
        <h4 className="text-xs font-semibold text-foreground/70">1. 指标达成证据填写</h4>

        {checkpoints.map((cp) => {
          const rawValue = evidence[cp.checkpoint_id]?.value;
          const numericValue =
            typeof rawValue === 'number' || typeof rawValue === 'string'
              ? rawValue
              : '';
          const textValue = typeof rawValue === 'string' ? rawValue : '';
          return (
            <div key={cp.checkpoint_id} className="flex flex-col gap-1.5">
              <label className="text-xs font-medium text-foreground/80 flex items-center gap-2">
                {cp.title}
                <span className="text-[10px] text-muted-foreground">
                  (期望: {cp.operator === 'gte' ? '≥' : cp.operator === 'lte' ? '≤' : '存在'} {cp.target_value} {cp.unit})
                </span>
              </label>

              {/* Numeric Type Input */}
              {cp.evidence_type === 'numeric' && (
                <div className="flex items-center gap-2">
                  <input
                    type="number"
                    value={numericValue}
                    onChange={(e) => handleCheckpointChange(cp.checkpoint_id, e.target.value ? Number(e.target.value) : '')}
                    className="w-32 px-3 py-1.5 rounded-lg border border-border bg-background text-sm text-foreground focus:outline-none focus:ring-1 focus:ring-primary"
                    placeholder="输入数值"
                    disabled={isPending}
                  />
                  <span className="text-xs text-muted-foreground">{cp.unit}</span>
                </div>
              )}

              {/* Artifact Type Input */}
              {cp.evidence_type === 'artifact' && (
                <input
                  type="text"
                  value={textValue}
                  onChange={(e) => handleCheckpointChange(cp.checkpoint_id, e.target.value)}
                  className="w-full px-3 py-1.5 rounded-lg border border-border bg-background text-sm text-foreground focus:outline-none focus:ring-1 focus:ring-primary"
                  placeholder="提供报告URL或成果描述文档..."
                  disabled={isPending}
                />
              )}

              {/* Self Assessment Type Input */}
              {cp.evidence_type === 'self_assessment' && (
                <div className="flex items-center gap-2">
                  {[1, 2, 3, 4, 5].map((score) => (
                    <button
                      key={score}
                      type="button"
                      onClick={() => handleCheckpointChange(cp.checkpoint_id, score)}
                      disabled={isPending}
                      className={`w-9 h-9 rounded-lg border text-sm font-medium transition-all cursor-pointer ${
                        Number(rawValue) === score
                          ? 'bg-primary text-primary-foreground border-primary font-semibold'
                          : 'border-border bg-background text-muted-foreground hover:text-foreground hover:bg-accent/10'
                      }`}
                    >
                      {score}
                    </button>
                  ))}
                  <span className="text-xs text-muted-foreground ml-2">(自评打分，5分最高)</span>
                </div>
              )}
            </div>
          );
        })}

        <div className="grid grid-cols-1 md:grid-cols-2 gap-4 mt-2">
          <div className="flex flex-col gap-1.5">
            <label className="text-xs font-medium text-foreground/80">本次阶段痛点/难度总结 (选填)</label>
            <textarea
              value={difficulty}
              onChange={(e) => setDifficulty(e.target.value)}
              className="w-full px-3 py-1.5 rounded-lg border border-border bg-background text-xs text-foreground focus:outline-none focus:ring-1 focus:ring-primary min-h-[4rem]"
              placeholder="记录在此阶段遇到的主要障碍和困难..."
              disabled={isPending}
            />
          </div>

          <div className="flex flex-col gap-1.5">
            <label className="text-xs font-medium text-foreground/80">下阶段能力沉淀/调整备忘 (选填)</label>
            <textarea
              value={nextCapacity}
              onChange={(e) => setNextCapacity(e.target.value)}
              className="w-full px-3 py-1.5 rounded-lg border border-border bg-background text-xs text-foreground focus:outline-none focus:ring-1 focus:ring-primary min-h-[4rem]"
              placeholder="总结积累了哪些核心能力，未来阶段有什么建议..."
              disabled={isPending}
            />
          </div>
        </div>

        <div className="flex items-center gap-4 mt-2 pt-2 border-t border-border/20">
          <label className="flex items-center gap-2 cursor-pointer text-xs text-muted-foreground">
            <input
              type="checkbox"
              checked={earlyReviewRequested}
              onChange={(e) => setEarlyReviewRequested(e.target.checked)}
              disabled={isPending}
              className="cursor-pointer rounded border-border"
            />
            <span>申请提前面板复盘 (即便时间周期或指标未完成)</span>
          </label>

          <button
            type="submit"
            disabled={isPending}
            className="flex items-center gap-1.5 px-4 py-2 rounded-lg text-xs font-medium bg-secondary text-secondary-foreground hover:opacity-90 active:scale-95 transition-all ml-auto cursor-pointer"
          >
            <Save size={13} />
            <span>保存复盘草稿</span>
          </button>
        </div>
      </form>

      {/* 2. Decision Formulation Form */}
      {activeReview && (
        <form onSubmit={handleSubmitDecision} className="flex flex-col gap-4 border border-border/30 rounded-xl p-4 bg-card shadow-sm">
          <h4 className="text-xs font-semibold text-foreground/70">2. 复盘决策流转</h4>

          <div className="flex flex-col gap-2">
            <label className="text-xs font-medium text-foreground/80">选择行动决策：</label>
            <div className="grid grid-cols-2 md:grid-cols-4 gap-2">
              <button
                type="button"
                onClick={() => setDecision('proceed')}
                className={`py-2 px-3 border rounded-lg text-xs font-medium transition-all text-center cursor-pointer ${
                  decision === 'proceed'
                    ? 'border-emerald-500/40 bg-emerald-500/5 text-emerald-600 font-semibold'
                    : 'border-border bg-background text-muted-foreground hover:bg-accent/5'
                }`}
              >
                解锁并进入下阶段
              </button>

              <button
                type="button"
                onClick={() => setDecision('extend')}
                className={`py-2 px-3 border rounded-lg text-xs font-medium transition-all text-center cursor-pointer ${
                  decision === 'extend'
                    ? 'border-blue-500/40 bg-blue-500/5 text-blue-600 font-semibold'
                    : 'border-border bg-background text-muted-foreground hover:bg-accent/5'
                }`}
              >
                延长当前阶段
              </button>

              <button
                type="button"
                onClick={() => setDecision('adjust')}
                className={`py-2 px-3 border rounded-lg text-xs font-medium transition-all text-center cursor-pointer ${
                  decision === 'adjust'
                    ? 'border-amber-500/40 bg-amber-500/5 text-amber-600 font-semibold'
                    : 'border-border bg-background text-muted-foreground hover:bg-accent/5'
                }`}
              >
                调整循环练习次数
              </button>

              <button
                type="button"
                onClick={() => setDecision('override')}
                className={`py-2 px-3 border rounded-lg text-xs font-medium transition-all text-center cursor-pointer ${
                  decision === 'override'
                    ? 'border-purple-500/40 bg-purple-500/5 text-purple-600 font-semibold'
                    : 'border-border bg-background text-muted-foreground hover:bg-accent/5'
                }`}
              >
                人工强行解锁
              </button>
            </div>
          </div>

          {/* Conditional Decision Form Blocks */}
          {decision === 'extend' && (
            <div className="flex items-center gap-3 p-3 rounded-lg bg-blue-500/5 border border-blue-500/10 text-xs">
              <span>延长时长：</span>
              <select
                value={extensionWeeks}
                onChange={(e) => setExtensionWeeks(Number(e.target.value))}
                className="px-2 py-1 border border-border rounded bg-background focus:outline-none"
                disabled={isPending}
              >
                {[1, 2, 3, 4].map((w) => (
                  <option key={w} value={w}>
                    {w} 周
                  </option>
                ))}
              </select>
            </div>
          )}

          {decision === 'adjust' && loops.length > 0 && (
            <div className="flex flex-col gap-3 p-3 rounded-lg bg-amber-500/5 border border-amber-500/10 text-xs">
              <span className="font-semibold text-amber-800">调整后续活跃循环的次数：</span>
              {loops.map((loop) => {
                const val = adjustments[loop.loopId]?.target_per_week ?? loop.targetPerWeek;
                return (
                  <div key={loop.loopId} className="flex items-center gap-3 justify-between">
                    <span className="font-medium">{loop.title}</span>
                    <div className="flex items-center gap-2">
                      <label>每周练习：</label>
                      <input
                        type="number"
                        min="1"
                        max="7"
                        value={val}
                        onChange={(e) =>
                          setAdjustments((prev) => ({
                            ...prev,
                            [loop.loopId]: {
                              ...prev[loop.loopId],
                              target_per_week: Number(e.target.value)
                            }
                          }))
                        }
                        className="w-16 px-2 py-1 border border-border rounded bg-background focus:outline-none text-center"
                        disabled={isPending}
                      />
                      <span>次</span>
                    </div>
                  </div>
                );
              })}
            </div>
          )}

          {decision === 'override' && (
            <div className="flex flex-col gap-2 p-3 rounded-lg bg-purple-500/5 border border-purple-500/10 text-xs">
              <label className="font-semibold text-purple-800">人工强行解锁原因 (必填)：</label>
              <textarea
                value={overrideReason}
                onChange={(e) => setOverrideReason(e.target.value)}
                className="w-full px-3 py-1.5 rounded-lg border border-border bg-background text-xs text-foreground focus:outline-none focus:ring-1 focus:ring-purple-500 min-h-[4rem]"
                placeholder="说明强解锁的特殊原因 (如本阶段学习重点发生微调，需由人工批准进入下一阶段)..."
                disabled={isPending}
              />
            </div>
          )}

          {decision === 'proceed' && !isRecommendProceed && (
            <div className="flex items-center gap-2 p-3 rounded-lg bg-amber-500/10 border border-amber-500/20 text-amber-700 text-xs font-medium">
              <AlertCircle size={14} />
              <span>当前阶段指标未达标，直接解锁已被系统拒绝。请选择“延长当前阶段”或选择“人工强行解锁”并提交原因。</span>
            </div>
          )}

          <button
            type="submit"
            disabled={isPending || showOverrideWarning || (decision === 'proceed' && !isRecommendProceed)}
            className="flex items-center gap-1.5 px-4 py-2 rounded-lg text-xs font-semibold bg-primary text-primary-foreground hover:opacity-90 active:scale-95 disabled:pointer-events-none disabled:bg-muted disabled:text-muted-foreground transition-all ml-auto cursor-pointer"
          >
            <Check size={14} />
            <span>提交决策并归档复盘</span>
          </button>
        </form>
      )}
    </div>
  );
};
