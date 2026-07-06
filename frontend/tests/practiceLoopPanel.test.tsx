// @vitest-environment jsdom
import { describe, expect, it, vi, afterEach } from 'vitest';
import React from 'react';
import { render, screen, fireEvent, cleanup } from '@testing-library/react';
import { PracticeLoopPanel } from '../src/components/PracticeLoopPanel';

afterEach(() => {
  cleanup();
});

describe('PracticeLoopPanel Component', () => {
  const mockLoops = [
    {
      loopId: 'loop-1',
      loopKey: 'n3_vocab',
      title: '完成一次 N3 词汇练习',
      doneCriteria: '完成 20 道题',
      targetPerWeek: 3,
      currentWeekCompleted: 2,
      totalCompleted: 5,
      requiredCompletions: 8,
      estimatedEnd: '2026-08-02',
      status: 'active' as const,
      canScheduleToday: true,
      activeOccurrenceTaskId: null,
      weeklyLabel: '本周 2 / 3 次',
      totalLabel: '总计 5 / 8 次'
    }
  ];

  it('shows progress and schedules only when backend allows it', () => {
    const onSchedule = vi.fn().mockResolvedValue(undefined);
    render(
      <PracticeLoopPanel
        loops={mockLoops}
        onSchedule={onSchedule}
        isPending={false}
        practiceError={null}
      />
    );

    expect(screen.getByText('本周 2 / 3 次')).toBeTruthy();
    expect(screen.getByText('总计 5 / 8 次')).toBeTruthy();
    expect(screen.getByText('完成 20 道题')).toBeTruthy();

    const button = screen.getByRole('button', { name: '安排到今天' });
    expect((button as HTMLButtonElement).disabled).toBe(false);

    fireEvent.click(button);
    expect(onSchedule).toHaveBeenCalledWith('loop-1');
  });

  it('disables schedule button if active occurrence task exists', () => {
    const loopsWithActive = [
      {
        ...mockLoops[0],
        activeOccurrenceTaskId: 'task-123'
      }
    ];

    render(
      <PracticeLoopPanel
        loops={loopsWithActive}
        onSchedule={vi.fn()}
        isPending={false}
        practiceError={null}
      />
    );

    const button = screen.getByRole('button', { name: '安排到今天' });
    expect((button as HTMLButtonElement).disabled).toBe(true);
    expect(screen.getByText('今天已生成待办任务，请先完成')).toBeTruthy();
  });
});
