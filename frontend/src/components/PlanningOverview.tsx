import React from 'react';
import { useAppStore } from '../store/useAppStore';
import { selectPlanningView } from '../store/planningState';
import { CheckCircle2, Circle, Lock, Unlock, ArrowRight } from 'lucide-react';
import { motion } from 'framer-motion';
import { RoadmapPhase } from '../types/api';

export const PlanningOverview: React.FC = () => {
  const { taskTree, boardTasks, selectedProjectId, generateNextPhasePlan, isPhaseRequestPending, intent } = useAppStore();

  if (import.meta.env.VITE_PHASE_PLANNING_ENABLED === 'false') {
    return null;
  }

  if (!taskTree || !boardTasks || !selectedProjectId) {
    return null;
  }

  const planningView = selectPlanningView(taskTree, boardTasks, selectedProjectId);
  if (!planningView) {
    return null;
  }

  const { nextAction, canUnlock, totalAiActions, completedAiActions, context } = planningView;
  const roadmap = context.roadmap;
  const currentPhase = roadmap.find((p: RoadmapPhase) => p.phase_id === context.current_phase?.phase_id);
  const currentPhaseIndex = roadmap.findIndex((p: RoadmapPhase) => p.phase_id === currentPhase?.phase_id);
  const nextPhaseNumber = currentPhaseIndex >= 0 ? currentPhaseIndex + 2 : roadmap.length + 1;

  return (
    <div className="w-full max-w-4xl mx-auto mb-8 bg-background/50 border border-muted/50 rounded-xl p-6 backdrop-blur-sm">
      <div className="flex flex-col gap-6">
        
        {/* Target & Progress */}
        <div className="flex justify-between items-start gap-4">
          <div className="flex-1">
            <h2 className="text-sm font-medium text-muted-foreground uppercase tracking-wider mb-1">Target</h2>
            <p className="text-lg text-foreground font-medium">{intent}</p>
          </div>
          <div className="flex flex-col items-end gap-2">
            <span className="text-sm text-muted-foreground">
              {completedAiActions} / {totalAiActions} Tasks
            </span>
            <div className="w-32 h-1.5 bg-muted rounded-full overflow-hidden">
              <motion.div 
                className="h-full bg-foreground/70"
                initial={{ width: 0 }}
                animate={{ width: `${totalAiActions > 0 ? (completedAiActions / totalAiActions) * 100 : 0}%` }}
                transition={{ duration: 0.5, ease: "easeOut" }}
              />
            </div>
          </div>
        </div>

        {/* Roadmap Timeline */}
        <div className="py-4 border-y border-muted/30">
          <h3 className="text-xs font-semibold text-muted-foreground uppercase tracking-widest mb-4">Roadmap</h3>
          <div className="flex items-center gap-2 overflow-x-auto pb-2 scrollbar-hide">
            {roadmap.map((phase: RoadmapPhase, idx: number) => {
              const isCompleted = phase.status === 'completed';
              const isCurrent = phase.phase_id === currentPhase?.phase_id;
              
              return (
                <React.Fragment key={phase.phase_id}>
                  <div className={`flex items-center gap-2 px-3 py-1.5 rounded-full border ${isCurrent ? 'bg-foreground/5 border-foreground/20' : 'border-transparent'}`}>
                    {isCompleted ? (
                      <CheckCircle2 size={14} className="text-foreground/50" />
                    ) : isCurrent ? (
                      <Circle size={14} className="text-foreground" />
                    ) : (
                      <Circle size={14} className="text-muted-foreground/30" />
                    )}
                    <span className={`text-sm whitespace-nowrap ${isCurrent ? 'text-foreground font-medium' : isCompleted ? 'text-foreground/60' : 'text-muted-foreground'}`}>
                      Phase {idx + 1}: {phase.title}
                    </span>
                  </div>
                  {idx < roadmap.length - 1 && (
                    <ArrowRight size={14} className="text-muted-foreground/30 shrink-0" />
                  )}
                </React.Fragment>
              );
            })}
          </div>
        </div>

        {/* Current Phase & Next Action */}
        <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
          <div className="flex flex-col gap-3">
            <h3 className="text-xs font-semibold text-muted-foreground uppercase tracking-widest">Current Phase</h3>
            {currentPhase ? (
              <div className="p-4 rounded-lg bg-foreground/5 border border-foreground/10 h-full flex flex-col justify-center">
                <h4 className="font-medium text-foreground mb-2">{currentPhase.title}</h4>
                <p className="text-sm text-muted-foreground line-clamp-2">{currentPhase.objective}</p>
              </div>
            ) : (
              <div className="p-4 rounded-lg bg-muted/20 border border-muted/50 h-full flex items-center justify-center text-sm text-muted-foreground">
                All phases completed
              </div>
            )}
          </div>

          <div className="flex flex-col gap-3">
            <h3 className="text-xs font-semibold text-muted-foreground uppercase tracking-widest">Next Action</h3>
            {nextAction ? (
              <div className="p-4 rounded-lg bg-foreground/5 border border-foreground/10 h-full flex flex-col justify-center">
                <div className="flex items-start gap-3">
                  <div className="mt-0.5">
                    <Circle size={16} className="text-foreground/60" />
                  </div>
                  <div>
                    <h4 className="font-medium text-foreground text-sm leading-tight mb-1">{nextAction.title}</h4>
                    {nextAction.description && (
                      <p className="text-xs text-muted-foreground line-clamp-1">{nextAction.description}</p>
                    )}
                  </div>
                </div>
              </div>
            ) : (
              <div className="p-4 rounded-lg bg-muted/20 border border-muted/50 h-full flex items-center justify-center text-sm text-muted-foreground">
                No immediate action
              </div>
            )}
          </div>
        </div>

        {/* Unlock Button */}
        <div className="flex justify-end mt-2">
          <button
            onClick={generateNextPhasePlan}
            disabled={!canUnlock || isPhaseRequestPending}
            className={`flex items-center gap-2 px-4 py-2 rounded-lg text-sm font-medium transition-all ${
              canUnlock && !isPhaseRequestPending
                ? 'bg-foreground text-background hover:bg-foreground/90 shadow-sm cursor-pointer'
                : 'bg-muted text-muted-foreground cursor-not-allowed opacity-50'
            }`}
          >
            {canUnlock && !isPhaseRequestPending ? <Unlock size={16} /> : <Lock size={16} />}
            {isPhaseRequestPending ? 'Generating...' : `Unlock Phase ${nextPhaseNumber}`}
          </button>
        </div>

      </div>
    </div>
  );
};
