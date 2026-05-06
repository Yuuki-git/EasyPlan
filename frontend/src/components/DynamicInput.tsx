import React, { useState, useEffect } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import { useAppStore } from '../store/useAppStore';
import { Command } from 'lucide-react';

export const DynamicInput: React.FC = () => {
  const { appState, setAppState, intent } = useAppStore();
  const [value, setValue] = useState(intent);

  useEffect(() => {
    setValue(intent);
  }, [intent]);

  const getPlaceholder = () => {
    switch (appState) {
      case 'INITIAL':
        return 'What is your intent?';
      case 'PENDING':
        return 'Need to refine? (e.g., "Cut it in half")';
      default:
        return 'Processing...';
    }
  };

  const getGreeting = () => {
    const hour = new Date().getHours();
    if (hour < 12) return 'Good morning. What is the most important thing today?';
    if (hour < 18) return 'Good afternoon. Ready to make some progress?';
    return 'Good evening. Let\'s wrap up the day.';
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!value.trim()) return;

    if (appState === 'INITIAL') {
      useAppStore.getState().submitIntent(value);
    } else if (appState === 'PENDING') {
      // Trigger refinement
      try {
        setAppState('THINKING');
        const { threadId, token } = useAppStore.getState();
        
        const headers: Record<string, string> = {
          'Content-Type': 'application/json',
          'X-User-Timezone': Intl.DateTimeFormat().resolvedOptions().timeZone
        };
        if (token) headers['Authorization'] = `Bearer ${token}`;

        const response = await fetch(`/api/threads/${threadId}/confirm`, {
          method: 'POST',
          headers,
          body: JSON.stringify({ 
            request_id: crypto.randomUUID(),
            action: 'refine',
            feedback: value 
          })
        });
        if (!response.ok) throw new Error('Failed to refine plan');
      } catch (err) {
        useAppStore.getState().setError((err as Error).message);
      }
    }
  };

  return (
    <motion.div
      layout
      initial={{ opacity: 0, y: 20 }}
      animate={{ opacity: 1, y: 0 }}
      className="w-full max-w-2xl relative"
    >
      <AnimatePresence>
        {appState === 'INITIAL' && !value && (
          <motion.div
            initial={{ opacity: 0, y: 10 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: -10 }}
            transition={{ delay: 0.2 }}
            className="absolute -top-8 left-2 text-sm font-light text-muted-foreground/60 tracking-wide"
          >
            {getGreeting()}
          </motion.div>
        )}
      </AnimatePresence>

      <form onSubmit={handleSubmit} className="relative group">
        <input
          type="text"
          value={value}
          onChange={(e) => setValue(e.target.value)}
          disabled={appState === 'THINKING' || appState === 'SYNCING'}
          placeholder={getPlaceholder()}
          className="w-full bg-transparent border-b border-muted py-4 px-2 text-2xl focus:outline-none focus:border-foreground transition-colors placeholder:text-muted-foreground/50 disabled:opacity-50"
          autoFocus
        />
        <div className="absolute right-2 top-1/2 -translate-y-1/2 flex items-center gap-2 text-muted-foreground/30 group-focus-within:text-muted-foreground/60 transition-colors">
          <Command size={16} />
          <span className="text-xs font-mono">ENTER</span>
        </div>
      </form>
      
      <AnimatePresence>
        {appState === 'INITIAL' && !value && (
          <motion.div
            initial={{ opacity: 0, y: -10 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: -10 }}
            transition={{ delay: 0.3 }}
            className="absolute -bottom-12 left-2 flex gap-3"
          >
            {[
              "Plan a weekend trip",
              "Finish my thesis draft",
              "Workout schedule"
            ].map((prompt, idx) => (
              <button
                key={idx}
                type="button"
                onClick={() => setValue(prompt)}
                className="text-xs font-light text-muted-foreground/70 bg-muted/20 border border-muted/50 hover:bg-muted/40 hover:border-muted hover:text-foreground/80 transition-all rounded-full px-3 py-1 cursor-pointer shadow-sm"
              >
                {prompt}
              </button>
            ))}
          </motion.div>
        )}
      </AnimatePresence>
    </motion.div>
  );
};
