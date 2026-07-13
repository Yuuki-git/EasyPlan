// @vitest-environment jsdom
import { describe, test, expect, vi, beforeEach, afterEach } from 'vitest';
import React from 'react';
import { render, screen, fireEvent, cleanup } from '@testing-library/react';
import { BoardTaskNode, TaskBoard } from '../src/components/TaskBoard';
import { useAppStore } from '../src/store/useAppStore';
import { TaskNode } from '../src/types/api';

describe('Task Assist Roll-up progress and Checkbox locking UI tests', () => {
  afterEach(cleanup);

  beforeEach(() => {
    useAppStore.getState().reset();
    useAppStore.getState().resetTaskAssist();
    
    // Stub VITE_TASK_ASSIST_ENABLED
    import.meta.env.VITE_TASK_ASSIST_ENABLED = 'true';
  });

  // 1. Renders coach button on active action task, but not on group/completed/practice task
  test('Coach button visibility rules', () => {
    const activeAction: TaskNode = {
      id: 't-active', title: 'Active action', node_type: 'action', status: 'active',
      user_id: 'u1', thread_id: 'thread-1', parent_task_id: null, client_node_id: 'a1', description: null, view_bucket: 'planned', estimated_minutes: 10, sort_order: 1
    };

    const completedAction: TaskNode = {
      id: 't-completed', title: 'Completed action', node_type: 'action', status: 'completed',
      user_id: 'u1', thread_id: 'thread-1', parent_task_id: null, client_node_id: 'a2', description: null, view_bucket: 'planned', estimated_minutes: 10, sort_order: 1
    };

    const groupNode: TaskNode = {
      id: 't-group', title: 'Group task', node_type: 'group', status: 'active',
      user_id: 'u1', thread_id: 'thread-1', parent_task_id: null, client_node_id: 'g1', description: null, view_bucket: 'planned', estimated_minutes: null, sort_order: 1
    };

    const practiceAction: TaskNode = {
      id: 't-practice', title: 'Practice occurrence', node_type: 'action', status: 'active', practice_loop_id: 'loop-1',
      user_id: 'u1', thread_id: 'thread-1', parent_task_id: null, client_node_id: 'a3', description: null, view_bucket: 'planned', estimated_minutes: 10, sort_order: 1
    };

    // Render active action - Coach button should be visible
    const { rerender } = render(<BoardTaskNode node={activeAction} />);
    const coachButton = screen.queryByTitle('行动教练 (AI 辅助)');
    expect(coachButton).toBeDefined();

    // Rerender completed action - Coach button should NOT be visible
    rerender(<BoardTaskNode node={completedAction} />);
    expect(screen.queryByTitle('行动教练 (AI 辅助)')).toBeNull();

    // Rerender group node
    rerender(<BoardTaskNode node={groupNode} />);
    expect(screen.queryByTitle('行动教练 (AI 辅助)')).toBeNull();

    // Rerender practice action
    rerender(<BoardTaskNode node={practiceAction} />);
    expect(screen.queryByTitle('行动教练 (AI 辅助)')).toBeNull();
  });

  // 2. Click Coach button triggers open panel
  test('Clicking coach button opens panel', () => {
    const activeAction: TaskNode = {
      id: 't-active', title: 'Active action', node_type: 'action', status: 'active',
      user_id: 'u1', thread_id: 'thread-1', parent_task_id: null, client_node_id: 'a1', description: null, view_bucket: 'planned', estimated_minutes: 10, sort_order: 1
    };

    const setOpenSpy = vi.fn();
    const setTaskIdSpy = vi.fn();
    useAppStore.setState({
      setTaskAssistPanelOpen: setOpenSpy,
      setTaskAssistActiveTaskId: setTaskIdSpy
    });

    render(<BoardTaskNode node={activeAction} />);
    const coachButton = screen.getByTitle('行动教练 (AI 辅助)');
    fireEvent.click(coachButton);

    expect(setTaskIdSpy).toHaveBeenCalledWith('t-active');
    expect(setOpenSpy).toHaveBeenCalledWith(true);
  });

  // 3. Roll-up locking: disables parent toggle checkbox while incomplete children exist, displays tooltip
  test('disables checkbox and locks toggle when there are incomplete children', async () => {
    const updateStatusSpy = vi.fn();
    useAppStore.setState({ updateTaskStatus: updateStatusSpy });

    const parentNode: TaskNode = {
      id: 'parent-1', title: 'Parent Task', node_type: 'action', status: 'active',
      user_id: 'u1', thread_id: 't1', parent_task_id: null, client_node_id: 'p1', description: null, view_bucket: 'planned', estimated_minutes: 30, sort_order: 1,
      children: [
        { id: 'child-1', title: 'Child A', node_type: 'action', status: 'completed', user_id: 'u1', thread_id: 't1', parent_task_id: 'parent-1', client_node_id: 'c1', description: null, view_bucket: 'planned', estimated_minutes: 15, sort_order: 1, source: 'task_assist' },
        { id: 'child-2', title: 'Child B', node_type: 'action', status: 'active', user_id: 'u1', thread_id: 't1', parent_task_id: 'parent-1', client_node_id: 'c2', description: null, view_bucket: 'planned', estimated_minutes: 15, sort_order: 2, source: 'task_assist' }
      ]
    };

    render(<BoardTaskNode node={parentNode} />);

    // Progress text
    expect(screen.getByText('子任务 1/2')).toBeDefined();

    // Checkbox container wrapper has the title tooltip
    const checkboxWrapper = screen.getByTitle('有未完成的子任务，请先完成子任务以自动归纳/完成父任务');
    expect(checkboxWrapper).toBeDefined();

    // Try to toggle parent - clicking checkboxWrapper should stop propagation and handleToggle should reject
    fireEvent.click(checkboxWrapper);
    expect(updateStatusSpy).not.toHaveBeenCalled();

    // Try to toggle whole card
    const card = screen.getByText('Parent Task');
    fireEvent.click(card);
    expect(updateStatusSpy).not.toHaveBeenCalled();
  });

  // 4. Expand/Collapse subtasks click
  test('can collapse and expand subtasks rendering', () => {
    const parentNode: TaskNode = {
      id: 'parent-1', title: 'Parent Task', node_type: 'action', status: 'active',
      user_id: 'u1', thread_id: 't1', parent_task_id: null, client_node_id: 'p1', description: null, view_bucket: 'planned', estimated_minutes: 30, sort_order: 1,
      children: [
        { id: 'child-1', title: 'Child A', node_type: 'action', status: 'completed', user_id: 'u1', thread_id: 't1', parent_task_id: 'parent-1', client_node_id: 'c1', description: null, view_bucket: 'planned', estimated_minutes: 15, sort_order: 1, source: 'task_assist' }
      ]
    };

    render(<BoardTaskNode node={parentNode} />);

    expect(screen.getByText('Child A')).toBeDefined();
    expect(screen.getByText('收起子任务')).toBeDefined();

    // Collapse
    fireEvent.click(screen.getByText('收起子任务'));
    expect(screen.queryByText('Child A')).toBeNull();
    expect(screen.getByText('展开子任务')).toBeDefined();

    // Expand
    fireEvent.click(screen.getByText('展开子任务'));
    expect(screen.getByText('Child A')).toBeDefined();
  });

  // 5. Reconstruct My Day tree and displays progress/rollup check rules
  test('reconstructs My Day tree and displays progress/rollup check rules', () => {
    useAppStore.setState({
      currentViewBucket: 'my_day',
      boardTasks: [
        { id: 'p-1', title: 'Parent Task', node_type: 'action', status: 'active', user_id: 'u1', thread_id: 't1', parent_task_id: null, client_node_id: 'p1', description: null, view_bucket: 'planned', estimated_minutes: 30, sort_order: 1, is_in_my_day: true },
        { id: 'c-1', title: 'Assist Child', node_type: 'action', status: 'active', user_id: 'u1', thread_id: 't1', parent_task_id: 'p-1', client_node_id: 'c1', description: null, view_bucket: 'planned', estimated_minutes: 15, sort_order: 1, source: 'task_assist', is_in_my_day: false }
      ]
    });

    useAppStore.setState({
      token: 'fake-token',
      selectedProjectId: 't1',
      currentViewBucket: 'my_day'
    });

    render(<TaskBoard />);

    expect(screen.getAllByText('Parent Task').length).toBeGreaterThanOrEqual(1);
    expect(screen.getByText('子任务 0/1')).toBeDefined();

    const checkboxWrapper = screen.getByTitle('有未完成的子任务，请先完成子任务以自动归纳/完成父任务');
    expect(checkboxWrapper).toBeDefined();
  });

  // 6. Assist child does not show My Day sun button
  test('Assist child does not show My Day sun button', () => {
    const assistChildNode: TaskNode = {
      id: 'c-1', title: 'Assist Child', node_type: 'action', status: 'active',
      user_id: 'u1', thread_id: 't1', parent_task_id: 'p-1', client_node_id: 'c1',
      description: null, view_bucket: 'planned', estimated_minutes: 15, sort_order: 1,
      source: 'task_assist'
    };

    render(<BoardTaskNode node={assistChildNode} />);
    expect(screen.queryByTitle('加入我的一天')).toBeNull();
    expect(screen.queryByTitle('移出我的一天')).toBeNull();
  });

  // 7. Orphan assist child is omitted from My Day top-level tasks
  test('Orphan assist child is omitted from top-level tasks in My Day view', () => {
    useAppStore.setState({
      token: 'fake-token',
      selectedProjectId: 't1',
      currentViewBucket: 'my_day',
      boardTasks: [
        { id: 'c-orphan', title: 'Orphan Assist Child', node_type: 'action', status: 'active', user_id: 'u1', thread_id: 't1', parent_task_id: 'parent-missing', client_node_id: 'c2', description: null, view_bucket: 'planned', estimated_minutes: 15, sort_order: 1, source: 'task_assist', is_in_my_day: true }
      ]
    });

    render(<TaskBoard />);
    expect(screen.queryByText('Orphan Assist Child')).toBeNull();
  });
});
