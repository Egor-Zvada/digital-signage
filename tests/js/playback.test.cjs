const test = require('node:test');
const assert = require('node:assert/strict');
const {PlaybackQueue} = require('../../apps/web/static/js/playback-core.js');

const item = (key, durationMs = 10000) => ({key, durationMs});

test('a newly opened browser starts the first item at zero elapsed', () => {
  const queue = new PlaybackQueue();
  queue.update([item('first'), item('second')], 842344);
  const slot = queue.current(842344);
  assert.equal(slot.item.key, 'first');
  assert.equal(slot.elapsed, 0);
  assert.equal(slot.remaining, 10000);
});

test('one item repeats with a fresh serial and zero elapsed', () => {
  const queue = new PlaybackQueue();
  queue.update([item('only')], 3000);
  const oldSerial = queue.current(3000).serial;
  queue.advance(13000);
  const slot = queue.current(13000);
  assert.equal(slot.item.key, 'only');
  assert.equal(slot.elapsed, 0);
  assert.ok(slot.serial > oldSerial);
});

test('several items repeat in playlist order', () => {
  const queue = new PlaybackQueue();
  queue.update([item('first'), item('second'), item('third')], 10);
  const keys = [];
  for (let count = 0; count < 7; count += 1) {
    keys.push(queue.current(10 + count * 10000).item.key);
    queue.advance(10010 + count * 10000);
  }
  assert.deepEqual(keys, ['first', 'second', 'third', 'first', 'second', 'third', 'first']);
});

test('manual next and previous remain selected on later schedule polls', () => {
  const queue = new PlaybackQueue();
  const items = [item('first'), item('second'), item('third')];
  queue.update(items, 0);
  queue.advance(4000);
  queue.update(items, 5000);
  assert.equal(queue.current(5000).item.key, 'second');
  assert.equal(queue.current(5000).elapsed, 1000);
  queue.advance(5500, -1);
  queue.update(items, 6000);
  assert.equal(queue.current(6000).item.key, 'first');
  queue.advance(6500, -1);
  queue.update(items, 7000);
  assert.equal(queue.current(7000).item.key, 'third');
});

test('schedule idle followed by active items begins from the first eligible item', () => {
  const queue = new PlaybackQueue();
  queue.update([item('old')], 0);
  queue.update([], 5000);
  assert.equal(queue.current(5000), null);
  queue.update([item('new-first'), item('new-second')], 100000);
  assert.equal(queue.current(100000).item.key, 'new-first');
  assert.equal(queue.current(100000).elapsed, 0);
});

test('refreshes preserve the current item while restart resets it', () => {
  const queue = new PlaybackQueue();
  queue.update([item('first'), item('second')], 0);
  queue.advance(2000);
  queue.update([item('inserted'), item('first'), item('second')], 3000);
  assert.equal(queue.current(3000).item.key, 'second');
  assert.equal(queue.current(3000).elapsed, 1000);
  queue.restart(4000);
  assert.equal(queue.current(4000).item.key, 'inserted');
  assert.equal(queue.current(4000).elapsed, 0);
});
