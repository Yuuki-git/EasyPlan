// @vitest-environment jsdom
import { describe, test, expect, vi, beforeEach, afterEach } from 'vitest';
import React from 'react';
import { render, act, cleanup } from '@testing-library/react';
import { useExecutionRefine } from '../src/hooks/useExecutionRefine';
import { useAppStore } from '../src/store/useAppStore';
import type { ExecutionRefineProposal } from '../src/types/api';

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
  useExecutionRefine();
  return null;
};

describe('useExecutionRefine hook lifecycle tests', () => {
  beforeEach(() => {
    MockEventSource.instances = [];
    vi.useFakeTimers();
    useAppStore.getState().reset();
    useAppStore.getState().resetExecutionRefine();
  });

  afterEach(() => {
    cleanup();
    vi.useRealTimers();
    vi.restoreAllMocks();
  });

  const makeEnvelope = (
    eventType: string,
    requestId: string,
    payload?: unknown,
    seq: number = 1
  ) => {
    return {
      event_id: `t1:execution_refine:${requestId}:${seq}`,
      thread_id: 't1',
      request_id: requestId,
      run_type: 'execution_refine',
      event_type: eventType,
      seq,
      created_at: new Date().toISOString(),
      payload,
    };
  };

  // 1. Correct URL structure with token
  test('connects to EventSource with correct URL and token when active', () => {
    useAppStore.setState({
      isExecutionRefinePanelOpen: true,
      selectedProjectId: 't1',
      executionRefineActiveRequestId: 'req-1',
      token: 'fake-token'
    });

    render(<TestComponent />);

    expect(MockEventSource.instances.length).toBe(1);
    const inst = MockEventSource.instances[0];
    expect(inst.url).toContain('/api/threads/t1/refine-diffs/req-1/events');
    expect(inst.url).toContain('token=fake-token');
  });

  // 2. Disconnects when panel is closed
  test('disconnects EventSource when panel is closed', () => {
    useAppStore.setState({
      isExecutionRefinePanelOpen: true,
      selectedProjectId: 't1',
      executionRefineActiveRequestId: 'req-1',
      token: 'fake-token'
    });

    const { unmount } = render(<TestComponent />);
    expect(MockEventSource.instances.length).toBe(1);
    const inst = MockEventSource.instances[0];
    expect(inst.closed).toBe(false);

    // Close panel
    act(() => {
      useAppStore.setState({ isExecutionRefinePanelOpen: false });
    });

    expect(inst.closed).toBe(true);
    unmount();
  });

  // 3. Filters mismatched events
  test('ignores events with mismatched request_id or run_type', () => {
    useAppStore.setState({
      isExecutionRefinePanelOpen: true,
      selectedProjectId: 't1',
      executionRefineActiveRequestId: 'req-1'
    });

    render(<TestComponent />);
    const inst = MockEventSource.instances[0];

    // Dispatch event with wrong request_id
    act(() => {
      inst.dispatchEvent('run_started', makeEnvelope('run_started', 'req-wrong'));
    });
    expect(useAppStore.getState().executionRefineLogs).toEqual([]);

    // Dispatch event with wrong run_type
    const wrongRunTypeEnvelope = makeEnvelope('run_started', 'req-1');
    Object.assign(wrongRunTypeEnvelope, { run_type: 'planning' });
    act(() => {
      inst.dispatchEvent('run_started', wrongRunTypeEnvelope);
    });
    expect(useAppStore.getState().executionRefineLogs).toEqual([]);

    // Dispatch correct event
    act(() => {
      inst.dispatchEvent('run_started', makeEnvelope('run_started', 'req-1'));
    });
    expect(useAppStore.getState().executionRefineLogs).toEqual(['已启动计划执行调整引擎...']);
  });

  // 4. Receives diff_ready and loads proposal
  test('loads proposal when diff_ready is received', () => {
    useAppStore.setState({
      isExecutionRefinePanelOpen: true,
      selectedProjectId: 't1',
      executionRefineActiveRequestId: 'req-1'
    });

    render(<TestComponent />);
    const inst = MockEventSource.instances[0];

    const proposal = {
      schema_version: 1,
      proposal_type: 'execution_refine',
      mode: 'time_budget',
      summary: 'Focus and adjust',
      user_facing_reasons: ['Reason 1'],
      preserved_constraints: [],
      operations: [],
      focus_task_ids: [],
      estimated_focus_minutes: 30,
      buffer_minutes: 5,
      warnings: []
    };

    act(() => {
      inst.dispatchEvent('diff_ready', makeEnvelope('diff_ready', 'req-1', { proposal }));
    });

    const state = useAppStore.getState();
    expect(state.executionRefineStatus).toBe('ready');
    expect(state.executionRefineProposal).toEqual(proposal);
  });

  // 5. Handles connection errors and schedules reconnect
  test('reconnects with last_event_id query param after connection error', () => {
    useAppStore.setState({
      isExecutionRefinePanelOpen: true,
      selectedProjectId: 't1',
      executionRefineActiveRequestId: 'req-1'
    });

    render(<TestComponent />);
    expect(MockEventSource.instances.length).toBe(1);
    const inst1 = MockEventSource.instances[0];

    // Dispatch an event to set lastEventId
    act(() => {
      inst1.dispatchEvent('run_started', makeEnvelope('run_started', 'req-1'), 'event-cursor-456');
    });

    // Simulate error/disconnect
    act(() => {
      const errorListeners = inst1.listeners['error'];
      if (errorListeners && errorListeners[0]) {
        errorListeners[0](new Event('error'));
      }
    });

    expect(inst1.closed).toBe(true);

    // Reconnect timer runs for 3000ms
    act(() => {
      vi.advanceTimersByTime(3000);
    });

    expect(MockEventSource.instances.length).toBe(2);
    const inst2 = MockEventSource.instances[1];
    expect(inst2.url).toContain('last_event_id=event-cursor-456');
  });

  // 6. Duplicate events filter test
  test('ignores duplicate events with the same event_id', () => {
    useAppStore.setState({
      isExecutionRefinePanelOpen: true,
      selectedProjectId: 't1',
      executionRefineActiveRequestId: 'req-1'
    });

    render(<TestComponent />);
    const inst = MockEventSource.instances[0];

    const envelope = makeEnvelope('run_started', 'req-1', {}, 1);

    act(() => {
      inst.dispatchEvent('run_started', envelope);
    });
    expect(useAppStore.getState().executionRefineLogs).toEqual(['已启动计划执行调整引擎...']);

    // Clear logs to check if second dispatch is processed
    useAppStore.setState({ executionRefineLogs: [] });

    act(() => {
      inst.dispatchEvent('run_started', envelope);
    });
    expect(useAppStore.getState().executionRefineLogs).toEqual([]);
  });

  // 7. snapshot_required event test
  test('handles snapshot_required event by calling fetchExecutionRefineSnapshot and closing EventSource if terminal', async () => {
    const syncedProposal: ExecutionRefineProposal = {
      schema_version: 1,
      proposal_type: 'execution_refine',
      mode: 'time_budget',
      summary: 'synced summary',
      user_facing_reasons: [],
      preserved_constraints: [],
      operations: [],
      focus_task_ids: [],
      estimated_focus_minutes: 0,
      buffer_minutes: 0,
      warnings: []
    };
    const fetchSnapshotSpy = vi.fn().mockImplementation(async () => {
      useAppStore.setState({
        executionRefineStatus: 'ready',
        executionRefineStage: 'ready',
        executionRefineProposal: syncedProposal
      });
      return {
        run_id: 'run-1',
        thread_id: 't1',
        request_id: 'req-1',
        mode: 'time_budget',
        status: 'ready',
        stage: 'ready',
        scope_fingerprint: 'f1',
        proposal: syncedProposal,
        created_at: '',
        updated_at: '',
        expires_at: ''
      };
    });

    useAppStore.setState({
      isExecutionRefinePanelOpen: true,
      selectedProjectId: 't1',
      executionRefineActiveRequestId: 'req-1',
      fetchExecutionRefineSnapshot: fetchSnapshotSpy
    });

    render(<TestComponent />);
    const inst = MockEventSource.instances[0];

    await act(async () => {
      inst.dispatchEvent('snapshot_required', makeEnvelope('snapshot_required', 'req-1'));
    });

    expect(fetchSnapshotSpy).toHaveBeenCalledWith('req-1');
    expect(useAppStore.getState().executionRefineStatus).toBe('ready');
    expect(inst.closed).toBe(true);
  });

  // 8. Old EventSource events ignore test
  test('ignores events from old EventSource after instantiation of new EventSource', () => {
    useAppStore.setState({
      isExecutionRefinePanelOpen: true,
      selectedProjectId: 't1',
      executionRefineActiveRequestId: 'req-1'
    });

    const { rerender } = render(<TestComponent />);
    expect(MockEventSource.instances.length).toBe(1);
    const inst1 = MockEventSource.instances[0];

    act(() => {
      useAppStore.setState({ executionRefineActiveRequestId: 'req-2' });
    });
    rerender(<TestComponent />);

    expect(MockEventSource.instances.length).toBe(2);
    const inst2 = MockEventSource.instances[1];

    act(() => {
      inst1.dispatchEvent('run_started', makeEnvelope('run_started', 'req-1'));
    });
    expect(useAppStore.getState().executionRefineLogs).toEqual([]);

    act(() => {
      inst2.dispatchEvent('run_started', makeEnvelope('run_started', 'req-2'));
    });
    expect(useAppStore.getState().executionRefineLogs).toEqual(['已启动计划执行调整引擎...']);
  });
});
