// @vitest-environment jsdom
import { describe, test, expect, vi, beforeEach, afterEach } from 'vitest';
import React from 'react';
import { render, screen, fireEvent, cleanup } from '@testing-library/react';
import { PortfolioOverview } from '../src/components/PortfolioOverview';
import { useAppStore } from '../src/store/useAppStore';
import { TaskResponse, ThreadSnapshot } from '../src/types/api';

describe('PortfolioOverview component tests', () => {
  afterEach(cleanup);

  const mockProjects = [
    { id: 'proj-1', title: 'Transition to PM', source: 'ai' },
    { id: 'proj-2', title: 'Manual Project', source: 'manual' }
  ];

  const mockTasks: TaskResponse[] = [
    { id: 't1', client_node_id: 'a1', thread_id: 'proj-1', phase_id: 'phase-1', source: 'ai', node_type: 'action', status: 'completed', phase_order: 1, sort_order: 1, title: '完成自评' },
    { id: 't2', client_node_id: 'a2', thread_id: 'proj-1', phase_id: 'phase-1', source: 'ai', node_type: 'action', status: 'active', phase_order: 1, sort_order: 2, title: '访谈一位产品经理' }
  ];

  const mockSnapshot: ThreadSnapshot = {
    thread_id: 'proj-1',
    status: 'succeeded',
    state_version: 1,
    last_event_id: null,
    server_time: '2026-07-04T00:00:00Z',
    intent_text: 'Transition to PM',
    task_tree: {
      root: { client_node_id: 'root', title: 'Root', verb: 'do', estimated_minutes: 0, node_type: 'group' },
      summary: 'Transition plan',
      planning_context: {
        schema_version: 1,
        intent_type: 'exploration_decision',
        time_horizon: 'weeks',
        roadmap: [
          { phase_id: 'phase-1', order: 1, title: '验证岗位匹配度', objective: 'Verify fit', status: 'current' }
        ],
        current_phase: {
          phase_id: 'phase-1',
          title: '验证岗位匹配度',
          objective: 'Verify fit',
          completion_rule: 'all_ai_actions_completed'
        },
        next_action_client_node_id: 'a2'
      }
    }
  };

  const fetchProjectSnapshotsSpy = vi.fn().mockResolvedValue(undefined);
  const setSelectedProjectIdSpy = vi.fn();
  const setCurrentViewBucketSpy = vi.fn();

  beforeEach(() => {
    vi.clearAllMocks();
    useAppStore.setState({
      projectSnapshots: {
        'proj-1': mockSnapshot,
        'proj-2': undefined
      },
      fetchProjectSnapshots: fetchProjectSnapshotsSpy,
      setSelectedProjectId: setSelectedProjectIdSpy,
      setCurrentViewBucket: setCurrentViewBucketSpy,
      highlightedProjectId: null
    });
  });

  test('renders project phase, progress and next action', () => {
    render(<PortfolioOverview projects={mockProjects} tasks={mockTasks} />);

    // Assert labels derived from selector are rendered
    expect(screen.getByText('验证岗位匹配度')).toBeTruthy();
    expect(screen.getByText('1 / 2')).toBeTruthy();
    expect(screen.getByText('访谈一位产品经理')).toBeTruthy();
  });

  test('one failed/missing snapshot still leaves every project card visible', () => {
    render(<PortfolioOverview projects={mockProjects} tasks={mockTasks} />);

    expect(screen.getByText('Transition to PM')).toBeTruthy();
    expect(screen.getByText('Manual Project')).toBeTruthy();
  });

  test('manual project displays "手动计划" type label', () => {
    render(<PortfolioOverview projects={mockProjects} tasks={mockTasks} />);

    expect(screen.getByText('手动计划')).toBeTruthy();
  });

  test('clicking a card calls setSelectedProjectId with that project ID', () => {
    render(<PortfolioOverview projects={mockProjects} tasks={mockTasks} />);

    const card = screen.getByText('Transition to PM').closest('div');
    expect(card).toBeTruthy();
    if (card) fireEvent.click(card);

    expect(setSelectedProjectIdSpy).toHaveBeenCalledWith('proj-1');
  });

  test('rerendering with the same project IDs does not refetch every snapshot', () => {
    const { rerender } = render(<PortfolioOverview projects={mockProjects} tasks={mockTasks} />);
    expect(fetchProjectSnapshotsSpy).toHaveBeenCalledTimes(1);

    // Rerender with new array reference but identical IDs
    rerender(<PortfolioOverview projects={[...mockProjects]} tasks={mockTasks} />);
    expect(fetchProjectSnapshotsSpy).toHaveBeenCalledTimes(1); // Should remain 1
  });

  test('no project-level Roadmap panel is rendered in the portfolio view', () => {
    render(<PortfolioOverview projects={mockProjects} tasks={mockTasks} />);

    // Roadmap panel headings or sections (e.g., "Roadmap", "Current Phase Objectives") should not exist
    expect(screen.queryByText('Current Phase Objectives')).toBeNull();
    expect(screen.queryByText('Roadmap Overview')).toBeNull();
  });
});
