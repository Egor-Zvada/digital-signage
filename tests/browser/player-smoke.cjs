/* Run with NODE_PATH pointing to a Playwright installation and SIGNAGE_TEST_VIDEO
 * pointing to a short H.264/AAC MP4. No production data or accounts are used. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const http = require('node:http');
const crypto = require('node:crypto');
const {chromium} = require('playwright');
const repo = path.resolve(__dirname, '../..');
const video = fs.readFileSync(process.env.SIGNAGE_TEST_VIDEO);
const image = Buffer.from('<svg xmlns="http://www.w3.org/2000/svg" width="1920" height="1080"><rect width="1920" height="1080" fill="#123456"/></svg>');
const prefix = '/display/test/session/';
let offlineEnabled = false;
let revoked = false;
let heartbeat = {};
let commands = [];
let manifestRequests = 0;
const hash = (buffer) => crypto.createHash('sha256').update(buffer).digest('hex');
const manifest = () => ({revision:1, channel:{id:1, timezone:'Asia/Sakhalin', defaultVolume:100},
  screen:{id:'test',settings:{offlineEnabled}},
  items:[
    {key:'photo',type:'asset',durationMs:2000,fit:'cover',asset:{id:'photo',kind:'image',mediaUrl:`${prefix}media/photo?revision=1`,sha256:hash(image),size:image.length}},
    {key:'video',type:'asset',durationMs:3040,fit:'contain',asset:{id:'video',kind:'video',mediaUrl:`${prefix}media/video?revision=1`,sha256:hash(video),size:video.length}},
    {key:'announcement',type:'scene',durationMs:2000,scene:{type:'announcement',theme:{shortName:'Тестовая школа'},config:{title:'Тест очереди',text:'Проверка объявления'}}},
  ]});
const html = `<!doctype html><html><head><meta charset="utf-8"><link rel="stylesheet" href="/static/css/player.css?v=0.5.0"></head><body>
<main id="signage-player" data-manifest-url="${prefix}manifest.json" data-heartbeat-url="${prefix}heartbeat" data-command-prefix="${prefix}commands/">
<section id="stage"></section><section id="empty-state"><span id="boot-detail"></span></section><div id="connection-state" hidden></div></main>
<script src="/static/js/playback-core.js?v=0.5.0" defer></script><script src="/static/js/player.js?v=0.5.0" defer></script></body></html>`;
const server = http.createServer(async (request,response) => {
  const url = new URL(request.url,'http://localhost');
  response.setHeader('Cache-Control','no-store');
  if (url.pathname === prefix) { response.setHeader('Content-Type','text/html'); response.end(html); return; }
  if (url.pathname === `${prefix}player-sw.js`) {
    response.setHeader('Content-Type','text/javascript'); response.end(fs.readFileSync(path.join(repo,'apps/server/templates/player/service-worker.js'))); return;
  }
  if (url.pathname === `${prefix}manifest.json`) {
    manifestRequests += 1;
    if (revoked) {response.writeHead(404); response.end(); return;}
    const etag = `"test-${offlineEnabled}"`;
    response.setHeader('ETag',etag);
    if (request.headers['if-none-match'] === etag) {response.writeHead(304); response.end(); return;}
    response.setHeader('Content-Type','application/json'); response.end(JSON.stringify(manifest())); return;
  }
  if (url.pathname === `${prefix}heartbeat`) {
    let body = ''; for await (const chunk of request) body += chunk;
    heartbeat = JSON.parse(body);
    response.setHeader('Content-Type','application/json'); response.end(JSON.stringify({commands})); return;
  }
  if (url.pathname.includes('/commands/')) { commands = []; response.end('{}'); return; }
  if (url.pathname.startsWith(`${prefix}media/`)) {
    const data = url.pathname.endsWith('/video') ? video : image;
    response.setHeader('Content-Type',url.pathname.endsWith('/video') ? 'video/mp4' : 'image/svg+xml');
    response.setHeader('Accept-Ranges','bytes');
    const range = /^bytes=(\d+)-(\d*)$/.exec(request.headers.range || '');
    if (range) {
      const start = Number(range[1]); const end = range[2] ? Math.min(Number(range[2]),data.length - 1) : data.length - 1;
      response.writeHead(206,{'Content-Range':`bytes ${start}-${end}/${data.length}`,'Content-Length':end - start + 1});
      response.end(data.subarray(start,end + 1)); return;
    }
    response.setHeader('Content-Length',data.length); response.end(data); return;
  }
  if (url.pathname.startsWith('/static/')) {
    const relative = url.pathname.slice('/static/'.length);
    const file = path.join(repo,relative.startsWith('brand/') ? 'apps/web/public' : 'apps/web/static',relative);
    response.setHeader('Content-Type',relative.endsWith('.js') ? 'text/javascript' : relative.endsWith('.css') ? 'text/css' : 'image/png');
    response.end(fs.readFileSync(file)); return;
  }
  response.writeHead(404); response.end();
});

async function visibleType(page, type) {
  await page.waitForFunction((kind) => {
    const nodes = [...document.querySelectorAll('#stage > article')];
    const node = nodes.at(-1);
    return node && (kind === 'photo' ? node.querySelector('img.fit-cover') : kind === 'video' ? node.querySelector('video') : node.querySelector('.announcement-layout'));
  },type,{timeout:15000});
}
async function until(condition, message) {
  const end = Date.now() + 20000;
  while (Date.now() < end) { if (condition()) return; await new Promise((resolve) => setTimeout(resolve,100)); }
  throw new Error(message);
}

(async () => {
  await new Promise((resolve) => server.listen(0,'127.0.0.1',resolve));
  const origin = `http://127.0.0.1:${server.address().port}`;
  const browser = await chromium.launch({headless:true,executablePath:process.env.SIGNAGE_TEST_BROWSER || 'C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe',args:['--autoplay-policy=no-user-gesture-required']});
  try {
    const context = await browser.newContext();
    // Speed up network polls, preserving actual playback/transition/clock timing.
    await context.addInitScript(() => {
      const interval = window.setInterval;
      window.setInterval = (callback,ms,...args) => interval(callback,ms === 15000 ? 250 : ms === 30000 ? 500 : ms,...args);
    });
    const page = await context.newPage();
    const errors = []; page.on('pageerror',(error) => errors.push(error.message));
    await page.goto(origin + prefix);
    await visibleType(page,'photo');
    await visibleType(page,'video');
    await page.waitForFunction(() => { const v = document.querySelector('#stage > article:last-child video'); return v && !v.paused && v.currentTime > .3 && !v.error; });
    const first = await page.locator('#stage > article:last-child video').evaluate((v) => v.currentTime);
    await page.waitForTimeout(500);
    const second = await page.locator('#stage > article:last-child video').evaluate((v) => v.currentTime);
    assert.ok(second > first + .2,'Video clock must advance');
    await visibleType(page,'announcement');
    await visibleType(page,'photo');
    console.log('PASS: photo → moving video with audio → widget → repeat');
    const mediaEntries = await page.evaluate(async () => {
      const names = await caches.keys(); let count = 0;
      for (const name of names.filter((n) => n.startsWith('signage-media-'))) count += (await (await caches.open(name)).keys()).filter((r) => r.url.includes('/media/')).length;
      return count;
    });
    assert.equal(mediaEntries,0,'Streaming must not predownload entire playlist');
    const oldSession = heartbeat.capabilities?.sessionId;
    await page.close();
    const reopened = await context.newPage();
    reopened.on('pageerror',(error) => errors.push(error.message));
    await reopened.goto(origin + prefix);
    await visibleType(reopened,'photo');
    await until(() => typeof heartbeat.capabilities?.sessionId === 'string' && heartbeat.capabilities.sessionId !== oldSession,'New browser session was not reported');
    console.log('PASS: close/reopen starts at first item with a new session');
    commands = [{id:1,command:'next'}];
    await visibleType(reopened,'video');
    commands = [{id:2,command:'previous'}];
    await visibleType(reopened,'photo');
    console.log('PASS: remote next/previous stays in sequence');
    offlineEnabled = true;
    await until(() => heartbeat.capabilities?.offline?.state === 'ready','Offline files did not finish caching');
    await context.setOffline(true);
    await reopened.reload({waitUntil:'domcontentloaded'});
    await visibleType(reopened,'photo');
    await visibleType(reopened,'video');
    await reopened.waitForFunction(() => {const v=document.querySelector('#stage > article:last-child video');return v && v.currentTime > .3 && !v.paused && !v.error;});
    console.log('PASS: optional offline cache reloads and plays actual MP4 using ranges');
    await context.setOffline(false);
    await visibleType(reopened,'photo');
    revoked = true;
    try {
      await reopened.waitForFunction(() => document.getElementById('boot-detail').textContent.includes('отозвана'),null,{timeout:12000});
    } catch (error) {
      console.error('REVOCATION DIAGNOSTICS', {manifestRequests,heartbeat,errors,
        page:await reopened.evaluate(async () => ({detail:document.getElementById('boot-detail').textContent,
          status:await fetch(document.getElementById('signage-player').dataset.manifestUrl).then((r) => r.status).catch((e) => String(e))}))});
      throw error;
    }
    assert.equal(await reopened.locator('#stage article').count(),0);
    assert.deepEqual(errors,[]);
    console.log('PASS: revoked display stops cached playback; no JavaScript errors');
    await context.close();
  } finally { await browser.close(); await new Promise((resolve) => server.close(resolve)); }
})().catch((error) => {console.error(error);server.close();process.exitCode=1;});
