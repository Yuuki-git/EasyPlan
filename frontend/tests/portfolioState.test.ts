import { describe, test, expect } from 'vitest';
import { selectPortfolioCard, PortfolioProject } from '../src/store/portfolioState';
import { ThreadSnapshot, TaskResponse, StrategyContext } from '../src/types/api';

describe('selectPortfolioCard selector tests', () => {
  // 1. AI project with current phase, two completed actions out of five, and a Next Action.
  test('AI project with current phase, progress and next action', () => {
    const project: PortfolioProject = {
      id: 'proj-1',
      title: 'AI Product Career Transition',
      source: 'ai'
    };

    const snapshot: ThreadSnapshot = {
      thread_id: 'proj-1',
      status: 'succeeded',
      state_version: 1,
      last_event_id: null,
      server_time: '2026-07-04T00:00:00Z',
      intent_text: 'Transition to PM',
      task_tree: {
        root: { client_node_id: 'root', title: 'Root', verb: 'do', estimated_minutes: 0, node_type: 'group' },
        summary: 'Transition plan',
        planning_context: {
          schema_version: 1,
          intent_type: 'exploration_decision',
          time_horizon: 'weeks',
          roadmap: [
            { phase_id: 'phase-1', order: 1, title: '验证岗位匹配度', objective: 'Verify fit', status: 'current' }
          ],
          current_phase: {
            phase_id: 'phase-1',
            title: '验证岗位匹配度',
            objective: 'Verify fit',
            completion_rule: 'all_ai_actions_completed'
          },
          next_action_client_node_id: 'action-2'
        }
      }
    };

    const tasks: TaskResponse[] = [
      // 5 AI actions total in this phase
      { id: 't1', client_node_id: 'action-1', thread_id: 'proj-1', phase_id: 'phase-1', source: 'ai', node_type: 'action', status: 'completed', phase_order: 1, sort_order: 1, title: '完成自评' },
      { id: 't2', client_node_id: 'action-2', thread_id: 'proj-1', phase_id: 'phase-1', source: 'ai', node_type: 'action', status: 'active', phase_order: 1, sort_order: 2, title: '访谈一位产品经理' },
      { id: 't3', client_node_id: 'action-3', thread_id: 'proj-1', phase_id: 'phase-1', source: 'ai', node_type: 'action', status: 'active', phase_order: 1, sort_order: 3, title: '阅读书籍' },
      { id: 't4', client_node_id: 'action-4', thread_id: 'proj-1', phase_id: 'phase-1', source: 'ai', node_type: 'action', status: 'completed', phase_order: 1, sort_order: 4, title: '完成调研' },
      { id: 't5', client_node_id: 'action-5', thread_id: 'proj-1', phase_id: 'phase-1', source: 'ai', node_type: 'action', status: 'active', phase_order: 1, sort_order: 5, title: '总结报告' }
    ];

    const view = selectPortfolioCard(project, snapshot, tasks);
    expect(view.projectId).toBe('proj-1');
    expect(view.title).toBe('AI Product Career Transition');
    expect(view.typeLabel).toBe('探索决策');
    expect(view.currentPhaseLabel).toBe('验证岗位匹配度');
    expect(view.progressLabel).toBe('2 / 5');
    expect(view.nextActionLabel).toBe('访谈一位产品经理');
    expect(view.snapshotAvailable).toBe(true);
  });

  // 2. Completed phase with no Next Action.
  test('Completed phase with no Next Action', () => {
    const project: PortfolioProject = {
      id: 'proj-2',
      title: 'Completed Project',
      source: 'ai'
    };

    const snapshot: ThreadSnapshot = {
      thread_id: 'proj-2',
      status: 'succeeded',
      state_version: 1,
      last_event_id: null,
      server_time: '2026-07-04T00:00:00Z',
      intent_text: 'Completed target',
      task_tree: {
        root: { client_node_id: 'root', title: 'Root', verb: 'do', estimated_minutes: 0, node_type: 'group' },
        summary: 'Summary',
        planning_context: {
          schema_version: 1,
          intent_type: 'long_term_growth',
          time_horizon: 'weeks',
          roadmap: [
            { phase_id: 'phase-1', order: 1, title: 'Phase 1', objective: 'Obj 1', status: 'completed' }
          ],
          current_phase: {
            phase_id: 'phase-1',
            title: 'Phase 1',
            objective: 'Obj 1',
            completion_rule: 'all_ai_actions_completed'
          },
          next_action_client_node_id: null
        }
      }
    };

    const tasks: TaskResponse[] = [
      { id: 't1', client_node_id: 'action-1', thread_id: 'proj-2', phase_id: 'phase-1', source: 'ai', node_type: 'action', status: 'completed', phase_order: 1, sort_order: 1, title: 'Action 1' }
    ];

    const view = selectPortfolioCard(project, snapshot, tasks);
    expect(view.currentPhaseLabel).toBe('Phase 1');
    expect(view.progressLabel).toBe('1 / 1');
    expect(view.nextActionLabel).toBe('当前阶段已完成');
  });

  // 3. Manual project without planning_context.
  test('Manual project without planning_context', () => {
    const project: PortfolioProject = {
      id: 'proj-3',
      title: 'My Manual Plan',
      source: 'manual'
    };

    const snapshot: ThreadSnapshot = {
      thread_id: 'proj-3',
      status: 'succeeded',
      state_version: 1,
      last_event_id: null,
      server_time: '2026-07-04T00:00:00Z',
      intent_text: 'Manual text',
      task_tree: {
        root: { client_node_id: 'root', title: 'Root', verb: 'do', estimated_minutes: 0, node_type: 'group' },
        summary: 'Summary'
      }
    };

    const view = selectPortfolioCard(project, snapshot, []);
    expect(view.typeLabel).toBe('手动计划');
    expect(view.currentPhaseLabel).toBe('尚未建立阶段');
    expect(view.progressLabel).toBe(null);
    expect(view.nextActionLabel).toBe('暂无下一步动作');
  });

  // 4. AI project without planning_context.
  test('AI project without planning_context', () => {
    const project: PortfolioProject = {
      id: 'proj-4',
      title: 'Direct AI Plan',
      source: 'ai'
    };

    const snapshot: ThreadSnapshot = {
      thread_id: 'proj-4',
      status: 'succeeded',
      state_version: 1,
      last_event_id: null,
      server_time: '2026-07-04T00:00:00Z',
      intent_text: 'Direct text',
      task_tree: {
        root: { client_node_id: 'root', title: 'Root', verb: 'do', estimated_minutes: 0, node_type: 'group' },
        summary: 'Summary'
      }
    };

    const view = selectPortfolioCard(project, snapshot, []);
    expect(view.typeLabel).toBe('直接计划');
    expect(view.currentPhaseLabel).toBe('尚未建立阶段');
    expect(view.progressLabel).toBe(null);
    expect(view.nextActionLabel).toBe('暂无下一步动作');
  });

  // 5. Missing snapshot.
  test('Missing snapshot', () => {
    const project: PortfolioProject = {
      id: 'proj-5',
      title: 'Missing Snapshot Project',
      source: 'ai'
    };

    const view = selectPortfolioCard(project, undefined, []);
    expect(view.projectId).toBe('proj-5');
    expect(view.title).toBe('Missing Snapshot Project');
    expect(view.typeLabel).toBe('直接计划');
    expect(view.currentPhaseLabel).toBe('尚未建立阶段');
    expect(view.progressLabel).toBe(null);
    expect(view.nextActionLabel).toBe('暂无下一步动作');
    expect(view.snapshotAvailable).toBe(false);
  });

  // 6. AI project with schema v2 and practice loops progress.
  test('AI project with schema v2 and practice loops progress', () => {
    const project: PortfolioProject = {
      id: 'proj-v2',
      title: 'Long-term practice project',
      source: 'ai'
    };

    const snapshot: ThreadSnapshot = {
      thread_id: 'proj-v2',
      status: 'succeeded',
      state_version: 1,
      last_event_id: null,
      server_time: '2026-07-04T00:00:00Z',
      intent_text: 'V2 project',
      task_tree: {
        root: { client_node_id: 'root', title: 'Root', verb: 'do', estimated_minutes: 0, node_type: 'group' },
        summary: 'V2 plan',
        planning_context: {
          schema_version: 2,
          intent_type: 'long_term_growth',
          time_horizon: 'months',
          roadmap: [
            { phase_id: 'phase-1', order: 1, title: 'Phase 1', objective: 'Obj 1', status: 'current' }
          ],
          current_phase: {
            phase_id: 'phase-1',
            title: 'Phase 1',
            objective: 'Obj 1',
            completion_rule: 'long_term_execution_gate',
            estimated_duration_weeks: 4
          },
          next_action_client_node_id: null
        }
      },
      long_term_execution: {
        phase_id: 'phase-1',
        recommendation: 'partial',
        review_available: false,
        one_off_ready: true,
        process_ready: false,
        outcome_ready: false,
        loops: [
          {
            loop_id: 'loop-1',
            loop_key: 'l1',
            title: 'Practice 1',
            done_criteria: 'criteria 1',
            target_per_week: 3,
            current_week_completed: 1,
            total_completed: 4,
            required_completions: 10,
            estimated_end: '2026-08-01',
            status: 'active',
            can_schedule_today: true,
            active_occurrence_task_id: null
          }
        ],
        active_review: null,
        latest_finalized_review: null,
        review_history: []
      }
    };

    const view = selectPortfolioCard(project, snapshot, []);
    expect(view.typeLabel).toBe('长期成长');
    expect(view.currentPhaseLabel).toBe('Phase 1');
    expect(view.progressLabel).toBe('练习 4 / 10');
  });

  // 7. AI project with malformed strategy_context (valid discriminator but missing fields)
  test('AI project with malformed strategy_context does not crash and falls back', () => {
    const project: PortfolioProject = {
      id: 'proj-malformed',
      title: 'Malformed project',
      source: 'ai'
    };

    const snapshot: ThreadSnapshot = {
      thread_id: 'proj-malformed',
      status: 'succeeded',
      state_version: 1,
      last_event_id: null,
      server_time: '2026-07-04T00:00:00Z',
      intent_text: 'Malformed context',
      task_tree: {
        root: { client_node_id: 'root', title: 'Root', verb: 'do', estimated_minutes: 0, node_type: 'group' },
        summary: 'Standard summary fallback',
        strategy_context: {
          strategy_type: 'delivery'
        } as unknown as StrategyContext
      }
    };

    const view = selectPortfolioCard(project, snapshot, []);
    expect(view.typeLabel).toBe('直接计划'); // falls back to default
    expect(view.currentPhaseLabel).toBe('尚未建立阶段');
    expect(view.progressLabel).toBeNull();
  });
});
