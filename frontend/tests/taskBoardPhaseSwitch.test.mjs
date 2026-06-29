import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';
import ts from 'typescript';

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
            return {
              canUnlock: true,
              currentTasks: tasks.filter((task) => task.thread_id === selectedProjectId && task.phase_id === phaseId),
              historicalPhases: [],
            };
          }
        };
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
  const context = {
    exports: module.exports,
    module,
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
      if (specifier.includes('planningState')) {
        return {
          selectPlanningView: (taskTree, tasks, selectedProjectId) => {
            const phaseId = taskTree?.planning_context?.current_phase?.phase_id ?? null;
            return {
              canUnlock: true,
              currentTasks: tasks.filter((task) => task.thread_id === selectedProjectId && task.phase_id === phaseId),
              historicalPhases: [],
            };
          }
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
  taskTree: phase1Tree,
  boardError: null,
  appState: 'INITIAL',
});

const { render } = loadTaskBoard(useAppStore);

const firstRender = render();
assert.ok(findInVdom(firstRender, (node) => typeof node === 'string' && node.includes('Phase 1 Task')));
assert.equal(findInVdom(firstRender, (node) => typeof node === 'string' && node.includes('Phase 2 Task')), null);

useAppStore.setState({
  taskTree: phase2Tree,
});

const secondRender = render();
assert.ok(findInVdom(secondRender, (node) => typeof node === 'string' && node.includes('Phase 2 Task')), 'Task board should switch to Phase 2 tasks when the current phase changes');

console.log('taskBoardPhaseSwitch tests passed');
