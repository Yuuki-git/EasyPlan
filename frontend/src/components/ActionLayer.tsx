import React from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import { useAppStore } from '../store/useAppStore';
import { Command, AlertTriangle, RotateCw } from 'lucide-react';

export const ActionLayer: React.FC = () => {
  const {
    appState,
    reset,
    confirmPlan,
    previewMode,
    cancelPlanPreview,
    isRunStalled,
    setRunStalled,
    returnToCommittedPlan,
    submitIntent,
    generateNextPhasePlan,
    intent,
    startNewIntent,
    error
  } = useAppStore();

  const isVisible = appState === 'PENDING' || appState === 'THINKING' || appState === 'ERROR' || isRunStalled;

  const handleCancel = () => {
    if (previewMode === 'next_phase') {
      cancelPlanPreview();
    } else {
      reset();
    }
  };

  const handleRetry = () => {
    if (previewMode === 'next_phase') {
      generateNextPhasePlan();
    } else {
      submitIntent(intent);
    }
  };

  return (
    <AnimatePresence>
      {isVisible && (
        <motion.div
          initial={{ opacity: 0, y: 10 }}
          animate={{ opacity: 1, y: 0 }}
          exit={{ opacity: 0, y: 10 }}
          className="fixed bottom-0 left-0 w-full pb-12 pt-24 bg-gradient-to-t from-background via-background/90 to-transparent flex justify-center pointer-events-none z-40"
        >
          <div className="flex flex-col items-center gap-4 pointer-events-auto max-w-xl w-full px-4">
            {appState === 'ERROR' ? (
              <motion.div
                initial={{ scale: 0.95 }}
                animate={{ scale: 1 }}
                className="flex flex-col items-center gap-4 p-5 rounded-2xl border border-red-500/30 bg-red-500/5 backdrop-blur-md w-full shadow-lg"
              >
                <div className="flex items-center gap-2 text-red-400">
                  <AlertTriangle size={18} />
                  <span className="text-sm font-medium">{error || '这次规划没有顺利完成，请重试一次'}</span>
                </div>
                <div className="flex items-center gap-4 w-full justify-center">
                  <button
                    onClick={handleRetry}
                    className="flex items-center gap-2 text-sm font-medium px-4 py-2 border border-red-500/50 hover:border-red-500 rounded-full bg-red-500/10 hover:bg-red-500/20 text-red-400 transition-all shadow-sm"
                  >
                    <RotateCw size={14} />
                    重试本次生成
                  </button>
                  <button
                    onClick={returnToCommittedPlan}
                    className="flex items-center gap-2 text-sm font-medium px-4 py-2 border border-muted hover:border-foreground/30 rounded-full hover:bg-white/5 transition-all text-muted-foreground hover:text-foreground"
                  >
                    返回当前计划
                  </button>
                  <button
                    onClick={startNewIntent}
                    className="flex items-center gap-2 text-sm font-medium px-4 py-2 border border-muted hover:border-foreground/30 rounded-full hover:bg-white/5 transition-all text-muted-foreground hover:text-foreground"
                  >
                    播种新想法
                  </button>
                </div>
              </motion.div>
            ) : isRunStalled ? (
              <motion.div
                initial={{ scale: 0.95 }}
                animate={{ scale: 1 }}
                className="flex flex-col items-center gap-4 p-5 rounded-2xl border border-amber-500/30 bg-amber-500/5 backdrop-blur-md w-full shadow-lg"
              >
                <div className="flex items-center gap-2 text-amber-500">
                  <AlertTriangle size={18} className="animate-bounce" />
                  <span className="text-sm font-medium">生成似乎卡住了...</span>
                </div>
                <div className="flex items-center gap-4 w-full justify-center">
                  <button
                    onClick={() => setRunStalled(false)}
                    className="flex items-center gap-2 text-sm font-medium px-4 py-2 border border-muted hover:border-foreground/30 rounded-full hover:bg-white/5 transition-all text-muted-foreground hover:text-foreground"
                  >
                    继续等待
                  </button>
                  <button
                    onClick={handleRetry}
                    className="flex items-center gap-2 text-sm font-medium px-4 py-2 border border-amber-500/50 hover:border-amber-500 rounded-full bg-amber-500/10 hover:bg-amber-500/20 text-amber-500 transition-all shadow-sm"
                  >
                    <RotateCw size={14} />
                    重试本次生成
                  </button>
                  <button
                    onClick={returnToCommittedPlan}
                    className="flex items-center gap-2 text-sm font-medium px-4 py-2 border border-muted hover:border-foreground/30 rounded-full hover:bg-white/5 transition-all text-muted-foreground hover:text-foreground"
                  >
                    返回当前计划
                  </button>
                </div>
              </motion.div>
            ) : (
              <div className="flex items-center gap-8 bg-background/50 backdrop-blur-md border border-muted px-6 py-3 rounded-full shadow-lg">
                {appState === 'THINKING' ? (
                  <>
                    <button
                      onClick={returnToCommittedPlan}
                      className="group flex items-center gap-2 text-muted-foreground/60 hover:text-foreground transition-colors"
                    >
                      <span className="text-sm font-light">返回当前计划</span>
                    </button>
                    <div className="w-px h-4 bg-muted/60" />
                    <button
                      onClick={handleCancel}
                      className="group flex items-center gap-2 text-muted-foreground/60 hover:text-foreground transition-colors"
                    >
                      <div className="px-1.5 py-0.5 border border-muted rounded text-[10px] font-mono group-hover:border-muted-foreground transition-colors">
                        ESC
                      </div>
                      <span className="text-sm font-light">取消本次生成</span>
                    </button>
                  </>
                ) : (
                  <>
                    <button
                      onClick={returnToCommittedPlan}
                      className="group flex items-center gap-2 text-muted-foreground/60 hover:text-foreground transition-colors"
                    >
                      <span className="text-sm font-light">返回当前计划</span>
                    </button>
                    <div className="w-px h-4 bg-muted/60" />
                    <button
                      onClick={handleCancel}
                      className="group flex items-center gap-2 text-muted-foreground/60 hover:text-foreground transition-colors"
                    >
                      <div className="px-1.5 py-0.5 border border-muted rounded text-[10px] font-mono group-hover:border-muted-foreground transition-colors">
                        ESC
                      </div>
                      <span className="text-sm font-light">取消本次生成</span>
                    </button>
                    <div className="w-px h-4 bg-muted/60" />
                    <button
                      onClick={confirmPlan}
                      className="group flex items-center gap-2 text-foreground/80 hover:text-foreground transition-colors px-4 py-2 border border-muted hover:border-foreground/40 rounded-full bg-white/5 backdrop-blur-sm"
                    >
                      <div className="flex items-center gap-1 px-1.5 py-0.5 border border-muted/50 rounded text-[10px] font-mono group-hover:border-foreground/30 transition-colors">
                        <Command size={10} />
                        <span>ENTER</span>
                      </div>
                      <span className="text-sm font-medium">
                        {previewMode === 'next_phase' ? '追加到当前计划' : '确认并保存'}
                      </span>
                    </button>
                  </>
                )}
              </div>
            )}
          </div>
        </motion.div>
      )}
    </AnimatePresence>
  );
};
