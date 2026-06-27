import assert from 'node:assert/strict';
import { loadTsModule } from './testHelpers/loadTsModule.mjs';

const { createRunEventTracker, isRunStalled } = loadTsModule('../../src/lib/runEvents.ts');

const tracker = createRunEventTracker();
assert.equal(tracker.accept('evt-1', 'thread-1', 'req-1'), true);
assert.equal(tracker.accept('evt-1', 'thread-1', 'req-1'), false);
assert.equal(tracker.accept('evt-1', 'thread-2', 'req-1'), true);
assert.equal(tracker.accept('evt-1', 'thread-1', 'req-2'), true);

assert.equal(tracker.accept(null, 'thread-1', 'req-1'), true);
assert.equal(tracker.accept(undefined, 'thread-1', 'req-1'), true);

assert.equal(isRunStalled(0, 11000, 10000), true);
assert.equal(isRunStalled(0, 9000, 10000), false);
assert.equal(isRunStalled(null, 11000, 10000), false);

console.log('runEvents tests passed');
