import React, { useState } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import { TaskNode } from '../types/api';
import { useAppStore } from '../store/useAppStore';
import { 
  ChevronRight, 
  Circle, 
  Clock, 
  CheckCircle2, 
  AlertCircle, 
  RotateCcw, 
  Loader2 
} from 'lucide-react';
import { clsx } from 'clsx';

interface TaskTreeVisualizerProps {
  node: TaskNode;
  depth?: number;
}

export const TaskTreeVisualizer: React.FC<TaskTreeVisualizerProps> = ({ node, depth = 0 }) => {
  const { nodeStatuses, retryNode, appState, taskTree } = useAppStore();
  const [isExpanded, setIsExpanded] = useState(true);
  const isGroup = node.node_type === 'group';
  const hasChildren = node.children && node.children.length > 0;
  const status = nodeStatuses[node.client_node_id] || 'pending';

  // Performance Guard: Count total nodes to decide on layout complexity
  // In a real app, this could be a memoized selector in the store
  const isLargeTree = (taskTree?.root ? 100 : 0) > 50; // Simplified check for demonstration

  const renderStatus = () => {
    // Only show status icons after SYNCING has started
    if (appState === 'PENDING' || appState === 'INITIAL' || appState === 'THINKING') return null;
    
    switch (status) {
      case 'success':
        return <CheckCircle2 size={14} className="text-green-500/80" />;
      case 'error':
        return (
          <button 
            onClick={() => retryNode(node.client_node_id)}
            className="flex items-center gap-1 text-red-400 hover:text-red-300 transition-colors"
          >
            <AlertCircle size={14} />
            <span className="text-[10px] font-mono">重试</span>
            <RotateCcw size={10} />
          </button>
        );
      case 'syncing':
        return <Loader2 size={14} className="text-accent animate-spin" />;
      default:
        return null;
    }
  };

  return (
    <motion.div
      initial={{ opacity: 0, x: -10 }}
      whileInView={{ opacity: 1, x: 0 }}
      viewport={{ once: true, margin: "0px 0px -50px 0px" }} // Clip animation for non-visible nodes
      layout={isLargeTree ? "position" : true} // Use lighter "position-only" layout for large trees
      transition={{ 
        delay: depth * 0.1, 
        duration: 0.5, 
        ease: [0.22, 1, 0.36, 1] 
      }}
      className={clsx(
        "flex flex-col w-full relative task-tree-node",
        depth > 0 && "ml-8 mt-6"
      )}
    >
      {/* Growth Line */}
      {depth > 0 && (
        <div className="absolute -left-4 top-0 bottom-0 w-[1px] bg-gradient-to-b from-muted via-muted/50 to-transparent" />
      )}

      <div className="flex items-start gap-4 group">
        <div 
          className={clsx(
            "mt-1.5 shrink-0 transition-transform duration-500",
            isGroup ? "text-accent-foreground cursor-pointer" : "text-muted-foreground/40",
            status === 'success' && "text-green-500/50",
            status === 'error' && "text-red-500/50"
          )}
          onClick={() => isGroup && setIsExpanded(!isExpanded)}
        >
          {isGroup ? (
            <motion.div 
              animate={{ rotate: isExpanded ? 90 : 0 }}
              transition={{ duration: 0.2 }}
              className="w-5 h-5 flex items-center justify-center rounded-sm bg-accent/20 border border-accent/30 group-hover:scale-110 transition-transform"
            >
              <ChevronRight size={14} className="text-accent-foreground" />
            </motion.div>
          ) : (
            <div className="w-5 h-5 flex items-center justify-center group-hover:scale-110 transition-transform">
              <Circle size={8} fill="currentColor" />
            </div>
          )}
        </div>
        
        <div className="flex-1 space-y-1.5 pb-2">
          <div className="flex items-center justify-between gap-4">
            <h4 
              className={clsx(
                "tracking-tight transition-colors",
                isGroup ? "text-lg font-medium text-foreground cursor-pointer" : "text-base font-normal text-foreground/80",
                status === 'success' && "text-foreground/40 line-through decoration-muted-foreground/30"
              )}
              onClick={() => isGroup && setIsExpanded(!isExpanded)}
            >
              {node.title}
            </h4>
            
            <div className="flex items-center gap-3">
              {renderStatus()}
              <div className="flex items-center gap-1.5 px-2 py-0.5 rounded-full bg-muted/30 text-[9px] font-mono text-muted-foreground/60 border border-muted/20">
                <Clock size={10} />
                <span>{node.estimated_minutes}分钟</span>
              </div>
            </div>
          </div>
          
          {node.description && (
            <p className={clsx(
              "text-xs font-light leading-relaxed max-w-lg transition-colors",
              status === 'success' ? "text-muted-foreground/20" : "text-muted-foreground/50"
            )}>
              {node.description}
            </p>
          )}
        </div>
      </div>

      <AnimatePresence initial={false}>
        {hasChildren && isExpanded && (
          <motion.div 
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: 'auto', opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            transition={{ duration: 0.3, ease: "easeInOut" }}
            className="flex flex-col overflow-hidden"
          >
            {node.children!.map((child) => (
              <TaskTreeVisualizer key={child.client_node_id} node={child} depth={depth + 1} />
            ))}
          </motion.div>
        )}
      </AnimatePresence>
    </motion.div>
  );
};

export const TaskTreeRoot: React.FC = () => {
  const { taskTree, appState } = useAppStore();

  if (!taskTree || (appState !== 'PENDING' && appState !== 'SYNCING' && appState !== 'SUCCESS' && appState !== 'PARTIAL_ERROR')) {
    return null;
  }

  return (
    <div className="w-full flex flex-col items-center mt-12 pb-40">
      <div className="w-full max-w-xl px-2 mb-8">
        <h3 className="text-xs font-mono text-muted-foreground/40 tracking-widest mb-2">
          建议行动计划
        </h3>
        <p className="text-lg font-light text-foreground/80 leading-snug">
          {taskTree.summary}
        </p>
      </div>
      
      <div className="w-full max-w-xl px-2">
        <TaskTreeVisualizer node={taskTree.root} />
      </div>

      {/* "End of Plan" Decorator */}
      <motion.div
        initial={{ opacity: 0 }}
        animate={{ opacity: 1 }}
        transition={{ delay: 1, duration: 1 }}
        className="mt-24 flex flex-col items-center gap-4"
      >
        <div className="w-px h-12 bg-gradient-to-b from-muted to-transparent" />
        <span className="text-[10px] font-mono tracking-widest text-muted-foreground/30">
          计划结束
        </span>
      </motion.div>
    </div>
  );
};
