// @vitest-environment jsdom
import { describe, test, expect, vi, beforeEach, afterEach } from 'vitest';
import React from 'react';
import { render, screen, fireEvent, cleanup } from '@testing-library/react';
import { ExecutionRefinePanel } from '../src/components/ExecutionRefinePanel';
import { useAppStore } from '../src/store/useAppStore';
import { TaskResponse, ExecutionRefineProposal } from '../src/types/api';

vi.mock('../src/hooks/useExecutionRefine', () => ({
  useExecutionRefine: () => {}
}));

vi.mock('../src/hooks/useTaskAssist', () => ({
  useTaskAssist: () => {}
}));

describe('ExecutionRefinePanel Component UI Tests', () => {
  afterEach(cleanup);

  const mockTasks: TaskResponse[] = [
    { id: 'task-1', user_id: 'u1', thread_id: 't1', parent_task_id: null, client_node_id: 'n1', title: 'Prepare Slides', description: null, node_type: 'action', status: 'active', view_bucket: 'planned', estimated_minutes: 30, sort_order: 1 }
  ];

  beforeEach(() => {
    useAppStore.getState().reset();
    useAppStore.getState().resetExecutionRefine();
    useAppStore.setState({ boardTasks: mockTasks, selectedProjectId: 't1' });
  });

  // 1. Closed state
  test('renders nothing when closed', () => {
    const { container } = render(<ExecutionRefinePanel />);
    expect(container.firstChild).toBeNull();
  });

  // 2. Idle State - Modes & Inputs
  test('renders idle form input state when opened', () => {
    useAppStore.setState({
      isExecutionRefinePanelOpen: true,
      executionRefineStatus: null
    });

    render(<ExecutionRefinePanel />);

    expect(screen.getByText('调整当前计划 (Execution Refine)')).toBeDefined();
    expect(screen.getByText('时间预算')).toBeDefined();
    expect(screen.getByText('进度恢复')).toBeDefined();
    expect(screen.getByText('条件变更')).toBeDefined();

    // Default is time_budget
    expect(screen.getByText('今日可支配时间容量 (available minutes)')).toBeDefined();
  });

  // 3. Shifting modes
  test('switching modes shows different inputs', () => {
    useAppStore.setState({
      isExecutionRefinePanelOpen: true,
      executionRefineStatus: null
    });

    render(<ExecutionRefinePanel />);

    // Click "条件变更"
    fireEvent.click(screen.getByText('条件变更'));
    expect(screen.getByText('新截止日期 / New Deadline')).toBeDefined();
    expect(screen.getByText('高优先级任务 / Priority Tasks (最多 5 个)')).toBeDefined();
  });

  // 4. Clicking Start triggers startExecutionRefine action
  test('start refine triggers startExecutionRefine action', async () => {
    const startRefineSpy = vi.fn().mockResolvedValue({ status: 'running' });
    useAppStore.setState({
      isExecutionRefinePanelOpen: true,
      executionRefineStatus: null,
      startExecutionRefine: startRefineSpy
    });

    render(<ExecutionRefinePanel />);

    const button = screen.getByText('生成调整方案');
    fireEvent.click(button);

    expect(startRefineSpy).toHaveBeenCalledWith('time_budget', {
      available_minutes: 30,
      new_deadline: null,
      priority_task_ids: [],
      blocked_task_ids: [],
      user_context: null
    });
  });

  // 5. Running State rendering
  test('renders running state with logs', () => {
    useAppStore.setState({
      isExecutionRefinePanelOpen: true,
      executionRefineStatus: 'running',
      executionRefineLogs: ['Logging start...', 'Calculating budget...']
    });

    render(<ExecutionRefinePanel />);

    expect(screen.getByText('AI 正在精心微调计划中...')).toBeDefined();
    expect(screen.getByText('Calculating budget...')).toBeDefined();
  });

  // 6. Ready State preview and applying
  test('renders proposal preview and triggers apply action', async () => {
    const proposal: ExecutionRefineProposal = {
      schema_version: 1,
      proposal_type: 'execution_refine',
      mode: 'time_budget',
      summary: 'Focus on slides',
      user_facing_reasons: ['Short on time'],
      preserved_constraints: [],
      operations: [],
      focus_task_ids: [],
      estimated_focus_minutes: 30,
      buffer_minutes: 5,
      warnings: []
    };

    const applyRefineSpy = vi.fn().mockResolvedValue({ status: 'applied' });

    useAppStore.setState({
      isExecutionRefinePanelOpen: true,
      executionRefineActiveRequestId: 'req-999',
      executionRefineStatus: 'ready',
      executionRefineProposal: proposal,
      applyExecutionRefine: applyRefineSpy
    });

    render(<ExecutionRefinePanel />);

    expect(screen.getByText('Focus on slides')).toBeDefined();

    const applyBtn = screen.getByText('应用本次调整');
    fireEvent.click(applyBtn);

    expect(applyRefineSpy).toHaveBeenCalledWith('req-999', null);
  });

  // 7. Applied State
  test('renders success state when applied', () => {
    useAppStore.setState({
      isExecutionRefinePanelOpen: true,
      executionRefineStatus: 'applied'
    });

    render(<ExecutionRefinePanel />);

    expect(screen.getByText('执行计划已成功微调！')).toBeDefined();
    expect(screen.getByText('完成')).toBeDefined();
  });

  // 8. Stale Context Error State
  test('renders stale context regenerate button', () => {
    const startRefineSpy = vi.fn().mockResolvedValue({ status: 'running' });
    useAppStore.setState({
      isExecutionRefinePanelOpen: true,
      executionRefineStatus: 'failed',
      executionRefineErrorCode: 'EXECUTION_REFINE_CONTEXT_STALE',
      startExecutionRefine: startRefineSpy
    });

    render(<ExecutionRefinePanel />);

    expect(screen.getByText('任务已发生变化，请保留当前输入偏好并重新生成。')).toBeDefined();
    const regenBtn = screen.getByText('重新生成调整方案');
    fireEvent.click(regenBtn);

    expect(startRefineSpy).toHaveBeenCalled();
  });

  // 9. Refresh Recovery Test
  test('refresh recovery restores selectedProjectId and request_id and fetches snapshot', async () => {
    const fetchSnapshotSpy = vi.fn().mockResolvedValue({
      run_id: 'run-1',
      thread_id: 't1',
      request_id: 'req-123',
      mode: 'time_budget',
      status: 'running',
      stage: 'queued',
      scope_fingerprint: 'f1',
      proposal: null,
      created_at: '',
      updated_at: '',
      expires_at: ''
    });

    localStorage.setItem('easyplan_execution_refine_thread_id', 't1');
    localStorage.setItem('easyplan_execution_refine_request_id', 'req-123');

    useAppStore.setState({
      fetchExecutionRefineSnapshot: fetchSnapshotSpy
    });

    const { TaskBoard } = await import('../src/components/TaskBoard');
    render(<TaskBoard />);

    expect(useAppStore.getState().selectedProjectId).toBe('t1');
    expect(useAppStore.getState().executionRefineActiveRequestId).toBe('req-123');
    expect(fetchSnapshotSpy).toHaveBeenCalledWith('req-123');

    localStorage.removeItem('easyplan_execution_refine_thread_id');
    localStorage.removeItem('easyplan_execution_refine_request_id');
  });
});
