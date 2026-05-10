import React, { useState } from 'react';
import { motion } from 'framer-motion';
import { useAppStore } from '../store/useAppStore';
import { Sun, Calendar, Menu, Plus, CheckCircle2, Circle } from 'lucide-react';
import { clsx } from 'clsx';
import { TaskNode, TaskTree } from '../types/api';

const Sidebar: React.FC<{ isOpen: boolean; toggle: () => void }> = ({ isOpen }) => {
  return (
    <motion.div
      initial={{ width: 240 }}
      animate={{ width: isOpen ? 240 : 0, opacity: isOpen ? 1 : 0 }}
      className="h-full bg-background/50 border-r border-muted/30 backdrop-blur-md overflow-hidden shrink-0"
    >
      <div className="w-[240px] p-4 flex flex-col h-full">
        <div className="flex items-center justify-between mb-8">
          <span className="font-medium text-foreground/80 tracking-wide px-2">Views</span>
        </div>
        
        <div className="space-y-1 flex-1">
          <button className="w-full flex items-center gap-3 px-3 py-2 rounded-lg bg-accent/20 text-accent-foreground font-medium transition-colors">
            <Sun size={16} className="text-amber-500" />
            <span>我的一天 (My Day)</span>
          </button>
          <button className="w-full flex items-center gap-3 px-3 py-2 rounded-lg text-muted-foreground hover:bg-muted/30 transition-colors">
            <Calendar size={16} className="text-blue-500" />
            <span>计划中 (Planned)</span>
          </button>
        </div>
      </div>
    </motion.div>
  );
};

const BoardTaskNode: React.FC<{ node: TaskNode; depth?: number }> = ({ node, depth = 0 }) => {
  const isGroup = node.node_type === 'group';
  const hasChildren = node.children && node.children.length > 0;
  const [completed, setCompleted] = useState(false);

  if (isGroup) {
    return (
      <div className={clsx("mb-6", depth > 0 && "ml-4")}>
        <h2 className="text-xl font-medium text-foreground tracking-tight mb-3">
          {node.title}
        </h2>
        {hasChildren && (
          <div className="flex flex-col space-y-1">
            {node.children!.map(child => (
              <BoardTaskNode key={child.client_node_id} node={child} depth={depth + 1} />
            ))}
          </div>
        )}
      </div>
    );
  }

  // Action Node
  return (
    <motion.div 
      layout
      className={clsx(
        "group flex items-start gap-3 p-3 rounded-xl border transition-all cursor-pointer",
        completed 
          ? "bg-muted/10 border-transparent" 
          : "bg-background border-muted/50 hover:border-muted hover:shadow-sm",
        depth > 0 && "ml-4"
      )}
      onClick={() => setCompleted(!completed)}
    >
      <div className="mt-0.5 shrink-0">
        {completed ? (
          <CheckCircle2 size={18} className="text-green-500" />
        ) : (
          <Circle size={18} className="text-muted-foreground/30 group-hover:text-foreground/50 transition-colors" />
        )}
      </div>
      <div className="flex-1">
        <h4 className={clsx(
          "text-base transition-colors",
          completed ? "text-muted-foreground/50 line-through decoration-muted-foreground/30" : "text-foreground/90 font-medium"
        )}>
          {node.title}
        </h4>
        {node.description && (
          <p className={clsx(
            "text-xs mt-1 transition-colors",
            completed ? "text-muted-foreground/30 line-through" : "text-muted-foreground/60"
          )}>
            {node.description}
          </p>
        )}
        {!completed && (
          <div className="flex items-center gap-2 mt-2">
            <span className="text-[10px] font-mono text-muted-foreground/50 bg-muted/20 px-2 py-0.5 rounded-full">
              {node.estimated_minutes} min
            </span>
          </div>
        )}
      </div>
    </motion.div>
  );
};

const MOCK_TASK_TREE: TaskTree = {
  summary: "为您生成的周末露营计划已就绪。这包含了从准备到返程的完整步骤，旨在确保您享受一个无压力且充满乐趣的自然之旅。",
  root: {
    client_node_id: "root-1",
    title: "策划周末露营",
    verb: "plan",
    estimated_minutes: 0,
    node_type: "group",
    children: [
      {
        client_node_id: "group-1",
        title: "第一阶段：物资筹备与确认",
        verb: "prepare",
        estimated_minutes: 0,
        node_type: "group",
        children: [
          {
            client_node_id: "action-1",
            title: "检查帐篷、睡袋与防潮垫的完好性",
            description: "确保帐篷拉链顺滑无破损，睡袋保暖级别适合当季气温。",
            verb: "check",
            estimated_minutes: 15,
            node_type: "action"
          },
          {
            client_node_id: "action-2",
            title: "列出食材清单并完成采购",
            description: "重点购买易于保存且饱腹的食材，如全麦面包、牛肉干和速溶咖啡。",
            verb: "buy",
            estimated_minutes: 45,
            node_type: "action"
          },
          {
            client_node_id: "action-3",
            title: "准备急救包与防蚊虫用品",
            description: "包括创可贴、碘伏、抗组胺药膏及长效驱蚊液。",
            verb: "pack",
            estimated_minutes: 10,
            node_type: "action"
          }
        ]
      },
      {
        client_node_id: "group-2",
        title: "第二阶段：路线规划与营地预定",
        verb: "plan",
        estimated_minutes: 0,
        node_type: "group",
        children: [
          {
            client_node_id: "action-4",
            title: "确认国家公园的露营地配额并预订",
            description: "需提前在线支付费用，获取确认邮件以便入场查验。",
            verb: "book",
            estimated_minutes: 20,
            node_type: "action"
          },
          {
            client_node_id: "action-5",
            title: "下载离线地图并设定导航点",
            description: "山区可能无信号，确保手机已下载完整区域的离线地图数据。",
            verb: "download",
            estimated_minutes: 5,
            node_type: "action"
          }
        ]
      }
    ]
  }
};

export const TaskBoard: React.FC = () => {
  const { taskTree, reset } = useAppStore();
  const [sidebarOpen, setSidebarOpen] = useState(true);

  const displayTree = taskTree || MOCK_TASK_TREE;

  if (!displayTree) return null;

  return (
    <motion.div 
      initial={{ opacity: 0, x: 20 }}
      animate={{ opacity: 1, x: 0 }}
      exit={{ opacity: 0, x: 20 }}
      transition={{ duration: 0.5, ease: [0.22, 1, 0.36, 1] }}
      className="fixed inset-0 bg-background flex z-40"
    >
      <Sidebar isOpen={sidebarOpen} toggle={() => setSidebarOpen(!sidebarOpen)} />
      
      <div className="flex-1 flex flex-col h-full overflow-hidden">
        <header className="h-16 border-b border-muted/20 flex items-center px-4 shrink-0 bg-background/80 backdrop-blur-sm z-10 justify-between">
          <div className="flex items-center gap-4">
            <button 
              onClick={() => setSidebarOpen(!sidebarOpen)}
              className="text-muted-foreground hover:text-foreground transition-colors p-2 rounded-lg hover:bg-muted/20"
            >
              <Menu size={20} />
            </button>
            <h1 className="text-xl font-medium tracking-tight text-foreground">
              ☀️ 我的一天
            </h1>
          </div>
          
          <div className="flex items-center gap-4">
            <button 
              onClick={() => {
                reset();
                useAppStore.getState().setView('input');
              }}
              className="text-xs text-muted-foreground hover:text-foreground transition-colors px-3 py-1.5 rounded-full hover:bg-muted/20"
            >
              新计划
            </button>
          </div>
        </header>
        
        <main className="flex-1 overflow-y-auto p-8 lg:px-24">
          <div className="max-w-3xl mx-auto pb-32">
            <div className="mb-12">
              <h3 className="text-xs font-mono text-muted-foreground/40 tracking-widest uppercase mb-4">
                Generated Plan
              </h3>
              <p className="text-muted-foreground/80 leading-relaxed">
                {displayTree.summary}
              </p>
            </div>
            
            <BoardTaskNode node={displayTree.root} />
            
            <button className="mt-8 flex items-center gap-2 text-muted-foreground/50 hover:text-foreground/80 transition-colors py-2 group">
              <div className="p-1 rounded-full group-hover:bg-muted/20 transition-colors">
                <Plus size={16} />
              </div>
              <span className="text-sm">添加任务...</span>
            </button>
          </div>
        </main>
      </div>
    </motion.div>
  );
};
