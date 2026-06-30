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

function loadAppStoreModule(fetchImpl, initialLocalStorage = {}) {
  const source = readFileSync(new URL('../src/store/useAppStore.ts', import.meta.url), 'utf8');
  const { outputText } = ts.transpileModule(source, {
    compilerOptions: {
      module: ts.ModuleKind.CommonJS,
      target: ts.ScriptTarget.ES2020,
    },
  });
  const runnableOutput = outputText.replaceAll('import.meta.env', '({VITE_PHASE_PLANNING_ENABLED: "true"})');

  const module = { exports: {} };
  const localStorageValues = new Map(Object.entries(initialLocalStorage));

  if (!localStorageValues.has('auth_token')) {
    localStorageValues.set('auth_token', 'mock-token');
  }

  const context = {
    exports: module.exports,
    module,
    console: { ...console, error: () => {} },
    fetch: fetchImpl,
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
            return { canUnlock: true };
          }
        };
      }
      throw new Error(`Unexpected require: ${specifier}`);
    },
    Intl: Intl
  };

  vm.runInNewContext(runnableOutput, context);
  return {
    useAppStore: module.exports.useAppStore,
    localStorageValues,
  };
}

function loadComponentModule(filePath, useAppStoreInstance) {
  const source = readFileSync(new URL(filePath, import.meta.url), 'utf8');
  const { outputText } = ts.transpileModule(source, {
    compilerOptions: {
      module: ts.ModuleKind.CommonJS,
      target: ts.ScriptTarget.ES2020,
      jsx: ts.JsxEmit.React,
    },
  });

  const mockReact = {
    useState: (init) => {
      let val = typeof init === 'function' ? init() : init;
      const setter = (newVal) => {
        val = typeof newVal === 'function' ? newVal(val) : newVal;
      };
      return [val, setter];
    },
    useEffect: (fn) => {
      fn();
    },
    useMemo: (fn) => fn(),
    useRef: (init) => ({ current: init }),
    useCallback: (fn) => fn,
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
        return {
          PlanningOverview: () => mockReact.createElement('div', {}, 'PlanningOverviewComponent')
        };
      }
      if (specifier.includes('PortfolioOverview')) {
        return {
          PortfolioOverview: ({ projects }) => {
            return mockReact.createElement('div', { className: 'portfolio' },
              projects.map(p => mockReact.createElement('span', { key: p.id }, p.title))
            );
          }
        };
      }
      if (specifier.includes('planningState')) {
        return {
          selectPlanningView: (taskTree, tasks, selectedProjectId) => {
            return {
              canUnlock: true,
              currentTasks: tasks,
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
  return module.exports;
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
    const res = predicate(node);
    if (res) return node;
  } else {
    if (predicate(node)) return node;
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

async function runTests() {
  console.log('Running boardRestoration integration tests...');

  // --- 测试场景 1: 全部计划 (Portfolio Overview) 首次挂载自动自举恢复与渲染 ---
  {
    let fetchTasksCalled = false;
    const fetchMock = async (url) => {
      if (url.includes('/api/tasks')) {
        fetchTasksCalled = true;
        return {
          ok: true,
          status: 200,
          json: async () => [
            { id: 't-1', title: 'Global Task A', status: 'active', parent_task_id: null, thread_id: 'thread-p1', source: 'ai' }
          ]
        };
      }
      return { ok: false, status: 404 };
    };

    const { useAppStore } = loadAppStoreModule(fetchMock, {
      'easyplan_view': 'board',
      'easyplan_selected_project_id': ''
    });

    const { TaskBoard } = loadComponentModule('../src/components/TaskBoard.tsx', useAppStore);

    // Initial render triggering useEffect bootstrap
    const initialVdom = TaskBoard({});
    assert.ok(fetchTasksCalled);

    // Wait for the async bootstrapping actions to fully resolve in the store
    await new Promise(resolve => setTimeout(resolve, 50));

    // Render again now that store is hydrated
    const hydratedVdom = TaskBoard({});
    const foundTaskTitle = findInVdom(hydratedVdom, (n) => typeof n === 'string' && n.includes('Global Task A'));
    assert.ok(foundTaskTitle, 'Global Task A should be hydrated and rendered on the board');
  }

  // --- 测试场景 2: 具体项目看板首次挂载自动自举恢复与渲染 ---
  {
    let snapshotFetched = false;
    let tasksFetched = false;

    const fetchMock = async (url) => {
      if (url.includes('/api/threads/proj-123')) {
        snapshotFetched = true;
        return {
          ok: true,
          status: 200,
          json: async () => ({
            thread_id: 'proj-123',
            task_tree: { root: { title: 'Committed Project Tree Root' } }
          })
        };
      }
      if (url.includes('/api/tasks')) {
        tasksFetched = true;
        return {
          ok: true,
          status: 200,
          json: async () => [
            { id: 't-2', title: 'Committed Project Task B', status: 'active', parent_task_id: null, thread_id: 'proj-123', source: 'ai' }
          ]
        };
      }
      return { ok: false, status: 404 };
    };

    const { useAppStore } = loadAppStoreModule(fetchMock, {
      'easyplan_view': 'board',
      'easyplan_selected_project_id': 'proj-123',
      'easyplan_thread_id': 'proj-123'
    });

    const { TaskBoard } = loadComponentModule('../src/components/TaskBoard.tsx', useAppStore);

    // First render mounts & triggers bootstrap (snapshot load + tasks fetch)
    const initialVdom = TaskBoard({});
    assert.ok(snapshotFetched, 'Should fetch project snapshot');

    // Wait for the async bootstrapping actions to fully resolve in the store
    await new Promise(resolve => setTimeout(resolve, 50));

    assert.ok(tasksFetched, 'Should fetch project tasks');

    // Second render with hydrated state
    const hydratedVdom = TaskBoard({});
    const foundSnapshotTask = findInVdom(hydratedVdom, (n) => typeof n === 'string' && n.includes('Committed Project Task B'));
    assert.ok(foundSnapshotTask, 'Committed Project Task B should be hydrated and rendered on the board');
  }

  // --- 测试场景 3: 初始规划无项目上下文时，ActionLayer 不渲染“返回当前计划” ---
  {
    const { useAppStore } = loadAppStoreModule(() => {});
    useAppStore.setState({
      selectedProjectId: null,
      appState: 'PENDING',
    });

    const { ActionLayer } = loadComponentModule('../src/components/ActionLayer.tsx', useAppStore);
    const vdom = ActionLayer({});

    const returnBtn = findInVdom(vdom, (n) => typeof n === 'string' && n.includes('返回当前计划'));
    assert.equal(returnBtn, null, 'ActionLayer should not render "返回当前计划" when there is no committed plan');
  }

  // --- 测试场景 4: 存在已提交项目上下文时，ActionLayer 渲染“返回当前计划” ---
  {
    const { useAppStore } = loadAppStoreModule(() => {});
    useAppStore.setState({
      selectedProjectId: 'proj-123',
      appState: 'PENDING',
    });

    const { ActionLayer } = loadComponentModule('../src/components/ActionLayer.tsx', useAppStore);
    const vdom = ActionLayer({});

    const returnBtn = findInVdom(vdom, (n) => typeof n === 'string' && n.includes('返回当前计划'));
    assert.ok(returnBtn, 'ActionLayer should render "返回当前计划" when there is a committed plan');
  }

  // --- 测试场景 5: Token 缺失时 fetchTasks 触发 AuthModal 弹出并设置 boardError ---
  {
    const { useAppStore } = loadAppStoreModule(() => {});
    useAppStore.setState({ token: null, showAuthModal: false, boardError: null });

    useAppStore.getState().fetchTasks('planned');

    assert.equal(useAppStore.getState().showAuthModal, true, 'Should pop up AuthModal');
    assert.ok(useAppStore.getState().boardError && useAppStore.getState().boardError.includes('请先登录'), 'Should end loading state with auth error message');
  }

  // --- 测试场景 6: 保留 board 上下文，并在重连登录后自动恢复 ---
  {
    let snapshotFetched = false;
    let tasksFetched = false;

    const fetchMock = async (url) => {
      if (url.includes('/api/threads/proj-789')) {
        snapshotFetched = true;
        return {
          ok: true,
          status: 200,
          json: async () => ({
            thread_id: 'proj-789',
            task_tree: { root: { title: 'Restored Project Tree Root' } }
          })
        };
      }
      if (url.includes('/api/tasks')) {
        tasksFetched = true;
        return {
          ok: true,
          status: 200,
          json: async () => [
            { id: 't-9', title: 'Restored Project Task C', status: 'active', parent_task_id: null, thread_id: 'proj-789', source: 'ai' }
          ]
        };
      }
      return { ok: false, status: 404 };
    };

    const { useAppStore } = loadAppStoreModule(fetchMock, {
      'auth_token': '',
      'easyplan_view': 'board',
      'easyplan_selected_project_id': 'proj-789',
      'easyplan_thread_id': 'proj-789'
    });

    useAppStore.setState({
      view: 'board',
      selectedProjectId: 'proj-789',
      token: null
    });

    useAppStore.getState().fetchTasks('planned');
    assert.equal(useAppStore.getState().showAuthModal, true);
    assert.equal(useAppStore.getState().view, 'board', 'Should retain view context');
    assert.equal(useAppStore.getState().selectedProjectId, 'proj-789', 'Should retain selectedProjectId context');

    useAppStore.getState().setToken('valid-login-token');

    await new Promise(resolve => setTimeout(resolve, 50));

    assert.ok(snapshotFetched, 'Should automatically restore project snapshot on login');
    assert.ok(tasksFetched, 'Should automatically restore project tasks on login');
    assert.deepEqual(useAppStore.getState().boardTasks, [
      { id: 't-9', title: 'Restored Project Task C', status: 'active', parent_task_id: null, thread_id: 'proj-789', source: 'ai' }
    ]);
  }

  // --- 测试场景 7: 显式登出彻底清理项目上下文与状态隔离 ---
  {
    const mockFetch = async () => ({ ok: true, status: 200, json: async () => [] });
    const { useAppStore, localStorageValues } = loadAppStoreModule(mockFetch, {
      'auth_token': 'user-A-token',
      'easyplan_selected_project_id': 'user-A-proj',
      'easyplan_thread_id': 'user-A-proj',
      'easyplan_view': 'board'
    });

    useAppStore.setState({
      token: 'user-A-token',
      selectedProjectId: 'user-A-proj',
      threadId: 'user-A-proj',
      view: 'board',
      boardTasks: [{ id: 't-1', title: 'Task 1', status: 'active', parent_task_id: null, thread_id: 'user-A-proj', source: 'ai' }],
      committedTaskTree: { root: { title: 'User A Tree' } },
      previewTaskTree: { root: { title: 'User A Tree' } },
      boardError: 'Some prior error',
      showAuthModal: true,
      pendingIntent: 'user-A-intent',
      isPhaseRequestPending: true,
    });

    // 显式登出
    useAppStore.getState().setToken(null, true);

    // 校验内存字段全部彻底重置/清空
    assert.equal(useAppStore.getState().token, null);
    assert.equal(useAppStore.getState().selectedProjectId, null);
    assert.equal(useAppStore.getState().threadId, null);
    assert.equal(useAppStore.getState().committedTaskTree, null);
    assert.equal(useAppStore.getState().previewTaskTree, null);
    assert.equal(useAppStore.getState().boardTasks, null);
    assert.equal(useAppStore.getState().boardError, null);
    assert.equal(useAppStore.getState().view, 'input');
    assert.equal(useAppStore.getState().showAuthModal, false, 'showAuthModal should be false');
    assert.equal(useAppStore.getState().pendingIntent, null, 'pendingIntent should be null');
    assert.equal(useAppStore.getState().isPhaseRequestPending, false, 'isPhaseRequestPending should be false');

    // 校验本地持久化彻底移除/重设为 input
    assert.equal(localStorageValues.has('auth_token'), false);
    assert.equal(localStorageValues.has('easyplan_selected_project_id'), false);
    assert.equal(localStorageValues.has('easyplan_thread_id'), false);
    assert.equal(localStorageValues.get('easyplan_view'), 'input');

    // 状态防挂起闪烁校验：setView('board') 应该强制清空 taskTree 和 boardTasks
    useAppStore.setState({
      committedTaskTree: { root: { title: 'Old Tree' } },
      previewTaskTree: { root: { title: 'Old Tree' } },
      boardTasks: [{ id: 't-old' }]
    });

    useAppStore.getState().setView('board');
    assert.equal(useAppStore.getState().committedTaskTree, null, 'committedTaskTree should be cleared on setView');
    assert.equal(useAppStore.getState().previewTaskTree, null, 'previewTaskTree should be cleared on setView');
    assert.equal(useAppStore.getState().boardTasks, null, 'boardTasks should be cleared on setView');
  }

  // --- Scenario 8: stale persisted project context should recover to the planned board ---
  {
    let tasksFetched = false;
    const fetchMock = async (url) => {
      if (url.includes('/api/threads/stale-proj')) {
        return { ok: false, status: 404 };
      }
      if (url.includes('/api/tasks')) {
        tasksFetched = true;
        return { ok: true, status: 200, json: async () => [] };
      }
      return { ok: false, status: 404 };
    };

    const { useAppStore, localStorageValues } = loadAppStoreModule(fetchMock, {
      'easyplan_view': 'board',
      'easyplan_selected_project_id': 'stale-proj',
      'easyplan_thread_id': 'stale-proj'
    });

    const { TaskBoard } = loadComponentModule('../src/components/TaskBoard.tsx', useAppStore);

    TaskBoard({});
    await new Promise(resolve => setTimeout(resolve, 50));

    const state = useAppStore.getState();
    assert.ok(tasksFetched, 'Should fall back to loading planned tasks after stale project recovery');
    assert.equal(state.selectedProjectId, null, 'Stale selectedProjectId should be cleared');
    assert.equal(state.threadId, null, 'Stale threadId should be cleared');
    assert.equal(state.boardError, null, 'Stale project recovery should not leave the board in an error state');
    assert.deepEqual(state.boardTasks, [], 'Planned board should hydrate after stale project recovery');
    assert.equal(localStorageValues.has('easyplan_selected_project_id'), false, 'Persisted stale project id should be removed');
    assert.equal(localStorageValues.has('easyplan_thread_id'), false, 'Persisted stale thread id should be removed');
  }

  console.log('boardRestoration integration tests passed');
}

runTests().catch(err => {
  console.error('Test failed:', err);
  process.exit(1);
});
