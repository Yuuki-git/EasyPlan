import React, { useState, useEffect } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import { useAppStore } from '../store/useAppStore';
import { Command } from 'lucide-react';

export const DynamicInput: React.FC = () => {
  const { appState, setAppState, intent } = useAppStore();
  const [value, setValue] = useState(intent);

  const [greeting, setGreeting] = useState('');

  useEffect(() => {
    setValue(intent);
  }, [intent]);

  useEffect(() => {
    const hour = new Date().getHours();
    const minute = new Date().getMinutes();
    const time = hour + minute / 60;
    
    let options: string[] = [];
    if (time >= 6 && time < 11.5) {
      options = [
        '早安。新的一天开始了，今天哪件事对你最重要？',
        '早上好。喝杯热茶，写下今天最期待完成的一件事吧。'
      ];
    } else if (time >= 11.5 && time < 14) {
      options = [
        '中午好。先好好吃顿饭，休息一下，下午再出发。',
        '午安。上午辛苦了，花一分钟整理下思绪，准备迎接下午吧。'
      ];
    } else if (time >= 14 && time < 18) {
      options = [
        '下午好。进度慢一点也没关系，深呼吸，我们继续完成接下来的目标。',
        '下午好。站起来活动一下吧？然后写下接下来的核心专注点。'
      ];
    } else if (time >= 18 && time < 22) {
      options = [
        '晚上好。今天辛苦了，把还在脑子里乱转的想法先存放在这吧。',
        '晚上好。今天辛苦了，把还在脑子里乱转的想法先存放在这吧。' // Using same to fulfill exact PM request
      ];
    } else {
      options = [
        '夜深了。清空待办，把明天的烦恼留给明天，今晚睡个好觉。',
        '夜深了。清空待办，把明天的烦恼留给明天，今晚睡个好觉。' // Using same to fulfill exact PM request
      ];
    }
    setGreeting(options[Math.floor(Math.random() * options.length)]);
  }, []);

  const getPlaceholder = () => {
    switch (appState) {
      case 'INITIAL':
        return '输入您的任何想法...';
      case 'PENDING':
        return '需要微调吗？（例如："缩减一半时间"）';
      default:
        return '处理中...';
    }
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
            className="absolute -top-12 left-2 text-sm font-medium text-muted-foreground/80 tracking-wide"
          >
            {greeting}
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
          className="w-full bg-transparent border-b border-muted py-4 px-2 text-2xl focus:outline-none focus:border-foreground transition-colors placeholder:text-muted-foreground/60 disabled:opacity-50"
          autoFocus
        />
        <div className="absolute right-2 top-1/2 -translate-y-1/2 flex items-center gap-2 text-muted-foreground/50 group-focus-within:text-foreground/70 transition-colors">
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
              "策划周末短途旅行",
              "写完毕业论文初稿",
              "制定健身计划"
            ].map((prompt, idx) => (
              <button
                key={idx}
                type="button"
                onClick={() => setValue(prompt)}
                className="text-xs font-medium text-muted-foreground/90 bg-muted/40 border border-muted-foreground/30 hover:bg-muted/60 hover:border-foreground/50 hover:text-foreground transition-all rounded-full px-3 py-1 cursor-pointer shadow-sm"
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
