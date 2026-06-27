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

async function runTests() {
  console.log('Running stateRestoration tests...');

  // --- 测试场景 1: 已有 Committed Plan 刷新恢复 ---
  {
    let fetchCalled = false;
    const fetchMock = async (url) => {
      fetchCalled = true;
      assert.ok(url.includes('/api/threads/thread-committed'));
      return {
        ok: true,
        status: 200,
        json: async () => ({
          thread_id: 'thread-committed',
          status: 'running',
          intent_text: 'my intent',
          task_tree: { root: { title: 'committed-root-task' }, summary: 'summary' }
        })
      };
    };

    const initialLocal = {
      'easyplan_selected_project_id': 'thread-committed',
      'easyplan_thread_id': 'thread-committed',
    };

    const { useAppStore } = loadAppStoreModule(fetchMock, initialLocal);

    const state = useAppStore.getState();
    assert.equal(state.selectedProjectId, 'thread-committed');
    assert.equal(state.threadId, 'thread-committed');

    await state.alignState('thread-committed');

    const updatedState = useAppStore.getState();
    assert.ok(fetchCalled);

    assert.equal(updatedState.view, 'board');
    assert.equal(updatedState.appState, 'INITIAL');
    assert.equal(updatedState.taskTree.root.title, 'committed-root-task');
  }

  // --- 测试场景 2: awaiting_confirmation preview 刷新恢复 ---
  {
    let fetchCalled = false;
    const fetchMock = async (url) => {
      fetchCalled = true;
      assert.ok(url.includes('/api/threads/thread-preview'));
      return {
        ok: true,
        status: 200,
        json: async () => ({
          thread_id: 'thread-preview',
          status: 'awaiting_confirmation',
          intent_text: 'my intent',
          task_tree: null,
          interrupt_payload: {
            type: 'next_phase_review',
            request_id: 'req-preview',
            status: 'awaiting_confirmation',
            task_tree: { root: { title: 'preview-root-task' }, summary: 'preview-summary' }
          }
        })
      };
    };

    const initialLocal = {
      'easyplan_selected_project_id': 'thread-preview',
      'easyplan_thread_id': 'thread-preview',
      'easyplan_preview_mode': 'next_phase',
      'easyplan_phase_request_id': 'req-preview',
    };

    const { useAppStore } = loadAppStoreModule(fetchMock, initialLocal);

    const state = useAppStore.getState();
    assert.equal(state.selectedProjectId, 'thread-preview');
    assert.equal(state.threadId, 'thread-preview');
    assert.equal(state.previewMode, 'next_phase');
    assert.equal(state.phaseRequestId, 'req-preview');

    await state.alignState('thread-preview');

    const updatedState = useAppStore.getState();
    assert.ok(fetchCalled);

    assert.equal(updatedState.view, 'board');
    assert.equal(updatedState.appState, 'PENDING');
    assert.ok(updatedState.taskTree);
    assert.equal(updatedState.taskTree.root.title, 'preview-root-task');
    assert.equal(updatedState.previewMode, 'next_phase');
  }

  // --- 测试场景 3: next-phase running 状态刷新恢复 ---
  {
    let fetchCalled = false;
    const fetchMock = async (url) => {
      fetchCalled = true;
      assert.ok(url.includes('/api/threads/thread-running'));
      return {
        ok: true,
        status: 200,
        json: async () => ({
          thread_id: 'thread-running',
          status: 'running',
          intent_text: 'my intent',
          task_tree: null
        })
      };
    };

    const initialLocal = {
      'easyplan_selected_project_id': 'thread-running',
      'easyplan_thread_id': 'thread-running',
      'easyplan_preview_mode': 'next_phase',
      'easyplan_phase_request_id': 'req-running',
    };

    const { useAppStore } = loadAppStoreModule(fetchMock, initialLocal);

    const state = useAppStore.getState();
    assert.equal(state.selectedProjectId, 'thread-running');
    assert.equal(state.threadId, 'thread-running');
    assert.equal(state.previewMode, 'next_phase');
    assert.equal(state.phaseRequestId, 'req-running');

    await state.alignState('thread-running');

    const updatedState = useAppStore.getState();
    assert.ok(fetchCalled);

    assert.equal(updatedState.view, 'board');
    assert.equal(updatedState.appState, 'THINKING');
    assert.equal(updatedState.taskTree, null);
    assert.equal(updatedState.previewMode, 'next_phase');
    assert.equal(updatedState.phaseRequestId, 'req-running');
  }

  console.log('stateRestoration tests passed');
}

runTests().catch(err => {
  console.error('Test failed:', err);
  process.exit(1);
});
