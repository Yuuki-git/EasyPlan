import React from 'react';
import { useAppStore } from './store/useAppStore';
import { DynamicInput } from './components/DynamicInput';
import { ActionLayer } from './components/ActionLayer';
import { ReasoningStream } from './components/ReasoningStream';
import { TaskTreeRoot } from './components/TaskTreeVisualizer';
import { useSSE } from './hooks/useSSE';
import { motion, AnimatePresence } from 'framer-motion';
import { clsx, type ClassValue } from 'clsx';
import { twMerge } from 'tailwind-merge';

function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

const App: React.FC = () => {
  const { appState, error } = useAppStore();
  
  // Initialize SSE listener
  useSSE();

  return (
    <main className={cn('the-void', appState !== 'INITIAL' && 'top')}>
      <Header />
      
      <div className="w-full flex flex-col items-center">
        <DynamicInput />
        
        <ReasoningStream />
        
        <TaskTreeRoot />

        <AnimatePresence>
          {error && (
            <motion.div
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              exit={{ opacity: 0 }}
              className="mt-8 px-4 py-2 bg-red-500/10 border border-red-500/20 rounded text-red-400 text-xs font-mono"
            >
              Error: {error}
            </motion.div>
          )}
        </AnimatePresence>
      </div>

      <ActionLayer />
    </main>
  );
};

const Header: React.FC = () => {
  return (
    <header className="fixed top-0 left-0 w-full p-8 flex justify-between items-center z-50">
      <div className="text-sm font-bold tracking-widest text-foreground/80">
        EASYPLAN
      </div>
      <div className="flex gap-6">
        <button className="text-xs text-muted-foreground hover:text-foreground transition-colors uppercase tracking-tighter">
          Integrations
        </button>
        <div className="w-8 h-8 rounded-full bg-muted border border-white/10" />
      </div>
    </header>
  );
};

export default App;
