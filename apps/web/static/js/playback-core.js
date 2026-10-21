/* The queue belongs to this browser session, not to a shared server epoch. */
((scope) => {
  'use strict';
  class PlaybackQueue {
    constructor() { this.items = []; this.index = 0; this.started = 0; this.serial = 0; }
    update(items, timestamp, restart = false) {
      const key = this.items[this.index]?.key;
      this.items = items;
      const index = items.findIndex((item) => item.key === key);
      if (restart || index < 0) { this.index = 0; this.started = timestamp; this.serial += 1; }
      else this.index = index;
    }
    advance(timestamp, delta = 1) {
      if (!this.items.length) return;
      this.index = ((this.index + delta) % this.items.length + this.items.length) % this.items.length;
      this.started = timestamp;
      this.serial += 1;
    }
    restart(timestamp) { this.index = 0; this.started = timestamp; this.serial += 1; }
    current(timestamp) {
      if (!this.items.length) return null;
      const item = this.items[this.index];
      const duration = Math.max(1000, Number(item.durationMs) || 10000);
      const elapsed = Math.max(0, timestamp - this.started);
      return {item, index: this.index, elapsed, remaining: Math.max(0, duration - elapsed), serial: this.serial};
    }
  }
  const api = {PlaybackQueue};
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else scope.SignagePlayback = api;
})(globalThis);
