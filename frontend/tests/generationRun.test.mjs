import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';
import ts from 'typescript';
import { loadTsModule } from './testHelpers/loadTsModule.mjs';

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
    __test__: true,
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
  globalThis.__test__ = true;
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
      committedTaskTree: { root: {} },
      previewTaskTree: { root: {} },
      nodeStatuses: { 'node-1': 'success' },
      error: 'some error',
      isRunStalled: true
    });

    await useAppStore.getState().submitIntent('some intent');

    const state = useAppStore.getState();
    assert.ok(fetchCalled);
    assert.equal(state.isRunStalled, false);
    assert.deepEqual(plain(state.reasoningLogs), []);
    assert.equal(state.committedTaskTree, null);
    assert.equal(state.previewTaskTree, null);
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
      committedTaskTree: { planning_context: {} }, // 能够 unlock
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
    assert.deepEqual(plain(state.committedTaskTree), { planning_context: {} });
    assert.equal(state.previewTaskTree, null);
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
      previewTaskTree: { root: {} },
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
    assert.equal(state.previewTaskTree, null);
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
      committedTaskTree: { root: {} },
      previewTaskTree: { root: {} }
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
    assert.equal(state.committedTaskTree, null);
    assert.equal(state.previewTaskTree, null);
  }

  // --- 测试场景 5: 错误呈现与契约错误友好文案转换 ---
  {
    const { getFriendlyErrorMessage } = loadTsModule('../../src/lib/errorHelper.ts');

    // 契约/内部错误映射
    assert.equal(
      getFriendlyErrorMessage("planning_context time_horizon must match IntentProfile"),
      "这次规划没有顺利完成，请重试一次"
    );
    assert.equal(
      getFriendlyErrorMessage("TypeError: Cannot read properties of undefined"),
      "这次规划没有顺利完成，请重试一次"
    );
    assert.equal(
      getFriendlyErrorMessage("validation_error: invalid type"),
      "这次规划没有顺利完成，请重试一次"
    );

    // 正常业务错误保留
    assert.equal(
      getFriendlyErrorMessage("余额不足，无法执行该操作"),
      "余额不足，无法执行该操作"
    );
    assert.equal(
      getFriendlyErrorMessage("预览已过期/请求不匹配，请重新生成下一阶段"),
      "预览已过期/请求不匹配，请重新生成下一阶段"
    );
  }

  // --- 测试场景 6: 探索决策“先答后拆”摘要解析 ---
  {
    const { parseExplorationSummary } = loadTsModule('../../src/lib/explorationHelper.ts');

    // 格式 1: 显式标记
    const summary1 = "当前判断：可以考虑，但先做低成本验证。判断依据：目前转行门槛较高，且竞争大。下一步探索：调研3个成功案例。";
    const res1 = parseExplorationSummary(summary1);
    assert.equal(res1.judgment, "可以考虑，但先做低成本验证。");
    assert.equal(res1.basis, "目前转行门槛较高，且竞争大。");
    assert.equal(res1.exploration, "调研3个成功案例。");

    // 格式 2: 纯段落（按句子拆分）
    const summary2 = "进行低成本验证是值得的。因为目前市场竞争激烈，不确定因素多。建议开展澄清和调研。";
    const res2 = parseExplorationSummary(summary2);
    assert.equal(res2.judgment, "进行低成本验证是值得的。");
    assert.equal(res2.basis, "因为目前市场竞争激烈，不确定因素多。");
    assert.equal(res2.exploration, "建议开展澄清和调研。");
  }

  // --- 测试场景 7: refinePlan 清除上一轮运行临时状态与可见 reasoning ---
  {
    let fetchCalled = false;
    const fetchMock = async (url, options) => {
      fetchCalled = true;
      assert.ok(url.includes('/api/threads/thread-refine/confirm'));
      assert.equal(options.method, 'POST');
      const body = JSON.parse(options.body);
      assert.equal(body.action, 'refine');
      assert.equal(body.feedback, 'more detailed plan');
      return {
        ok: true,
        status: 202,
        json: async () => ({})
      };
    };

    const { useAppStore } = loadAppStoreModule(fetchMock, {
      'easyplan_thread_id': 'thread-refine'
    });

    // Preset some old run states
    useAppStore.setState({
      appState: 'PENDING',
      reasoningLogs: ['old log 1', 'old log 2'],
      committedTaskTree: { root: { title: 'old root' } },
      previewTaskTree: { root: { title: 'old root' } },
      nodeStatuses: { 'node-1': 'success' },
      error: 'some error'
    });

    await useAppStore.getState().refinePlan('more detailed plan');

    const state = useAppStore.getState();
    assert.ok(fetchCalled);
    assert.equal(state.appState, 'THINKING');
    assert.equal(state.error, null);
    assert.equal(state.previewTaskTree, null);
    assert.deepEqual(plain(state.reasoningLogs), []);
    assert.deepEqual(plain(state.nodeStatuses), {});
  }

  // --- 测试场景 8: Unlock Phase N 后 view 仍是 board 且请求在路上时立刻进入 inline loading 状态 ---
  {
    let resolveFetch;
    const fetchMock = async () => {
      return new Promise((resolve) => {
        resolveFetch = () => resolve({
          ok: true,
          status: 200,
          json: async () => ({})
        });
      });
    };

    const { useAppStore } = loadAppStoreModule(fetchMock);

    // Setup initial state
    useAppStore.setState({
      selectedProjectId: 'proj-123',
      committedTaskTree: { planning_context: {} },
      boardTasks: [],
      view: 'board'
    });

    const promise = useAppStore.getState().generateNextPhasePlan();

    // 显式等待微任务队列调度，允许异步 import 完成并运行首个 set 状态变更
    await new Promise(resolve => setTimeout(resolve, 5));

    // 校验请求在途（in-flight）时，状态已经同步变更为 next_phase 且为 THINKING 态，确保界面原地进入 loading
    const midState = useAppStore.getState();
    assert.equal(midState.view, 'board', 'Unlock Phase N should keep view as board during generation');
    assert.equal(midState.previewMode, 'next_phase', 'Should enter next_phase previewMode immediately');
    assert.equal(midState.appState, 'THINKING', 'Should enter THINKING state immediately for inline loading');
    assert.equal(midState.isPhaseRequestPending, true);

    // 允许请求返回
    resolveFetch();
    await promise;

    // 校验完成后状态依然保持
    const state = useAppStore.getState();
    assert.equal(state.view, 'board', 'Unlock Phase N should keep view as board after generation completes');
    assert.equal(state.previewMode, 'next_phase');
    assert.equal(state.appState, 'THINKING');
    assert.equal(state.isPhaseRequestPending, false);
  }

  // --- Scenario 9: transient committed snapshots must not knock next-phase runs back to portfolio mode ---
  {
    const fetchMock = async (url) => {
      if (url.includes('/api/threads/proj-race')) {
        return {
          ok: true,
          status: 200,
          json: async () => ({
            thread_id: 'proj-race',
            status: 'succeeded',
            intent_text: 'my intent',
            task_tree: {
              root: { title: 'Committed Phase 1' },
              planning_context: {
                roadmap: [],
                current_phase: { phase_id: 'phase-1', title: 'Phase 1', objective: 'Finish phase 1' },
              }
            }
          })
        };
      }
      if (url.includes('/api/tasks')) {
        return {
          ok: true,
          status: 200,
          json: async () => ([])
        };
      }
      return {
        ok: true,
        status: 200,
        json: async () => ({})
      };
    };

    const { useAppStore, localStorageValues } = loadAppStoreModule(fetchMock, {
      'easyplan_view': 'board',
      'easyplan_selected_project_id': 'proj-race',
      'easyplan_thread_id': 'proj-race',
      'easyplan_preview_mode': 'next_phase',
      'easyplan_phase_request_id': 'req-race',
      'easyplan_base_phase_id': 'phase-1'
    });

    useAppStore.setState({
      view: 'board',
      selectedProjectId: 'proj-race',
      threadId: 'proj-race',
      appState: 'THINKING',
      previewMode: 'next_phase',
      phaseRequestId: 'req-race',
      basePhaseId: 'phase-1',
      boardTasks: [],
      committedTaskTree: { planning_context: { current_phase: { phase_id: 'phase-1' } } },
      previewTaskTree: { planning_context: {} }
    });

    await useAppStore.getState().alignState('proj-race');

    const alignedState = useAppStore.getState();
    assert.equal(alignedState.selectedProjectId, 'proj-race');
    assert.equal(alignedState.previewMode, 'next_phase', 'alignState should preserve next_phase preview until the server snapshot catches up');
    assert.equal(alignedState.phaseRequestId, 'req-race', 'alignState should preserve the in-flight phase request id');
    assert.equal(alignedState.appState, 'THINKING', 'alignState should keep the inline loading state during the transition window');
    assert.equal(localStorageValues.get('easyplan_preview_mode'), 'next_phase');
    assert.equal(localStorageValues.get('easyplan_phase_request_id'), 'req-race');

    await useAppStore.getState().finishAgentRun({
      thread_id: 'proj-race',
      run_type: 'next_phase',
      request_id: 'req-race',
      state_version: 2
    });

    const finishedState = useAppStore.getState();
    assert.equal(finishedState.selectedProjectId, 'proj-race', 'finishing a next-phase run should stay inside the current project');
    assert.equal(finishedState.view, 'board');
  }

  // --- 测试场景 8: finishAgentRun - 历史陈旧 done 应该什么都不做 ---
  {
    const { useAppStore, localStorageValues } = loadAppStoreModule(async () => ({
      ok: true,
      status: 200,
      json: async () => ({})
    }), {
      'easyplan_view': 'board',
      'easyplan_selected_project_id': 'proj-proof',
      'easyplan_thread_id': 'proj-proof',
      'easyplan_preview_mode': 'next_phase',
      'easyplan_phase_request_id': 'req-current',
      'easyplan_base_phase_id': 'phase-1'
    });

    useAppStore.setState({
      view: 'board',
      selectedProjectId: 'proj-proof',
      threadId: 'proj-proof',
      previewMode: 'next_phase',
      phaseRequestId: 'req-current',
      basePhaseId: 'phase-1'
    });

    // 历史 done 事件：requestId 为 req-old
    await useAppStore.getState().finishAgentRun({
      thread_id: 'proj-proof',
      run_type: 'next_phase',
      request_id: 'req-old',
      state_version: 2
    });

    const state = useAppStore.getState();
    assert.equal(state.previewMode, 'next_phase', 'stale finishAgentRun should not clear previewMode');
    assert.equal(state.phaseRequestId, 'req-current');
  }

  // --- 测试场景 9: finishAgentRun - 匹配但未确认 the snapshot 不应清掉 preview ---
  {
    const fetchMock = async (url) => {
      if (url.includes('/api/threads/proj-proof')) {
        return {
          ok: true,
          status: 200,
          json: async () => ({
            thread_id: 'proj-proof',
            status: 'awaiting_confirmation',
            interrupt_payload: {
              type: 'phase_generation_state',
              request_id: 'req-current',
              status: 'running' // 未确认
            }
          })
        };
      }
      if (url.includes('/api/tasks')) {
        return {
          ok: true,
          status: 200,
          json: async () => ([])
        };
      }
      return {
        ok: true,
        status: 200,
        json: async () => ({})
      };
    };

    const { useAppStore } = loadAppStoreModule(fetchMock, {
      'easyplan_view': 'board',
      'easyplan_selected_project_id': 'proj-proof',
      'easyplan_thread_id': 'proj-proof',
      'easyplan_preview_mode': 'next_phase',
      'easyplan_phase_request_id': 'req-current',
      'easyplan_base_phase_id': 'phase-1'
    });

    useAppStore.setState({
      view: 'board',
      selectedProjectId: 'proj-proof',
      threadId: 'proj-proof',
      previewMode: 'next_phase',
      phaseRequestId: 'req-current',
      basePhaseId: 'phase-1'
    });

    await useAppStore.getState().finishAgentRun({
      thread_id: 'proj-proof',
      run_type: 'next_phase',
      request_id: 'req-current',
      state_version: 2
    });

    const state = useAppStore.getState();
    assert.equal(state.previewMode, 'next_phase', 'unconfirmed snapshot should not clear preview');
    assert.ok(state.error && state.error.includes('未检测到下一阶段的确认'));
  }

  // --- 测试场景 10: finishAgentRun - 匹配且已确认但阶段未推进不应清掉 preview ---
  {
    const fetchMock = async (url) => {
      if (url.includes('/api/threads/proj-proof')) {
        return {
          ok: true,
          status: 200,
          json: async () => ({
            thread_id: 'proj-proof',
            status: 'succeeded',
            interrupt_payload: {
              type: 'phase_generation_state',
              request_id: 'req-current',
              status: 'confirmed'
            },
            task_tree: {
              planning_context: {
                current_phase: {
                  phase_id: 'phase-1' // 仍是原阶段
                }
              }
            }
          })
        };
      }
      if (url.includes('/api/tasks')) {
        return {
          ok: true,
          status: 200,
          json: async () => ([])
        };
      }
      return {
        ok: true,
        status: 200,
        json: async () => ({})
      };
    };

    const { useAppStore } = loadAppStoreModule(fetchMock, {
      'easyplan_view': 'board',
      'easyplan_selected_project_id': 'proj-proof',
      'easyplan_thread_id': 'proj-proof',
      'easyplan_preview_mode': 'next_phase',
      'easyplan_phase_request_id': 'req-current',
      'easyplan_base_phase_id': 'phase-1'
    });

    useAppStore.setState({
      view: 'board',
      selectedProjectId: 'proj-proof',
      threadId: 'proj-proof',
      previewMode: 'next_phase',
      phaseRequestId: 'req-current',
      basePhaseId: 'phase-1'
    });

    await useAppStore.getState().finishAgentRun({
      thread_id: 'proj-proof',
      run_type: 'next_phase',
      request_id: 'req-current',
      state_version: 2
    });

    const state = useAppStore.getState();
    assert.equal(state.previewMode, 'next_phase', 'snapshot without advanced phase should not clear preview');
  }

  // --- 测试场景 11: finishAgentRun - 阶段已推进但无相应 AI 任务不应清掉 preview ---
  {
    const fetchMock = async (url) => {
      if (url.includes('/api/threads/proj-proof')) {
        return {
          ok: true,
          status: 200,
          json: async () => ({
            thread_id: 'proj-proof',
            status: 'succeeded',
            interrupt_payload: {
              type: 'phase_generation_state',
              request_id: 'req-current',
              status: 'confirmed'
            },
            task_tree: {
              planning_context: {
                current_phase: {
                  phase_id: 'phase-2' // 已推进
                }
              }
            }
          })
        };
      }
      if (url.includes('/api/tasks')) {
        return {
          ok: true,
          status: 200,
          json: async () => ([]) // 无任务
        };
      }
      return {
        ok: true,
        status: 200,
        json: async () => ({})
      };
    };

    const { useAppStore } = loadAppStoreModule(fetchMock, {
      'easyplan_view': 'board',
      'easyplan_selected_project_id': 'proj-proof',
      'easyplan_thread_id': 'proj-proof',
      'easyplan_preview_mode': 'next_phase',
      'easyplan_phase_request_id': 'req-current',
      'easyplan_base_phase_id': 'phase-1'
    });

    useAppStore.setState({
      view: 'board',
      selectedProjectId: 'proj-proof',
      threadId: 'proj-proof',
      previewMode: 'next_phase',
      phaseRequestId: 'req-current',
      basePhaseId: 'phase-1'
    });

    await useAppStore.getState().finishAgentRun({
      thread_id: 'proj-proof',
      run_type: 'next_phase',
      request_id: 'req-current',
      state_version: 2
    });

    const state = useAppStore.getState();
    assert.equal(state.previewMode, 'next_phase', 'snapshot with advanced phase but no phase tasks should not clear preview');
  }

  // --- 测试场景 12: finishAgentRun - 全部条件匹配应成功提交 Phase 2 并清空 preview ---
  {
    const fetchMock = async (url) => {
      if (url.includes('/api/threads/proj-proof')) {
        return {
          ok: true,
          status: 200,
          json: async () => ({
            thread_id: 'proj-proof',
            status: 'succeeded',
            interrupt_payload: {
              type: 'phase_generation_state',
              request_id: 'req-current',
              status: 'confirmed'
            },
            task_tree: {
              root: { title: 'Phase 2 Root' },
              planning_context: {
                current_phase: {
                  phase_id: 'phase-2'
                }
              }
            }
          })
        };
      }
      if (url.includes('/api/tasks')) {
        return {
          ok: true,
          status: 200,
          json: async () => ([
            {
              id: 'task-1',
              thread_id: 'proj-proof',
              phase_id: 'phase-2',
              source: 'ai'
            }
          ])
        };
      }
      return {
        ok: true,
        status: 200,
        json: async () => ({})
      };
    };

    const { useAppStore, localStorageValues } = loadAppStoreModule(fetchMock, {
      'easyplan_view': 'board',
      'easyplan_selected_project_id': 'proj-proof',
      'easyplan_thread_id': 'proj-proof',
      'easyplan_preview_mode': 'next_phase',
      'easyplan_phase_request_id': 'req-current',
      'easyplan_base_phase_id': 'phase-1'
    });

    useAppStore.setState({
      view: 'board',
      selectedProjectId: 'proj-proof',
      threadId: 'proj-proof',
      previewMode: 'next_phase',
      phaseRequestId: 'req-current',
      basePhaseId: 'phase-1'
    });

    await useAppStore.getState().finishAgentRun({
      thread_id: 'proj-proof',
      run_type: 'next_phase',
      request_id: 'req-current',
      state_version: 2
    });

    const state = useAppStore.getState();
    assert.equal(state.previewMode, null, 'successful commit proof should clear previewMode');
    assert.equal(state.phaseRequestId, null);
    assert.equal(state.committedTaskTree?.root?.title, 'Phase 2 Root');
    assert.equal(localStorageValues.has('easyplan_preview_mode'), false);
    assert.equal(localStorageValues.has('easyplan_phase_request_id'), false);
    assert.equal(localStorageValues.has('easyplan_base_phase_id'), false);
  }

  console.log('generationRun tests passed');
}

runTests().catch(err => {
  console.error('Test failed:', err);
  process.exit(1);
});
