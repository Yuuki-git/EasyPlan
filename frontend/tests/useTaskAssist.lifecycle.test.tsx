// @vitest-environment jsdom
import { describe, test, expect, vi, beforeEach, afterEach } from 'vitest';
import React from 'react';
import { render, act, cleanup } from '@testing-library/react';
import { useTaskAssist } from '../src/hooks/useTaskAssist';
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
  useTaskAssist();
  return null;
};

describe('useTaskAssist hook lifecycle tests', () => {
  beforeEach(() => {
    MockEventSource.instances = [];
    vi.useFakeTimers();
    useAppStore.getState().reset();
    useAppStore.getState().resetTaskAssist();
  });

  afterEach(() => {
    cleanup();
    vi.useRealTimers();
    vi.restoreAllMocks();
  });

  const makeEnvelope = (
    eventType: string,
    taskId: string,
    requestId: string,
    payload?: unknown,
    seq: number = 1
  ) => {
    return {
      event_id: `${taskId}:task_assist:${requestId}:${seq}`,
      thread_id: 't1',
      request_id: requestId,
      run_type: 'task_assist',
      event_type: eventType,
      seq,
      created_at: new Date().toISOString(),
      payload,
    };
  };

  // 1. Correct URL structure with token
  test('connects to EventSource with correct URL and token when active', () => {
    useAppStore.setState({
      isTaskAssistPanelOpen: true,
      taskAssistActiveTaskId: 'task-1',
      taskAssistActiveRequestId: 'req-1',
      token: 'fake-token'
    });

    render(<TestComponent />);

    expect(MockEventSource.instances.length).toBe(1);
    const inst = MockEventSource.instances[0];
    expect(inst.url).toContain('/api/tasks/task-1/assist/req-1/events');
    expect(inst.url).toContain('token=fake-token');
  });

  // 2. Disconnects when panel is closed or IDs cleared
  test('disconnects EventSource when panel is closed', () => {
    useAppStore.setState({
      isTaskAssistPanelOpen: true,
      taskAssistActiveTaskId: 'task-1',
      taskAssistActiveRequestId: 'req-1',
      token: 'fake-token'
    });

    const { unmount } = render(<TestComponent />);
    expect(MockEventSource.instances.length).toBe(1);
    const inst = MockEventSource.instances[0];
    expect(inst.closed).toBe(false);

    // Close panel
    act(() => {
      useAppStore.setState({ isTaskAssistPanelOpen: false });
    });

    expect(inst.closed).toBe(true);
    unmount();
  });

  // 3. Filters mismatched events
  test('ignores events with mismatched request_id or run_type', () => {
    useAppStore.setState({
      isTaskAssistPanelOpen: true,
      taskAssistActiveTaskId: 'task-1',
      taskAssistActiveRequestId: 'req-1'
    });

    render(<TestComponent />);
    const inst = MockEventSource.instances[0];

    // Dispatch event with wrong request_id
    act(() => {
      inst.dispatchEvent('run_started', makeEnvelope('run_started', 'task-1', 'req-wrong'));
    });
    expect(useAppStore.getState().taskAssistLogs).toEqual([]);

    // Dispatch event with wrong run_type
    const wrongRunTypeEnvelope = makeEnvelope('run_started', 'task-1', 'req-1');
    Object.assign(wrongRunTypeEnvelope, { run_type: 'initial' });
    act(() => {
      inst.dispatchEvent('run_started', wrongRunTypeEnvelope);
    });
    expect(useAppStore.getState().taskAssistLogs).toEqual([]);

    // Dispatch correct event
    act(() => {
      inst.dispatchEvent('run_started', makeEnvelope('run_started', 'task-1', 'req-1'));
    });
    expect(useAppStore.getState().taskAssistLogs).toEqual(['已启动辅导任务分析...']);
  });

  // 4. Receives assist_ready and loads proposal
  test('loads proposal when assist_ready is received', () => {
    useAppStore.setState({
      isTaskAssistPanelOpen: true,
      taskAssistActiveTaskId: 'task-1',
      taskAssistActiveRequestId: 'req-1'
    });

    render(<TestComponent />);
    const inst = MockEventSource.instances[0];

    const proposal = {
      schema_version: 1,
      proposal_type: 'start',
      summary: 'summary text',
      starter_step: { draft_id: 'd1', title: 'Start here', estimated_minutes: 5, done_criteria: 'Criteria' }
    };

    act(() => {
      inst.dispatchEvent('assist_ready', makeEnvelope('assist_ready', 'task-1', 'req-1', { proposal }));
    });

    const state = useAppStore.getState();
    expect(state.taskAssistStatus).toBe('ready');
    expect(state.taskAssistProposal).toEqual(proposal);
  });

  // 5. Handles connection errors and schedules reconnect with last event id cursor
  test('reconnects with last_event_id query param after connection error', () => {
    useAppStore.setState({
      isTaskAssistPanelOpen: true,
      taskAssistActiveTaskId: 'task-1',
      taskAssistActiveRequestId: 'req-1'
    });

    render(<TestComponent />);
    expect(MockEventSource.instances.length).toBe(1);
    const inst1 = MockEventSource.instances[0];

    // Dispatch an event to set lastEventId
    act(() => {
      inst1.dispatchEvent('run_started', makeEnvelope('run_started', 'task-1', 'req-1'), 'event-cursor-123');
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
    expect(inst2.url).toContain('last_event_id=event-cursor-123');
  });
});
