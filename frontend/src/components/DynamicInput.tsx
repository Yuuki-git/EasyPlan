import React, { useState, useEffect } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import { useAppStore } from '../store/useAppStore';
import { Command } from 'lucide-react';

export const DynamicInput: React.FC = () => {
  const { appState, setIntent, setAppState, intent } = useAppStore();
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

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!value.trim()) return;

    if (appState === 'INITIAL') {
      try {
        setAppState('THINKING');
        const response = await fetch('/api/intents', {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            'X-User-Timezone': Intl.DateTimeFormat().resolvedOptions().timeZone
          },
          body: JSON.stringify({ intent_text: value })
        });
        
        if (!response.ok) throw new Error('Failed to submit intent');
        
        const data = await response.json();
        setIntent(value);
        useAppStore.getState().setThreadId(data.thread_id);
      } catch (err) {
        useAppStore.getState().setError((err as Error).message);
      }
    } else if (appState === 'PENDING') {
      // Trigger refinement - this would be another POST to /api/threads/{id}/confirm with action='refine'
      try {
        setAppState('THINKING');
        const threadId = useAppStore.getState().threadId;
        const response = await fetch(`/api/threads/${threadId}/confirm`, {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            'X-User-Timezone': Intl.DateTimeFormat().resolvedOptions().timeZone
          },
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
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            className="absolute -bottom-8 left-2 flex gap-4"
          >
            <span className="text-xs text-muted-foreground/40 cursor-pointer hover:text-muted-foreground/80 transition-colors">
              "Write a paper"
            </span>
            <span className="text-xs text-muted-foreground/40 cursor-pointer hover:text-muted-foreground/80 transition-colors">
              "Workout plan"
            </span>
          </motion.div>
        )}
      </AnimatePresence>
    </motion.div>
  );
};
