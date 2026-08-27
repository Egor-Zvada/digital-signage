'use strict';

const WORKER_VERSION = '0.2.0';
const scopeUrl = new URL(self.registration.scope);
const scopeParts = scopeUrl.pathname.split('/').filter(Boolean);
const screenKey = (scopeParts.at(-2) || 'screen').replace(/[^a-zA-Z0-9-]/g, '');
const playerUrl = new URL('./', scopeUrl).href;
const manifestUrl = new URL('manifest.json', scopeUrl).href;
const metaUrl = new URL('__offline_meta__', scopeUrl).href;
const shellCache = `signage-shell-${screenKey}-v2`;
const metaCache = `signage-meta-${screenKey}`;
const mediaPrefix = `signage-media-${screenKey}-r`;
const shellAssets = [
  playerUrl,
  new URL('/static/css/player.css', scopeUrl).href,
  new URL('/static/js/player.js', scopeUrl).href,
  new URL('/static/brand/school-logo.png', scopeUrl).href,
];
let stagingPromise = null;

self.addEventListener('install', (event) => {
  event.waitUntil(caches.open(shellCache).then((cache) => cache.addAll(shellAssets)).then(() => self.skipWaiting()));
});

self.addEventListener('activate', (event) => {
  event.waitUntil((async () => {
    const names = await caches.keys();
    await Promise.all(names
      .filter((name) => name.startsWith(`signage-shell-${screenKey}-`) && name !== shellCache)
      .map((name) => caches.delete(name)));
    await self.clients.claim();
  })());
});

self.addEventListener('fetch', (event) => {
  const requestUrl = new URL(event.request.url);
  if (event.request.method !== 'GET') return;
  if (requestUrl.href === manifestUrl) {
    event.respondWith(handleManifest(event));
    return;
  }
  if (event.request.mode === 'navigate' && requestUrl.pathname.startsWith(scopeUrl.pathname)) {
    event.respondWith(networkFirstPage(event.request));
    return;
  }
  if (requestUrl.origin === scopeUrl.origin && requestUrl.pathname.startsWith('/static/')) {
    event.respondWith(staleWhileRevalidate(event));
    return;
  }
  if (requestUrl.origin === scopeUrl.origin && requestUrl.pathname.includes('/media/')) {
    event.respondWith(cachedMedia(event.request));
  }
});

async function readMeta() {
  const cache = await caches.open(metaCache);
  const response = await cache.match(metaUrl);
  if (!response) return {active: null, previous: null, revision: null};
  try { return await response.json(); } catch (_) { return {active: null, previous: null, revision: null}; }
}

async function writeMeta(value) {
  const cache = await caches.open(metaCache);
  await cache.put(metaUrl, new Response(JSON.stringify(value), {
    headers: {'Content-Type': 'application/json'},
  }));
}

async function activeManifest(meta) {
  if (!meta.active) return null;
  return (await caches.open(meta.active)).match(manifestUrl);
}

function withOfflineHeader(response, offline) {
  const headers = new Headers(response.headers);
  headers.set('X-Signage-Offline', offline ? '1' : '0');
  headers.set('Cache-Control', 'no-store');
  return new Response(response.body, {status: response.status, statusText: response.statusText, headers});
}

async function handleManifest(event) {
  const meta = await readMeta();
  let remote;
  try {
    remote = await fetch(event.request, {cache: 'no-store'});
    if (remote.status === 304) return remote;
    if (!remote.ok) throw new Error(`HTTP ${remote.status}`);
  } catch (_) {
    const cached = await activeManifest(meta);
    return cached
      ? withOfflineHeader(cached, true)
      : new Response(JSON.stringify({error: 'offline_without_cache'}), {status: 503, headers: {'Content-Type': 'application/json'}});
  }

  let candidate;
  try { candidate = await remote.clone().json(); } catch (_) { return remote; }
  const revision = Number(candidate.revision || 0);
  if (meta.active && meta.revision === revision) {
    await (await caches.open(meta.active)).put(manifestUrl, remote.clone());
    return withOfflineHeader(remote, false);
  }

  const stage = () => stageRevision(candidate, remote.clone(), meta);
  if (!meta.active) {
    await stage();
    const current = await activeManifest(await readMeta());
    return withOfflineHeader(current || remote, false);
  }

  if (!stagingPromise) {
    stagingPromise = stage().catch((error) => {
      console.error('Digital Signage: staging failed', error);
    }).finally(() => { stagingPromise = null; });
  }
  event.waitUntil(stagingPromise);
  const current = await activeManifest(meta);
  return withOfflineHeader(current || remote, false);
}

async function stageRevision(candidate, manifestResponse, oldMeta) {
  const revision = Number(candidate.revision || 0);
  const cacheName = `${mediaPrefix}${revision}`;
  await caches.delete(cacheName);
  const cache = await caches.open(cacheName);
  try {
    for (const item of candidate.items || []) {
      const asset = item.asset || {};
      if (!asset.mediaUrl) continue;
      const url = new URL(asset.mediaUrl, scopeUrl).href;
      const options = {cache: 'no-store', credentials: 'same-origin'};
      if (/^[0-9a-f]{64}$/i.test(asset.sha256 || '')) {
        options.integrity = `sha256-${hexToBase64(asset.sha256)}`;
      }
      const response = await fetch(url, options);
      if (!response.ok) throw new Error(`${asset.name || asset.id}: HTTP ${response.status}`);
      await cache.put(url, response);
    }
    await cache.put(manifestUrl, manifestResponse);
    const nextMeta = {active: cacheName, previous: oldMeta.active || null, revision};
    await writeMeta(nextMeta);
    await removeOldMediaCaches(nextMeta);
    const clients = await self.clients.matchAll({type: 'window', includeUncontrolled: true});
    clients.forEach((client) => client.postMessage({type: 'revision-ready', revision}));
  } catch (error) {
    await caches.delete(cacheName);
    throw error;
  }
}

async function removeOldMediaCaches(meta) {
  const keep = new Set([meta.active, meta.previous].filter(Boolean));
  const names = await caches.keys();
  await Promise.all(names
    .filter((name) => name.startsWith(mediaPrefix) && !keep.has(name))
    .map((name) => caches.delete(name)));
}

async function cachedMedia(request) {
  const meta = await readMeta();
  for (const name of [meta.active, meta.previous].filter(Boolean)) {
    const response = await (await caches.open(name)).match(request, {ignoreVary: true});
    if (response) return rangedResponse(response, request.headers.get('Range'));
  }
  return fetch(request);
}

async function rangedResponse(response, rangeHeader) {
  if (!rangeHeader || !rangeHeader.startsWith('bytes=') || rangeHeader.includes(',')) return response;
  const blob = await response.blob();
  const [startText, endText] = rangeHeader.slice(6).split('-', 2);
  let start;
  let end;
  if (startText) {
    start = Number(startText);
    end = endText ? Number(endText) : blob.size - 1;
  } else {
    const suffix = Number(endText);
    if (!Number.isFinite(suffix) || suffix <= 0) return response;
    start = Math.max(0, blob.size - suffix);
    end = blob.size - 1;
  }
  if (!Number.isFinite(start) || !Number.isFinite(end) || start < 0 || start >= blob.size || end < start) {
    return new Response(null, {status: 416, headers: {'Content-Range': `bytes */${blob.size}`}});
  }
  end = Math.min(end, blob.size - 1);
  const headers = new Headers(response.headers);
  headers.set('Accept-Ranges', 'bytes');
  headers.set('Content-Range', `bytes ${start}-${end}/${blob.size}`);
  headers.set('Content-Length', String(end - start + 1));
  return new Response(blob.slice(start, end + 1), {status: 206, statusText: 'Partial Content', headers});
}

async function networkFirstPage(request) {
  const cache = await caches.open(shellCache);
  try {
    const response = await fetch(request, {cache: 'no-store'});
    if (response.ok) await cache.put(playerUrl, response.clone());
    return response;
  } catch (_) {
    return (await cache.match(playerUrl)) || Response.error();
  }
}

async function staleWhileRevalidate(event) {
  const cache = await caches.open(shellCache);
  const cached = await cache.match(event.request, {ignoreVary: true});
  const update = fetch(event.request, {cache: 'no-store'}).then(async (response) => {
    if (response.ok) await cache.put(event.request, response.clone());
    return response;
  });
  if (cached) {
    event.waitUntil(update.catch(() => {}));
    return cached;
  }
  return update;
}

function hexToBase64(hex) {
  let binary = '';
  for (let index = 0; index < hex.length; index += 2) {
    binary += String.fromCharCode(parseInt(hex.slice(index, index + 2), 16));
  }
  return btoa(binary);
}
