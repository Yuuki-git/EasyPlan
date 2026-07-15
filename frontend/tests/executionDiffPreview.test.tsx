// @vitest-environment jsdom
import { describe, test, expect, beforeEach, afterEach } from 'vitest';
import React from 'react';
import { render, screen, cleanup } from '@testing-library/react';
import { ExecutionDiffPreview } from '../src/components/ExecutionDiffPreview';
import { useAppStore } from '../src/store/useAppStore';
import { TaskResponse, ExecutionRefineProposal } from '../src/types/api';

describe('ExecutionDiffPreview Component Tests', () => {
  afterEach(cleanup);

  const mockTasks: TaskResponse[] = [
    { id: 'task-1', user_id: 'u1', thread_id: 't1', parent_task_id: null, client_node_id: 'n1', title: 'Prepare Presentation', description: 'Make slides', node_type: 'action', status: 'active', view_bucket: 'planned', estimated_minutes: 30, sort_order: 1 },
    { id: 'task-2', user_id: 'u1', thread_id: 't1', parent_task_id: null, client_node_id: 'n2', title: 'Conduct Review', description: null, node_type: 'action', status: 'active', view_bucket: 'planned', estimated_minutes: 20, sort_order: 2 }
  ];

  beforeEach(() => {
    useAppStore.getState().reset();
    useAppStore.setState({ boardTasks: mockTasks });
  });

  test('renders diff preview proposal correctly', () => {
    const proposal: ExecutionRefineProposal = {
      schema_version: 1,
      proposal_type: 'execution_refine',
      mode: 'context_change',
      summary: 'Adjust priorities and duration',
      user_facing_reasons: ['Reason A', 'Reason B'],
      preserved_constraints: ['Preserved C'],
      focus_task_ids: ['task-1'],
      estimated_focus_minutes: 30,
      buffer_minutes: 10,
      warnings: ['Warning W'],
      operations: [
        {
          operation_type: 'update_task',
          task_id: 'task-1',
          changes: {
            title: 'Prepare Presentation (Updated)',
            estimated_minutes: 45
          },
          reason: 'Longer slides preparation needed'
        },
        {
          operation_type: 'add_task',
          draft_id: 'draft-9',
          parent_task_id: null,
          title: 'New Integration Test',
          description: 'Run integration test suite',
          estimated_minutes: 15,
          done_criteria: 'All green',
          depends_on_refs: [],
          insert_after_task_id: 'task-1',
          reason: 'Required for validation'
        },
        {
          operation_type: 'reorder_siblings',
          parent_task_id: null,
          ordered_task_ids: ['task-2', 'task-1'],
          reason: 'Switch order'
        },
        {
          operation_type: 'set_my_day',
          task_id: 'task-2',
          is_in_my_day: true,
          reason: 'Crucial for today'
        }
      ]
    };

    render(<ExecutionDiffPreview proposal={proposal} />);

    // Check Summary
    expect(screen.getByText('Adjust priorities and duration')).toBeDefined();

    // Check Stats
    expect(screen.getAllByText('30 min').length).toBeGreaterThanOrEqual(1);
    expect(screen.getByText('10 min')).toBeDefined();

    // Check Update changes
    expect(screen.getByText('Prepare Presentation (Updated)')).toBeDefined();
    expect(screen.getByText(/Longer slides preparation needed/)).toBeDefined();

    // Check Add changes
    expect(screen.getByText('New Integration Test')).toBeDefined();
    expect(screen.getByText('Run integration test suite')).toBeDefined();

    // Check Reorder details
    expect(screen.getByText(/Switch order/)).toBeDefined();

    // Check My Day changes
    expect(screen.getByText('加入我的一天')).toBeDefined();
    expect(screen.getByText(/Crucial for today/)).toBeDefined();

    // Check reasoning and preserved constraints
    expect(screen.getByText('Reason A')).toBeDefined();
    expect(screen.getByText('Preserved C')).toBeDefined();
    expect(screen.getByText('Warning W')).toBeDefined();
  });
});
