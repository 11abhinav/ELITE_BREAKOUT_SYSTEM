// ============================================================
// Elite Breakout System — Service Worker
// Provides: offline caching, background sync, push notifications
// ============================================================

const CACHE_NAME = 'elite-breakout-v1';
const STATIC_ASSETS = [
  '/',
  '/login',
  '/static/manifest.json',
  '/static/icons/icon-192.png',
  '/static/icons/icon-512.png',
  'https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=JetBrains+Mono:wght@400;600&display=swap',
  'https://cdn.jsdelivr.net/npm/chart.js',
];

// ── INSTALL: Cache static assets ────────────────────────────
self.addEventListener('install', event => {
  console.log('[SW] Installing service worker...');
  event.waitUntil(
    caches.open(CACHE_NAME).then(cache => {
      return cache.addAll(STATIC_ASSETS).catch(err => {
        // Non-fatal — some external assets may fail, that's OK
        console.warn('[SW] Some assets failed to cache:', err);
      });
    }).then(() => self.skipWaiting())
  );
});

// ── ACTIVATE: Clean old caches ───────────────────────────────
self.addEventListener('activate', event => {
  console.log('[SW] Activating service worker...');
  event.waitUntil(
    caches.keys().then(keys =>
      Promise.all(
        keys.filter(k => k !== CACHE_NAME).map(k => caches.delete(k))
      )
    ).then(() => self.clients.claim())
  );
});

// ── FETCH: Network-first for API, Cache-first for static ─────
self.addEventListener('fetch', event => {
  const url = new URL(event.request.url);

  // Always go network-first for API calls and authenticated pages
  // Never serve stale trading data — freshness is critical
  if (url.pathname.startsWith('/api/') ||
      url.pathname.startsWith('/data/') ||
      url.pathname === '/' ||
      url.pathname === '/admin' ||
      url.pathname === '/wealth') {
    event.respondWith(networkFirstWithOfflineFallback(event.request));
    return;
  }

  // Cache-first for static assets (fonts, icons, scripts)
  if (url.pathname.startsWith('/static/') ||
      url.hostname.includes('fonts.googleapis.com') ||
      url.hostname.includes('cdn.jsdelivr.net')) {
    event.respondWith(cacheFirst(event.request));
    return;
  }

  // Default: network with cache fallback
  event.respondWith(networkFirstWithOfflineFallback(event.request));
});

async function networkFirstWithOfflineFallback(request) {
  try {
    const networkResponse = await fetch(request);
    // Only cache successful GET responses
    if (request.method === 'GET' && networkResponse.ok) {
      const cache = await caches.open(CACHE_NAME);
      cache.put(request, networkResponse.clone());
    }
    return networkResponse;
  } catch (err) {
    // Network failed — try cache
    const cached = await caches.match(request);
    if (cached) {
      console.log('[SW] Offline: serving from cache:', request.url);
      return cached;
    }
    // If login page is offline, return a simple offline page
    return new Response(offlineHTML(), {
      headers: { 'Content-Type': 'text/html' }
    });
  }
}

async function cacheFirst(request) {
  const cached = await caches.match(request);
  if (cached) return cached;
  try {
    const networkResponse = await fetch(request);
    if (networkResponse.ok) {
      const cache = await caches.open(CACHE_NAME);
      cache.put(request, networkResponse.clone());
    }
    return networkResponse;
  } catch {
    return new Response('', { status: 503 });
  }
}

// ── PUSH NOTIFICATIONS ───────────────────────────────────────
self.addEventListener('push', event => {
  if (!event.data) return;

  let data;
  try {
    data = event.data.json();
  } catch {
    data = { title: 'Elite Breakout', body: event.data.text() };
  }

  const options = {
    body: data.body || 'New alert from your scanners',
    icon: '/static/icons/icon-192.png',
    badge: '/static/icons/icon-72.png',
    tag: data.tag || 'elite-alert',
    renotify: true,
    vibrate: [200, 100, 200, 100, 200],
    data: {
      url: data.url || '/',
      symbol: data.symbol || '',
    },
    actions: [
      { action: 'view', title: '📊 View Alert' },
      { action: 'dismiss', title: 'Dismiss' }
    ]
  };

  event.waitUntil(
    self.registration.showNotification(data.title || '🚨 New Breakout Alert!', options)
  );
});

self.addEventListener('notificationclick', event => {
  event.notification.close();

  if (event.action === 'dismiss') return;

  const targetUrl = event.notification.data?.url || '/';
  event.waitUntil(
    clients.matchAll({ type: 'window', includeUncontrolled: true }).then(clientList => {
      for (const client of clientList) {
        if (client.url.includes(self.location.origin) && 'focus' in client) {
          client.navigate(targetUrl);
          return client.focus();
        }
      }
      if (clients.openWindow) return clients.openWindow(targetUrl);
    })
  );
});

// ── OFFLINE PAGE ─────────────────────────────────────────────
function offlineHTML() {
  return `<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Elite Breakout — Offline</title>
  <style>
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body {
      background: #0b0e14; color: #e8eaf0;
      font-family: -apple-system, BlinkMacSystemFont, 'Inter', sans-serif;
      display: flex; flex-direction: column;
      align-items: center; justify-content: center;
      min-height: 100vh; padding: 24px; text-align: center;
    }
    .icon { font-size: 64px; margin-bottom: 24px; }
    h1 { font-size: 24px; font-weight: 700; color: #00e5a0; margin-bottom: 12px; }
    p { font-size: 15px; color: #94a3b8; max-width: 320px; line-height: 1.6; margin-bottom: 24px; }
    button {
      background: #00e5a0; color: #0b0e14; border: none;
      padding: 14px 32px; border-radius: 12px; font-size: 15px;
      font-weight: 700; cursor: pointer;
    }
    .last-data { font-size: 12px; color: #4b5563; margin-top: 20px; }
  </style>
</head>
<body>
  <div class="icon">📡</div>
  <h1>You're Offline</h1>
  <p>Elite Breakout needs an internet connection to show live scanner data and alerts.</p>
  <button onclick="window.location.reload()">Try Again</button>
  <div class="last-data">Last cached data may still be available after reconnecting.</div>
</body>
</html>`;
}
