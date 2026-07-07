// @vitest-environment jsdom
import { describe, expect, it, afterEach } from 'vitest';
import React from 'react';
import { render, screen, fireEvent, cleanup } from '@testing-library/react';
import { PhaseRecords } from '../src/components/PhaseRecords';
import { PhaseReview, RoadmapPhase } from '../types/api';

afterEach(() => {
  cleanup();
});

describe('PhaseRecords Component', () => {
  const roadmap: RoadmapPhase[] = [
    { phase_id: 'phase-1', order: 1, title: '第一阶段词汇基础', objective: 'Objective 1', status: 'completed' }
  ];

  const reviewHistory: PhaseReview[] = [
    {
      id: 'review-history-1',
      phase_id: 'phase-1',
      status: 'finalized',
      recommendation: 'ready',
      decision: 'proceed',
      evidence: {
        vocab_test: { value: 75, url: 'https://test.com/result' }
      },
      difficulty: '遇到了一些生僻词',
      next_capacity: '熟练掌握了300个基础词',
      override_reason: null,
      statistics: {
        adherence: 0.9,
        completed_days: 9,
        total_days: 10
      },
      created_at: '2026-07-05T00:00:00Z',
      updated_at: '2026-07-05T00:00:00Z'
    }
  ];

  it('renders finalized phase list and expands details on click', () => {
    render(<PhaseRecords reviewHistory={reviewHistory} roadmap={roadmap} />);

    const headerBtn = screen.getByRole('button', { name: /第一阶段词汇基础/ });
    expect(headerBtn).toBeTruthy();

    expect(screen.queryByText('练习周坚持率 (Adherence)')).toBeNull();

    fireEvent.click(headerBtn);

    expect(screen.getByText('练习周坚持率 (Adherence)')).toBeTruthy();
    expect(screen.getByText('90%')).toBeTruthy();
    expect(screen.getByText('遇到了一些生僻词')).toBeTruthy();
    expect(screen.getByText('熟练掌握了300个基础词')).toBeTruthy();
    expect(screen.getByText('https://test.com/result')).toBeTruthy();
  });
});
