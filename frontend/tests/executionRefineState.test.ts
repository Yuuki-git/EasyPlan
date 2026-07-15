// @vitest-environment jsdom
import { describe, test, expect, beforeEach } from 'vitest';
import { useAppStore } from '../src/store/useAppStore';
import { mergeApplyReceipt, getExecutionRefineModeLabel } from '../src/lib/executionRefine';
import { TaskResponse, ExecutionRefineProposal } from '../src/types/api';

describe('Execution Refine Zustand Store and helper functions tests', () => {
  beforeEach(() => {
    useAppStore.getState().reset();
    useAppStore.getState().resetExecutionRefine();
  });

  // 1. Initial State and Setters Test
  test('initial state and setters function correctly', () => {
    const store = useAppStore.getState();
    expect(store.executionRefineActiveRequestId).toBeNull();
    expect(store.executionRefineStatus).toBeNull();
    expect(store.executionRefineStage).toBeNull();
    expect(store.executionRefineProposal).toBeNull();
    expect(store.executionRefineErrorCode).toBeNull();
    expect(store.executionRefineErrorMessage).toBeNull();
    expect(store.executionRefineLogs).toEqual([]);
    expect(store.isExecutionRefinePanelOpen).toBe(false);

    // Test setters
    store.setExecutionRefineActiveRequestId('req-1234');
    store.setExecutionRefineStatus('ready');
    store.setExecutionRefineStage('ready');
    store.setExecutionRefineErrorCode('ERR_CODE');
    store.setExecutionRefineErrorMessage('Error Message');
    store.addExecutionRefineLog('Log Line 1');
    store.setExecutionRefinePanelOpen(true);

    const proposal: ExecutionRefineProposal = {
      schema_version: 1,
      proposal_type: 'execution_refine',
      mode: 'time_budget',
      summary: 'Adjusted focus and tasks',
      user_facing_reasons: ['Reason 1'],
      preserved_constraints: ['Preserve 1'],
      operations: [],
      focus_task_ids: ['task-1'],
      estimated_focus_minutes: 20,
      buffer_minutes: 5,
      warnings: ['Warning 1']
    };
    store.setExecutionRefineProposal(proposal);

    const updated = useAppStore.getState();
    expect(updated.executionRefineActiveRequestId).toBe('req-1234');
    expect(updated.executionRefineStatus).toBe('ready');
    expect(updated.executionRefineStage).toBe('ready');
    expect(updated.executionRefineErrorCode).toBe('ERR_CODE');
    expect(updated.executionRefineErrorMessage).toBe('Error Message');
    expect(updated.executionRefineLogs).toEqual(['Log Line 1']);
    expect(updated.executionRefineProposal).toEqual(proposal);
    expect(updated.isExecutionRefinePanelOpen).toBe(true);

    // Test Reset
    updated.resetExecutionRefine();
    const reseted = useAppStore.getState();
    expect(reseted.executionRefineActiveRequestId).toBeNull();
    expect(reseted.executionRefineStatus).toBeNull();
    expect(reseted.executionRefineLogs).toEqual([]);
  });

  // 2. mergeApplyReceipt
  test('mergeApplyReceipt merges updated and created tasks correctly', () => {
    const currentTasks: TaskResponse[] = [
      { id: 'task-1', user_id: 'u1', thread_id: 't1', parent_task_id: null, client_node_id: 'n1', title: 'Task 1', description: 'Old desc', node_type: 'action', status: 'active', view_bucket: 'planned', estimated_minutes: 30, sort_order: 1 }
    ];

    const updatedOrCreatedTasks: TaskResponse[] = [
      // 1 update
      { id: 'task-1', user_id: 'u1', thread_id: 't1', parent_task_id: null, client_node_id: 'n1', title: 'Task 1 (Updated)', description: 'New desc', node_type: 'action', status: 'active', view_bucket: 'planned', estimated_minutes: 15, sort_order: 1 },
      // 1 create
      { id: 'task-2', user_id: 'u1', thread_id: 't1', parent_task_id: null, client_node_id: 'n2', title: 'New Task 2', description: null, node_type: 'action', status: 'active', view_bucket: 'planned', estimated_minutes: 10, sort_order: 2, source: 'ai', created_by: 'execution_refine' }
    ];

    const merged = mergeApplyReceipt(currentTasks, updatedOrCreatedTasks);

    expect(merged.length).toBe(2);
    const t1 = merged.find(t => t.id === 'task-1')!;
    expect(t1.title).toBe('Task 1 (Updated)');
    expect(t1.description).toBe('New desc');
    expect(t1.estimated_minutes).toBe(15);

    const t2 = merged.find(t => t.id === 'task-2')!;
    expect(t2.title).toBe('New Task 2');
    expect(t2.created_by).toBe('execution_refine');
  });

  // 3. Mode formatter
  test('getExecutionRefineModeLabel formats mode strings correctly', () => {
    expect(getExecutionRefineModeLabel('time_budget')).toBe('时间预算');
    expect(getExecutionRefineModeLabel('progress_recovery')).toBe('进度恢复');
    expect(getExecutionRefineModeLabel('context_change')).toBe('条件变更');
  });
});
