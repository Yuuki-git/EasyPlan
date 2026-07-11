import { describe, test, expect } from 'vitest';
import { 
  isDeliveryContext, 
  isDecisionContext, 
  formatPlannedTime, 
  findNodeById, 
  resolveNodeReferences, 
  getLegacyExplorationSummary 
} from '../src/lib/strategyContext';
import { StrategyContext, TaskNode } from '../src/types/api';

describe('StrategyContext pure helper and selector tests', () => {
  
  // 1. Guards
  test('type guards works correctly', () => {
    const deliveryCtx: StrategyContext = {
      schema_version: 1,
      strategy_type: 'delivery',
      deliverable: { title: 'BP', format: 'doc', quality_bar: ['Q1'] },
      deadline: { text: 'today', is_explicit: true },
      time_plan: { planned_minutes: 180, buffer_minutes: 30 },
      scope: { must_have: ['Must'], should_have: [], can_cut: [] },
      workstreams: [],
      critical_path_client_node_ids: []
    };

    const decisionCtx: StrategyContext = {
      schema_version: 1,
      strategy_type: 'decision',
      question: 'Should I do it?',
      options: ['Yes', 'No'],
      current_judgment: { direction: 'continue_exploring', statement: 'judgment info', confidence: 'medium' },
      basis: [],
      missing_information: [],
      experiments: [],
      decision_gate: { review_after: 'tomorrow', proceed_if: [], stop_if: [] }
    };

    expect(isDeliveryContext(deliveryCtx)).toBe(true);
    expect(isDeliveryContext(decisionCtx)).toBe(false);
    expect(isDeliveryContext(null)).toBe(false);

    expect(isDecisionContext(decisionCtx)).toBe(true);
    expect(isDecisionContext(deliveryCtx)).toBe(false);
    expect(isDecisionContext(undefined)).toBe(false);
  });

  // 2. formatPlannedTime
  test('formatPlannedTime formats minutes to rounded hours', () => {
    expect(formatPlannedTime(195)).toBe('3.5 小时');
    expect(formatPlannedTime(45)).toBe('1 小时');
    expect(formatPlannedTime(15)).toBe('15 分钟');
    expect(formatPlannedTime(0)).toBe('0 分钟');
    expect(formatPlannedTime(-10)).toBe('0 分钟');
    expect(formatPlannedTime(300)).toBe('5 小时');
  });

  // 3. findNodeById
  test('findNodeById recursively searches Node Tree', () => {
    const root: TaskNode = {
      client_node_id: 'root',
      title: 'Root Node',
      verb: 'do',
      estimated_minutes: 10,
      node_type: 'group',
      children: [
        {
          client_node_id: 'child-1',
          title: 'Child 1',
          verb: 'write',
          estimated_minutes: 30,
          node_type: 'action',
          children: []
        },
        {
          client_node_id: 'child-2',
          title: 'Child 2',
          verb: 'check',
          estimated_minutes: 20,
          node_type: 'group',
          children: [
            {
              client_node_id: 'grandchild-1',
              title: 'Grandchild 1',
              verb: 'deploy',
              estimated_minutes: 50,
              node_type: 'action',
              children: []
            }
          ]
        }
      ]
    };

    expect(findNodeById(root, 'root')?.title).toBe('Root Node');
    expect(findNodeById(root, 'child-1')?.title).toBe('Child 1');
    expect(findNodeById(root, 'grandchild-1')?.title).toBe('Grandchild 1');
    expect(findNodeById(root, 'unknown-id')).toBeNull();
  });

  // 4. resolveNodeReferences
  test('resolveNodeReferences resolves references and warns on missing IDs', () => {
    const root: TaskNode = {
      client_node_id: 'root',
      title: 'Root',
      verb: 'do',
      estimated_minutes: 0,
      node_type: 'group',
      children: [
        { client_node_id: 'a1', title: 'Task A1', verb: 'write', estimated_minutes: 10, node_type: 'action' }
      ]
    };

    const { references, diagnostics } = resolveNodeReferences(root, ['a1', 'missing-node']);
    expect(references).toHaveLength(2);
    expect(references[0]).toEqual({ nodeId: 'a1', title: 'Task A1', exists: true });
    expect(references[1]).toEqual({ nodeId: 'missing-node', title: 'missing-node', exists: false });

    // Assert diagnostics report
    expect(diagnostics.missingNodeIds).toEqual(['missing-node']);
  });

  // 5. getLegacyExplorationSummary
  test('getLegacyExplorationSummary parses legacy formatted summary', () => {
    const goodSummary = '当前判断：值得尝试\n判断依据：具有强烈偏好\n下一步探索：访谈业界大佬';
    const legacy = getLegacyExplorationSummary(goodSummary);
    expect(legacy).not.toBeNull();
    expect(legacy?.judgment).toBe('值得尝试');
    expect(legacy?.basis).toBe('具有强烈偏好');
    expect(legacy?.exploration).toBe('访谈业界大佬');

    // Partial summary should still resolve with parser fallbacks
    const badSummary = '当前判断：值得尝试';
    const legacyBad = getLegacyExplorationSummary(badSummary);
    expect(legacyBad).not.toBeNull();
    expect(legacyBad?.judgment).toBe('值得尝试');
    expect(legacyBad?.basis).toContain('当前信息不足');
    expect(legacyBad?.exploration).toContain('建议开展澄清');
    expect(getLegacyExplorationSummary(null)).toBeNull();
  });
});
