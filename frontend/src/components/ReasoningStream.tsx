import React, { useEffect, useRef } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import { useAppStore } from '../store/useAppStore';

export const ReasoningStream: React.FC = () => {
  const { reasoningLogs, appState, isRunStalled } = useAppStore();
  const scrollRef = useRef<HTMLDivElement>(null);

  // Auto-scroll to bottom as logs arrive
  useEffect(() => {
    if (scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, [reasoningLogs]);

  if (appState !== 'THINKING' && reasoningLogs.length === 0) return null;

  return (
    <div
      ref={scrollRef}
      className="w-full max-w-2xl mt-12 max-h-[40vh] overflow-y-auto px-2 space-y-4 mask-fade-out"
    >
      <AnimatePresence mode="popLayout">
        {reasoningLogs.map((log, index) => (
          <motion.div
            key={`${index}-${log}`}
            initial={{ opacity: 0, x: -10 }}
            animate={{ opacity: 1, x: 0 }}
            transition={{ duration: 0.4, ease: "easeOut" }}
            className="flex items-start gap-4"
          >
            <div className="w-1.5 h-1.5 rounded-full bg-muted-foreground/30 mt-2 shrink-0" />
            <p className="text-sm font-light text-muted-foreground leading-relaxed">
              {log}
            </p>
          </motion.div>
        ))}
      </AnimatePresence>

      {appState === 'THINKING' && (
        <div className="flex items-center gap-2 px-6 py-2">
          {isRunStalled ? (
            <span className="text-xs font-mono text-amber-500/80 tracking-widest flex items-center gap-1.5">
              <span className="inline-block w-1.5 h-1.5 rounded-full bg-amber-500 animate-ping" />
              网络连接较慢，您可以选择重新连接或返回当前计划
            </span>
          ) : (
            <span className="text-xs font-mono text-muted-foreground/60 tracking-widest animate-pulse">
              AI 正在思考...
            </span>
          )}
        </div>
      )}
    </div>
  );
};
