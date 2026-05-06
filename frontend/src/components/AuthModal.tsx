import React, { useState } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import { useAppStore } from '../store/useAppStore';

export const AuthModal: React.FC = () => {
  const { token, setToken } = useAppStore();
  const [isLogin, setIsLogin] = useState(true);
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(false);

  if (token) return null;

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
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setIsLoading(false);
    }
  };

  return (
    <AnimatePresence>
      <motion.div
        initial={{ opacity: 0 }}
        animate={{ opacity: 1 }}
        exit={{ opacity: 0 }}
        className="fixed inset-0 z-[100] flex items-center justify-center bg-background/80 backdrop-blur-sm p-4"
      >
        <motion.div
          initial={{ scale: 0.95, opacity: 0, y: 20 }}
          animate={{ scale: 1, opacity: 1, y: 0 }}
          className="w-full max-w-sm bg-background border border-muted p-8 shadow-2xl relative"
        >
          <div className="absolute -top-px left-0 right-0 h-px bg-gradient-to-r from-transparent via-foreground/20 to-transparent" />
          
          <h2 className="text-xl font-medium tracking-tight mb-2">
            {isLogin ? 'Welcome back' : 'Create account'}
          </h2>
          <p className="text-sm text-muted-foreground mb-8 font-light">
            Authenticate to continue to EasyPlan.
          </p>

          <form onSubmit={handleSubmit} className="space-y-4">
            <div className="space-y-1.5">
              <label className="text-[10px] uppercase tracking-widest text-muted-foreground/60 font-mono">
                Email
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
              <label className="text-[10px] uppercase tracking-widest text-muted-foreground/60 font-mono">
                Password
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
              <p className="text-xs text-red-400 font-light mt-2">{error}</p>
            )}

            <button
              type="submit"
              disabled={isLoading}
              className="w-full mt-6 py-2.5 bg-foreground text-background text-sm font-medium hover:bg-foreground/90 transition-colors disabled:opacity-50"
            >
              {isLoading ? 'Processing...' : isLogin ? 'Sign In' : 'Sign Up'}
            </button>
          </form>

          <div className="mt-6 text-center">
            <button
              onClick={() => setIsLogin(!isLogin)}
              className="text-xs text-muted-foreground hover:text-foreground transition-colors"
            >
              {isLogin ? "Don't have an account? Sign up" : "Already have an account? Sign in"}
            </button>
          </div>
        </motion.div>
      </motion.div>
    </AnimatePresence>
  );
};
