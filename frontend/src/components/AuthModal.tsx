import React, { useState } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import { useAppStore } from '../store/useAppStore';
import { X } from 'lucide-react';

export const AuthModal: React.FC = () => {
  const { showAuthModal, setShowAuthModal, setToken, pendingIntent, submitIntent } = useAppStore();
  const [isLogin, setIsLogin] = useState(true);
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(false);

  if (!showAuthModal) return null;

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    setIsLoading(true);

    try {
      const endpoint = isLogin ? '/api/auth/token' : '/api/auth/register';
      const body = isLogin 
        ? { email, password } 
        : { email, password, display_name: email.split('@')[0] };

      const response = await fetch(endpoint, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body)
      });

      if (!response.ok) {
        const errorData = await response.json();
        throw new Error(errorData.detail?.[0]?.msg || errorData.detail || 'Authentication failed');
      }

      const data = await response.json();
      setToken(data.access_token);
      setShowAuthModal(false);
      
      // Lazy Auth: Resume pending intent if it exists
      if (pendingIntent) {
        // Use setTimeout to ensure state updates (like token) have propagated
        setTimeout(() => submitIntent(pendingIntent), 0);
      }

    } catch (err) {
      setError((err as Error).message);
    } finally {
      setIsLoading(false);
    }
  };

  const handleClose = () => {
    setShowAuthModal(false);
    useAppStore.getState().setPendingIntent(null);
  };

  return (
    <AnimatePresence>
      <motion.div
        initial={{ opacity: 0 }}
        animate={{ opacity: 1 }}
        exit={{ opacity: 0 }}
        className="fixed inset-0 z-[100] flex items-center justify-center bg-background/60 backdrop-blur-md p-4"
      >
        <motion.div
          initial={{ scale: 0.95, opacity: 0, y: 20 }}
          animate={{ scale: 1, opacity: 1, y: 0 }}
          className="w-full max-w-sm bg-background border border-muted/50 p-8 shadow-xl rounded-2xl relative"
        >
          <button 
            onClick={handleClose}
            className="absolute top-4 right-4 text-muted-foreground/50 hover:text-foreground transition-colors"
          >
            <X size={16} />
          </button>
          
          <h2 className="text-xl font-medium tracking-tight mb-2 text-foreground">
            {isLogin ? '欢迎回来' : '创建账号'}
          </h2>
          <p className="text-sm text-muted-foreground mb-8 font-light">
            请先登录或注册，以便我们为您保存计划。
          </p>

          <form onSubmit={handleSubmit} className="space-y-4">
            <div className="space-y-1.5">
              <label className="text-[10px] tracking-widest text-muted-foreground/60 font-mono">
                邮箱
              </label>
              <input
                type="email"
                required
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                className="w-full bg-transparent border-b border-muted py-2 text-sm focus:outline-none focus:border-foreground/50 transition-colors"
                placeholder="you@example.com"
              />
            </div>
            
            <div className="space-y-1.5">
              <label className="text-[10px] tracking-widest text-muted-foreground/60 font-mono">
                密码
              </label>
              <input
                type="password"
                required
                minLength={8}
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                className="w-full bg-transparent border-b border-muted py-2 text-sm focus:outline-none focus:border-foreground/50 transition-colors"
                placeholder="••••••••"
              />
            </div>

            {error && (
              <p className="text-xs text-red-500 font-light mt-2">{error}</p>
            )}

            <button
              type="submit"
              disabled={isLoading}
              className="w-full mt-6 py-2.5 bg-foreground text-background rounded-xl text-sm font-medium hover:bg-foreground/90 transition-colors disabled:opacity-50 shadow-sm"
            >
              {isLoading ? '处理中...' : isLogin ? '登 录' : '注 册'}
            </button>
          </form>

          <div className="mt-6 text-center">
            <button
              onClick={() => setIsLogin(!isLogin)}
              className="text-xs text-muted-foreground hover:text-foreground transition-colors"
            >
              {isLogin ? "还没有账号？点击注册" : "已有账号？点击登录"}
            </button>
          </div>
        </motion.div>
      </motion.div>
    </AnimatePresence>
  );
};
