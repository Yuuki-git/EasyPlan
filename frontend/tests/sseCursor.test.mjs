import assert from 'node:assert/strict';
import { loadTsModule } from './testHelpers/loadTsModule.mjs';

const { reconcileSseCursor } = loadTsModule('../../src/lib/sseCursor.ts');

assert.equal(
  reconcileSseCursor({
    previousThreadId: 'thread-1',
    nextThreadId: 'thread-1',
    currentLastEventId: 'evt_00000042',
  }),
  'evt_00000042',
  'same-thread request changes should preserve the SSE cursor',
);

assert.equal(
  reconcileSseCursor({
    previousThreadId: 'thread-1',
    nextThreadId: 'thread-2',
    currentLastEventId: 'evt_00000042',
  }),
  null,
  'switching threads should clear the SSE cursor',
);

assert.equal(
  reconcileSseCursor({
    previousThreadId: null,
    nextThreadId: 'thread-1',
    currentLastEventId: 'evt_00000042',
  }),
  null,
  'fresh thread subscriptions should start without a carried cursor',
);

console.log('sseCursor tests passed');
