(() => {
  'use strict';
  const VERSION = '0.3.0-browser';
  const root = document.getElementById('signage-player');
  const stage = document.getElementById('stage');
  const empty = document.getElementById('empty-state');
  const bootDetail = document.getElementById('boot-detail');
  const connectionState = document.getElementById('connection-state');
  const manifestUrl = root.dataset.manifestUrl;
  const heartbeatUrl = root.dataset.heartbeatUrl;
  const commandPrefix = root.dataset.commandPrefix;
  let manifest = null;
  let etag = '';
  let timer = null;
  let clockTimer = null;
  let activeNode = null;
  let activeKey = '';
  let currentIndex = 0;
  let serverOffsetMs = 0;
  let lastError = '';
  let forcedIndex = null;
  let globalMuted = false;
  let globalVolume = 1;
  let manifestLoading = false;
  let volumeOverridden = false;
  let audioPrompt = null;

  const now = () => Date.now() + serverOffsetMs;
  const esc = (value) => String(value ?? '').replace(/[&<>'"]/g, (char) => ({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[char]));
  const logoUrl = (path) => `/static/${String(path || 'brand/school-logo.png').replace(/^\/+/, '')}`;
  const fitClass = (fit) => `fit-${['cover','contain','stretch'].includes(fit) ? fit : 'cover'}`;

  function localParts(timestamp, timezone) {
    const parts = new Intl.DateTimeFormat('en-CA', {
      timeZone: timezone, weekday: 'short', hour: '2-digit', minute: '2-digit', second: '2-digit', hourCycle: 'h23'
    }).formatToParts(new Date(timestamp));
    return Object.fromEntries(parts.map((part) => [part.type, part.value]));
  }

  function scheduleActive(schedule, timezone, timestamp = now()) {
    if (!schedule) return true;
    if (schedule.activeFrom && timestamp < Date.parse(schedule.activeFrom)) return false;
    if (schedule.activeUntil && timestamp >= Date.parse(schedule.activeUntil)) return false;
    const parts = localParts(timestamp, timezone);
    const weekdayMap = {Mon:0,Tue:1,Wed:2,Thu:3,Fri:4,Sat:5,Sun:6};
    if (schedule.weekdays?.length && !schedule.weekdays.includes(weekdayMap[parts.weekday])) return false;
    const current = `${parts.hour}:${parts.minute}:${parts.second}`;
    const start = schedule.dailyStart;
    const end = schedule.dailyEnd;
    if (start && end && end <= start) return current >= start || current < end;
    if (start && current < start) return false;
    if (end && current >= end) return false;
    return true;
  }

  function currentSlot(items) {
    if (!items.length) return null;
    if (forcedIndex !== null) {
      const index = ((forcedIndex % items.length) + items.length) % items.length;
      return {item: items[index], index, elapsed: 0, remaining: items[index].durationMs};
    }
    const total = items.reduce((sum, item) => sum + Math.max(1000, item.durationMs), 0);
    const epoch = Date.parse(manifest.screen?.timelineEpoch || manifest.timelineEpoch || manifest.generatedAt);
    const timelinePosition = now() - epoch;
    const cycle = Math.floor(timelinePosition / total);
    let phase = (timelinePosition % total + total) % total;
    for (let index = 0; index < items.length; index += 1) {
      const duration = Math.max(1000, items[index].durationMs);
      if (phase < duration) return {item: items[index], index, elapsed: phase, remaining: duration - phase, cycle};
      phase -= duration;
    }
    return {item: items[0], index: 0, elapsed: 0, remaining: items[0].durationMs, cycle};
  }

  function activeItems() {
    const timezone = manifest?.channel?.timezone || 'Asia/Sakhalin';
    return (manifest?.items || []).filter((item) => scheduleActive(item.schedule, timezone));
  }

  function weatherSymbol(code, isDay = true) {
    if (code === 0) return isDay ? '☀' : '☾';
    if ([1,2].includes(code)) return '◕';
    if (code === 3) return '☁';
    if ([45,48].includes(code)) return '≋';
    if ([51,53,55,61,63,65,80,81,82].includes(code)) return '☂';
    if ([71,73,75,77,85,86].includes(code)) return '❄';
    if ([95,96,99].includes(code)) return 'ϟ';
    return '◌';
  }

  function sceneFrame(scene) {
    const theme = scene.theme || manifest.theme || {};
    const header = `<header class="scene-header"><img src="${logoUrl(theme.logoPath)}" alt=""><div><strong>${esc(theme.shortName)}</strong><span>${esc(scene.config?.headerSubtitle || 'Спортивная школа восточных видов единоборств')}</span></div><div class="header-right"><i class="red-dot"></i>${esc(scene.config?.headerBadge || 'Официальный экран школы')}</div></header>`;
    const footer = `<footer class="scene-footer"><span>${esc(theme.shortName)}</span><span class="footer-time"></span></footer>`;
    return `${header}<div class="ambient-circle circle-one"></div><div class="ambient-circle circle-two"></div>${footer}`;
  }

  function selectedSlogan(scene) {
    const items = (scene.slogans?.items || []).filter((item) => scheduleActive(item.schedule, manifest.channel.timezone));
    if (!items.length) return {text: scene.config?.text || 'Дисциплина превращает движение в мастерство.', subtitle: scene.config?.subtitle || ''};
    const epoch = Date.parse(manifest.screen?.timelineEpoch || manifest.timelineEpoch);
    const slot = Math.floor((now() - epoch) / ((items[0].durationSeconds || 10) * 1000));
    return items[((slot % items.length) + items.length) % items.length];
  }

  function renderScene(item) {
    const scene = item.scene;
    const config = scene.config || {};
    const weather = scene.weather?.data || manifest.weather?.data || {};
    const base = sceneFrame(scene);
    let body = '';
    if (scene.type === 'identity') {
      body = `<div class="identity-content"><p>${esc(config.kicker || 'Сахалинская область')}</p><h1>${esc(scene.theme.fullName || scene.theme.shortName)}</h1><span>${esc(config.subtitle || 'Каратэ · Фехтование · Дисциплина · Уважение')}</span></div>`;
    } else if (scene.type === 'clock') {
      body = `<div class="clock-layout"><div class="clock-value"></div><div class="date-value"></div><span class="timezone-pill">Сахалинское время · Asia/Sakhalin</span></div>`;
    } else if (scene.type === 'weather') {
      body = `<div class="weather-layout"><div class="weather-place">${esc(weather.location || 'Южно-Сахалинск')}</div><div class="weather-temp">${Math.round(weather.temperature ?? 0)}°</div><div class="weather-symbol">${weatherSymbol(weather.weatherCode, weather.isDay)}</div><div class="weather-summary">${esc(weather.condition || 'Получаем данные о погоде')}</div><div class="weather-details"><span>Ощущается как ${Math.round(weather.apparentTemperature ?? 0)}°</span><span>Ветер ${Math.round(weather.windSpeed ?? 0)} км/ч</span><span>Влажность ${Math.round(weather.humidity ?? 0)}%</span></div></div>`;
    } else if (scene.type === 'clock_weather') {
      body = `<div class="combined-layout"><div class="combined-clock"><div class="clock-value"></div><div class="date-value"></div></div><div class="combined-weather"><div class="weather-symbol">${weatherSymbol(weather.weatherCode, weather.isDay)}</div><div class="weather-temp">${Math.round(weather.temperature ?? 0)}°</div><p>${esc(weather.condition || 'Погода обновляется')}</p></div></div>`;
    } else if (scene.type === 'slogan') {
      const slogan = selectedSlogan(scene);
      body = `<div class="slogan-layout"><div class="slogan-text">${esc(slogan.text)}</div><div class="slogan-subtitle">${esc(slogan.subtitle || config.subtitle || 'Тренируйся. Уважай. Расти.')}</div></div>`;
    } else if (scene.type === 'announcement') {
      body = `<div class="announcement-layout"><span class="announcement-kicker">${esc(config.kicker || 'Объявление')}</span><h1>${esc(config.title || 'Информационное сообщение')}</h1><p>${esc(config.text || 'Текст объявления настраивается в панели управления.')}</p></div>`;
    } else if (scene.type === 'photo_message') {
      body = `${config.imageUrl ? `<img class="photo-message-bg" src="${esc(config.imageUrl)}" alt="">` : ''}<div class="photo-message-shade"></div><div class="photo-message-copy"><h1>${esc(config.title || 'Сила характера начинается с дисциплины')}</h1><p>${esc(config.text || '')}</p></div>`;
    }
    return `<article class="stage-item brand-scene">${base}${body}</article>`;
  }

  function renderAsset(item, elapsed) {
    const asset = item.asset;
    if (asset.kind === 'image' || asset.websiteMode === 'snapshot') return `<article class="stage-item"><img class="${fitClass(item.fit)}" src="${esc(asset.mediaUrl)}" alt=""></article>`;
    if (asset.kind === 'video') return `<article class="stage-item"><video class="${fitClass(item.fit)}" src="${esc(asset.mediaUrl)}" playsinline autoplay preload="auto" ${item.muted || manifest.channel.muted ? 'muted' : ''}></video></article>`;
    if (asset.kind === 'website') return `<article class="stage-item"><iframe class="website-frame" src="${esc(asset.url)}" sandbox="allow-scripts allow-same-origin allow-forms allow-popups" referrerpolicy="no-referrer"></iframe></article>`;
    return `<article class="stage-item player-error"><strong>Неизвестный тип контента</strong></article>`;
  }

  function updateClock(node) {
    const timezone = manifest?.channel?.timezone || 'Asia/Sakhalin';
    const date = new Date(now());
    const clock = new Intl.DateTimeFormat('ru-RU', {timeZone:timezone,hour:'2-digit',minute:'2-digit',second:'2-digit',hourCycle:'h23'}).format(date);
    const formattedDate = new Intl.DateTimeFormat('ru-RU', {timeZone:timezone,weekday:'long',day:'numeric',month:'long',year:'numeric'}).format(date);
    node.querySelectorAll('.clock-value').forEach((element) => element.textContent = clock);
    node.querySelectorAll('.date-value').forEach((element) => element.textContent = formattedDate[0].toUpperCase() + formattedDate.slice(1));
    node.querySelectorAll('.footer-time').forEach((element) => element.textContent = `${formattedDate} · ${clock.slice(0,5)} · ${timezone}`);
  }

  function videoShouldBeMuted(item) {
    return Boolean(item.muted || manifest.channel.muted || globalMuted);
  }

  function applyVideoVolume(video, item) {
    const itemVolume = Math.min(1, Math.max(0, (item.volume ?? 100) / 100));
    video.dataset.itemVolume = String(itemVolume);
    video.volume = Math.min(1, Math.max(0, itemVolume * globalVolume));
  }

  function showAudioPrompt() {
    if (!audioPrompt) {
      audioPrompt = document.createElement('button');
      audioPrompt.type = 'button';
      audioPrompt.className = 'audio-unlock';
      audioPrompt.textContent = 'Нажмите, чтобы включить звук';
      root.appendChild(audioPrompt);
      audioPrompt.addEventListener('click', unlockAudio);
    }
    audioPrompt.hidden = false;
  }

  async function unlockAudio() {
    const video = activeNode?.querySelector('video');
    if (!video || globalMuted || manifest.channel.muted) return;
    video.muted = false;
    try {
      await video.play();
      audioPrompt.hidden = true;
      if (lastError.startsWith('Звук заблокирован')) lastError = '';
    } catch (error) {
      video.muted = true;
      lastError = `Звук заблокирован браузером: ${error.message}`;
    }
  }

  async function startVideo(video, item, elapsed, node) {
    if (!node.isConnected) return;
    const target = Math.min(elapsed / 1000, Math.max(0, video.duration - .2));
    if (Number.isFinite(target)) video.currentTime = target;
    video.muted = videoShouldBeMuted(item);
    applyVideoVolume(video, item);
    try {
      await video.play();
      if (audioPrompt) audioPrompt.hidden = true;
      if (!video.muted && lastError.startsWith('Звук заблокирован')) lastError = '';
    } catch (error) {
      if (error.name === 'NotAllowedError' && !video.muted) {
        video.muted = true;
        try {
          await video.play();
          lastError = 'Звук заблокирован браузером · видео воспроизводится без звука';
          showAudioPrompt();
          return;
        } catch (mutedError) {
          lastError = `Видео: ${mutedError.message}`;
          return;
        }
      }
      lastError = `Видео: ${error.message}`;
    }
  }

  function showSlot(slot) {
    currentIndex = slot.index;
    const signature = `${manifest.revision}:${slot.item.key}:${forcedIndex ?? `cycle-${slot.cycle}`}`;
    if (signature === activeKey) return;
    activeKey = signature;
    clearInterval(clockTimer);
    if (audioPrompt) audioPrompt.hidden = true;
    const wrapper = document.createElement('div');
    wrapper.innerHTML = slot.item.type === 'scene' ? renderScene(slot.item) : renderAsset(slot.item, slot.elapsed);
    const node = wrapper.firstElementChild;
    stage.appendChild(node);
    requestAnimationFrame(() => node.classList.add('visible'));
    if (slot.item.type === 'scene') {
      updateClock(node);
      clockTimer = setInterval(() => updateClock(node), 1000);
    }
    const video = node.querySelector('video');
    if (video) {
      const begin = () => startVideo(video, slot.item, slot.elapsed, node);
      if (video.readyState >= 1) begin();
      else video.addEventListener('loadedmetadata', begin, {once:true});
      video.addEventListener('error', () => { lastError = 'Не удалось воспроизвести видео'; });
      video.addEventListener('playing', () => {
        if (!video.muted && lastError.startsWith('Звук заблокирован')) lastError = '';
        if (lastError.startsWith('Видео:') || lastError === 'Не удалось воспроизвести видео') lastError = '';
      });
      video.addEventListener('ended', () => setTimeout(() => {
        if (activeNode === node && video.ended) {
          activeKey = '';
          schedulePlayback();
        }
      }, 250), {once:true});
    }
    const image = node.querySelector('img.fit-cover,img.fit-contain,img.fit-stretch');
    if (image) image.addEventListener('error', () => { lastError = 'Не удалось загрузить изображение'; });
    empty.classList.add('hidden');
    const previous = activeNode;
    activeNode = node;
    setTimeout(() => previous?.remove(), 420);
  }

  function schedulePlayback() {
    clearTimeout(timer);
    if (!manifest) return;
    const items = activeItems();
    if (!items.length) {
      empty.classList.remove('hidden');
      bootDetail.textContent = manifest.revision ? 'Сейчас нет активного контента по расписанию' : 'Канал ещё не опубликован';
      timer = setTimeout(schedulePlayback, 5000);
      return;
    }
    const slot = currentSlot(items);
    showSlot(slot);
    forcedIndex = null;
    timer = setTimeout(schedulePlayback, Math.max(150, slot.remaining + 20));
  }

  async function loadManifest() {
    if (manifestLoading) return;
    manifestLoading = true;
    try {
      const response = await fetch(manifestUrl, {cache:'no-store',headers:etag ? {'If-None-Match':etag} : {}});
      const offline = response.headers.get('X-Signage-Offline') === '1';
      if (response.status === 304) { if (!offline) connectionState.hidden = true; return; }
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const next = await response.json();
      etag = response.headers.get('ETag') || '';
      if (next.serverTime && !offline) serverOffsetMs = Date.parse(next.serverTime) - Date.now();
      const changed = !manifest || next.revision !== manifest.revision;
      manifest = next;
      if (!volumeOverridden) {
        globalVolume = Math.min(1, Math.max(0, (manifest.channel?.defaultVolume ?? 100) / 100));
      }
      connectionState.hidden = !offline;
      if (offline) lastError = 'Нет связи с сервером · показ из кеша';
      else if (lastError.startsWith('Нет связи с сервером') || lastError.startsWith('Связь:')) lastError = '';
      if (changed) { activeKey = ''; schedulePlayback(); }
    } catch (error) {
      connectionState.hidden = false;
      lastError = `Связь: ${error.message}`;
      if (!manifest) bootDetail.textContent = 'Сервер временно недоступен';
    } finally {
      manifestLoading = false;
    }
  }

  async function prepareOfflineCache() {
    if (!('serviceWorker' in navigator) || !window.isSecureContext) return;
    try {
      const workerUrl = new URL('player-sw.js', location.href);
      const scope = new URL('./', location.href).pathname;
      await navigator.serviceWorker.register(workerUrl, {scope});
      await navigator.serviceWorker.ready;
      if (!navigator.serviceWorker.controller) {
        await Promise.race([
          new Promise((resolve) => navigator.serviceWorker.addEventListener('controllerchange', resolve, {once:true})),
          new Promise((resolve) => setTimeout(resolve, 2000)),
        ]);
      }
      if (navigator.storage?.persist) await navigator.storage.persist();
    } catch (error) {
      console.warn('Автономный кеш недоступен:', error);
    }
  }

  async function acknowledge(commandId) {
    try { await fetch(`${commandPrefix}${commandId}/ack`, {method:'POST'}); } catch (_) {}
  }

  async function heartbeat() {
    try {
      const response = await fetch(heartbeatUrl, {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({
        playerVersion:VERSION,revision:manifest?.revision || 0,currentItem:activeKey,error:lastError,
        capabilities:{viewport:`${innerWidth}x${innerHeight}`,userAgent:navigator.userAgent,webPlayer:true}
      })});
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const payload = await response.json();
      if (payload.serverTime) serverOffsetMs = Date.parse(payload.serverTime) - Date.now();
      for (const item of payload.commands || []) {
        const items = activeItems();
        if (item.command === 'next' && items.length) { forcedIndex = currentIndex + 1; activeKey=''; schedulePlayback(); }
        if (item.command === 'previous' && items.length) { forcedIndex = currentIndex - 1; activeKey=''; schedulePlayback(); }
        if (item.command === 'reload') location.reload();
        if (item.command === 'mute') {
          globalMuted = true;
          if (audioPrompt) audioPrompt.hidden = true;
          activeNode?.querySelectorAll('video').forEach((video) => { video.muted = true; });
        }
        if (item.command === 'unmute') {
          globalMuted = false;
          activeNode?.querySelectorAll('video').forEach((video) => {
            video.muted = false;
            video.play().catch(() => {
              video.muted = true;
              lastError = 'Звук заблокирован браузером · видео воспроизводится без звука';
              showAudioPrompt();
            });
          });
        }
        if (item.command === 'volume') {
          volumeOverridden = true;
          globalVolume = Math.max(0,Math.min(1,(item.payload?.value ?? 100)/100));
          activeNode?.querySelectorAll('video').forEach((video) => {
            const itemVolume = Number(video.dataset.itemVolume || 1);
            video.volume = Math.min(1, Math.max(0, itemVolume * globalVolume));
          });
        }
        acknowledge(item.id);
      }
    } catch (_) { connectionState.hidden = false; }
  }

  document.addEventListener('visibilitychange', () => { if (!document.hidden) { activeKey=''; schedulePlayback(); } });
  window.addEventListener('online', loadManifest);
  navigator.serviceWorker?.addEventListener('message', (event) => {
    if (event.data?.type === 'revision-ready') loadManifest();
  });
  prepareOfflineCache().then(loadManifest).then(schedulePlayback);
  setInterval(loadManifest, 30000);
  setInterval(heartbeat, 15000);
  heartbeat();
})();
