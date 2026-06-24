import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';
import ts from 'typescript';

function loadPlanningStateModule() {
  const source = readFileSync(new URL('../src/store/planningState.ts', import.meta.url), 'utf8');
  const { outputText } = ts.transpileModule(source, {
    compilerOptions: {
      module: ts.ModuleKind.CommonJS,
      target: ts.ScriptTarget.ES2020,
    },
  });

  const module = { exports: {} };
  const context = {
    exports: module.exports,
    module,
  };

  vm.runInNewContext(outputText, context);
  return module.exports;
}

const { selectPlanningView } = loadPlanningStateModule();

const taskTree = {
  root: { client_node_id: 'root', title: 'root', verb: 'do', estimated_minutes: 0, node_type: 'group' },
  summary: '',
  planning_context: {
    schema_version: 1,
    intent_type: 'long_term_growth',
    time_horizon: 'weeks',
    roadmap: [
      { phase_id: 'phase-current', order: 1, title: 'Phase 1', objective: 'Obj 1', status: 'current' },
      { phase_id: 'phase-planned', order: 2, title: 'Phase 2', objective: 'Obj 2', status: 'planned' }
    ],
    current_phase: {
      phase_id: 'phase-current',
      title: 'Phase 1',
      objective: 'Obj 1',
      completion_rule: 'all_ai_actions_completed'
    },
    next_action_client_node_id: 'node-1'
  }
};

const tasks = [
  { id: 'phase-current', client_node_id: 'node-1', thread_id: 'thread-1', phase_id: 'phase-current', source: 'ai', node_type: 'action', status: 'active', phase_order: 1, sort_order: 1 },
  { id: 'manual', client_node_id: 'node-m', thread_id: 'thread-1', phase_id: null, source: 'manual', node_type: 'action', status: 'active', sort_order: 2 }
];

assert.equal(selectPlanningView(null, tasks, 'thread-1'), null);

const view = selectPlanningView(taskTree, tasks, 'thread-1');
assert.equal(JSON.stringify(view.currentTasks.map((task) => task.id)), JSON.stringify(['phase-current', 'manual']));
assert.equal(view.nextAction.id, 'phase-current');
assert.equal(view.canUnlock, false);

const completedTasks = [
  { ...tasks[0], status: 'completed' },
  tasks[1]
];

const completedView = selectPlanningView(taskTree, completedTasks, 'thread-1');
assert.equal(completedView.nextAction, null);
assert.equal(completedView.canUnlock, true);
assert.equal(completedView.isGoalComplete, false);

console.log('planningState tests passed');
