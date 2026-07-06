// @vitest-environment jsdom
import { describe, expect, it, vi, afterEach } from 'vitest';
import React from 'react';
import { render, screen, fireEvent, cleanup } from '@testing-library/react';
import { PhaseReviewPanel } from '../src/components/PhaseReviewPanel';
import { OutcomeCheckpoint, PhaseReviewResponse } from '../types/api';

afterEach(() => {
  cleanup();
});

describe('PhaseReviewPanel Component', () => {
  const checkpoints: OutcomeCheckpoint[] = [
    {
      checkpoint_id: 'vocab_test',
      title: '完成词汇测试',
      evidence_type: 'numeric',
      unit: 'percent',
      operator: 'gte',
      target_value: 65
    },
    {
      checkpoint_id: 'self_rate',
      title: '口语表达流畅度',
      evidence_type: 'self_assessment',
      operator: 'gte',
      target_value: 4
    }
  ];

  const activeReview: PhaseReviewResponse = {
    id: 'review-1',
    phase_id: 'phase-1',
    status: 'draft',
    recommendation: 'partial',
    decision: null,
    evidence: {
      vocab_test: { value: 60 }
    },
    difficulty: '有点难度',
    next_capacity: '学到了新词汇',
    override_reason: null,
    statistics: {},
    created_at: '2026-07-05T00:00:00Z',
    updated_at: '2026-07-05T00:00:00Z'
  };

  it('renders checkpoints and supports saving draft', () => {
    const onSave = vi.fn().mockResolvedValue(undefined);
    render(
      <PhaseReviewPanel
        phaseId="phase-1"
        checkpoints={checkpoints}
        activeReview={activeReview}
        recommendation="partial"
        reviewAvailable={true}
        oneOffReady={true}
        processReady={false}
        outcomeReady={false}
        onSave={onSave}
        onDecide={vi.fn()}
        isPending={false}
        practiceError={null}
      />
    );

    expect(screen.getByText('完成词汇测试')).toBeTruthy();
    expect(screen.getByText('口语表达流畅度')).toBeTruthy();

    const score3Btn = screen.getByRole('button', { name: '3' });
    fireEvent.click(score3Btn);

    const saveBtn = screen.getByRole('button', { name: '保存复盘草稿' });
    fireEvent.click(saveBtn);

    expect(onSave).toHaveBeenCalledWith(
      expect.objectContaining({
        evidence: expect.objectContaining({
          self_rate: { value: 3, url: undefined }
        })
      })
    );
  });

  it('requires override reason for override decision', () => {
    const onDecide = vi.fn().mockResolvedValue(undefined);
    render(
      <PhaseReviewPanel
        phaseId="phase-1"
        checkpoints={checkpoints}
        activeReview={activeReview}
        recommendation="partial"
        reviewAvailable={true}
        oneOffReady={true}
        processReady={false}
        outcomeReady={false}
        onSave={vi.fn()}
        onDecide={onDecide}
        isPending={false}
        practiceError={null}
      />
    );

    const overrideTab = screen.getByRole('button', { name: '人工强行解锁' });
    fireEvent.click(overrideTab);

    const submitBtn = screen.getByRole('button', { name: '提交决策并归档复盘' });
    expect((submitBtn as HTMLButtonElement).disabled).toBe(true);

    const textarea = screen.getByPlaceholderText(/说明强解锁的特殊原因/);
    fireEvent.change(textarea, { target: { value: '特殊计划变动' } });

    expect((submitBtn as HTMLButtonElement).disabled).toBe(false);
    fireEvent.click(submitBtn);

    expect(onDecide).toHaveBeenCalledWith({
      decision: 'override',
      override_reason: '特殊计划变动'
    });
  });
});
