// @vitest-environment jsdom
import { describe, test, expect, vi, beforeEach, afterEach } from 'vitest';
import React from 'react';
import { render, screen, fireEvent, cleanup } from '@testing-library/react';
import { TaskCoachPanel } from '../src/components/TaskCoachPanel';
import { useAppStore } from '../src/store/useAppStore';
import { TaskResponse, TaskAssistProposal } from '../src/types/api';

// Mock SSE hook
vi.mock('../src/hooks/useTaskAssist', () => ({
  useTaskAssist: () => {}
}));

describe('TaskCoachPanel and TaskAssistProposal UI component tests', () => {
  afterEach(cleanup);

  const mockTasks: TaskResponse[] = [
    {
      id: 'task-1',
      user_id: 'u1',
      thread_id: 't1',
      parent_task_id: null,
      client_node_id: 'a1',
      title: 'Interview a PM',
      description: 'Prepare questions first',
      node_type: 'action',
      status: 'active',
      view_bucket: 'planned',
      estimated_minutes: 30,
      sort_order: 1
    }
  ];

  beforeEach(() => {
    useAppStore.getState().reset();
    useAppStore.getState().resetTaskAssist();
    useAppStore.setState({ boardTasks: mockTasks });
  });

  // 1. Render nothing when closed
  test('renders nothing when closed', () => {
    const { container } = render(<TaskCoachPanel />);
    expect(container.firstChild).toBeNull();
  });

  // 2. Idle State rendering
  test('renders idle input state when opened', () => {
    useAppStore.setState({
      isTaskAssistPanelOpen: true,
      taskAssistActiveTaskId: 'task-1',
      taskAssistStatus: null
    });

    render(<TaskCoachPanel />);

    expect(screen.getByText('目标任务：Interview a PM')).toBeDefined();
    expect(screen.getByText('帮我开始')).toBeDefined();
    expect(screen.getByText('我卡住了')).toBeDefined();
    expect(screen.getByText('拆得更细')).toBeDefined();
    expect(screen.getByPlaceholderText('可以补充您当前的实际情况，例如：“我现在只有10分钟，且有点累。”（可选）')).toBeDefined();
    expect(screen.getByText('召唤教练辅助方案')).toBeDefined();
  });

  // 3. Start Mode click triggers startTaskAssist
  test('mode change and start assist trigger action', async () => {
    const startAssistSpy = vi.fn().mockResolvedValue({ status: 'running' });
    useAppStore.setState({
      isTaskAssistPanelOpen: true,
      taskAssistActiveTaskId: 'task-1',
      taskAssistStatus: null,
      startTaskAssist: startAssistSpy
    });

    render(<TaskCoachPanel />);

    // Click "我卡住了"
    fireEvent.click(screen.getByText('我卡住了'));
    expect(screen.getByPlaceholderText('可以补充具体卡在哪里，例如：“找了半天没找到官方API，也没有找到教程。”（可选）')).toBeDefined();

    // Fill textarea
    const textarea = screen.getByPlaceholderText(/可以补充具体卡在哪里/);
    fireEvent.change(textarea, { target: { value: 'Blocked by library bug' } });

    // Click Submit
    fireEvent.click(screen.getByText('召唤教练辅助方案'));
    expect(startAssistSpy).toHaveBeenCalledWith('task-1', 'unstick', 'Blocked by library bug');
  });

  // 4. Running State rendering
  test('renders running state with logs', () => {
    useAppStore.setState({
      isTaskAssistPanelOpen: true,
      taskAssistActiveTaskId: 'task-1',
      taskAssistStatus: 'running',
      taskAssistLogs: ['Starting...', 'Context ready.']
    });

    render(<TaskCoachPanel />);

    expect(screen.getByText('教练正在深度构思中...')).toBeDefined();
    expect(screen.getByText('Context ready.')).toBeDefined();
    expect(screen.getByText('取消生成')).toBeDefined();
  });

  // 5. Start Proposal Ready rendering
  test('renders start proposal details and applies it', () => {
    const applyAssistSpy = vi.fn().mockResolvedValue(undefined);
    const startProposal: TaskAssistProposal = {
      schema_version: 1,
      proposal_type: 'start',
      summary: 'You can start by creating the list',
      starter_step: {
        draft_id: 'draft-1',
        title: 'Draft PM interview guide outline',
        description: 'Outline 5 key areas',
        estimated_minutes: 5,
        done_criteria: 'Guide outline written',
        start_hint: 'Open editor and type headers',
        fallback_action: null
      }
    };

    useAppStore.setState({
      isTaskAssistPanelOpen: true,
      taskAssistActiveTaskId: 'task-1',
      taskAssistActiveRequestId: 'req-1',
      taskAssistStatus: 'ready',
      taskAssistProposal: startProposal,
      applyTaskAssist: applyAssistSpy
    });

    render(<TaskCoachPanel />);

    expect(screen.getByText('You can start by creating the list')).toBeDefined();
    expect(screen.getByText('Draft PM interview guide outline')).toBeDefined();
    expect(screen.getByText('Guide outline written')).toBeDefined();
    expect(screen.getByText('Open editor and type headers')).toBeDefined();
    expect(screen.getByText('5 分钟')).toBeDefined();

    const applyButton = screen.getByText('保存为开始提示');
    expect(applyButton).toBeDefined();
    fireEvent.click(applyButton);

    expect(applyAssistSpy).toHaveBeenCalledWith('task-1', 'req-1', null);
  });

  // 6. Unstick Proposal Ready rendering with selection requirement
  test('renders unstick proposal with selection requirements', () => {
    const applyAssistSpy = vi.fn().mockResolvedValue(undefined);
    const unstickProposal: TaskAssistProposal = {
      schema_version: 1,
      proposal_type: 'unstick',
      obstacle_summary: 'Stuck on guide structure',
      recommended_option_id: 'opt-1',
      options: [
        { option_id: 'opt-1', title: 'Find outline template', action: 'Search template online', estimated_minutes: 5, tradeoff: 'May not fit precisely' },
        { option_id: 'opt-2', title: 'Write down first questions', action: 'Write 3 questions directly', estimated_minutes: 10, tradeoff: 'Could miss general topics' }
      ]
    };

    useAppStore.setState({
      isTaskAssistPanelOpen: true,
      taskAssistActiveTaskId: 'task-1',
      taskAssistActiveRequestId: 'req-1',
      taskAssistStatus: 'ready',
      taskAssistProposal: unstickProposal,
      applyTaskAssist: applyAssistSpy
    });

    render(<TaskCoachPanel />);

    expect(screen.getByText('Stuck on guide structure')).toBeDefined();
    expect(screen.getByText('Find outline template')).toBeDefined();
    expect(screen.getByText('Write 3 questions directly')).toBeDefined();

    const applyButton = screen.getByText('使用这个降级动作');
    expect((applyButton as HTMLButtonElement).disabled).toBeFalsy();
    fireEvent.click(applyButton);
    expect(applyAssistSpy).toHaveBeenCalledWith('task-1', 'req-1', 'opt-1');
  });

  // 7. Decompose Proposal Ready rendering
  test('renders decompose proposal details', () => {
    const applyAssistSpy = vi.fn().mockResolvedValue(undefined);
    const decomposeProposal: TaskAssistProposal = {
      schema_version: 1,
      proposal_type: 'decompose',
      summary: 'Decomposing the task into smaller chunks',
      completion_rule: 'all_subtasks_completed',
      subtasks: [
        { draft_id: 'draft-1', title: 'Subtask A', description: 'Desc A', estimated_minutes: 10, done_criteria: 'Crit A', start_hint: null, fallback_action: null },
        { draft_id: 'draft-2', title: 'Subtask B', description: 'Desc B', estimated_minutes: 20, done_criteria: 'Crit B', start_hint: null, fallback_action: null }
      ],
      dependencies: []
    };

    useAppStore.setState({
      isTaskAssistPanelOpen: true,
      taskAssistActiveTaskId: 'task-1',
      taskAssistActiveRequestId: 'req-1',
      taskAssistStatus: 'ready',
      taskAssistProposal: decomposeProposal,
      applyTaskAssist: applyAssistSpy
    });

    render(<TaskCoachPanel />);

    expect(screen.getByText('Decomposing the task into smaller chunks')).toBeDefined();
    expect(screen.getByText('Subtask A')).toBeDefined();
    expect(screen.getByText('Subtask B')).toBeDefined();

    const applyButton = screen.getByText('确认拆分任务');
    fireEvent.click(applyButton);
    expect(applyAssistSpy).toHaveBeenCalledWith('task-1', 'req-1', null);
  });

  // 8. Running cancel success: clears state and closes panel
  test('running cancel success clears state and closes panel', async () => {
    const cancelAssistSpy = vi.fn().mockResolvedValue(undefined);
    useAppStore.setState({
      isTaskAssistPanelOpen: true,
      taskAssistActiveTaskId: 'task-1',
      taskAssistActiveRequestId: 'req-1',
      taskAssistStatus: 'running',
      taskAssistStage: 'queued',
      taskAssistLogs: ['queued log...'],
      cancelTaskAssist: cancelAssistSpy
    });

    render(<TaskCoachPanel />);

    const cancelButton = screen.getByText('取消生成');
    fireEvent.click(cancelButton);

    expect(cancelAssistSpy).toHaveBeenCalledWith('task-1', 'req-1');

    await vi.waitFor(() => {
      const state = useAppStore.getState();
      expect(state.isTaskAssistPanelOpen).toBe(false);
      expect(state.taskAssistActiveTaskId).toBeNull();
    });
  });

  // 9. Running cancel failure: retains panel and shows visible error
  test('running cancel failure retains panel and shows visible error', async () => {
    const cancelAssistSpy = vi.fn().mockRejectedValue(new Error('Cancel failed server error'));
    useAppStore.setState({
      isTaskAssistPanelOpen: true,
      taskAssistActiveTaskId: 'task-1',
      taskAssistActiveRequestId: 'req-1',
      taskAssistStatus: 'running',
      taskAssistStage: 'queued',
      taskAssistLogs: ['queued log...'],
      cancelTaskAssist: cancelAssistSpy
    });

    render(<TaskCoachPanel />);

    const cancelButton = screen.getByText('取消生成');
    fireEvent.click(cancelButton);

    expect(cancelAssistSpy).toHaveBeenCalledWith('task-1', 'req-1');

    // Wait for the rejection to render
    expect(await screen.findByText('Cancel failed server error')).toBeDefined();

    // Verify panel is still open and loading components are not cleared
    const state = useAppStore.getState();
    expect(state.isTaskAssistPanelOpen).toBe(true);
    expect(screen.getByText('教练正在深度构思中...')).toBeDefined();
  });
});
