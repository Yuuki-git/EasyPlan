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
    __test__: true,
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
  globalThis.__test__ = true;
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
    assert.equal(updatedState.committedTaskTree.root.title, 'committed-root-task');
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
    assert.ok(updatedState.previewTaskTree);
    assert.equal(updatedState.previewTaskTree.root.title, 'preview-root-task');
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
    assert.equal(updatedState.previewTaskTree, null);
    assert.equal(updatedState.previewMode, 'next_phase');
    assert.equal(updatedState.phaseRequestId, 'req-running');
  }

  // --- 测试场景 4: 全部计划 (Portfolio Overview) 视图刷新恢复 ---
  {
    const initialLocal = {
      'easyplan_view': 'board',
    };

    const { useAppStore } = loadAppStoreModule(() => {}, initialLocal);

    const state = useAppStore.getState();
    assert.equal(state.view, 'board');
    assert.equal(state.selectedProjectId, null);
  }

  // --- 测试场景 5: stalled status 刷新恢复 ---
  {
    let fetchCalled = false;
    const fetchMock = async (url) => {
      fetchCalled = true;
      assert.ok(url.includes('/api/threads/thread-stalled'));
      return {
        ok: true,
        status: 200,
        json: async () => ({
          thread_id: 'thread-stalled',
          status: 'stalled',
          intent_text: 'my intent',
          task_tree: null
        })
      };
    };

    const initialLocal = {
      'easyplan_selected_project_id': 'thread-stalled',
      'easyplan_thread_id': 'thread-stalled',
      'easyplan_preview_mode': 'next_phase',
      'easyplan_phase_request_id': 'req-stalled',
    };

    const { useAppStore } = loadAppStoreModule(fetchMock, initialLocal);

    const state = useAppStore.getState();
    await state.alignState('thread-stalled');

    const updatedState = useAppStore.getState();
    assert.ok(fetchCalled);
    assert.equal(updatedState.view, 'board');
    assert.equal(updatedState.appState, 'THINKING');
    assert.equal(updatedState.isRunStalled, true);
    assert.equal(updatedState.previewTaskTree, null);
    assert.equal(updatedState.previewMode, 'next_phase');
    assert.equal(updatedState.phaseRequestId, 'req-stalled');
  }

  // --- 测试场景 6: next-phase pending (awaiting_confirmation) 状态刷新恢复到 board 内联态 ---
  {
    let fetchCalled = false;
    const fetchMock = async (url) => {
      fetchCalled = true;
      assert.ok(url.includes('/api/threads/thread-pending'));
      return {
        ok: true,
        status: 200,
        json: async () => ({
          thread_id: 'thread-pending',
          status: 'awaiting_confirmation',
          intent_text: 'my intent',
          interrupt_payload: {
            type: 'next_phase_review',
            request_id: 'req-pending',
            task_tree: { root: { title: 'Pending Phase Title' } }
          }
        })
      };
    };

    const initialLocal = {
      'easyplan_selected_project_id': 'thread-pending',
      'easyplan_thread_id': 'thread-pending',
      'easyplan_preview_mode': 'next_phase',
      'easyplan_phase_request_id': 'req-pending',
    };

    const { useAppStore } = loadAppStoreModule(fetchMock, initialLocal);
    const state = useAppStore.getState();
    await state.alignState('thread-pending');

    const updatedState = useAppStore.getState();
    assert.ok(fetchCalled);
    assert.equal(updatedState.view, 'board', 'Pending next phase should restore view as board');
    assert.equal(updatedState.appState, 'PENDING');
    assert.deepEqual(updatedState.previewTaskTree, { root: { title: 'Pending Phase Title' } });
    assert.equal(updatedState.previewMode, 'next_phase');
    assert.equal(updatedState.phaseRequestId, 'req-pending');
  }

  // --- Scenario 7: stale persisted thread context should be cleared on 404 snapshot recovery ---
  {
    let fetchCalled = false;
    const fetchMock = async (url) => {
      fetchCalled = true;
      assert.ok(url.includes('/api/threads/thread-gone'));
      return {
        ok: false,
        status: 404,
      };
    };

    const initialLocal = {
      'easyplan_view': 'board',
      'easyplan_selected_project_id': 'thread-gone',
      'easyplan_thread_id': 'thread-gone',
      'easyplan_preview_mode': 'next_phase',
      'easyplan_phase_request_id': 'req-gone',
    };

    const { useAppStore, localStorageValues } = loadAppStoreModule(fetchMock, initialLocal);
    await useAppStore.getState().alignState('thread-gone');

    const updatedState = useAppStore.getState();
    assert.ok(fetchCalled);
    assert.equal(updatedState.selectedProjectId, null, 'Stale selectedProjectId should be cleared');
    assert.equal(updatedState.threadId, null, 'Stale threadId should be cleared');
    assert.equal(updatedState.committedTaskTree, null);
    assert.equal(updatedState.previewTaskTree, null);
    assert.equal(updatedState.previewMode, null, 'Stale preview mode should be cleared');
    assert.equal(updatedState.phaseRequestId, null, 'Stale phase request id should be cleared');
    assert.equal(updatedState.appState, 'INITIAL', 'Stale thread recovery should not leave the app in ERROR');
    assert.equal(localStorageValues.has('easyplan_selected_project_id'), false);
    assert.equal(localStorageValues.has('easyplan_thread_id'), false);
    assert.equal(localStorageValues.has('easyplan_preview_mode'), false);
    assert.equal(localStorageValues.has('easyplan_phase_request_id'), false);
  }

  // --- Scenario 8: confirmed next-phase snapshot should clear stale local preview restore state ---
  {
    let fetchCalled = false;
    let receiptCalled = false;
    const fetchMock = async (url) => {
      fetchCalled = true;
      if (url.includes('/api/threads/thread-confirmed/phases/next/commit')) {
        receiptCalled = true;
        return {
          ok: true,
          status: 200,
          json: async () => ({
            thread_id: 'thread-confirmed',
            request_id: 'req-confirmed',
            status: 'confirmed',
            current_phase_id: 'phase_02',
            task_tree: {
              root: {
                client_node_id: 'phase-2-root',
                title: 'Phase 2 Root',
                children: []
              },
              planning_context: {
                roadmap: [
                  { phase_id: 'phase_01', order: 1, title: 'Phase 1', objective: 'Start', status: 'completed' },
                  { phase_id: 'phase_02', order: 2, title: 'Phase 2', objective: 'Build', status: 'current' },
                ],
                current_phase: { phase_id: 'phase_02', title: 'Phase 2', objective: 'Build' },
              },
            },
            tasks: [
              {
                id: 'task-new',
                thread_id: 'thread-confirmed',
                client_node_id: 'phase-2-root',
                phase_id: 'phase_02',
                source: 'ai'
              }
            ]
          })
        };
      }
      if (url.includes('/api/threads/thread-confirmed')) {
        return {
          ok: true,
          status: 200,
          json: async () => ({
            thread_id: 'thread-confirmed',
            status: 'succeeded',
            intent_text: 'my intent',
            task_tree: {
              root: {
                client_node_id: 'phase-2-root',
                title: 'Phase 2 Root',
                children: []
              },
              planning_context: {
                roadmap: [
                  { phase_id: 'phase_01', order: 1, title: 'Phase 1', objective: 'Start', status: 'completed' },
                  { phase_id: 'phase_02', order: 2, title: 'Phase 2', objective: 'Build', status: 'current' },
                ],
                current_phase: { phase_id: 'phase_02', title: 'Phase 2', objective: 'Build' },
              },
            },
            interrupt_payload: {
              type: 'phase_generation_state',
              request_id: 'req-confirmed',
              status: 'confirmed',
              history: {
                'req-confirmed': { status: 'confirmed' },
              },
            },
          }),
        };
      }
      throw new Error(`legacy synchronization endpoint called: ${url}`);
    };

    const initialLocal = {
      'easyplan_view': 'board',
      'easyplan_selected_project_id': 'thread-confirmed',
      'easyplan_thread_id': 'thread-confirmed',
      'easyplan_preview_mode': 'next_phase',
      'easyplan_phase_request_id': 'req-confirmed',
      'easyplan_base_phase_id': 'phase_01',
      'auth_token': '',
    };

    const { useAppStore, localStorageValues } = loadAppStoreModule(fetchMock, initialLocal);
    await useAppStore.getState().alignState('thread-confirmed');

    const updatedState = useAppStore.getState();
    assert.ok(fetchCalled);
    assert.ok(receiptCalled);
    assert.equal(updatedState.view, 'board');
    assert.equal(updatedState.appState, 'INITIAL');
    assert.equal(updatedState.previewMode, null, 'Confirmed snapshot should exit preview mode');
    assert.equal(updatedState.phaseRequestId, null, 'Confirmed snapshot should clear stale phase request id');
    assert.equal(updatedState.committedTaskTree?.planning_context?.current_phase?.phase_id, 'phase_02');
    assert.equal(updatedState.boardTasks?.[0]?.phase_id, 'phase_02');
    assert.equal(updatedState.boardTasks?.[0]?.source, 'ai');
    assert.equal(localStorageValues.has('easyplan_preview_mode'), false);
    assert.equal(localStorageValues.has('easyplan_phase_request_id'), false);
  }

  // --- Scenario 9: late snapshot requests must not overwrite newer state (snapshotRequestGate) ---
  {
    let resolveA;
    let resolveB;
    const promiseA = new Promise((resolve) => { resolveA = resolve; });
    const promiseB = new Promise((resolve) => { resolveB = resolve; });

    let fetchCount = 0;
    const fetchMock = async (url) => {
      fetchCount++;
      if (fetchCount === 1) {
        await promiseA;
        return {
          ok: true,
          status: 200,
          json: async () => ({
            thread_id: 'thread-1',
            status: 'succeeded',
            intent_text: 'my intent',
            task_tree: { root: { title: 'Phase 1' } }
          })
        };
      } else {
        await promiseB;
        return {
          ok: true,
          status: 200,
          json: async () => ({
            thread_id: 'thread-1',
            status: 'succeeded',
            intent_text: 'my intent',
            task_tree: { root: { title: 'Phase 2' } }
          })
        };
      }
    };

    const { useAppStore } = loadAppStoreModule(fetchMock, {
      'easyplan_view': 'board',
      'easyplan_selected_project_id': 'thread-1',
    });

    const alignPromiseA = useAppStore.getState().alignState('thread-1');
    const alignPromiseB = useAppStore.getState().alignState('thread-1');

    // Resolve B first (it's the second request but resolves first)
    resolveB();
    await alignPromiseB;

    // Check that committedTaskTree is Phase 2
    assert.equal(useAppStore.getState().committedTaskTree.root.title, 'Phase 2');

    // Now resolve A last (the first request)
    resolveA();
    await alignPromiseA;

    // Verify A did NOT alter committedTaskTree, previewTaskTree, previewMode, phaseRequestId, appState, view
    const finalState = useAppStore.getState();
    assert.equal(finalState.committedTaskTree.root.title, 'Phase 2', 'Late Phase 1 response should not overwrite Phase 2');
    assert.equal(finalState.previewTaskTree, null);
    assert.equal(finalState.previewMode, null);
    assert.equal(finalState.phaseRequestId, null);
    assert.equal(finalState.appState, 'INITIAL');
    assert.equal(finalState.view, 'board');
  }

  // --- 测试场景 7: initial running 且尚无 interrupt 时保留 active run ---
  {
    let fetchCalled = false;
    const fetchMock = async (url) => {
      fetchCalled = true;
      assert.ok(url.includes('/api/threads/thread-initial'));
      return {
        ok: true,
        status: 200,
        json: async () => ({
          thread_id: 'thread-initial',
          status: 'running',
          task_tree: null,
          interrupt_payload: null
        })
      };
    };

    const initialLocal = {
      'easyplan_selected_project_id': 'thread-initial',
      'easyplan_thread_id': 'thread-initial',
      'easyplan_active_run': JSON.stringify({
        threadId: 'thread-initial',
        runType: 'initial',
        requestId: 'request-a'
      })
    };

    const { useAppStore, localStorageValues } = loadAppStoreModule(fetchMock, initialLocal);

    const state = useAppStore.getState();
    assert.equal(state.activeRun?.threadId, 'thread-initial');
    assert.equal(state.activeRun?.runType, 'initial');
    assert.equal(state.activeRun?.requestId, 'request-a');

    await state.alignState('thread-initial');

    const updatedState = useAppStore.getState();
    assert.ok(fetchCalled);
    assert.equal(updatedState.activeRun?.threadId, 'thread-initial');
    assert.equal(updatedState.activeRun?.runType, 'initial');
    assert.equal(updatedState.activeRun?.requestId, 'request-a');
    assert.equal(updatedState.appState, 'THINKING');
    assert.equal(localStorageValues.has('easyplan_active_run'), true);
  }

  console.log('stateRestoration tests passed');
}

runTests().catch(err => {
  console.error('Test failed:', err);
  process.exit(1);
});
