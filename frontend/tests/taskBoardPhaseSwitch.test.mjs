import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';
import ts from 'typescript';
import { loadTsModule } from './testHelpers/loadTsModule.mjs';

const taskAssistModule = loadTsModule('../../src/lib/taskAssist.ts');

function createStore(initializer) {
  let state;
  const api = {
    getState: () => state,
    setState: (partial) => {
      const nextPartial = typeof partial === 'function' ? partial(state) : partial;
      state = { ...state, ...nextPartial };
    },
  };
  state = initializer(api.setState, api.getState);
  function useStore() { return state; }
  useStore.getState = api.getState;
  useStore.setState = api.setState;
  return useStore;
}

function loadAppStoreModule() {
  const source = readFileSync(new URL('../src/store/useAppStore.ts', import.meta.url), 'utf8');
  const { outputText } = ts.transpileModule(source, {
    compilerOptions: {
      module: ts.ModuleKind.CommonJS,
      target: ts.ScriptTarget.ES2020,
    },
  });
  const runnableOutput = outputText.replaceAll('import.meta.env', '({VITE_PHASE_PLANNING_ENABLED: "true"})');

  const module = { exports: {} };
  const localStorageValues = new Map([['auth_token', 'mock-token']]);

  const context = {
    exports: module.exports,
    module,
    console: { ...console, error: () => {} },
    fetch: async () => ({ ok: true, status: 200, json: async () => [] }),
    setTimeout,
    localStorage: {
      getItem: (key) => localStorageValues.get(key) ?? null,
      setItem: (key, value) => localStorageValues.set(key, value),
      removeItem: (key) => localStorageValues.delete(key),
    },
    crypto: {
      randomUUID: () => 'test-uuid',
    },
    require: (specifier) => {
      if (specifier === 'zustand') {
        return { create: createStore };
      }
      if (specifier === './authRecovery') {
        return {
          buildAuthRecoveryState: () => ({}),
          isUnauthorizedResponse: (response) => response.status === 401,
        };
      }
      if (specifier === './intentRequest') {
        return {
          buildIntentRequest: () => ({}),
          resolvePlannerProvider: () => 'openai',
        };
      }
      if (specifier === './planningState') {
        return {
          selectPlanningView: (taskTree, tasks, selectedProjectId) => {
            const phaseId = taskTree?.planning_context?.current_phase?.phase_id ?? null;
            const threadTasks = tasks.filter((task) => task.thread_id === selectedProjectId);
            const roadmap = taskTree?.planning_context?.roadmap ?? [];
            return {
              canUnlock: true,
              currentTasks: threadTasks.filter((task) => task.phase_id === phaseId),
              historicalPhases: roadmap
                .filter((phase) => phase.status === 'completed')
                .map((phase) => ({
                  phase,
                  tasks: threadTasks.filter((task) => task.phase_id === phase.phase_id),
                })),
            };
          }
        };
      }
      if (specifier === './snapshotRequestGate') {
        return {
          createLatestRequestGate: () => {
            let latest = 0;
            return {
              begin: () => {
                const seq = ++latest;
                return () => seq === latest;
              },
              invalidate: () => { latest++; }
            };
          }
        };
      }
      if (specifier === '../lib/taskAssist') {
        return taskAssistModule;
      }
      throw new Error(`Unexpected require: ${specifier}`);
    },
    Intl: Intl
  };

  vm.runInNewContext(runnableOutput, context);
  return module.exports.useAppStore;
}

function loadTaskBoard(useAppStoreInstance) {
  const source = readFileSync(new URL('../src/components/TaskBoard.tsx', import.meta.url), 'utf8');
  const { outputText } = ts.transpileModule(source, {
    compilerOptions: {
      module: ts.ModuleKind.CommonJS,
      target: ts.ScriptTarget.ES2020,
      jsx: ts.JsxEmit.React,
    },
  });

  const memoSlots = [];
  let memoIndex = 0;
  const callbackSlots = [];
  let callbackIndex = 0;

  const mockReact = {
    useState: (init) => {
      let val = typeof init === 'function' ? init() : init;
      const setter = (newVal) => {
        val = typeof newVal === 'function' ? newVal(val) : newVal;
      };
      return [val, setter];
    },
    useEffect: () => {},
    useMemo: (fn, deps) => {
      const index = memoIndex++;
      const existing = memoSlots[index];
      if (existing && shallowEqual(existing.deps, deps)) {
        return existing.value;
      }
      const value = fn();
      memoSlots[index] = { deps: [...deps], value };
      return value;
    },
    useRef: (init) => ({ current: init }),
    useCallback: (fn, deps) => {
      const index = callbackIndex++;
      const existing = callbackSlots[index];
      if (existing && shallowEqual(existing.deps, deps)) {
        return existing.value;
      }
      callbackSlots[index] = { deps: [...deps], value: fn };
      return fn;
    },
    createElement: (type, props, ...children) => {
      if (typeof type === 'function') {
        return type({ ...props, children: children.flat(Infinity) });
      }
      return { type, props, children: children.flat(Infinity) };
    },
    Fragment: 'Fragment',
    default: null,
  };
  mockReact.default = mockReact;

  const mockFramerMotion = {
    motion: {
      div: (props) => mockReact.createElement('div', props),
      button: (props) => mockReact.createElement('button', props),
      form: (props) => mockReact.createElement('form', props),
      input: (props) => mockReact.createElement('input', props),
      span: (props) => mockReact.createElement('span', props),
      main: (props) => mockReact.createElement('main', props),
    },
    AnimatePresence: ({ children }) => children,
  };

  const mockLucideReact = {
    Sun: 'Sun', Calendar: 'Calendar', Menu: 'Menu', Plus: 'Plus', CheckCircle2: 'CheckCircle2', Circle: 'Circle',
    Pencil: 'Pencil', Trash2: 'Trash2', Folder: 'Folder', ChevronDown: 'ChevronDown', ArrowRight: 'ArrowRight',
    Clock: 'Clock', Lock: 'Lock', Unlock: 'Unlock', AlertTriangle: 'AlertTriangle', RotateCw: 'RotateCw'
  };

  const module = { exports: {} };
  const localStorageValues = new Map();
  const context = {
    exports: module.exports,
    module,
    localStorage: {
      getItem: (key) => localStorageValues.get(key) ?? null,
      setItem: (key, value) => localStorageValues.set(key, value),
      removeItem: (key) => localStorageValues.delete(key),
    },
    require: (specifier) => {
      if (specifier === 'react') return mockReact;
      if (specifier === 'framer-motion') return mockFramerMotion;
      if (specifier === 'lucide-react') return mockLucideReact;
      if (specifier === 'clsx') {
        const fn = (...args) => args.filter(Boolean).join(' ');
        fn.clsx = fn;
        fn.default = fn;
        return fn;
      }
      if (specifier.includes('useAppStore')) return { useAppStore: useAppStoreInstance };
      if (specifier.includes('PlanningOverview')) {
        return { PlanningOverview: () => mockReact.createElement('div', {}, 'PlanningOverviewComponent') };
      }
      if (specifier.includes('PortfolioOverview')) {
        return { PortfolioOverview: () => mockReact.createElement('div', {}, 'PortfolioOverviewComponent') };
      }
      if (specifier.includes('StrategyOverview')) {
        return {
          StrategyOverview: () => null,
          default: () => null
        };
      }
      if (specifier.includes('planningState')) {
        return {
          selectPlanningView: (taskTree, tasks, selectedProjectId) => {
            const phaseId = taskTree?.planning_context?.current_phase?.phase_id ?? null;
            const threadTasks = tasks.filter((task) => task.thread_id === selectedProjectId);
            const roadmap = taskTree?.planning_context?.roadmap ?? [];
            return {
              canUnlock: true,
              currentTasks: threadTasks.filter((task) => task.phase_id === phaseId),
              historicalPhases: roadmap
                .filter((phase) => phase.status === 'completed')
                .map((phase) => ({
                  phase,
                  tasks: threadTasks.filter((task) => task.phase_id === phase.phase_id),
                })),
            };
          }
        };
      }
      if (specifier.includes('TaskCoachPanel')) {
        return {
          TaskCoachPanel: () => null,
          default: () => null
        };
      }
      if (specifier.includes('ExecutionRefinePanel')) {
        return {
          ExecutionRefinePanel: () => null,
          default: () => null
        };
      }
      throw new Error(`Unexpected import in component: ${specifier}`);
    },
    console: { ...console, error: () => {} }
  };

  const runnableOutput = outputText.replaceAll('import.meta.env', '({VITE_PHASE_PLANNING_ENABLED: "true"})');
  vm.runInNewContext(runnableOutput, context);

  return {
    render() {
      memoIndex = 0;
      callbackIndex = 0;
      return module.exports.TaskBoard({});
    }
  };
}

function shallowEqual(a, b) {
  if (a === b) return true;
  if (!Array.isArray(a) || !Array.isArray(b) || a.length !== b.length) return false;
  return a.every((value, index) => Object.is(value, b[index]));
}

function findInVdom(node, predicate) {
  if (!node) return null;
  if (Array.isArray(node)) {
    for (const item of node) {
      const found = findInVdom(item, predicate);
      if (found) return found;
    }
    return null;
  }
  if (typeof node === 'string') {
    if (predicate(node)) return node;
  } else if (predicate(node)) {
    return node;
  }
  const kids = [];
  if (node.children && node.children.length > 0) {
    kids.push(...node.children);
  }
  if (node.props && node.props.children) {
    if (Array.isArray(node.props.children)) {
      kids.push(...node.props.children);
    } else {
      kids.push(node.props.children);
    }
  }
  for (const child of kids) {
    const found = findInVdom(child, predicate);
    if (found) return found;
  }
  return null;
}

const useAppStore = loadAppStoreModule();
const phase1Tree = {
  planning_context: {
    roadmap: [],
    current_phase: { phase_id: 'phase_01', title: 'Phase 1', objective: 'Start' },
  },
};
const phase2Tree = {
  planning_context: {
    roadmap: [],
    current_phase: { phase_id: 'phase_02', title: 'Phase 2', objective: 'Build' },
  },
};
const previewPhaseTree = {
  root: {
    client_node_id: 'phase_02_root',
    title: 'Phase 2 Preview',
    description: null,
    verb: 'Plan',
    estimated_minutes: 30,
    node_type: 'group',
    children: [
      {
        client_node_id: 'phase_02_preview_action',
        title: 'Phase 2 Preview Task',
        description: 'Preview only until confirm',
        verb: 'Draft',
        estimated_minutes: 15,
        node_type: 'action',
        depends_on: [],
        children: [],
      },
    ],
  },
  planning_context: {
    roadmap: [
      { phase_id: 'phase_01', order: 1, title: 'Phase 1', objective: 'Start', status: 'completed' },
      { phase_id: 'phase_02', order: 2, title: 'Phase 2', objective: 'Build', status: 'current' },
    ],
    current_phase: { phase_id: 'phase_02', title: 'Phase 2', objective: 'Build' },
  },
};
const previewBoardTasks = [
  {
    id: 'project-root',
    title: 'Project Title',
    status: 'active',
    parent_task_id: null,
    thread_id: 'proj-1',
    client_node_id: 'phase_01_root',
    description: null,
    node_type: 'group',
    user_id: 'user-1',
    view_bucket: 'planned',
    estimated_minutes: 30,
    sort_order: 0,
    is_in_my_day: false,
    phase_id: 'phase_01',
    phase_order: 1,
    source: 'ai',
  },
  {
    id: 'phase1-preview-regression',
    title: 'Committed Phase 1 Task',
    status: 'active',
    parent_task_id: 'project-root',
    thread_id: 'proj-1',
    client_node_id: 'phase_01_action_preview_regression',
    description: null,
    node_type: 'action',
    user_id: 'user-1',
    view_bucket: 'planned',
    estimated_minutes: 5,
    sort_order: 1,
    is_in_my_day: false,
    phase_id: 'phase_01',
    phase_order: 1,
    source: 'ai',
  },
];
const boardTasks = [
  {
    id: 'phase1-task',
    title: 'Phase 1 Task',
    status: 'active',
    parent_task_id: null,
    thread_id: 'proj-1',
    client_node_id: 'phase_01_action',
    description: null,
    node_type: 'action',
    user_id: 'user-1',
    view_bucket: 'planned',
    estimated_minutes: 5,
    sort_order: 0,
    is_in_my_day: false,
    phase_id: 'phase_01',
    phase_order: 1,
    source: 'ai',
  },
  {
    id: 'phase2-task',
    title: 'Phase 2 Task',
    status: 'active',
    parent_task_id: null,
    thread_id: 'proj-1',
    client_node_id: 'phase_02_action',
    description: null,
    node_type: 'action',
    user_id: 'user-1',
    view_bucket: 'planned',
    estimated_minutes: 5,
    sort_order: 1,
    is_in_my_day: false,
    phase_id: 'phase_02',
    phase_order: 2,
    source: 'ai',
  }
];

useAppStore.setState({
  view: 'board',
  currentViewBucket: 'planned',
  selectedProjectId: 'proj-1',
  boardTasks,
  committedTaskTree: phase1Tree,
  boardError: null,
  appState: 'INITIAL',
});

const { render } = loadTaskBoard(useAppStore);

const firstRender = render();
assert.ok(findInVdom(firstRender, (node) => typeof node === 'string' && node.includes('Phase 1 Task')));
assert.equal(findInVdom(firstRender, (node) => typeof node === 'string' && node.includes('Phase 2 Task')), null);

useAppStore.setState({
  committedTaskTree: phase2Tree,
});

const secondRender = render();
assert.ok(findInVdom(secondRender, (node) => typeof node === 'string' && node.includes('Phase 2 Task')), 'Task board should switch to Phase 2 tasks when the current phase changes');

useAppStore.setState({
  selectedProjectId: 'proj-1',
  boardTasks: previewBoardTasks,
  committedTaskTree: phase1Tree,
  previewTaskTree: previewPhaseTree,
  previewMode: 'next_phase',
  appState: 'PENDING',
});

const previewRender = render();
assert.ok(
  findInVdom(previewRender, (node) => typeof node === 'string' && node.includes('Phase 2 Preview Task')),
  'Task board should render next-phase preview tasks before the new phase is committed',
);
assert.equal(
  findInVdom(previewRender, (node) => typeof node === 'string' && node.includes('Committed Phase 1 Task')),
  null,
  'Task board should not fall back to committed phase tasks while previewing the next phase',
);
assert.equal(
  findInVdom(previewRender, (node) => typeof node === 'string' && node.includes('Phase History')),
  null,
  'Task board should hide committed phase history while next-phase preview is active',
);

console.log('taskBoardPhaseSwitch tests passed');
