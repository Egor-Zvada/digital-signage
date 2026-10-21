const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const workerSource = fs.readFileSync(path.join(__dirname,
  '../../apps/server/templates/player/service-worker.js'), 'utf8');
const scope = 'https://signage.test/display/screen/secret/';
const manifestUrl = `${scope}manifest.json`;

function deferred() {
  let resolve;
  const promise = new Promise((done) => { resolve = done; });
  return {promise, resolve};
}

class FakeCache {
  constructor() { this.data = new Map(); }
  key(value) { return new URL(typeof value === 'string' ? value : value.url, scope).href; }
  async match(value) { return this.data.get(this.key(value))?.clone(); }
  async put(value, response) {
    const body = await response.arrayBuffer();
    this.data.set(this.key(value),new Response(body,{status:response.status,headers:response.headers}));
  }
  async delete(value) { return this.data.delete(this.key(value)); }
  async addAll() {}
}

function manifest(revision, offlineEnabled, files = 1) {
  return {channel:{id:1}, revision, screen:{settings:{offlineEnabled}},
    items:Array.from({length:files}, (_, index) => ({key:`item-${index}`,
      asset:{id:`asset-${index}`, kind:'video', size:10,
        mediaUrl:`${scope}media/asset-${index}?revision=${revision}`}}))};
}

function responseFor(candidate) {
  return new Response(JSON.stringify(candidate), {headers:{
    'Content-Type':'application/json', ETag:`"r${candidate.revision}-${candidate.screen.settings.offlineEnabled}"`,
  }});
}

function harness() {
  const stores = new Map();
  const messages = [];
  const listeners = new Map();
  let network;
  const context = vm.createContext({URL, Request, Response, Headers, AbortController,
    setTimeout, clearTimeout, console, btoa,
    fetch: (...args) => network(...args),
    caches:{
      async open(name) { if (!stores.has(name)) stores.set(name,new FakeCache()); return stores.get(name); },
      async keys() { return [...stores.keys()]; },
      async delete(name) { return stores.delete(name); },
    },
    self:{registration:{scope}, addEventListener:(type, listener) => listeners.set(type,listener),
      skipWaiting:async () => {},
      clients:{claim:async () => {}, matchAll:async () => [{postMessage:(data) => messages.push(data)}]},
    },
  });
  vm.runInContext(workerSource,context,{filename:'player-service-worker.js'});
  return {
    context, stores, messages,
    setNetwork(handler) { network = handler; },
    async request(headers = {}) {
      const waits = [];
      const event = {request:new Request(manifestUrl,{headers}), waitUntil:(promise) => waits.push(promise)};
      const response = await context.handleManifest(event);
      return {response, settle:() => Promise.all(waits)};
    },
    meta:() => context.readMeta(),
  };
}

test('streaming mode stores only manifest and never downloads all videos', async () => {
  const worker = harness();
  let mediaRequests = 0;
  worker.setNetwork(async (request) => {
    const url = typeof request === 'string' ? request : request.url;
    if (url === manifestUrl) return responseFor(manifest(1,false,5));
    mediaRequests += 1;
    return new Response('0123456789');
  });
  const fetched = await worker.request();
  await fetched.settle();
  assert.equal(mediaRequests,0);
  assert.equal((await worker.meta()).status.state,'disabled');
});

test('offline media downloads are sequential and cached videos support byte ranges', async () => {
  const worker = harness();
  let concurrent = 0;
  let maximum = 0;
  worker.setNetwork(async (request) => {
    const url = typeof request === 'string' ? request : request.url;
    if (url === manifestUrl) return responseFor(manifest(1,true,3));
    concurrent += 1;
    maximum = Math.max(maximum,concurrent);
    await new Promise((resolve) => setTimeout(resolve,5));
    concurrent -= 1;
    return new Response('0123456789',{headers:{'Content-Type':'video/mp4','Content-Length':'10'}});
  });
  const fetched = await worker.request();
  await fetched.settle();
  assert.equal(maximum,1);
  const meta = await worker.meta();
  assert.equal(meta.status.state,'ready');
  assert.equal(meta.status.completed,3);
  const ranged = await worker.context.cachedMedia(new Request(`${scope}media/asset-0?revision=1`,{
    headers:{Range:'bytes=2-5'},
  }));
  assert.equal(ranged.status,206);
  assert.equal(ranged.headers.get('Content-Range'),'bytes 2-5/10');
  assert.equal(await ranged.text(),'2345');
});

test('failed new revision preserves the previous complete offline program', async () => {
  const worker = harness();
  worker.setNetwork(async (request) => typeof request !== 'string'
    ? responseFor(manifest(1,true)) : new Response('old-video'));
  let fetched = await worker.request();
  await fetched.settle();
  worker.setNetwork(async (request) => {
    if (typeof request !== 'string') return responseFor(manifest(2,true));
    throw new TypeError('Network disconnected');
  });
  fetched = await worker.request();
  assert.equal((await fetched.response.json()).revision,1);
  await fetched.settle();
  assert.equal((await worker.meta()).revision,1);
  worker.setNetwork(async () => { throw new TypeError('Offline'); });
  const fallback = await worker.request();
  assert.equal(fallback.response.headers.get('X-Signage-Offline'),'1');
  assert.equal((await fallback.response.json()).revision,1);
});

test('a failed first offline download retries even when server answers 304', async () => {
  const worker = harness();
  let manifestRequests = 0;
  let mediaRequests = 0;
  worker.setNetwork(async (request) => {
    const url = typeof request === 'string' ? request : request.url;
    if (url === manifestUrl) {
      manifestRequests += 1;
      return manifestRequests === 1 ? responseFor(manifest(1,true)) : new Response(null,{status:304});
    }
    mediaRequests += 1;
    if (mediaRequests === 1) throw new TypeError('Temporary disconnect');
    return new Response('0123456789');
  });
  let fetched = await worker.request();
  await fetched.settle();
  assert.notEqual((await worker.meta()).status?.state,'ready');
  fetched = await worker.request({'If-None-Match':'"r1-true"'});
  await fetched.settle();
  assert.equal(mediaRequests,2);
  assert.equal((await worker.meta()).status.state,'ready');
});

test('revocation during download cannot resurrect offline access', async () => {
  const worker = harness();
  const downloadStarted = deferred();
  const downloadFinish = deferred();
  let revoked = false;
  worker.setNetwork(async (request) => {
    if (typeof request !== 'string') return revoked ? new Response(null,{status:404}) : responseFor(manifest(1,true));
    downloadStarted.resolve();
    await downloadFinish.promise;
    return new Response('0123456789');
  });
  const initial = await worker.request();
  await downloadStarted.promise;
  revoked = true;
  const rejected = await worker.request();
  assert.equal(rejected.response.status,404);
  downloadFinish.resolve();
  await initial.settle();
  worker.setNetwork(async () => { throw new TypeError('Offline'); });
  const fallback = await worker.request();
  assert.equal(fallback.response.status,503);
  assert.equal((await worker.meta()).active,undefined);
});

test('304 keeps a complete cache and avoids redundant media requests', async () => {
  const worker = harness();
  let downloaded = 0;
  worker.setNetwork(async (request) => {
    if (typeof request !== 'string') return responseFor(manifest(1,true));
    downloaded += 1;
    return new Response('0123456789');
  });
  const initial = await worker.request();
  await initial.settle();
  worker.setNetwork(async () => new Response(null,{status:304}));
  const unchanged = await worker.request({'If-None-Match':'"r1-true"'});
  await unchanged.settle();
  assert.equal(unchanged.response.status,304);
  assert.equal(downloaded,1);
  assert.equal((await worker.meta()).status.state,'ready');
});

test('concurrent polling shares one staging download', async () => {
  const worker = harness();
  const started = deferred();
  const finish = deferred();
  let downloaded = 0;
  worker.setNetwork(async (request) => {
    if (typeof request !== 'string') return responseFor(manifest(1,true));
    downloaded += 1;
    started.resolve();
    await finish.promise;
    return new Response('0123456789');
  });
  const first = await worker.request();
  await started.promise;
  const second = await worker.request();
  assert.equal(downloaded,1);
  finish.resolve();
  await Promise.all([first.settle(),second.settle()]);
  assert.equal((await worker.meta()).status.state,'ready');
  assert.equal(downloaded,1);
});

test('switching to streaming mode works without a new channel revision', async () => {
  const worker = harness();
  let enabled = true;
  let downloaded = 0;
  worker.setNetwork(async (request) => {
    if (typeof request !== 'string') return responseFor(manifest(1,enabled));
    downloaded += 1;
    return new Response('0123456789');
  });
  const first = await worker.request();
  await first.settle();
  enabled = false;
  const second = await worker.request();
  await second.settle();
  const third = await worker.request();
  assert.equal((await third.response.json()).screen.settings.offlineEnabled,false);
  assert.equal((await worker.meta()).status.state,'disabled');
  assert.equal(downloaded,1);
});

test('a replacement revision becomes active only after all media is stored', async () => {
  const worker = harness();
  let revision = 1;
  const started = deferred();
  const finish = deferred();
  worker.setNetwork(async (request) => {
    if (typeof request !== 'string') return responseFor(manifest(revision,true));
    if (revision === 2) {
      started.resolve();
      await finish.promise;
    }
    return new Response('0123456789');
  });
  const first = await worker.request();
  await first.settle();
  const oldCache = (await worker.meta()).active;
  revision = 2;
  const replacement = await worker.request();
  await started.promise;
  assert.equal((await replacement.response.json()).revision,1);
  assert.equal((await worker.meta()).active,oldCache);
  finish.resolve();
  await replacement.settle();
  const meta = await worker.meta();
  assert.equal(meta.revision,2);
  assert.equal(meta.previous,oldCache);
  assert.notEqual(meta.active,oldCache);
});

test('cached video supports open, suffix and unsatisfiable ranges', async () => {
  const worker = harness();
  for (const [range,code,expected] of [
    ['bytes=5-',206,'56789'],
    ['bytes=-3',206,'789'],
    ['bytes=0-99',206,'0123456789'],
    ['bytes=10-',416,''],
    ['bytes=-0',416,''],
    ['bytes=6-3',416,''],
    ['bytes=0-1,4-5',416,''],
  ]) {
    const response = await worker.context.rangedResponse(new Response('0123456789'),range);
    assert.equal(response.status,code,range);
    assert.equal(await response.text(),expected,range);
  }
});
