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
  const runnableOutput = outputText.replaceAll('import.meta.env', '({VITE_PHASE_PLANNING_ENABLED: "true"})');

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
  return module.exports;
}

let fetchedUrl = null;
let fetchOptions = null;
let resolveFetch;

const fetchImpl = async (url, options) => {
  fetchedUrl = url;
  fetchOptions = options;
  if (url.includes('/phases/next')) {
    return { ok: true, status: 200, json: async () => ({}) };
  }
  return { ok: true, status: 200, json: async () => ({}) };
};

const { useAppStore } = loadAppStoreModule(fetchImpl);

useAppStore.setState({
  token: 'token',
  selectedProjectId: 'thread-1',
  committedTaskTree: { planning_context: { roadmap: [] } },
  boardTasks: [],
  isPhaseRequestPending: false
});

await useAppStore.getState().generateNextPhasePlan();

assert.equal(fetchedUrl, '/api/threads/thread-1/phases/next');
assert.equal(fetchOptions.method, 'POST');
assert.equal(useAppStore.getState().previewMode, 'next_phase');
assert.equal(useAppStore.getState().appState, 'THINKING');
assert.equal(useAppStore.getState().isPhaseRequestPending, false);

console.log('phaseStore tests passed');
