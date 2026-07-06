import { describe, expect, it } from 'vitest';
import { selectLongTermExecutionView } from '../src/store/longTermExecution';
import { LongTermExecutionSnapshot, ThreadSnapshot } from '../src/types/api';

// Helper fixtures
function v1Snapshot(): ThreadSnapshot {
  return {
    thread_id: 'thread-v1',
    status: 'succeeded',
    state_version: 1,
    last_event_id: null,
    server_time: '2026-07-05T00:00:00Z',
    intent_text: 'V1 goal',
    task_tree: {
      root: { client_node_id: 'root', title: 'Root', verb: 'do', estimated_minutes: 0, node_type: 'group' },
      summary: 'V1 plan',
      planning_context: {
        schema_version: 1,
        intent_type: 'long_term_growth',
        time_horizon: 'weeks',
        roadmap: [],
        current_phase: null,
        next_action_client_node_id: null
      }
    }
  };
}

function v2Snapshot(longTermExecution: LongTermExecutionSnapshot): ThreadSnapshot {
  return {
    thread_id: 'thread-v2',
    status: 'succeeded',
    state_version: 1,
    last_event_id: null,
    server_time: '2026-07-05T00:00:00Z',
    intent_text: 'V2 goal',
    task_tree: {
      root: { client_node_id: 'root', title: 'Root', verb: 'do', estimated_minutes: 0, node_type: 'group' },
      summary: 'V2 plan',
      planning_context: {
        schema_version: 2,
        intent_type: 'long_term_growth',
        time_horizon: 'months',
        roadmap: [],
        current_phase: {
          phase_id: 'phase-1',
          title: 'Phase 1',
          objective: 'Objective 1',
          completion_rule: 'long_term_execution_gate',
          estimated_duration_weeks: 4
        },
        next_action_client_node_id: null
      }
    },
    long_term_execution: longTermExecution
  };
}

describe('selectLongTermExecutionView', () => {
  it('returns null for schema version 1', () => {
    expect(selectLongTermExecutionView(v1Snapshot())).toBeNull();
  });

  it('returns null if long_term_execution is missing in schema version 2', () => {
    expect(selectLongTermExecutionView(v2Snapshot(null))).toBeNull();
  });

  it('uses backend readiness instead of local task count', () => {
    const view = selectLongTermExecutionView(v2Snapshot({
      phase_id: 'phase-1',
      recommendation: 'ready',
      review_available: true,
      one_off_ready: true,
      process_ready: true,
      outcome_ready: true,
      loops: [
        {
          loop_id: 'loop-1',
          loop_key: 'n3_vocab',
          title: '完成一次 N3 词汇练习',
          done_criteria: '完成 20 道题',
          target_per_week: 3,
          current_week_completed: 2,
          total_completed: 5,
          required_completions: 8,
          estimated_end: '2026-08-02',
          status: 'active',
          can_schedule_today: true,
          active_occurrence_task_id: null
        }
      ],
      active_review: null,
      latest_finalized_review: null,
      review_history: []
    }));

    expect(view).not.toBeNull();
    expect(view?.canReview).toBe(true);
    expect(view?.loops[0].weeklyLabel).toBe('本周 2 / 3 次');
    expect(view?.loops[0].totalLabel).toBe('总计 5 / 8 次');
    expect(view?.loops[0].canScheduleToday).toBe(true);
  });
});
