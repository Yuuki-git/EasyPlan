import React, { useEffect } from 'react';
import { useAppStore } from '../store/useAppStore';
import { Folder, ArrowRight } from 'lucide-react';
import { motion } from 'framer-motion';
import { selectPortfolioCard, PortfolioProject } from '../store/portfolioState';
import { TaskResponse } from '../types/api';

interface PortfolioOverviewProps {
  projects: PortfolioProject[];
  tasks: TaskResponse[];
}

export const PortfolioOverview: React.FC<PortfolioOverviewProps> = ({ projects, tasks }) => {
  const {
    projectSnapshots,
    fetchProjectSnapshots,
    setSelectedProjectId,
    setCurrentViewBucket,
    highlightedProjectId,
    setHighlightedProjectId
  } = useAppStore();

  const projectIdsKey = projects
    .map((project) => project.id)
    .sort()
    .join('|');

  // Load project snapshots when mounting or when projects list changes
  useEffect(() => {
    void fetchProjectSnapshots();
  }, [fetchProjectSnapshots, projectIdsKey]);

  // Automatically clear highlight after 4 seconds
  useEffect(() => {
    if (highlightedProjectId) {
      const timer = setTimeout(() => {
        setHighlightedProjectId(null);
      }, 4000);
      return () => clearTimeout(timer);
    }
  }, [highlightedProjectId]);

  const getIntentTag = (typeLabel: string) => {
    switch (typeLabel) {
      case '长期成长':
        return { text: '长期成长', className: 'bg-emerald-500/10 text-emerald-400 border-emerald-500/20' };
      case '探索决策':
        return { text: '探索决策', className: 'bg-amber-500/10 text-amber-400 border-amber-500/20' };
      case '短期交付':
        return { text: '短期交付', className: 'bg-blue-500/10 text-blue-400 border-blue-500/20' };
      case '手动计划':
        return { text: '手动计划', className: 'bg-purple-500/10 text-purple-400 border-purple-500/20' };
      default:
        return { text: typeLabel, className: 'bg-blue-500/10 text-blue-400 border-blue-500/20' };
    }
  };

  return (
    <div className="space-y-6">
      <div className="mb-8">
        <h2 className="text-2xl font-semibold text-foreground/90 tracking-tight">想法画布</h2>
        <p className="text-sm text-muted-foreground mt-1">掌控所有想法的规划进度与下一行动，点击卡片进入详细计划。</p>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
        {projects.map((project) => {
          const snapshot = projectSnapshots[project.id];
          const card = selectPortfolioCard(project, snapshot, tasks);
          const isHighlighted = highlightedProjectId === project.id;
          const tag = getIntentTag(card.typeLabel);

          return (
            <motion.div
              key={project.id}
              onClick={() => {
                setCurrentViewBucket('planned');
                setSelectedProjectId(project.id);
              }}
              whileHover={{ y: -4, scale: 1.01 }}
              transition={{ duration: 0.2 }}
              className={`p-6 rounded-2xl border cursor-pointer flex flex-col justify-between min-h-[170px] transition-all duration-300 relative overflow-hidden group select-none ${
                isHighlighted
                  ? 'border-accent bg-accent/10 shadow-lg shadow-accent/5 ring-1 ring-accent'
                  : 'border-muted/50 bg-background/30 hover:border-foreground/20 hover:bg-background/50 hover:shadow-md'
              }`}
            >
              {isHighlighted && (
                <div className="absolute top-0 right-0 px-3 py-1 bg-accent text-accent-foreground text-[10px] font-medium rounded-bl-xl tracking-wider">
                  刚刚创建
                </div>
              )}

              <div className="space-y-4">
                <div className="flex items-start justify-between gap-3">
                  <div className="flex items-center gap-2 max-w-[70%]">
                    <Folder size={16} className="text-muted-foreground group-hover:text-foreground transition-colors shrink-0" />
                    <h3 className="font-semibold text-base text-foreground/90 tracking-tight line-clamp-1 group-hover:text-foreground transition-colors">
                      {card.title}
                    </h3>
                  </div>
                  <span className={`text-[10px] font-medium px-2 py-0.5 rounded-full border ${tag.className} shrink-0`}>
                    {tag.text}
                  </span>
                </div>

                <div className="space-y-1">
                  <span className="text-[10px] font-medium text-muted-foreground uppercase tracking-wider block">当前阶段</span>
                  <p className="text-sm font-semibold text-foreground line-clamp-1">{card.currentPhaseLabel}</p>
                </div>
              </div>

              <div className="mt-4 pt-4 border-t border-muted/20 flex items-center justify-between gap-3">
                <div className="flex items-center gap-2 text-xs text-muted-foreground max-w-[75%]">
                  {card.progressLabel && (
                    <span className="bg-foreground/5 px-2 py-0.5 rounded text-[10px] font-mono border border-foreground/10 shrink-0 text-foreground">
                      {card.progressLabel}
                    </span>
                  )}
                  <span className="truncate" title={card.nextActionLabel}>
                    {card.nextActionLabel}
                  </span>
                </div>

                <div className="flex items-center gap-1 text-xs font-medium text-muted-foreground group-hover:text-foreground transition-all shrink-0">
                  <span>进入项目</span>
                  <ArrowRight size={12} className="transform group-hover:translate-x-1 transition-transform" />
                </div>
              </div>
            </motion.div>
          );
        })}
      </div>
    </div>
  );
};
