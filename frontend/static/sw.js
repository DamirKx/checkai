'use strict';
// Service worker CheckAI (мобильная версия / PWA). Отдаётся сервером из корня: GET /sw.js.
//
// Что делает:
// - кэширует оболочку интерфейса (страница «/», CSS, JS, словари, иконки), чтобы приложение
//   открывалось сразу и без сети показывало интерфейс, а не ошибку браузера;
// - страница «/» — сначала сеть (свежая версия), при плохой сети или без неё — из кэша;
// - /static/* — сразу из кэша, а в фоне обновляется из сети (файлы версионируются через ?v=…).
// Что НЕ делает: данные — /api/*, фото чеков и аватары — не кэшируются и не перехватываются.
//
// Меняете этот файл — поднимите VERSION: старый кэш удалится при активации новой версии.
const VERSION = 'checkai-shell-v2';
const SHELL_URL = '/';
const NETWORK_TIMEOUT_MS = 4000;
// Файлы, на которые ссылается index.html: список берём из самой страницы, чтобы не держать его в двух местах
const ASSET_RE = /(?:href|src)="(\/static\/[^"]+)"/g;

self.addEventListener('install', event => {
  event.waitUntil((async () => {
    const cache = await caches.open(VERSION);
    const response = await fetch(SHELL_URL, { cache: 'no-store' });
    if (!response.ok) throw new Error(`Оболочка не загрузилась: ${response.status}`);
    const html = await response.clone().text();
    await cache.put(SHELL_URL, response);
    const assets = new Set(['/manifest.webmanifest']);
    for (const match of html.matchAll(ASSET_RE)) assets.add(match[1].replace(/&amp;/g, '&'));
    // Один недоступный файл не должен ломать установку — он докэшируется при первом запросе
    await Promise.all([...assets].map(url => cache.add(url).catch(() => null)));
    await self.skipWaiting();
  })());
});

self.addEventListener('activate', event => {
  event.waitUntil((async () => {
    for (const key of await caches.keys()) if (key !== VERSION) await caches.delete(key);
    await self.clients.claim();
  })());
});

self.addEventListener('fetch', event => {
  const request = event.request;
  if (request.method !== 'GET') return;
  const url = new URL(request.url);
  if (url.origin !== self.location.origin) return;
  if (request.mode === 'navigate' && url.pathname === '/') {
    event.respondWith(pageNetworkFirst(event, request));
  } else if (url.pathname.startsWith('/static/') || url.pathname === '/manifest.webmanifest') {
    event.respondWith(staleWhileRevalidate(event, request, url));
  }
  // Остальное (/api, /data/receipts, аватары, /docs) — браузер сам, мимо кэша
});

// Страница: свежая из сети; если сеть не ответила за NETWORK_TIMEOUT_MS или её нет — из кэша
async function pageNetworkFirst(event, request) {
  const cache = await caches.open(VERSION);
  const network = fetch(request).then(response => {
    if (response.ok) cache.put(SHELL_URL, response.clone());
    return response;
  });
  event.waitUntil(network.catch(() => null));
  const cached = await cache.match(SHELL_URL);
  try {
    if (!cached) return await network;
    return await Promise.race([
      network,
      new Promise((_, reject) => setTimeout(() => reject(new Error('timeout')), NETWORK_TIMEOUT_MS)),
    ]);
  } catch (e) {
    return cached || offlineResponse();
  }
}

// Статика: из кэша сразу, обновление — в фоне. Старые версии того же файла (другой ?v=) удаляются
async function staleWhileRevalidate(event, request, url) {
  const cache = await caches.open(VERSION);
  const cached = await cache.match(request);
  const network = fetch(request).then(async response => {
    if (response.ok) {
      await cache.put(request, response.clone());
      for (const key of await cache.keys()) {
        const keyUrl = new URL(key.url);
        if (keyUrl.pathname === url.pathname && key.url !== request.url) await cache.delete(key);
      }
    }
    return response;
  }).catch(() => null);
  if (cached) { event.waitUntil(network); return cached; }
  return (await network) || Response.error();
}

// Первый запуск без сети, когда в кэше ещё ничего нет
function offlineResponse() {
  const html = `<!doctype html><html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>CheckAI</title>
<style>body{margin:0;min-height:100vh;display:grid;place-items:center;padding:24px;box-sizing:border-box;
background:#07080d;color:#f4f2ec;font:16px/1.5 system-ui,sans-serif;text-align:center}
button{margin-top:18px;padding:12px 22px;border:0;border-radius:12px;background:#7c5cff;color:#fff;font:inherit;font-weight:700}</style>
</head><body><div><h1 style="font-size:22px;margin:0 0 8px">Нет соединения</h1>
<p style="margin:0;opacity:.75">Байланыс жоқ · No connection</p>
<button onclick="location.reload()">↻</button></div></body></html>`;
  return new Response(html, { status: 503, headers: { 'Content-Type': 'text/html; charset=utf-8' } });
}
