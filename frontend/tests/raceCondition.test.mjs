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
  console.log('Running raceCondition tests...');

  // --- 竞态: 播种 -> 提交 (验证是否 setTimeout 延迟清理脏 run 状态) ---
  {
    const fetchMock = async () => {
      return {
        ok: true,
        status: 200,
        json: async () => ({ thread_id: 'new-thread' })
      };
    };

    const { useAppStore } = loadAppStoreModule(fetchMock);

    // 1. 模拟初始化脏数据
    useAppStore.setState({
      committedTaskTree: { root: { title: 'old tree' } },
      previewTaskTree: { root: { title: 'old tree' } },
      nodeStatuses: { 'node-1': 'success' }
    });

    // 2. 执行 startNewIntent()
    useAppStore.getState().startNewIntent();

    // 3. 验证此时状态已经同步清空
    let state = useAppStore.getState();
    assert.equal(state.committedTaskTree, null);
    assert.equal(state.previewTaskTree, null);
    assert.equal(Object.keys(state.nodeStatuses).length, 0);

    // 4. 模拟新 run 写入
    const nextTree = { root: { title: 'new tree' } };
    useAppStore.setState({
      committedTaskTree: nextTree,
      previewTaskTree: nextTree,
      nodeStatuses: { 'node-2': 'syncing' }
    });

    // 5. 等待 600ms (避让原 500ms 延迟定时器)
    await new Promise((resolve) => setTimeout(resolve, 600));

    // 6. 验证新 run 状态仍然完好，未被任何旧定时器清理
    state = useAppStore.getState();
    assert.ok(state.committedTaskTree);
    assert.equal(state.committedTaskTree.root.title, 'new tree');
    assert.ok(state.previewTaskTree);
    assert.equal(state.previewTaskTree.root.title, 'new tree');
    assert.equal(Object.keys(state.nodeStatuses).length, 1);
    assert.equal(state.nodeStatuses['node-2'], 'syncing');
  }

  console.log('raceCondition tests passed');
}

runTests().catch(err => {
  console.error('Test failed:', err);
  process.exit(1);
});
