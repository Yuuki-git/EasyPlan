// @vitest-environment jsdom
import { describe, expect, it, vi, beforeEach, afterEach } from 'vitest';
import { useAppStore } from '../src/store/useAppStore';
import { ThreadSnapshot, TaskResponse } from '../src/types/api';

const mockedFetch = () => vi.mocked(globalThis.fetch);

describe('longTermExecutionStore tests', () => {
  beforeEach(() => {
    vi.stubGlobal('fetch', vi.fn());
    useAppStore.setState({
      token: 'mock-token',
      selectedProjectId: 'proj-1',
      boardTasks: [],
      longTermExecution: null,
      practiceError: null,
      isPracticeRequestPending: false
    });
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('snapshot loading stores longTermExecution', async () => {
    const mockSnapshot: ThreadSnapshot = {
      thread_id: 'proj-1',
      status: 'succeeded',
      state_version: 1,
      last_event_id: null,
      server_time: '2026-07-05T00:00:00Z',
      intent_text: 'V2 goal',
      task_tree: null,
      long_term_execution: {
        phase_id: 'phase-1',
        recommendation: 'ready',
        review_available: true,
        one_off_ready: true,
        process_ready: true,
        outcome_ready: true,
        loops: [],
        active_review: null,
        latest_finalized_review: null,
        review_history: []
      }
    };

    mockedFetch().mockResolvedValueOnce({
      ok: true,
      status: 200,
      json: async () => mockSnapshot
    });

    await useAppStore.getState().loadProjectSnapshot('proj-1');

    expect(useAppStore.getState().longTermExecution).toEqual(mockSnapshot.long_term_execution);
  });

  it('schedule uses timezone and auth headers', async () => {
    const mockTask: TaskResponse = {
      id: 'task-1',
      user_id: 'user-1',
      thread_id: 'proj-1',
      parent_task_id: null,
      client_node_id: 'node-1',
      title: 'Practice Loop Task',
      description: null,
      node_type: 'action',
      status: 'active',
      view_bucket: 'planned',
      estimated_minutes: null,
      sort_order: 1,
      is_in_my_day: true,
      practice_loop_id: 'loop-1',
      source: 'practice_loop'
    };

    const mockSnapshot: ThreadSnapshot = {
      thread_id: 'proj-1',
      status: 'succeeded',
      state_version: 1,
      last_event_id: null,
      server_time: '2026-07-05T00:00:00Z',
      intent_text: 'V2 goal',
      task_tree: null,
      long_term_execution: {
        phase_id: 'phase-1',
        recommendation: 'ready',
        review_available: true,
        one_off_ready: true,
        process_ready: true,
        outcome_ready: true,
        loops: [],
        active_review: null,
        latest_finalized_review: null,
        review_history: []
      }
    };

    mockedFetch()
      .mockResolvedValueOnce({
        ok: true,
        status: 200,
        json: async () => mockTask
      }) // schedule call
      .mockResolvedValueOnce({
        ok: true,
        status: 200,
        json: async () => mockSnapshot
      }) // loadProjectSnapshot call
      .mockResolvedValueOnce({
        ok: true,
        status: 200,
        json: async () => []
      }); // fetchTasks call

    await useAppStore.getState().schedulePracticeToday('loop-1');

    expect(globalThis.fetch).toHaveBeenCalledWith(
      '/api/threads/proj-1/practice-loops/loop-1/schedule-today',
      expect.objectContaining({
        method: 'POST',
        headers: expect.objectContaining({
          'Authorization': 'Bearer mock-token',
          'X-User-Timezone': expect.any(String)
        })
      })
    );
  });

  it('duplicate schedule merges the returned task by ID', async () => {
    const originalTask: TaskResponse = {
      id: 'task-1',
      user_id: 'user-1',
      thread_id: 'proj-1',
      parent_task_id: null,
      client_node_id: 'node-1',
      title: 'Original Title',
      description: null,
      node_type: 'action',
      status: 'active',
      view_bucket: 'planned',
      estimated_minutes: null,
      sort_order: 1,
      is_in_my_day: true,
      practice_loop_id: 'loop-1',
      source: 'practice_loop'
    };

    useAppStore.setState({ boardTasks: [originalTask] });

    const updatedTask: TaskResponse = {
      ...originalTask,
      title: 'Practice Loop Task'
    };

    mockedFetch()
      .mockResolvedValueOnce({
        ok: true,
        status: 200,
        json: async () => updatedTask
      })
      .mockResolvedValueOnce({
        ok: true,
        status: 200,
        json: async () => ({})
      })
      .mockResolvedValueOnce({
        ok: true,
        status: 200,
        json: async () => [updatedTask]
      });

    await useAppStore.getState().schedulePracticeToday('loop-1');

    const tasks = useAppStore.getState().boardTasks;
    expect(tasks?.length).toBe(1);
    expect(tasks?.[0].title).toBe('Practice Loop Task');
  });

  it('401 triggers auth recovery', async () => {
    mockedFetch().mockResolvedValueOnce({
      ok: false,
      status: 401,
      json: async () => ({ detail: 'Unauthorized' })
    });

    await useAppStore.getState().schedulePracticeToday('loop-1');

    expect(useAppStore.getState().token).toBeNull();
    expect(useAppStore.getState().showAuthModal).toBe(true);
  });

  it('409 stores a user-facing loop error without replacing board tasks', async () => {
    const originalTask: TaskResponse = {
      id: 'task-1',
      user_id: 'user-1',
      thread_id: 'proj-1',
      parent_task_id: null,
      client_node_id: 'node-1',
      title: 'Original Title',
      description: null,
      node_type: 'action',
      status: 'active',
      view_bucket: 'planned',
      estimated_minutes: null,
      sort_order: 1,
      is_in_my_day: true,
      practice_loop_id: 'loop-1',
      source: 'practice_loop'
    };

    useAppStore.setState({ boardTasks: [originalTask] });

    mockedFetch().mockResolvedValueOnce({
      ok: false,
      status: 409,
      json: async () => ({ detail: { message: 'Conflict error message' } })
    });

    await useAppStore.getState().schedulePracticeToday('loop-1');

    expect(useAppStore.getState().practiceError).toBe('Conflict error message');
    expect(useAppStore.getState().boardTasks).toEqual([originalTask]);
  });

  it('review update and decision reload the selected project snapshot', async () => {
    const mockSnapshot: ThreadSnapshot = {
      thread_id: 'proj-1',
      status: 'succeeded',
      state_version: 1,
      last_event_id: null,
      server_time: '2026-07-05T00:00:00Z',
      intent_text: 'V2 goal',
      task_tree: null,
      long_term_execution: {
        phase_id: 'phase-1',
        recommendation: 'ready',
        review_available: true,
        one_off_ready: true,
        process_ready: true,
        outcome_ready: true,
        loops: [],
        active_review: null,
        latest_finalized_review: null,
        review_history: []
      }
    };

    mockedFetch()
      .mockResolvedValueOnce({
        ok: true,
        status: 200,
        json: async () => ({})
      })
      .mockResolvedValueOnce({
        ok: true,
        status: 200,
        json: async () => mockSnapshot
      });

    await useAppStore.getState().savePhaseReview('phase-1', { evidence: {} });

    expect(globalThis.fetch).toHaveBeenCalledWith(
      '/api/threads/proj-1/phases/phase-1/review',
      expect.objectContaining({ method: 'PUT' })
    );
    expect(useAppStore.getState().longTermExecution).toEqual(mockSnapshot.long_term_execution);
  });

  it('task completion reloads execution progress without allowing stale snapshot writes', async () => {
    const mockTask: TaskResponse = {
      id: 'task-1',
      user_id: 'user-1',
      thread_id: 'proj-1',
      parent_task_id: null,
      client_node_id: 'node-1',
      title: 'Practice Task',
      description: null,
      node_type: 'action',
      status: 'active',
      view_bucket: 'planned',
      estimated_minutes: null,
      sort_order: 1,
      is_in_my_day: true,
      practice_loop_id: 'loop-1',
      source: 'practice_loop'
    };

    useAppStore.setState({ boardTasks: [mockTask] });

    const mockSnapshot: ThreadSnapshot = {
      thread_id: 'proj-1',
      status: 'succeeded',
      state_version: 1,
      last_event_id: null,
      server_time: '2026-07-05T00:00:00Z',
      intent_text: 'V2 goal',
      task_tree: null,
      long_term_execution: {
        phase_id: 'phase-1',
        recommendation: 'ready',
        review_available: true,
        one_off_ready: true,
        process_ready: true,
        outcome_ready: true,
        loops: [],
        active_review: null,
        latest_finalized_review: null,
        review_history: []
      }
    };

    mockedFetch()
      .mockResolvedValueOnce({
        ok: true,
        status: 200,
        json: async () => ({ ...mockTask, status: 'completed' })
      })
      .mockResolvedValueOnce({
        ok: true,
        status: 200,
        json: async () => mockSnapshot
      })
      .mockResolvedValueOnce({
        ok: true,
        status: 200,
        json: async () => []
      });

    await useAppStore.getState().updateTaskStatus('task-1', 'completed');

    expect(useAppStore.getState().longTermExecution).toEqual(mockSnapshot.long_term_execution);
  });
});
