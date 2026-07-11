// @vitest-environment jsdom
import { describe, test, expect, vi, afterEach } from 'vitest';
import React from 'react';
import { render, screen, cleanup } from '@testing-library/react';
import { DeliverySummary } from '../src/components/DeliverySummary';
import { DecisionCard } from '../src/components/DecisionCard';
import { StrategyOverview } from '../src/components/StrategyOverview';
import { PortfolioOverview } from '../src/components/PortfolioOverview';
import { useAppStore } from '../src/store/useAppStore';
import { TaskNode, TaskResponse, ThreadSnapshot, StrategyContext } from '../src/types/api';

describe('StrategyOverview visual components tests', () => {
  afterEach(cleanup);

  const mockRootNode: TaskNode = {
    client_node_id: 'root',
    title: 'Root Node',
    verb: 'do',
    estimated_minutes: 0,
    node_type: 'group',
    children: [
      { client_node_id: 'task-1', title: '撰写竞品分析', verb: 'write', estimated_minutes: 90, node_type: 'action' },
      { client_node_id: 'task-2', title: '整理商业画布', verb: 'draw', estimated_minutes: 105, node_type: 'action' },
      { client_node_id: 'task-3', title: '补充附录数据', verb: 'gather', estimated_minutes: 60, node_type: 'action' }
    ]
  };

  const deliveryCtx = {
    schema_version: 1 as const,
    strategy_type: 'delivery' as const,
    deliverable: {
      title: '商业计划书初稿',
      format: 'Word 文档',
      quality_bar: ['各章节逻辑自洽', '包含三页财务测算']
    },
    deadline: {
      text: '今天下午 4 点前',
      is_explicit: true
    },
    time_plan: {
      available_minutes: 240,
      planned_minutes: 195,
      buffer_minutes: 45
    },
    scope: {
      must_have: ['竞品分析表', '市场分析'],
      should_have: ['财务预测表'],
      can_cut: ['封面视觉美化']
    },
    workstreams: [
      {
        workstream_id: 'stream-1',
        title: '市场竞品分析',
        output: '商业竞品章节',
        task_client_node_ids: ['task-1']
      }
    ],
    critical_path_client_node_ids: ['task-1', 'task-2']
  };

  const decisionCtx = {
    schema_version: 1 as const,
    strategy_type: 'decision' as const,
    question: '现在是否应该直接离职创业？',
    options: ['立即离职创业', '保持当前主业并在周末兼职探索'],
    current_judgment: {
      direction: 'continue_exploring' as const,
      statement: '目前暂不建议立即直接离职，首选在周末进行低成本兼职验证。',
      confidence: 'medium' as const
    },
    basis: [
      { statement: '核心商业模式尚未闭环。', basis_type: 'known_constraint' as const },
      { statement: '用户目前没有真实的付费行为。', basis_type: 'working_assumption' as const }
    ],
    missing_information: ['目标客户的真实付费意愿', '获客渠道的真实转化率'],
    experiments: [
      {
        experiment_id: 'exp-1',
        title: '产品落地页付费意愿验证',
        hypothesis: '落地页转化率大于 5%',
        success_signal: '有超过 10 人点击模拟预约按钮',
        effort_level: 'low' as const,
        task_client_node_ids: ['task-1']
      }
    ],
    decision_gate: {
      review_after: '一个月后或付费意愿测试结束后',
      proceed_if: ['有超过 20 个高意向种子客户'],
      stop_if: ['付费测试反馈极差', '兼职精力严重透支']
    }
  };

  // 1. DeliverySummary Render Test
  test('renders DeliverySummary correctly with detailed fields', () => {
    render(<DeliverySummary context={deliveryCtx} rootNode={mockRootNode} />);

    // Check deliverable
    expect(screen.getByText('商业计划书初稿')).toBeTruthy();
    expect(screen.getByText('Word 文档')).toBeTruthy();
    expect(screen.getByText('各章节逻辑自洽')).toBeTruthy();

    // Check deadline
    expect(screen.getByText('今天下午 4 点前')).toBeTruthy();

    // Check time plan
    expect(screen.getByText('预计耗时: 3.5 小时')).toBeTruthy(); // 195 mins -> 3.5h
    expect(screen.getByText('安全缓冲: 1 小时')).toBeDefined();    // 45 mins -> 1h

    // Check scope headings and items
    expect(screen.getByText('必须完成 / Must Have')).toBeTruthy();
    expect(screen.getByText('竞品分析表')).toBeTruthy();
    expect(screen.getByText('时间不足时可舍弃 / Can Cut')).toBeTruthy();
    expect(screen.getByText('封面视觉美化')).toBeTruthy();

    // Check workstream output and task reference resolution
    expect(screen.getByText('市场竞品分析')).toBeTruthy();
    expect(screen.getByText('商业竞品章节')).toBeTruthy();
    expect(screen.getAllByText('撰写竞品分析').length).toBeGreaterThanOrEqual(1); // Resolved task title

    // Check critical path
    expect(screen.getByText('整理商业画布')).toBeTruthy(); // Resolved critical path task title
  });

  test('DeliverySummary hides missing optional scopes', () => {
    const contextNoCanCut = {
      ...deliveryCtx,
      scope: {
        must_have: ['竞品分析表'],
        should_have: [],
        can_cut: []
      }
    };

    render(<DeliverySummary context={contextNoCanCut} rootNode={mockRootNode} />);

    expect(screen.queryByText('时间不足时可舍弃 / Can Cut')).toBeNull();
    expect(screen.queryByText('封面视觉美化')).toBeNull();
  });

  // 2. DecisionCard Render Test
  test('renders DecisionCard correctly with confidence, basis and experiments', () => {
    render(<DecisionCard context={decisionCtx} rootNode={mockRootNode} />);

    // Question & options
    expect(screen.getByText('现在是否应该直接离职创业？')).toBeTruthy();
    expect(screen.getByText('选项 1: 立即离职创业')).toBeTruthy();

    // Judgment
    expect(screen.getByText('目前暂不建议立即直接离职，首选在周末进行低成本兼职验证。')).toBeTruthy();
    expect(screen.getByText('置信度：有一定依据')).toBeTruthy(); // Confidence 'medium' -> '有一定依据'

    // Basis
    expect(screen.getByText('核心商业模式尚未闭环。')).toBeTruthy();
    expect(screen.getByText('工作假设（需验证）')).toBeTruthy(); // Basis type 'working_assumption' check

    // Missing info
    expect(screen.getByText('目标客户的真实付费意愿')).toBeTruthy();

    // Experiments
    expect(screen.getByText('产品落地页付费意愿验证')).toBeTruthy();
    expect(screen.getByText('落地页转化率大于 5%')).toBeTruthy();
    expect(screen.getByText('撰写竞品分析')).toBeTruthy(); // Resolved task

    // Gate
    expect(screen.getByText('继续推进条件')).toBeTruthy();
    expect(screen.getByText('有超过 20 个高意向种子客户')).toBeTruthy();
    expect(screen.getByText('触发终止/调整条件')).toBeTruthy();
    expect(screen.getByText('兼职精力严重透支')).toBeTruthy();
  });

  // 3. StrategyOverview Switch & Fallback Test
  test('StrategyOverview routes to DeliverySummary', () => {
    const tree = {
      root: mockRootNode,
      summary: 'Raw summary',
      strategy_context: deliveryCtx
    };
    render(<StrategyOverview taskTree={tree} />);
    expect(screen.getByText('商业计划书初稿')).toBeTruthy();
  });

  test('StrategyOverview routes to DecisionCard', () => {
    const tree = {
      root: mockRootNode,
      summary: 'Raw summary',
      strategy_context: decisionCtx
    };
    render(<StrategyOverview taskTree={tree} />);
    expect(screen.getByText('现在是否应该直接离职创业？')).toBeTruthy();
  });

  test('StrategyOverview falls back to legacy exploration summary when strategy_context is missing', () => {
    const tree = {
      root: mockRootNode,
      summary: '当前判断：计划读研\n判断依据：追求学术深造\n下一步探索：查看招生简章',
      planning_context: {
        schema_version: 1 as const,
        intent_type: 'exploration_decision' as const,
        time_horizon: 'weeks' as const,
        roadmap: []
      }
    };
    render(<StrategyOverview taskTree={tree} />);
    expect(screen.getByText('计划读研')).toBeTruthy();
    expect(screen.getByText('追求学术深造')).toBeTruthy();
    expect(screen.getByText('查看招生简章')).toBeTruthy();
  });

  test('StrategyOverview falls back to standard summary for generic plans or parse failure', () => {
    const tree = {
      root: mockRootNode,
      summary: '这是一个普普通通的普通任务树摘要内容。'
    };
    render(<StrategyOverview taskTree={tree} />);
    expect(screen.getByText('这是一个普普通通的普通任务树摘要内容。')).toBeTruthy();
  });

  test('StrategyOverview falls back safely on malformed strategy_context', () => {
    const tree = {
      root: mockRootNode,
      summary: '容错机制摘要文本。',
      strategy_context: {
        strategy_type: 'invalid_type_here'
      } as unknown as StrategyContext
    };
    render(<StrategyOverview taskTree={tree} />);
    expect(screen.getByText('容错机制摘要文本。')).toBeTruthy();
  });

  test('StrategyOverview falls back safely on malformed strategy_context with valid discriminator but missing fields', () => {
    const tree = {
      root: mockRootNode,
      summary: '有效类别但缺少必需字段的摘要。',
      strategy_context: {
        strategy_type: 'delivery'
      } as unknown as StrategyContext
    };
    render(<StrategyOverview taskTree={tree} />);
    expect(screen.getByText('有效类别但缺少必需字段的摘要。')).toBeTruthy();
  });

  // 4. PortfolioOverview Single Line Summary Test
  test('PortfolioOverview renders single-line summaries and type label only, no strategy panels', () => {
    const mockProjects = [
      { id: 'proj-deliv', title: '交付物项目', source: 'ai' },
      { id: 'proj-decis', title: '决策性项目', source: 'ai' }
    ];

    const mockTasks: TaskResponse[] = [];

    const snapshotDeliv: ThreadSnapshot = {
      thread_id: 'proj-deliv',
      status: 'succeeded',
      state_version: 1,
      last_event_id: null,
      server_time: '2026-07-04T00:00:00Z',
      intent_text: 'Buy a laptop',
      task_tree: {
        root: mockRootNode,
        summary: 'Buy laptop summary',
        strategy_context: deliveryCtx
      }
    };

    const snapshotDecis: ThreadSnapshot = {
      thread_id: 'proj-decis',
      status: 'succeeded',
      state_version: 1,
      last_event_id: null,
      server_time: '2026-07-04T00:00:00Z',
      intent_text: 'Should I buy a laptop',
      task_tree: {
        root: mockRootNode,
        summary: 'Should buy summary',
        strategy_context: decisionCtx
      }
    };

    useAppStore.setState({
      projectSnapshots: {
        'proj-deliv': snapshotDeliv,
        'proj-decis': snapshotDecis
      },
      fetchProjectSnapshots: vi.fn(),
      setSelectedProjectId: vi.fn(),
      setCurrentViewBucket: vi.fn()
    });

    render(<PortfolioOverview projects={mockProjects} tasks={mockTasks} />);

    // Assert cards show type label and one-line summaries
    expect(screen.getByText('短期交付')).toBeTruthy();
    expect(screen.getByText('探索决策')).toBeTruthy();
    expect(screen.getByText('交付目标: 商业计划书初稿')).toBeTruthy();
    expect(screen.getByText('当前判断: 目前暂不建议立即直接离职，首选在周末进行低成本兼职验证。')).toBeTruthy();

    // Verify it doesn't render full card items like deadline buffer titles
    expect(screen.queryByText('预计耗时: 3.5 小时')).toBeNull();
    expect(screen.queryByText('安全缓冲: 1 小时')).toBeNull();
    expect(screen.queryByText('低成本验证 / Verification')).toBeNull();
  });
});
