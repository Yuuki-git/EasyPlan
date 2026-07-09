import React, { useEffect, useRef } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import { useAppStore } from '../store/useAppStore';

export const ReasoningStream: React.FC = () => {
  const {
    currentStage,
    recentEvents,
    reasoningLogs,
    isProcessPanelExpanded,
    setProcessPanelExpanded,
    appState,
    isRunStalled,
    lastRunErrorSummary,
    error
  } = useAppStore();

  const scrollRef = useRef<HTMLDivElement>(null);

  // Auto-scroll to bottom of detailed events as they arrive
  useEffect(() => {
    if (scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, [reasoningLogs]);

  // Determine if we should show anything at all
  const isGenerating = appState === 'THINKING' || appState === 'PENDING' || appState === 'SYNCING' || appState === 'ERROR';
  if (!isGenerating && !lastRunErrorSummary && reasoningLogs.length === 0) return null;

  // Derive stage labels and icons
  let statusIcon = (
    <span className="relative flex h-2.5 w-2.5">
      <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-primary opacity-75"></span>
      <span className="relative inline-flex rounded-full h-2.5 w-2.5 bg-primary"></span>
    </span>
  );
  let statusTitle = currentStage || "AI 正在分析意图...";

  if (isRunStalled) {
    statusIcon = <span className="inline-block w-2.5 h-2.5 rounded-full bg-amber-500 animate-pulse" />;
    statusTitle = "网络连接卡住，正在尝试重连...";
  } else if (appState === 'ERROR' || error) {
    statusIcon = <span className="inline-block w-2.5 h-2.5 rounded-full bg-destructive" />;
    statusTitle = lastRunErrorSummary || error || "规划生成失败";
  } else if (appState === 'SUCCESS') {
    statusIcon = <span className="inline-block w-2.5 h-2.5 rounded-full bg-emerald-500" />;
    statusTitle = "已完成规划";
  } else if (appState === 'SYNCING') {
    statusTitle = currentStage || "正在同步至您的日程...";
  }

  // Expansion toggle handler
  const toggleExpanded = () => {
    setProcessPanelExpanded(!isProcessPanelExpanded);
  };

  // Get the latest 3-5 user-facing events
  const displayEvents = recentEvents.slice(-5);

  return (
    <div className="w-full max-w-2xl mt-8 border border-muted/50 rounded-2xl bg-card text-card-foreground shadow-sm overflow-hidden transition-all duration-300">
      {/* Header / Compact Bar */}
      <div
        onClick={toggleExpanded}
        className="px-5 py-4 flex items-center justify-between cursor-pointer hover:bg-muted/10 transition-colors select-none"
      >
        <div className="flex items-center gap-3">
          {statusIcon}
          <span className="text-sm font-medium tracking-wide">
            {statusTitle}
          </span>
        </div>
        <button className="text-xs text-muted-foreground hover:text-foreground transition-colors flex items-center gap-1">
          {isProcessPanelExpanded ? '收起详情' : '查看进度'}
          <span className="text-[10px] transform transition-transform duration-200 inline-block" style={{ transform: isProcessPanelExpanded ? 'rotate(180deg)' : 'rotate(0deg)' }}>
            ▼
          </span>
        </button>
      </div>

      {/* Expanded Content */}
      <AnimatePresence initial={false}>
        {isProcessPanelExpanded && (
          <motion.div
            initial={{ height: 0 }}
            animate={{ height: 'auto' }}
            exit={{ height: 0 }}
            transition={{ duration: 0.3, ease: 'easeInOut' }}
            className="border-t border-muted/30 overflow-hidden"
          >
            <div className="p-5 space-y-4">
              {/* Recent Events (Compact representation of stages) */}
              {displayEvents.length > 0 && (
                <div className="space-y-2">
                  <span className="text-[11px] font-semibold text-muted-foreground uppercase tracking-wider">最近更新</span>
                  <div className="space-y-1.5 pl-1.5">
                    {displayEvents.map((evt, idx) => (
                      <div key={idx} className="flex items-center gap-3">
                        <div className={`w-1 h-1 rounded-full ${idx === displayEvents.length - 1 ? 'bg-primary' : 'bg-muted-foreground/35'}`} />
                        <span className={`text-xs ${idx === displayEvents.length - 1 ? 'text-foreground font-medium' : 'text-muted-foreground/80'}`}>
                          {evt}
                        </span>
                      </div>
                    ))}
                  </div>
                </div>
              )}

              {/* Detailed logs */}
              {reasoningLogs.length > 0 && (
                <div className="space-y-2 pt-2 border-t border-muted/20">
                  <span className="text-[11px] font-semibold text-muted-foreground uppercase tracking-wider">详细日志</span>
                  <div
                    ref={scrollRef}
                    className="max-h-40 overflow-y-auto font-mono text-[11px] text-muted-foreground/75 bg-muted/20 rounded-lg p-3 space-y-1.5 scrollbar-thin"
                  >
                    {reasoningLogs.map((log, index) => (
                      <div key={index} className="leading-relaxed break-all">
                        <span className="text-muted-foreground/40 mr-1.5">&gt;</span>
                        {log}
                      </div>
                    ))}
                  </div>
                </div>
              )}

              {/* Stalled / Connection Warning */}
              {isRunStalled && (
                <div className="p-3 bg-amber-500/10 border border-amber-500/20 rounded-xl text-xs text-amber-600 dark:text-amber-500 flex items-center gap-2">
                  <span className="inline-block w-1.5 h-1.5 rounded-full bg-amber-500 animate-ping" />
                  连接卡住，正在后台重试。您可以重新连接或返回当前已确认计划。
                </div>
              )}
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
};
