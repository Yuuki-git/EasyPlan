import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';
import ts from 'typescript';

const plain = (val) => JSON.parse(JSON.stringify(val));

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
      randomUUID: () => 'test-uuid-' + Math.random().toString(36).substring(2, 9),
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

async function runTests() {
  console.log('Running generationRun tests...');

  // --- 测试场景 1: submitIntent 触发新 Run 清空旧状态 ---
  {
    let fetchCalled = false;
    const fetchMock = async (url, options) => {
      fetchCalled = true;
      return {
        ok: true,
        status: 200,
        json: async () => ({ thread_id: 'new-thread-123' })
      };
    };

    const { useAppStore } = loadAppStoreModule(fetchMock);

    // 先手动往 store 塞一些上轮脏数据
    useAppStore.setState({
      reasoningLogs: ['dirty log'],
      taskTree: { root: {} },
      nodeStatuses: { 'node-1': 'success' },
      error: 'some error',
      isRunStalled: true
    });

    await useAppStore.getState().submitIntent('some intent');

    const state = useAppStore.getState();
    assert.ok(fetchCalled);
    assert.equal(state.isRunStalled, false);
    assert.deepEqual(plain(state.reasoningLogs), []);
    assert.equal(state.taskTree, null);
    assert.deepEqual(plain(state.nodeStatuses), {});
    assert.equal(state.error, null);
  }

  // --- 测试场景 2: generateNextPhasePlan 触发新 Run 重新生成 request_id 并重置 ---
  {
    let fetchCalled = false;
    let requestPayload = null;
    const fetchMock = async (url, options) => {
      fetchCalled = true;
      requestPayload = JSON.parse(options.body);
      return {
        ok: true,
        status: 200,
        json: async () => ({})
      };
    };

    const { useAppStore } = loadAppStoreModule(fetchMock);

    // 初始化有 project
    useAppStore.setState({
      selectedProjectId: 'proj-123',
      taskTree: { planning_context: {} }, // 能够 unlock
      boardTasks: [],
      reasoningLogs: ['dirty log'],
      nodeStatuses: { 'node-1': 'success' },
      error: 'some error',
      isRunStalled: true,
      phaseRequestId: 'old-req-id'
    });

    await useAppStore.getState().generateNextPhasePlan();

    const state = useAppStore.getState();
    assert.ok(fetchCalled);
    assert.ok(requestPayload.request_id);
    assert.notEqual(requestPayload.request_id, 'old-req-id'); // 必须是全新的
    assert.equal(state.phaseRequestId, requestPayload.request_id);
    assert.equal(state.isRunStalled, false);
    assert.deepEqual(plain(state.reasoningLogs), []);
    assert.equal(state.taskTree, null);
    assert.deepEqual(plain(state.nodeStatuses), {});
    assert.equal(state.error, null);
  }

  // --- 测试场景 3: retryNode 触发新 Run 产生新 syncRequestId 并清置状态 ---
  {
    let fetchCalled = false;
    let requestPayload = null;
    const fetchMock = async (url, options) => {
      fetchCalled = true;
      requestPayload = JSON.parse(options.body);
      return {
        ok: true,
        status: 200,
        json: async () => ({})
      };
    };

    const { useAppStore } = loadAppStoreModule(fetchMock);

    useAppStore.setState({
      threadId: 'thread-123',
      syncRequestId: 'old-sync-id',
      reasoningLogs: ['dirty log'],
      taskTree: { root: {} },
      nodeStatuses: { 'node-1': 'error', 'node-2': 'success' },
      error: 'some error',
      isRunStalled: true
    });

    await useAppStore.getState().retryNode('node-1');

    const state = useAppStore.getState();
    assert.ok(fetchCalled);
    assert.ok(requestPayload.request_id);
    assert.notEqual(requestPayload.request_id, 'old-sync-id'); // 重新生成的 syncRequestId
    assert.equal(state.syncRequestId, requestPayload.request_id);
    assert.equal(state.isRunStalled, false);
    assert.deepEqual(plain(state.reasoningLogs), []);
    assert.equal(state.taskTree, null);
    assert.deepEqual(plain(state.nodeStatuses), { 'node-1': 'syncing' }); // 只有重试节点为 syncing，其余清空
    assert.equal(state.error, null);
  }

  // --- 测试场景 4: returnToCommittedPlan 退出机制 ---
  {
    // 场景 A: 有项目上下文
    let loadProjectSnapshotCalled = false;
    let fetchTasksCalled = false;
    const fetchMock = async (url) => {
      if (url.includes('/api/threads/proj-123')) {
        loadProjectSnapshotCalled = true;
        return { ok: true, status: 200, json: async () => ({ task_tree: { root: {} } }) };
      }
      if (url.includes('/api/tasks')) {
        fetchTasksCalled = true;
        return { ok: true, status: 200, json: async () => ([]) };
      }
      return { ok: false };
    };

    const { useAppStore } = loadAppStoreModule(fetchMock);

    useAppStore.setState({
      selectedProjectId: 'proj-123',
      view: 'input',
      previewMode: 'next_phase',
      phaseRequestId: 'some-phase-id',
      appState: 'PENDING',
      isRunStalled: true,
      error: 'some error'
    });

    await useAppStore.getState().returnToCommittedPlan();

    const state = useAppStore.getState();
    assert.equal(state.view, 'board');
    assert.equal(state.previewMode, null);
    assert.equal(state.phaseRequestId, null);
    assert.equal(state.appState, 'INITIAL');
    assert.equal(state.error, null);
    assert.equal(state.isRunStalled, false);
    assert.ok(loadProjectSnapshotCalled);
    assert.ok(fetchTasksCalled);
  }
  {
    // 场景 B: 无项目上下文
    const fetchMock = async () => ({ ok: false });
    const { useAppStore } = loadAppStoreModule(fetchMock);

    useAppStore.setState({
      selectedProjectId: null,
      view: 'board',
      previewMode: 'initial',
      phaseRequestId: 'some-phase-id',
      appState: 'PENDING',
      isRunStalled: true,
      error: 'some error',
      threadId: 'some-thread',
      intent: 'some intent',
      taskTree: { root: {} }
    });

    await useAppStore.getState().returnToCommittedPlan();

    const state = useAppStore.getState();
    assert.equal(state.view, 'input');
    assert.equal(state.previewMode, null);
    assert.equal(state.phaseRequestId, null);
    assert.equal(state.appState, 'INITIAL');
    assert.equal(state.error, null);
    assert.equal(state.isRunStalled, false);
    assert.equal(state.threadId, null);
    assert.equal(state.intent, '');
    assert.equal(state.taskTree, null);
  }

  console.log('generationRun tests passed');
}

runTests().catch(err => {
  console.error('Test failed:', err);
  process.exit(1);
});
