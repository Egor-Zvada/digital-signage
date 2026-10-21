const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const {PlaybackQueue} = require('../../apps/web/static/js/playback-core.js');

function playerHarness() {
  let timestamp = 0;
  const elements = new Map();
  const player = fs.readFileSync(path.join(__dirname,
    '../../apps/web/static/js/player.js'),'utf8');
  const startup = player.indexOf('  prepareOfflineCache().then(loadManifest)');
  assert.ok(startup > 0,'Player bootstrap marker must be present');
  // Expose existing closures while suppressing browser boot/network activity.
  const instrumented = `${player.slice(0,startup)}
    globalThis.testPlayer = {queue, selectedSlogan, scheduleActive, loadManifest,
      setManifest(value) {manifest = value;},
      setConnection(value, error) {connected = value; lastError = error;},
      getError() {return lastError;}};
  })();`;
  const context = vm.createContext({
    window:{SignagePlayback:{PlaybackQueue},addEventListener:() => {}},
    document:{
      getElementById(id) {
        if (!elements.has(id)) elements.set(id,{dataset:{},classList:{add(){},remove(){}}});
        return elements.get(id);
      },
      addEventListener:() => {},
    },
    navigator:{}, performance:{now:() => timestamp},
    crypto:{randomUUID:() => 'test-session'}, Date, Intl, console, AbortSignal,
    clearTimeout, setTimeout,
    fetch:async () => new Response(null,{status:304}),
  });
  vm.runInContext(instrumented,context,{filename:'instrumented-player.js'});
  return {...context.testPlayer, setTime(value) {timestamp = value;}};
}

test('slogan sets continue rotating across repeated widget slots', () => {
  const player = playerHarness();
  const scene = {slogans:{items:[
    {text:'Первая фраза', durationSeconds:10},
    {text:'Вторая фраза', durationSeconds:10},
  ]},config:{}};
  player.setManifest({channel:{timezone:'UTC'}});
  player.queue.update([{key:'slogan',durationMs:10000}],0);
  assert.equal(player.selectedSlogan(scene).text,'Первая фраза');
  player.setTime(10001);
  player.queue.advance(10001);
  assert.equal(player.selectedSlogan(scene).text,'Вторая фраза');
  player.setTime(20001);
  player.queue.advance(20001);
  assert.equal(player.selectedSlogan(scene).text,'Первая фраза');
});

test('item dates and weekdays can leave the queue idle until the next eligible period', () => {
  const player = playerHarness();
  const monday = Date.parse('2026-10-19T10:00:00Z');
  const tuesday = Date.parse('2026-10-20T10:00:00Z');
  const schedule = {weekdays:[0],dailyStart:'09:00:00',dailyEnd:'18:00:00'};
  assert.equal(player.scheduleActive(schedule,'UTC',monday),true);
  assert.equal(player.scheduleActive(schedule,'UTC',tuesday),false);
  assert.equal(player.scheduleActive({activeFrom:'2026-10-20T00:00:00Z'},'UTC',monday),false);
  assert.equal(player.scheduleActive({activeUntil:'2026-10-19T10:00:00Z'},'UTC',monday),false);
});

test('a successful unchanged response clears a recovered network error', async () => {
  const player = playerHarness();
  player.setConnection(false,'Связь: Network error');
  await player.loadManifest();
  assert.equal(player.getError(),'');
});

test('invalid legacy timezone falls back instead of stopping scheduled playback', () => {
  const player = playerHarness();
  const schedule = {dailyStart:'09:00:00',dailyEnd:'18:00:00'};
  assert.equal(player.scheduleActive(schedule,'Invalid/Timezone',Date.parse('2026-10-18T22:00:00Z')),true);
});
