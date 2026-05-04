import React from 'react';
import { motion } from 'framer-motion';
import { TaskNode } from '../types/api';
import { useAppStore } from '../store/useAppStore';
import { ChevronRight, Circle, Clock } from 'lucide-react';
import { clsx } from 'clsx';

interface TaskTreeVisualizerProps {
  node: TaskNode;
  depth?: number;
}

export const TaskTreeVisualizer: React.FC<TaskTreeVisualizerProps> = ({ node, depth = 0 }) => {
  const isGroup = node.node_type === 'group';
  const hasChildren = node.children && node.children.length > 0;

  return (
    <motion.div
      initial={{ opacity: 0, x: -10 }}
      animate={{ opacity: 1, x: 0 }}
      transition={{ 
        delay: depth * 0.15, 
        duration: 0.6, 
        ease: [0.22, 1, 0.36, 1] // Custom "fluid" easing
      }}
      className={clsx(
        "flex flex-col w-full relative",
        depth > 0 && "ml-8 mt-6"
      )}
    >
      {/* Growth Line (The vertical connector) */}
      {depth > 0 && (
        <div className="absolute -left-4 top-0 bottom-0 w-[1px] bg-gradient-to-b from-muted via-muted/50 to-transparent" />
      )}

      <div className="flex items-start gap-4 group">
        <div className={clsx(
          "mt-1.5 shrink-0 transition-transform duration-500 group-hover:scale-110",
          isGroup ? "text-accent-foreground" : "text-muted-foreground/40"
        )}>
          {isGroup ? (
            <div className="w-5 h-5 flex items-center justify-center rounded-sm bg-accent/20 border border-accent/30">
              <ChevronRight size={14} className="text-accent-foreground" />
            </div>
          ) : (
            <div className="w-5 h-5 flex items-center justify-center">
              <Circle size={8} fill="currentColor" />
            </div>
          )}
        </div>
        
        <div className="flex-1 space-y-1.5 pb-2">
          <div className="flex items-center justify-between gap-4">
            <h4 className={clsx(
              "text-sm tracking-tight transition-colors",
              isGroup ? "font-semibold text-foreground" : "font-normal text-foreground/70"
            )}>
              {node.title}
            </h4>
            
            <div className="flex items-center gap-1.5 px-2 py-0.5 rounded-full bg-muted/30 text-[9px] font-mono text-muted-foreground/60 border border-muted/20">
              <Clock size={10} />
              <span>{node.estimated_minutes}m</span>
            </div>
          </div>
          
          {node.description && (
            <p className="text-xs text-muted-foreground/50 font-light leading-relaxed max-w-lg">
              {node.description}
            </p>
          )}
        </div>
      </div>

      {hasChildren && (
        <div className="flex flex-col">
          {node.children!.map((child) => (
            <TaskTreeVisualizer key={child.client_node_id} node={child} depth={depth + 1} />
          ))}
        </div>
      )}
    </motion.div>
  );
};

export const TaskTreeRoot: React.FC = () => {
  const { taskTree, appState } = useAppStore();

  if (!taskTree || (appState !== 'PENDING' && appState !== 'SYNCING' && appState !== 'SUCCESS')) {
    return null;
  }

  return (
    <div className="w-full flex flex-col items-center mt-12 pb-32">
      <div className="w-full max-w-2xl px-2 mb-8">
        <h3 className="text-xs font-mono text-muted-foreground/30 uppercase tracking-[0.2em] mb-2">
          Proposed Action Plan
        </h3>
        <p className="text-lg font-light text-foreground/80 leading-snug">
          {taskTree.summary}
        </p>
      </div>
      
      <TaskTreeVisualizer node={taskTree.root} />
    </div>
  );
};
