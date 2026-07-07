import assert from 'node:assert/strict';
import { loadTsModule } from './testHelpers/loadTsModule.mjs';

const { createLatestRequestGate } = loadTsModule('../../src/store/snapshotRequestGate.ts');

const gate = createLatestRequestGate();

const isCurrentA = gate.begin();
const isCurrentB = gate.begin();

assert.equal(isCurrentA(), false, 'Request A should be stale after B starts');
assert.equal(isCurrentB(), true, 'Request B should be current');

gate.invalidate();

assert.equal(isCurrentB(), false, 'Request B should be stale after invalidation');

console.log('snapshotRequestGate tests passed');
