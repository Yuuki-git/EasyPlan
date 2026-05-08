import React, { useEffect } from 'react';
import { useAppStore, ThemeType } from './store/useAppStore';
import { DynamicInput } from './components/DynamicInput';
import { ActionLayer } from './components/ActionLayer';
import { ReasoningStream } from './components/ReasoningStream';
import { TaskTreeRoot } from './components/TaskTreeVisualizer';
import { AuthModal } from './components/AuthModal';
import { useSSE } from './hooks/useSSE';
import { motion, AnimatePresence } from 'framer-motion';
import { clsx, type ClassValue } from 'clsx';
import { twMerge } from 'tailwind-merge';
import { LogOut, Palette } from 'lucide-react';

function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

const App: React.FC = () => {
  const { appState, error, theme } = useAppStore();
  
  // Initialize SSE listener
  useSSE();

  // Apply theme to HTML root so body picks up the CSS variables
  useEffect(() => {
    document.documentElement.classList.remove('theme-void', 'theme-parchment');
    if (theme !== 'zen') {
      document.documentElement.classList.add(`theme-${theme}`);
    }
  }, [theme]);

  return (
    <>
      <AuthModal />
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
    </>
  );
};

const Header: React.FC = () => {
  const { setToken, theme, setTheme } = useAppStore();

  const toggleTheme = () => {
    const themes: ThemeType[] = ['zen', 'void', 'parchment'];
    const currentIndex = themes.indexOf(theme);
    const nextTheme = themes[(currentIndex + 1) % themes.length];
    setTheme(nextTheme);
  };

  return (
    <header className="fixed top-0 left-0 w-full p-8 flex justify-between items-center z-50">
      <div className="text-base font-medium tracking-wide text-foreground/70">
        EasyPlan
      </div>
      <div className="flex items-center gap-6">
        <button 
          onClick={toggleTheme}
          className="text-muted-foreground hover:text-foreground transition-colors"
          title="切换主题"
        >
          <Palette size={16} />
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
