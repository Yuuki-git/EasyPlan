import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';

import ts from 'typescript';

function loadAuthRecoveryModule() {
  const source = readFileSync(new URL('../src/store/authRecovery.ts', import.meta.url), 'utf8');
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

const { buildAuthRecoveryState, isUnauthorizedResponse } = loadAuthRecoveryModule();

assert.equal(isUnauthorizedResponse({ status: 401 }), true);
assert.equal(isUnauthorizedResponse({ status: 403 }), false);
assert.deepEqual(JSON.parse(JSON.stringify(buildAuthRecoveryState('finish paper'))), {
  token: null,
  showAuthModal: true,
  pendingIntent: 'finish paper',
  appState: 'INITIAL',
  error: null,
});
