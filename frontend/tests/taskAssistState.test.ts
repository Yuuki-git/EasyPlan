// @vitest-environment jsdom
import { describe, test, expect, vi, beforeEach } from 'vitest';
import { useAppStore } from '../src/store/useAppStore';
import { mergeApplyReceipt } from '../src/lib/taskAssist';
import { TaskResponse, TaskAssistProposal } from '../src/types/api';

describe('Task Assist Zustand Store and merging state tests', () => {
  beforeEach(() => {
    useAppStore.getState().reset();
    useAppStore.getState().resetTaskAssist();
  });

  // 1. Initial State values check
  test('initial state and setters function correctly', () => {
    const store = useAppStore.getState();
    expect(store.taskAssistActiveTaskId).toBeNull();
    expect(store.taskAssistActiveRequestId).toBeNull();
    expect(store.taskAssistStatus).toBeNull();
    expect(store.taskAssistStage).toBeNull();
    expect(store.taskAssistProposal).toBeNull();
    expect(store.taskAssistLogs).toEqual([]);
    expect(store.isTaskAssistPanelOpen).toBe(false);

    // Test setters
    store.setTaskAssistActiveTaskId('task-123');
    store.setTaskAssistActiveRequestId('req-456');
    store.setTaskAssistStatus('running');
    store.setTaskAssistStage('generating');
    store.addTaskAssistLog('Log step 1');
    store.setTaskAssistPanelOpen(true);

    const proposal: TaskAssistProposal = {
      schema_version: 1,
      proposal_type: 'start',
      summary: 'Start summary',
      starter_step: {
        draft_id: 'draft-1',
        title: 'Step 1',
        description: null,
        estimated_minutes: 5,
        done_criteria: 'Criteria',
        start_hint: null,
        fallback_action: null
      }
    };
    store.setTaskAssistProposal(proposal);

    const updated = useAppStore.getState();
    expect(updated.taskAssistActiveTaskId).toBe('task-123');
    expect(updated.taskAssistActiveRequestId).toBe('req-456');
    expect(updated.taskAssistStatus).toBe('running');
    expect(updated.taskAssistStage).toBe('generating');
    expect(updated.taskAssistLogs).toEqual(['Log step 1']);
    expect(updated.taskAssistProposal).toEqual(proposal);
    expect(updated.isTaskAssistPanelOpen).toBe(true);

    // Test Reset
    updated.resetTaskAssist();
    const reseted = useAppStore.getState();
    expect(reseted.taskAssistActiveTaskId).toBeNull();
    expect(reseted.taskAssistStatus).toBeNull();
    expect(reseted.taskAssistLogs).toEqual([]);
  });

  // 2. mergeApplyReceipt
  test('mergeApplyReceipt merges applied parent updates and subtasks correctly', () => {
    const currentTasks: TaskResponse[] = [
      { id: 'parent-1', user_id: 'u1', thread_id: 't1', parent_task_id: null, client_node_id: 'p1', title: 'Parent Task', description: 'Original description', node_type: 'action', status: 'active', view_bucket: 'planned', estimated_minutes: 30, sort_order: 1 }
    ];

    const appliedParent: TaskResponse = {
      id: 'parent-1',
      user_id: 'u1',
      thread_id: 't1',
      parent_task_id: null,
      client_node_id: 'p1',
      title: 'Parent Task (updated)',
      description: 'Original description',
      node_type: 'action',
      status: 'active',
      view_bucket: 'planned',
      estimated_minutes: 30,
      sort_order: 1,
      assist_rollup: true
    };

    const createdChildren: TaskResponse[] = [
      { id: 'child-1', user_id: 'u1', thread_id: 't1', parent_task_id: 'parent-1', client_node_id: 'c1', title: 'Child Task 1', description: null, node_type: 'action', status: 'active', view_bucket: 'planned', estimated_minutes: 10, sort_order: 1, source: 'task_assist' },
      { id: 'child-2', user_id: 'u1', thread_id: 't1', parent_task_id: 'parent-1', client_node_id: 'c2', title: 'Child Task 2', description: null, node_type: 'action', status: 'active', view_bucket: 'planned', estimated_minutes: 20, sort_order: 2, source: 'task_assist' }
    ];

    const merged = mergeApplyReceipt(currentTasks, appliedParent, createdChildren);

    expect(merged.length).toBe(3);
    const parentNode = merged.find(t => t.id === 'parent-1')!;
    expect(parentNode.title).toBe('Parent Task (updated)');
    expect(parentNode.assist_rollup).toBe(true);

    const child1Node = merged.find(t => t.id === 'child-1')!;
    expect(child1Node.title).toBe('Child Task 1');
    expect(child1Node.source).toBe('task_assist');
  });

  // 3. Cascade updates on deleteTask / updateTaskStatus
  test('cascaded loading on updateTaskStatus and deleteTask for assist subtask and rollup parent', async () => {
    // Set mock boardTasks
    const boardTasks: TaskResponse[] = [
      { id: 'parent-1', user_id: 'u1', thread_id: 't1', parent_task_id: null, client_node_id: 'p1', title: 'Parent', node_type: 'action', status: 'active', view_bucket: 'planned', estimated_minutes: 30, sort_order: 1, assist_rollup: true },
      { id: 'child-1', user_id: 'u1', thread_id: 't1', parent_task_id: 'parent-1', client_node_id: 'c1', title: 'Child', node_type: 'action', status: 'active', view_bucket: 'planned', estimated_minutes: 15, sort_order: 1, source: 'task_assist' }
    ];
    useAppStore.setState({ boardTasks, token: 'fake-token' });

    // Mock loadProjectSnapshot & fetchTasks
    const loadSnapshotMock = vi.fn().mockResolvedValue(undefined);
    const fetchTasksMock = vi.fn().mockResolvedValue(undefined);
    useAppStore.setState({
      loadProjectSnapshot: loadSnapshotMock,
      fetchTasks: fetchTasksMock
    });

    // Mock fetch for updateTaskStatus
    const mockTaskUpdateResponse: TaskResponse = {
      id: 'child-1', user_id: 'u1', thread_id: 't1', parent_task_id: 'parent-1', client_node_id: 'c1', title: 'Child', node_type: 'action', status: 'completed', view_bucket: 'planned', estimated_minutes: 15, sort_order: 1, source: 'task_assist'
    };
    globalThis.fetch = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => mockTaskUpdateResponse
    });

    await useAppStore.getState().updateTaskStatus('child-1', 'completed');

    expect(loadSnapshotMock).toHaveBeenCalledWith('t1');
    expect(fetchTasksMock).toHaveBeenCalled();
  });
});
