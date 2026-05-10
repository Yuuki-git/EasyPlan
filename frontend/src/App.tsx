import React, { useEffect } from 'react';
import { useAppStore, ThemeType } from './store/useAppStore';
import { DynamicInput } from './components/DynamicInput';
import { ActionLayer } from './components/ActionLayer';
import { ReasoningStream } from './components/ReasoningStream';
import { TaskTreeRoot } from './components/TaskTreeVisualizer';
import { AuthModal } from './components/AuthModal';
import { TaskBoard } from './components/TaskBoard';
import { useSSE } from './hooks/useSSE';
import { motion, AnimatePresence } from 'framer-motion';
import { clsx, type ClassValue } from 'clsx';
import { twMerge } from 'tailwind-merge';
import { LogOut, Palette, LayoutDashboard } from 'lucide-react';

function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

const App: React.FC = () => {
  const { appState, error, theme, view } = useAppStore();
  
  // Initialize SSE listener
  useSSE();

  // Apply theme to HTML root so body picks up the CSS variables
  useEffect(() => {
    document.documentElement.classList.remove('theme-void');
    if (theme === 'void') {
      document.documentElement.classList.add('theme-void');
    }
  }, [theme]);

  return (
    <>
      <AuthModal />
      <AnimatePresence mode="wait">
        {view === 'input' ? (
          <motion.main 
            key="input-view"
            initial={{ opacity: 0, x: -20 }}
            animate={{ opacity: 1, x: 0 }}
            exit={{ opacity: 0, x: -20 }}
            transition={{ duration: 0.5, ease: [0.22, 1, 0.36, 1] }}
            className={cn('the-void', appState !== 'INITIAL' && 'top')}
          >
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
          </motion.main>
        ) : (
          <TaskBoard key="board-view" />
        )}
      </AnimatePresence>
    </>
  );
};

const Header: React.FC = () => {
  const { setToken, theme, setTheme, setView } = useAppStore();

  const toggleTheme = () => {
    const nextTheme: ThemeType = theme === 'void' ? 'parchment' : 'void';
    setTheme(nextTheme);
  };

  return (
    <header className="fixed top-0 left-0 w-full p-8 flex justify-between items-center z-50">
      <div className="text-base font-medium tracking-wide text-foreground/70">
        EasyPlan
      </div>
      <div className="flex items-center gap-6">
        <button 
          onClick={() => setView('board')}
          className="flex items-center gap-2 text-xs font-medium text-muted-foreground hover:text-foreground transition-colors tracking-wide"
          title="进入看板"
        >
          <LayoutDashboard size={14} />
          <span>看板</span>
        </button>

        <button 
          onClick={toggleTheme}
          className="flex items-center gap-2 text-xs font-medium text-muted-foreground hover:text-foreground transition-colors tracking-wide"
          title="切换主题"
        >
          <Palette size={14} />
          <span>Theme: {theme === 'void' ? 'Dark' : 'Light'}</span>
        </button>
        
        <button 
          onClick={() => setToken(null)}
          className="text-muted-foreground hover:text-foreground transition-colors"
          title="退出登录"
        >
          <LogOut size={16} />
        </button>
      </div>
    </header>
  );
};

export default App;
