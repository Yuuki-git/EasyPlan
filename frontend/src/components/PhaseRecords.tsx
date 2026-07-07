import React, { useState } from 'react';
import { Calendar, ChevronDown, ChevronUp, AlertCircle, FileText } from 'lucide-react';
import { PhaseReview, RoadmapPhase } from '../types/api';

interface PhaseRecordsProps {
  reviewHistory: PhaseReview[];
  roadmap: RoadmapPhase[];
}

export const PhaseRecords: React.FC<PhaseRecordsProps> = ({ reviewHistory, roadmap }) => {
  const [expandedReviewId, setExpandedReviewId] = useState<string | null>(null);

  if (!reviewHistory || reviewHistory.length === 0) return null;

  const toggleExpand = (reviewId: string) => {
    setExpandedReviewId((prev) => (prev === reviewId ? null : reviewId));
  };

  const getPhaseTitle = (phaseId: string) => {
    const phase = roadmap.find((p) => p.phase_id === phaseId);
    return phase ? phase.title : `阶段 ${phaseId}`;
  };

  return (
    <div className="w-full flex flex-col gap-4 mt-6 select-none">
      <div className="flex flex-col gap-1.5 border-b border-border/40 pb-2">
        <h3 className="text-sm font-semibold tracking-wide text-foreground/80 uppercase flex items-center gap-2">
          <FileText size={15} />
          历史阶段复盘记录
        </h3>
        <p className="text-xs text-muted-foreground">
          查看以往所有已归档阶段的复盘成果、统计数据及成长决策。
        </p>
      </div>

      <div className="flex flex-col gap-3">
        {reviewHistory.map((review) => {
          const isExpanded = expandedReviewId === review.id;
          const phaseTitle = getPhaseTitle(review.phase_id);
          const decisionLabels: Record<string, string> = {
            proceed: '解锁并进入下一阶段',
            extend: '延长当前阶段',
            adjust: '调整循环练习次数',
            override: '人工强行解锁'
          };
          const recLabels: Record<string, string> = {
            ready: '达标通过',
            partial: '部分达标',
            not_ready: '未达标',
            overridden: '人工通过'
          };

          return (
            <div
              key={review.id}
              className="border border-border/40 rounded-xl bg-card overflow-hidden shadow-sm transition-all duration-300 hover:border-border/80"
            >
              {/* Header Accordion Button */}
              <button
                onClick={() => toggleExpand(review.id)}
                className="w-full flex items-center justify-between p-4 text-left cursor-pointer hover:bg-accent/5 transition-colors focus:outline-none"
              >
                <div className="flex flex-col md:flex-row md:items-center gap-2 md:gap-4">
                  <span className="text-sm font-semibold text-foreground">
                    {phaseTitle}
                  </span>
                  <div className="flex flex-wrap gap-2 items-center">
                    <span className="text-[10px] px-2 py-0.5 rounded-full bg-emerald-500/10 text-emerald-600 border border-emerald-500/20 font-medium">
                      已归档
                    </span>
                    <span className="text-[10px] px-2 py-0.5 rounded-full bg-primary/10 text-primary border border-primary/20 font-medium">
                      决策: {decisionLabels[review.decision || ''] || review.decision}
                    </span>
                  </div>
                </div>

                <div className="flex items-center gap-3 text-muted-foreground">
                  <div className="hidden sm:flex items-center gap-1.5 text-xs">
                    <Calendar size={13} />
                    <span>归档于 {new Date(review.created_at).toLocaleDateString()}</span>
                  </div>
                  {isExpanded ? <ChevronUp size={16} /> : <ChevronDown size={16} />}
                </div>
              </button>

              {/* Collapsible Content */}
              {isExpanded && (
                <div className="p-4 border-t border-border/30 bg-muted/5 flex flex-col gap-4 text-xs">
                  {/* Stats Adherence */}
                  <div className="grid grid-cols-2 sm:grid-cols-4 gap-4 p-3 rounded-lg bg-accent/5 border border-border/30">
                    <div>
                      <span className="text-[10px] text-muted-foreground block">阶段建议状态</span>
                      <span className="font-semibold text-foreground/80">
                        {recLabels[review.recommendation] || review.recommendation}
                      </span>
                    </div>

                    <div>
                      <span className="text-[10px] text-muted-foreground block">练习周坚持率 (Adherence)</span>
                      <span className="font-semibold text-foreground/80">
                        {review.statistics && typeof review.statistics.adherence === 'number'
                          ? `${(review.statistics.adherence * 100).toFixed(0)}%`
                          : '100%'}
                      </span>
                    </div>

                    <div>
                      <span className="text-[10px] text-muted-foreground block">完成天数 / 周期天数</span>
                      <span className="font-semibold text-foreground/80">
                        {review.statistics
                          ? `${review.statistics.completed_days ?? 0} / ${review.statistics.total_days ?? 0} 天`
                          : '--'}
                      </span>
                    </div>

                    <div>
                      <span className="text-[10px] text-muted-foreground block">归档时间</span>
                      <span className="font-semibold text-foreground/80">
                        {new Date(review.created_at).toLocaleString()}
                      </span>
                    </div>
                  </div>

                  {/* Evidence List */}
                  <div className="flex flex-col gap-2">
                    <span className="font-semibold text-foreground/70">验证指标收集：</span>
                    <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                      {Object.entries(review.evidence || {}).map(([cpId, data]) => {
                        const value = data.value;
                        const displayValue =
                          typeof value === 'string' || typeof value === 'number'
                            ? value
                            : JSON.stringify(value);
                        const url = typeof data.url === 'string' ? data.url : null;
                        return (
                        <div key={cpId} className="p-3 rounded-lg border border-border/30 bg-card">
                          <span className="font-medium text-foreground block mb-1">
                            指标 ID: {cpId}
                          </span>
                          <div className="text-muted-foreground flex flex-col gap-0.5 text-[11px]">
                            <div>
                              提交证据: <strong className="text-foreground">{displayValue}</strong>
                            </div>
                            {url && (
                              <div className="truncate">
                                证据链接:{' '}
                                <a
                                  href={url}
                                  target="_blank"
                                  rel="noopener noreferrer"
                                  className="text-blue-500 hover:underline"
                                >
                                  {url}
                                </a>
                              </div>
                            )}
                          </div>
                        </div>
                        );
                      })}
                    </div>
                  </div>

                  {/* Text Summaries */}
                  <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                    {review.difficulty && (
                      <div className="flex flex-col gap-1 p-3 rounded-lg bg-card border border-border/20">
                        <span className="font-semibold text-foreground/75 block">阶段难点与阻碍：</span>
                        <p className="text-muted-foreground leading-relaxed whitespace-pre-wrap">
                          {review.difficulty}
                        </p>
                      </div>
                    )}

                    {review.next_capacity && (
                      <div className="flex flex-col gap-1 p-3 rounded-lg bg-card border border-border/20">
                        <span className="font-semibold text-foreground/75 block">后续能力与经验沉淀：</span>
                        <p className="text-muted-foreground leading-relaxed whitespace-pre-wrap">
                          {review.next_capacity}
                        </p>
                      </div>
                    )}
                  </div>

                  {/* Override Reason Warning */}
                  {review.decision === 'override' && review.override_reason && (
                    <div className="flex flex-col gap-1 p-3 rounded-lg bg-purple-500/5 border border-purple-500/10 text-purple-700">
                      <span className="font-semibold block flex items-center gap-1.5">
                        <AlertCircle size={13} />
                        强行解锁批准原因：
                      </span>
                      <p className="leading-relaxed whitespace-pre-wrap italic">
                        {review.override_reason}
                      </p>
                    </div>
                  )}
                </div>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
};
