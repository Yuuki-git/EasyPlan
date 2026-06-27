import assert from 'node:assert/strict';
import { loadTsModule } from './testHelpers/loadTsModule.mjs';

const { formatPreviewEffort, formatBoardMinutes } = loadTsModule('../../src/lib/effortDisplay.ts');

assert.equal(formatPreviewEffort(null), '投入未知');
assert.equal(formatPreviewEffort(undefined), '投入未知');
assert.equal(formatPreviewEffort(10), '低投入');
assert.equal(formatPreviewEffort(15), '低投入');
assert.equal(formatPreviewEffort(25), '中投入');
assert.equal(formatPreviewEffort(30), '中投入');
assert.equal(formatPreviewEffort(35), '较重投入');
assert.equal(formatPreviewEffort(60), '较重投入');

assert.equal(formatBoardMinutes(null), null);
assert.equal(formatBoardMinutes(undefined), null);
assert.equal(formatBoardMinutes(3), '5 分钟');
assert.equal(formatBoardMinutes(23), '25 分钟');
assert.equal(formatBoardMinutes(27), '25 分钟');
assert.equal(formatBoardMinutes(28), '30 分钟');
assert.equal(formatBoardMinutes(33), '30 分钟');
assert.equal(formatBoardMinutes(36), '40 分钟');
assert.equal(formatBoardMinutes(58), '60 分钟');

console.log('effortDisplay tests passed');
