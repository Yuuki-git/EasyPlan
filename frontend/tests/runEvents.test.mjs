import assert from 'node:assert/strict';
import { loadTsModule } from './testHelpers/loadTsModule.mjs';

const {
  RUN_STALL_THRESHOLD_MS,
  createRunEventTracker,
  isRunStalled,
  matchesRunIdentity,
} = loadTsModule('../../src/lib/runEvents.ts');

assert.equal(
  matchesRunIdentity(
    { thread_id: 'thread-1', run_type: 'next_phase', request_id: 'request-a' },
    { threadId: 'thread-1', runType: 'next_phase', requestId: 'request-b' },
  ),
  false,
);

assert.equal(
  matchesRunIdentity(
    { thread_id: 'thread-1', run_type: 'next_phase', request_id: 'request-a' },
    { threadId: 'thread-1', runType: 'next_phase', requestId: 'request-a' },
  ),
  true,
);

const tracker = createRunEventTracker();
assert.equal(tracker.accept('evt-1', 'thread-1'), true);
assert.equal(tracker.accept('evt-1', 'thread-1'), false);
assert.equal(tracker.accept('evt-1', 'thread-2'), true);
assert.equal(tracker.accept('evt-1', 'thread-1'), false);

assert.equal(tracker.accept(null, 'thread-1'), true);
assert.equal(tracker.accept(undefined, 'thread-1'), true);

assert.equal(RUN_STALL_THRESHOLD_MS, 30000);
assert.equal(isRunStalled(0, 29000), false);
assert.equal(isRunStalled(0, 30000), true);
assert.equal(isRunStalled(0, 11000, 10000), true);
assert.equal(isRunStalled(0, 9000, 10000), false);
assert.equal(isRunStalled(null, 31000), false);

console.log('runEvents tests passed');
