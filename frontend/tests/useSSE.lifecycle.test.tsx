// @vitest-environment jsdom
import { describe, test, expect, vi, beforeEach, afterEach } from 'vitest';
import React from 'react';
import { render, act, waitFor, cleanup } from '@testing-library/react';
import { useSSE } from '../src/hooks/useSSE';
import { useAppStore } from '../src/store/useAppStore';

/* eslint-disable @typescript-eslint/no-explicit-any */
class MockEventSource {
  url: string;
  listeners: Record<string, ((e: any) => void)[]> = {};
  static instances: MockEventSource[] = [];
  closed = false;

  constructor(url: string) {
    this.url = url;
    MockEventSource.instances.push(this);
  }

  addEventListener(type: string, listener: (e: any) => void) {
    if (!this.listeners[type]) this.listeners[type] = [];
    this.listeners[type].push(listener);
  }

  close() {
    this.closed = true;
  }

  dispatchEvent(type: string, data: any, lastEventId: string = '') {
    const list = this.listeners[type] || [];
    for (const cb of list) {
      cb({ data: JSON.stringify(data), lastEventId });
    }
  }
}

globalThis.EventSource = MockEventSource as any;
/* eslint-enable @typescript-eslint/no-explicit-any */

const TestComponent = () => {
  useSSE();
  return null;
};

describe('useSSE hook lifecycle tests', () => {
  beforeEach(() => {
    MockEventSource.instances = [];
    vi.stubGlobal('__test__', true);
  });

  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
  });

  test('next-phase success: subscribes only from activeRun, cleans up after matching done, and ignores historical events', async () => {
    const tasksMock = [
      {
        id: 'task-1',
        thread_id: 'proj-1',
        phase_id: 'phase-2',
        source: 'ai'
      }
    ];

    let isDoneDispatched = false;
    globalThis.fetch = vi.fn().mockImplementation(async (url: string) => {
      if (url.includes('/api/threads/proj-1/phases/next/commit')) {
        return {
          ok: true,
          status: 200,
          json: async () => ({
            thread_id: 'proj-1',
            request_id: 'req-next-phase',
            status: 'confirmed',
            current_phase_id: 'phase-2',
            task_tree: {
              root: { client_node_id: 'root', title: 'Phase 2 Root', verb: 'start', estimated_minutes: 0, node_type: 'group' },
              summary: 'summary',
              planning_context: {
                schema_version: 1,
                intent_type: 'exploration_decision',
                time_horizon: 'days',
                roadmap: [
                  { phase_id: 'phase-1', order: 1, title: 'Phase 1', objective: 'Objective 1', status: 'completed' },
                  { phase_id: 'phase-2', order: 2, title: 'Phase 2', objective: 'Objective 2', status: 'current' },
                ],
                current_phase: { phase_id: 'phase-2', title: 'Phase 2', objective: 'Objective 2' }
              }
            },
            tasks: tasksMock
          })
        };
      }
      if (url.includes('/api/threads/proj-1')) {
        if (!isDoneDispatched) {
          const res = {
            thread_id: 'proj-1',
            status: 'running',
            intent_text: 'my intent',
            task_tree: {
              root: { client_node_id: 'root', title: 'Phase 1 Root', verb: 'start', estimated_minutes: 0, node_type: 'group' },
              summary: 'summary',
              planning_context: {
                schema_version: 1,
                intent_type: 'exploration_decision',
                time_horizon: 'days',
                roadmap: [
                  { phase_id: 'phase-1', order: 1, title: 'Phase 1', objective: 'Objective 1', status: 'current' },
                  { phase_id: 'phase-2', order: 2, title: 'Phase 2', objective: 'Objective 2', status: 'planned' }
                ],
                current_phase: { phase_id: 'phase-1', title: 'Phase 1', objective: 'Objective 1' }
              }
            },
            interrupt_payload: null
          };
          return {
            ok: true,
            status: 200,
            json: async () => res
          };
        } else {
          const res = {
            thread_id: 'proj-1',
            status: 'succeeded',
            intent_text: 'my intent',
            task_tree: {
              root: { client_node_id: 'root', title: 'Phase 2 Root', verb: 'start', estimated_minutes: 0, node_type: 'group' },
              summary: 'summary',
              planning_context: {
                schema_version: 1,
                intent_type: 'exploration_decision',
                time_horizon: 'days',
                roadmap: [
                  { phase_id: 'phase-1', order: 1, title: 'Phase 1', objective: 'Objective 1', status: 'completed' },
                  { phase_id: 'phase-2', order: 2, title: 'Phase 2', objective: 'Objective 2', status: 'current' },
                ],
                current_phase: { phase_id: 'phase-2', title: 'Phase 2', objective: 'Objective 2' }
              }
            },
            interrupt_payload: {
              type: 'phase_generation_state',
              request_id: 'req-next-phase',
              status: 'confirmed'
            }
          };
          return {
            ok: true,
            status: 200,
            json: async () => res
          };
        }
      }
      if (url.includes('/api/tasks')) {
        return {
          ok: true,
          status: 200,
          json: async () => tasksMock
        };
      }
      return { ok: true, status: 200, json: async () => ({}) };
    });

    // Seed the store with active next_phase run
    act(() => {
      useAppStore.getState().reset();
      useAppStore.getState().setToken('mock-token');
      useAppStore.getState().setSelectedProjectId('proj-1');
      useAppStore.getState().setActiveRun({
        threadId: 'proj-1',
        runType: 'next_phase',
        requestId: 'req-next-phase',
      });
      useAppStore.getState().setCommittedTaskTree({
        root: { client_node_id: 'root', title: 'Phase 1 Root', verb: 'start', estimated_minutes: 0, node_type: 'group' },
        summary: 'Phase 1',
        planning_context: {
          schema_version: 1,
          intent_type: 'exploration_decision',
          time_horizon: 'days',
          roadmap: [
            { phase_id: 'phase-1', order: 1, title: 'Phase 1', objective: 'Objective 1', status: 'current' },
            { phase_id: 'phase-2', order: 2, title: 'Phase 2', objective: 'Objective 2', status: 'planned' }
          ],
          current_phase: { phase_id: 'phase-1', title: 'Phase 1', objective: 'Objective 1', completion_rule: 'all_ai_actions_completed' },
          next_action_client_node_id: null
        }
      });
      localStorage.setItem('easyplan_preview_mode', 'next_phase');
      localStorage.setItem('easyplan_phase_request_id', 'req-next-phase');
      localStorage.setItem('easyplan_base_phase_id', 'phase-1');
      useAppStore.setState({
        phaseRequestId: 'req-next-phase',
        basePhaseId: 'phase-1',
        previewMode: 'next_phase',
      });
    });

    // Mount hook
    let renderResult;
    await act(async () => {
      renderResult = render(<TestComponent />);
    });

    // Assert EventSource setup (wait for async connect)
    await waitFor(() => {
      expect(MockEventSource.instances).toHaveLength(1);
    });

    const esInstance = MockEventSource.instances[0];
    expect(esInstance.url).toContain('run_type=next_phase');
    expect(esInstance.url).toContain('request_id=req-next-phase');
    expect(esInstance.closed).toBe(false);

    // Attempt to dispatch historical initial 'done' event; should be rejected by finishAgentRun & useSSE callback
    await act(async () => {
      esInstance.dispatchEvent('done', {
        thread_id: 'proj-1',
        run_type: 'initial',
        request_id: 'req-historical-initial',
        state_version: 1
      });
    });

    // Verify nothing changed
    expect(useAppStore.getState().activeRun).not.toBeNull();
    expect(useAppStore.getState().previewMode).toBe('next_phase');

    // Dispatch matching next_phase done event
    await act(async () => {
      isDoneDispatched = true;
      esInstance.dispatchEvent('done', {
        thread_id: 'proj-1',
        run_type: 'next_phase',
        request_id: 'req-next-phase',
        state_version: 2
      });
    });

    // Verify terminal cleanups occurred
    await waitFor(() => {
      const finalState = useAppStore.getState();
      expect(finalState.activeRun).toBeNull();
      expect(finalState.previewMode).toBeNull();
      expect(finalState.selectedProjectId).toBe('proj-1');
      expect(finalState.committedTaskTree?.planning_context?.current_phase?.phase_id).toBe('phase-2');
    });

    // Verify EventSource is closed and no secondary connection established
    await waitFor(() => {
      expect(esInstance.closed).toBe(true);
      expect(MockEventSource.instances).toHaveLength(1);
    });

    // Unmount
    renderResult.unmount();
  });

  test('initial running: seeds active run, establishes and preserves EventSource', async () => {
    globalThis.fetch = vi.fn().mockImplementation(async (url: string) => {
      if (url.includes('/api/threads/proj-initial')) {
        return {
          ok: true,
          status: 200,
          json: async () => ({
            thread_id: 'proj-initial',
            status: 'running',
            intent_text: 'my initial intent',
            task_tree: null,
            interrupt_payload: null
          })
        };
      }
      return { ok: true, status: 200, json: async () => ({}) };
    });

    act(() => {
      useAppStore.getState().reset();
      useAppStore.getState().setToken('mock-token');
      useAppStore.getState().setSelectedProjectId('proj-initial');
      useAppStore.getState().setActiveRun({
        threadId: 'proj-initial',
        runType: 'initial',
        requestId: 'req-initial-a',
      });
      useAppStore.setState({
        previewMode: 'initial'
      });
    });

    const { unmount } = render(<TestComponent />);

    await waitFor(() => {
      expect(MockEventSource.instances).toHaveLength(1);
    });

    const esInstance = MockEventSource.instances[0];
    expect(esInstance.url).toContain('run_type=initial');
    expect(esInstance.url).toContain('request_id=req-initial-a');
    expect(esInstance.closed).toBe(false);

    unmount();
  });

  test('refresh then confirm initial: confirms using correct requestId, cleans up on done', async () => {
    let confirmUrl = null;
    let confirmBody = null;

    globalThis.fetch = vi.fn().mockImplementation(async (url: string, options = {}) => {
      if (url.includes('/api/threads/proj-initial/confirm')) {
        confirmUrl = url;
        confirmBody = JSON.parse(options.body);
        return {
          ok: true,
          status: 200,
          json: async () => ({})
        };
      }
      if (url.includes('/api/threads/proj-initial')) {
        return {
          ok: true,
          status: 200,
          json: async () => ({
            thread_id: 'proj-initial',
            status: 'awaiting_confirmation',
            intent_text: 'my initial intent',
            task_tree: null,
            interrupt_payload: {
              type: 'task_tree_review',
              request_id: 'req-initial-a',
              run_type: 'initial',
              task_tree: { root: { client_node_id: 'root', title: 'Initial Root' } }
            }
          })
        };
      }
      if (url.includes('/api/tasks')) {
        return { ok: true, status: 200, json: async () => [] };
      }
      return { ok: true, status: 200, json: async () => ({}) };
    });

    act(() => {
      useAppStore.getState().reset();
      useAppStore.getState().setToken('mock-token');
      useAppStore.getState().setSelectedProjectId('proj-initial');
      useAppStore.getState().setActiveRun({
        threadId: 'proj-initial',
        runType: 'initial',
        requestId: 'req-initial-a',
      });
      useAppStore.setState({
        previewMode: 'initial',
      });
    });

    const { unmount } = render(<TestComponent />);

    await waitFor(() => {
      expect(MockEventSource.instances).toHaveLength(1);
    });

    const esInstance = MockEventSource.instances[0];
    expect(esInstance.url).toContain('run_type=initial');
    expect(esInstance.url).toContain('request_id=req-initial-a');

    // Call confirmPlan
    await act(async () => {
      await useAppStore.getState().confirmPlan();
    });

    expect(confirmUrl).not.toBeNull();
    expect(confirmBody.request_id).toBe('req-initial-a');

    expect(MockEventSource.instances).toHaveLength(1);
    expect(esInstance.closed).toBe(false);

    // Dispatch done for A
    await act(async () => {
      esInstance.dispatchEvent('done', {
        thread_id: 'proj-initial',
        run_type: 'initial',
        request_id: 'req-initial-a',
        state_version: 1
      });
    });

    // Valid initial completion path runs: activeRun cleared, view is board, selectedProjectId is null
    await waitFor(() => {
      const state = useAppStore.getState();
      expect(state.activeRun).toBeNull();
      expect(state.previewMode).toBeNull();
      expect(state.selectedProjectId).toBeNull();
      expect(state.view).toBe('board');
      expect(esInstance.closed).toBe(true);
    });

    unmount();
  });

  test.each([
    {
      name: 'cancelPlanPreview',
      action: async () => {
        await useAppStore.getState().cancelPlanPreview();
      },
      setupFetch: (url: string) => {
        if (url.includes('/cancel')) {
          expect(url).toContain('request_id=req-exit-a');
          return { ok: true, status: 200, json: async () => ({ thread_id: 'proj-exit', intent_text: 'intent' }) };
        }
        return null;
      }
    },
    {
      name: 'returnToCommittedPlan with project',
      action: async () => {
        await useAppStore.getState().returnToCommittedPlan();
      },
      setupFetch: () => null
    },
    {
      name: 'returnToCommittedPlan without project',
      action: async () => {
        act(() => {
          useAppStore.setState({ selectedProjectId: null });
        });
        await useAppStore.getState().returnToCommittedPlan();
      },
      setupFetch: () => null
    },
    {
      name: 'startNewIntent',
      action: async () => {
        useAppStore.getState().startNewIntent();
      },
      setupFetch: () => null
    },
    {
      name: 'setSelectedProjectId(null)',
      action: async () => {
        useAppStore.getState().setSelectedProjectId(null);
      },
      setupFetch: () => null
    },
    {
      name: 'setView(board) leaving generation',
      action: async () => {
        act(() => {
          useAppStore.setState({ selectedProjectId: null });
        });
        useAppStore.getState().setView('board');
      },
      setupFetch: () => null
    }
  ])('exit during generation: $name closes EventSource and rejects late events', async ({ name, action, setupFetch }) => {
    globalThis.fetch = vi.fn().mockImplementation(async (url: string) => {
      const customRes = setupFetch(url);
      if (customRes) return customRes;
      if (url.includes('/api/threads/proj-exit')) {
        const isRunActive = useAppStore.getState().activeRun !== null;
        if (isRunActive) {
          return {
            ok: true,
            status: 200,
            json: async () => ({
              thread_id: 'proj-exit',
              status: 'running',
              intent_text: 'my intent',
              task_tree: null,
              interrupt_payload: null
            })
          };
        } else {
          return {
            ok: true,
            status: 200,
            json: async () => ({
              thread_id: 'proj-exit',
              status: 'succeeded',
              intent_text: 'my intent',
              // eslint-disable-next-line @typescript-eslint/no-explicit-any
              task_tree: { root: { title: 'Untouched committed tree' } } as any,
              interrupt_payload: null
            })
          };
        }
      }
      if (url.includes('/api/tasks')) {
        return { ok: true, status: 200, json: async () => [] };
      }
      return { ok: true, status: 200, json: async () => ({}) };
    });

    act(() => {
      useAppStore.getState().reset();
      useAppStore.getState().setToken('mock-token');
      useAppStore.getState().setSelectedProjectId('proj-exit');
      useAppStore.getState().setActiveRun({
        threadId: 'proj-exit',
        runType: name === 'cancelPlanPreview' ? 'next_phase' : 'initial',
        requestId: 'req-exit-a',
      });
      useAppStore.setState({
        previewMode: name === 'cancelPlanPreview' ? 'next_phase' : 'initial',
        phaseRequestId: name === 'cancelPlanPreview' ? 'req-exit-a' : null,
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        committedTaskTree: { root: { title: 'Untouched committed tree' } } as any
      });
      if (name === 'cancelPlanPreview') {
        localStorage.setItem('easyplan_preview_mode', 'next_phase');
        localStorage.setItem('easyplan_phase_request_id', 'req-exit-a');
      } else {
        localStorage.setItem('easyplan_preview_mode', 'initial');
      }
    });

    const { unmount } = render(<TestComponent />);

    await waitFor(() => {
      expect(MockEventSource.instances).toHaveLength(1);
    });

    const esInstance = MockEventSource.instances[0];
    expect(esInstance.closed).toBe(false);

    // Invoke exit action
    await act(async () => {
      await action();
    });

    // Assert the EventSource closes
    expect(esInstance.closed).toBe(true);
    expect(useAppStore.getState().activeRun).toBeNull();

    // Capture state values before dispatching late event
    const stateBeforeLateEvent = {
      view: useAppStore.getState().view,
      selectedProjectId: useAppStore.getState().selectedProjectId,
      committedTaskTree: useAppStore.getState().committedTaskTree
    };

    // Dispatch late event
    await act(async () => {
      esInstance.dispatchEvent('done', {
        thread_id: 'proj-exit',
        run_type: 'initial',
        request_id: 'req-exit-a',
        state_version: 100
      });
    });

    // Assert state did not change
    expect(useAppStore.getState().view).toBe(stateBeforeLateEvent.view);
    expect(useAppStore.getState().selectedProjectId).toBe(stateBeforeLateEvent.selectedProjectId);
    expect(useAppStore.getState().committedTaskTree).toEqual(stateBeforeLateEvent.committedTaskTree);

    unmount();
  });
});
