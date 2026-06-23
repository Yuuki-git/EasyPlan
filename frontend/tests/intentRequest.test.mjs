import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';

import ts from 'typescript';

function loadIntentRequestModule() {
  const source = readFileSync(new URL('../src/store/intentRequest.ts', import.meta.url), 'utf8');
  const { outputText } = ts.transpileModule(source, {
    compilerOptions: {
      module: ts.ModuleKind.CommonJS,
      target: ts.ScriptTarget.ES2020,
    },
  });
  const module = { exports: {} };
  vm.runInNewContext(outputText, { exports: module.exports, module });
  return module.exports;
}

const { buildIntentRequest, resolvePlannerProvider } = loadIntentRequestModule();

assert.equal(resolvePlannerProvider({ VITE_PLANNER_PROVIDER: 'deepseek' }), 'deepseek');
assert.equal(resolvePlannerProvider({ VITE_PLANNER_PROVIDER: ' bad-provider ' }), 'deepseek');

assert.deepEqual(
  JSON.parse(JSON.stringify(buildIntentRequest({
    intentText: 'write paper',
    preferredProvider: 'todoist',
    plannerProvider: 'deepseek',
  }))),
  {
    intent_text: 'write paper',
    preferred_provider: 'todoist',
    planner_provider: 'deepseek',
  },
);
