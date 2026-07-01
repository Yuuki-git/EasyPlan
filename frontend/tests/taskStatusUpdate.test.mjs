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

  function useStore() {
    return state;
  }

  useStore.getState = api.getState;
  useStore.setState = api.setState;
  return useStore;
}

function loadAppStoreModule(fetchImpl) {
  const source = readFileSync(new URL('../src/store/useAppStore.ts', import.meta.url), 'utf8');
  const { outputText } = ts.transpileModule(source, {
    compilerOptions: {
      module: ts.ModuleKind.CommonJS,
      target: ts.ScriptTarget.ES2020,
    },
  });
  const runnableOutput = outputText.replaceAll('import.meta.env', '({})');

  const module = { exports: {} };
  const localStorageValues = new Map([['auth_token', 'token']]);
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
          buildAuthRecoveryState: (pendingIntent) => ({
            token: null,
            showAuthModal: true,
            pendingIntent,
            appState: 'INITIAL',
            error: null,
          }),
          isUnauthorizedResponse: (response) => response.status === 401,
        };
      }
      if (specifier === './intentRequest') {
        return {
          buildIntentRequest: () => ({}),
          resolvePlannerProvider: () => 'openai',
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
  };

  vm.runInNewContext(runnableOutput, context);
  return module.exports;
}

let resolvePatch;
const patchResponse = new Promise((resolve) => {
  resolvePatch = resolve;
});

const { useAppStore } = loadAppStoreModule(() => patchResponse);

useAppStore.setState({
  token: 'token',
  view: 'board',
  boardError: null,
  error: null,
  boardTasks: [
    { id: 'task-a', title: 'A', status: 'active' },
    { id: 'task-b', title: 'B', status: 'active' },
  ],
});

const updatePromise = useAppStore.getState().updateTaskStatus('task-a', 'completed');

assert.deepEqual(
  useAppStore.getState().boardTasks.map((task) => [task.id, task.status]),
  [
    ['task-a', 'completed'],
    ['task-b', 'active'],
  ],
);

useAppStore.setState((state) => ({
  boardTasks: state.boardTasks.map((task) =>
    task.id === 'task-b' ? { ...task, status: 'completed' } : task,
  ),
}));

resolvePatch({ ok: false, status: 500, json: async () => ({}) });

await assert.rejects(updatePromise, /Failed to update task status/);

assert.deepEqual(
  useAppStore.getState().boardTasks.map((task) => [task.id, task.status]),
  [
    ['task-a', 'active'],
    ['task-b', 'completed'],
  ],
);
assert.equal(useAppStore.getState().boardError, '任务状态同步失败，请稍后重试');

let loadedSnapshotThread = null;
useAppStore.setState({ loadProjectSnapshot: async (threadId) => { loadedSnapshotThread = threadId; } });

// Now mock a successful AI phase task update
let resolveAiPatch;
const aiPatchResponse = new Promise((resolve) => {
  resolveAiPatch = resolve;
});
const { useAppStore: useAppStoreAi } = loadAppStoreModule(() => aiPatchResponse);

useAppStoreAi.setState({
  token: 'token',
  view: 'board',
  boardError: null,
  error: null,
  boardTasks: [
    { id: 'ai-task', title: 'AI Task', status: 'active', source: 'ai', phase_id: 'phase-1', thread_id: 'thread-1' }
  ],
  loadProjectSnapshot: async (threadId) => { loadedSnapshotThread = threadId; }
});

const aiUpdatePromise = useAppStoreAi.getState().updateTaskStatus('ai-task', 'completed');
resolveAiPatch({ ok: true, json: async () => ({ id: 'ai-task', status: 'completed', source: 'ai', phase_id: 'phase-1', thread_id: 'thread-1' }) });
await aiUpdatePromise;

assert.equal(loadedSnapshotThread, 'thread-1');
console.log('taskStatusUpdate tests passed');
