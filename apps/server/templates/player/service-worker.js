'use strict';

const WORKER_VERSION = '0.5.0';
const scopeUrl = new URL(self.registration.scope);
// Separate caches for different credentials, including legacy links for this screen.
const scopeParts = scopeUrl.pathname.split('/').filter(Boolean);
const screenKey = scopeParts.slice(-2).join('-').replace(/[^a-zA-Z0-9_-]/g, '');
const playerUrl = new URL('./', scopeUrl).href;
const manifestUrl = new URL('manifest.json', scopeUrl).href;
const metaUrl = new URL('__offline_meta__', scopeUrl).href;
const candidateUrl = new URL('__candidate__', scopeUrl).href;
const shellCache = `signage-shell-${screenKey}-v6`;
const metaCache = `signage-meta-${screenKey}-v3`;
const mediaPrefix = `signage-media-${screenKey}-v3-`;
const shellAssets = [playerUrl,
  new URL('/static/css/player.css?v=0.5.0', scopeUrl).href,
  new URL('/static/js/playback-core.js?v=0.5.0', scopeUrl).href,
  new URL('/static/js/player.js?v=0.5.0', scopeUrl).href,
  new URL('/static/brand/school-logo.png', scopeUrl).href];
let stagingPromise = null;
let generation = 0;
let status = {state:'unknown', completed:0, total:0, bytes:0};

self.addEventListener('install', (event) => {
  event.waitUntil(caches.open(shellCache)
    .then((cache) => cache.addAll(shellAssets.map((url) => new Request(url, {cache:'reload'}))))
    .then(() => self.skipWaiting()));
});
self.addEventListener('activate', (event) => { event.waitUntil(self.clients.claim()); });
self.addEventListener('message', (event) => {
  if (event.data?.type === 'offline-status') event.waitUntil((async () => {
    const meta = await readMeta();
    event.source?.postMessage({type:'offline-status', status:status.state === 'unknown' ? meta.status || status : status});
  })());
});
self.addEventListener('fetch', (event) => {
  const url = new URL(event.request.url);
  if (event.request.method !== 'GET') return;
  if (url.href === manifestUrl) { event.respondWith(handleManifest(event)); return; }
  if (event.request.mode === 'navigate' && url.pathname === scopeUrl.pathname) {
    event.respondWith(networkFirstPage(event.request)); return;
  }
  if (url.origin === scopeUrl.origin && url.pathname.startsWith('/static/')) {
    event.respondWith(cachedStatic(event)); return;
  }
  if (url.origin === scopeUrl.origin && url.pathname.startsWith(scopeUrl.pathname) && url.pathname.includes('/media/')) {
    event.respondWith(cachedMedia(event.request));
  }
});
async function timedFetch(request, options = {}, timeout = 12000) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeout);
  try { return await fetch(request, {...options, signal:controller.signal}); }
  finally { clearTimeout(timer); }
}
async function readMeta() {
  const response = await (await caches.open(metaCache)).match(metaUrl);
  try { return response ? await response.json() : {}; } catch (_) { return {}; }
}
async function writeMeta(meta) {
  await (await caches.open(metaCache)).put(metaUrl, new Response(JSON.stringify(meta), {headers:{'Content-Type':'application/json'}}));
}
async function notify(next) {
  status = {...next};
  const clients = await self.clients.matchAll({type:'window'});
  for (const client of clients) client.postMessage({type:'offline-status', status});
}
async function activeManifest(meta) {
  return meta.active ? (await caches.open(meta.active)).match(manifestUrl)
    : (await caches.open(metaCache)).match(manifestUrl);
}
function offlineResponse(response) {
  const headers = new Headers(response.headers);
  headers.set('X-Signage-Offline','1'); headers.set('Cache-Control','no-store');
  return new Response(response.body, {status:200, headers});
}
function manifestIdentity(candidate) {
  return `${candidate.channel?.id || 0}-r${candidate.revision || 0}-${candidate.screen?.settings?.offlineEnabled === true ? 'offline' : 'stream'}`;
}
async function handleManifest(event) {
  const meta = await readMeta();
  let remote;
  try { remote = await timedFetch(event.request, {cache:'no-store'}); }
  catch (_) { return fallbackManifest(meta); }
  // A reachable server can revoke a display; do not hide revocation with cached content.
  if (remote.status === 403 || remote.status === 404) {
    generation += 1;
    await writeMeta({});
    await (await caches.open(metaCache)).delete(manifestUrl);
    await (await caches.open(metaCache)).delete(candidateUrl);
    for (const name of await caches.keys()) if (name.startsWith(mediaPrefix)) await caches.delete(name);
    return remote;
  }
  if (remote.status === 304) {
    const pending = await (await caches.open(metaCache)).match(candidateUrl);
    if (!pending) return remote;
    const candidate = await pending.clone().json();
    if (meta.active && meta.id === manifestIdentity(candidate)) return remote;
    remote = pending;
  }
  if (!remote.ok) return fallbackManifest(meta);
  let candidate;
  try { candidate = await remote.clone().json(); } catch (_) { return remote; }
  const id = manifestIdentity(candidate);
  if (meta.active && meta.id === id) {
    await (await caches.open(meta.active)).put(manifestUrl, remote.clone());
    if (status.state === 'unknown') await notify(meta.status || {state:'disabled'});
    return remote;
  }
  await (await caches.open(metaCache)).put(candidateUrl, remote.clone());
  if (!meta.active) await (await caches.open(metaCache)).put(manifestUrl, remote.clone());
  if (!stagingPromise) {
    const stageGeneration = generation;
    stagingPromise = stageRevision(candidate, remote.clone(), meta, stageGeneration).catch(async (error) => {
      if (stageGeneration !== generation) return;
      await notify({...status, state:'error', error:error.name === 'QuotaExceededError'
        ? 'Недостаточно места в хранилище браузера' : String(error.message).slice(0,400)});
    }).finally(() => { stagingPromise = null; });
  }
  event.waitUntil(stagingPromise);
  return (await activeManifest(meta)) || remote;
}
async function fallbackManifest(meta) {
  const cached = await activeManifest(meta);
  return cached ? offlineResponse(cached) : new Response('{"error":"offline_without_cache"}', {
    status:503, headers:{'Content-Type':'application/json'}});
}
async function stageRevision(candidate, manifestResponse, oldMeta, stageGeneration = generation) {
  const id = manifestIdentity(candidate);
  const cacheName = `${mediaPrefix}${id}`;
  const cache = await caches.open(cacheName);
  const enabled = candidate.screen?.settings?.offlineEnabled === true;
  const assets = new Map();
  let liveSites = false;
  for (const item of candidate.items || []) {
    if (item.asset?.kind === 'website' && item.asset.websiteMode !== 'snapshot') liveSites = true;
    if (item.asset?.mediaUrl) assets.set(new URL(item.asset.mediaUrl, scopeUrl).href, item.asset);
  }
  const files = enabled ? [...assets] : [];
  const progress = {state:enabled ? 'downloading' : 'disabled', revision:candidate.revision || 0,
    completed:0, total:files.length, bytes:0,
    totalBytes:files.reduce((sum, [, asset]) => sum + (Number(asset.size) || 0),0)};
  await notify(progress);
  try {
    const oldResponse = await activeManifest(oldMeta);
    const old = oldResponse ? await oldResponse.json() : {};
    for (const [url, asset] of files) {
      if (stageGeneration !== generation) throw new Error('Display revoked');
      let response = await cache.match(url);
      if (!response && oldMeta.active && /^[a-f0-9]{64}$/i.test(asset.sha256 || '')) {
        const match = (old.items || []).find((item) => item.asset?.sha256 === asset.sha256 && item.asset?.mediaUrl);
        if (match) response = await (await caches.open(oldMeta.active)).match(new URL(match.asset.mediaUrl, scopeUrl).href);
      }
      if (!response) {
        const options = {cache:'no-store', credentials:'same-origin'};
        if (/^[a-f0-9]{64}$/i.test(asset.sha256 || '')) options.integrity = `sha256-${hexToBase64(asset.sha256)}`;
        response = await timedFetch(url, options,300000);
        if (!response.ok || response.status !== 200) throw new Error(`${asset.name || asset.id}: HTTP ${response.status}`);
      }
      // Stream into browser storage rather than allocating an entire video in JavaScript RAM.
      await cache.put(url,response);
      if (stageGeneration !== generation) throw new Error('Display revoked');
      progress.completed += 1; progress.bytes += Number(asset.size) || 0;
      await notify(progress);
    }
    await cache.put(manifestUrl,manifestResponse);
    if (stageGeneration !== generation) throw new Error('Display revoked');
    progress.state = enabled ? (liveSites ? 'partial' : 'ready') : 'disabled';
    if (liveSites && enabled) progress.error = 'Живые сайты требуют сеть. Для автономного показа выберите снимок сайта.';
    const nextMeta = {active:cacheName, previous:oldMeta.active !== cacheName ? oldMeta.active || null : oldMeta.previous,
      id, revision:candidate.revision || 0, status:progress};
    await writeMeta(nextMeta);
    await notify(progress);
    const keep = new Set([nextMeta.active,nextMeta.previous]);
    for (const name of await caches.keys()) if (name.startsWith(mediaPrefix) && !keep.has(name)) await caches.delete(name);
    const clients = await self.clients.matchAll({type:'window'});
    for (const client of clients) client.postMessage({type:'revision-ready', revision:candidate.revision});
  } catch (error) {
    if (stageGeneration !== generation) await caches.delete(cacheName);
    // Preserve the last complete revision and completed downloads for a retry.
    throw error;
  }
}
async function cachedMedia(request) {
  const meta = await readMeta();
  for (const name of [meta.active,meta.previous].filter(Boolean)) {
    const response = await (await caches.open(name)).match(request.url);
    if (response) return rangedResponse(response,request.headers.get('Range'));
  }
  return fetch(request);
}
async function rangedResponse(response,rangeHeader) {
  if (!rangeHeader) return response;
  const blob = await response.blob();
  const fail = () => new Response(null,{status:416,headers:{'Content-Range':`bytes */${blob.size}`}});
  const match = /^bytes=(\d*)-(\d*)$/.exec(rangeHeader);
  if (!match || (!match[1] && !match[2])) return fail();
  const start = match[1] ? Number(match[1]) : Math.max(0,blob.size - Number(match[2]));
  let end = match[1] && match[2] ? Number(match[2]) : blob.size - 1;
  if (!Number.isSafeInteger(start) || !Number.isSafeInteger(end) || start >= blob.size || end < start || (!match[1] && Number(match[2]) === 0)) return fail();
  end = Math.min(end,blob.size - 1);
  const headers = new Headers(response.headers);
  headers.delete('Content-Encoding');
  headers.set('Accept-Ranges','bytes'); headers.set('Content-Range',`bytes ${start}-${end}/${blob.size}`);
  headers.set('Content-Length',String(end - start + 1));
  return new Response(blob.slice(start,end + 1),{status:206,headers});
}
async function networkFirstPage(request) {
  const cache = await caches.open(shellCache);
  try {
    const response = await timedFetch(request,{cache:'no-store'},5000);
    if (response.ok) await cache.put(playerUrl,response.clone());
    if (response.status >= 500) return (await cache.match(playerUrl)) || response;
    return response;
  } catch (_) { return (await cache.match(playerUrl)) || Response.error(); }
}
async function cachedStatic(event) {
  const cache = await caches.open(shellCache);
  const cached = await cache.match(event.request);
  if (cached) return cached;
  const response = await fetch(event.request);
  if (response.ok) await cache.put(event.request,response.clone());
  return response;
}
function hexToBase64(hex) {
  return btoa(hex.match(/../g).map((pair) => String.fromCharCode(parseInt(pair,16))).join(''));
}
