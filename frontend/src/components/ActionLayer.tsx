import React from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import { useAppStore } from '../store/useAppStore';
import { Command } from 'lucide-react';

export const ActionLayer: React.FC = () => {
  const { appState, reset, confirmPlan, previewMode, cancelPlanPreview } = useAppStore();

  const isVisible = appState === 'PENDING' || appState === 'THINKING';

  const handleCancel = () => {
    if (previewMode === 'next_phase') {
      cancelPlanPreview();
    } else {
      reset();
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
          <div className="flex items-center gap-8 pointer-events-auto">
            {appState === 'THINKING' ? (
              <button
                onClick={handleCancel}
                className="group flex items-center gap-2 text-muted-foreground/60 hover:text-foreground transition-colors"
              >
                <div className="px-1.5 py-0.5 border border-muted rounded text-[10px] font-mono group-hover:border-muted-foreground transition-colors">
                  ESC
                </div>
                <span className="text-sm font-light">取消</span>
              </button>
            ) : (
              <>
                <button
                  onClick={handleCancel}
                  className="group flex items-center gap-2 text-muted-foreground/60 hover:text-foreground transition-colors"
                >
                  <div className="px-1.5 py-0.5 border border-muted rounded text-[10px] font-mono group-hover:border-muted-foreground transition-colors">
                    ESC
                  </div>
                  <span className="text-sm font-light">取消</span>
                </button>
                
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
        </motion.div>
      )}
    </AnimatePresence>
  );
};
