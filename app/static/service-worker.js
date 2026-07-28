// ============================================================
// Elite Breakout System — Service Worker
// Provides: offline caching, background sync, push notifications
// ============================================================

const CACHE_NAME = 'elite-breakout-v5-no-html-cache'; // bumped: purge stale HTML caches completely
const STATIC_ASSETS = [
  '/static/manifest.json',
  '/static/icons/icon-192.png',
  '/static/icons/icon-512.png',
  'https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=JetBrains+Mono:wght@400;600&display=swap',
  'https://cdn.jsdelivr.net/npm/chart.js',
];

// ── INSTALL: Cache ONLY static assets (manifest, icons, fonts) ──
self.addEventListener('install', event => {
  console.log('[SW] Installing service worker v5...');
  event.waitUntil(
    caches.open(CACHE_NAME).then(cache => {
      return cache.addAll(STATIC_ASSETS).catch(err => {
        console.warn('[SW] Some static assets failed to cache:', err);
      });
    }).then(() => self.skipWaiting())
  );
});

// ── ACTIVATE: Clean ALL old caches & evict stale HTML entries ──
self.addEventListener('activate', event => {
  console.log('[SW] Activating service worker v5 & purging old caches...');
  event.waitUntil(
    caches.keys().then(keys =>
      Promise.all(
        keys.map(k => {
          if (k !== CACHE_NAME) {
            console.log('[SW] Deleting legacy cache:', k);
            return caches.delete(k);
          }
        })
      )
    ).then(() => {
      // Clean out any stale HTML page entries from current cache
      return caches.open(CACHE_NAME).then(cache => {
        return cache.keys().then(requests => {
          return Promise.all(
            requests.map(req => {
              const u = req.url.toLowerCase();
              if (u.includes('/admin') || u.includes('/wealth') || u.endsWith('/') || u.includes('/login')) {
                console.log('[SW] Evicting stale HTML document:', req.url);
                return cache.delete(req);
              }
            })
          );
        });
      });
    }).then(() => self.clients.claim())
  );
});

// ── FETCH: Network-first for dynamic content, cache for static ──
self.addEventListener('fetch', event => {
  const url = new URL(event.request.url);

  // ALWAYS go network-first for API calls and authenticated pages
  // Never serve stale HTML trading pages — freshness is critical
  if (url.pathname.startsWith('/api/') ||
      url.pathname.startsWith('/data/') ||
      url.pathname === '/' ||
      url.pathname === '/admin' ||
      url.pathname === '/wealth' ||
      url.pathname === '/login' ||
      event.request.mode === 'navigate') {
    event.respondWith(networkFirstNoHtmlCache(event.request));
    return;
  }

  // Cache-first ONLY for static assets (fonts, icons, scripts)
  if (url.pathname.startsWith('/static/') ||
      url.hostname.includes('fonts.googleapis.com') ||
      url.hostname.includes('cdn.jsdelivr.net')) {
    event.respondWith(cacheFirst(event.request));
    return;
  }

  // Default: network first without HTML caching
  event.respondWith(networkFirstNoHtmlCache(event.request));
});

async function networkFirstNoHtmlCache(request) {
  const isHtmlRequest = request.mode === 'navigate' ||
                        request.headers.get('accept')?.includes('text/html') ||
                        request.url.includes('/admin') ||
                        request.url.includes('/wealth') ||
                        request.url.endsWith('/') ||
                        request.url.includes('/login');

  try {
    const networkResponse = await fetch(request);
    // NEVER cache HTML pages or API responses in CacheStorage
    if (!isHtmlRequest && request.method === 'GET' && networkResponse.ok && request.url.startsWith('http')) {
      const cache = await caches.open(CACHE_NAME);
      cache.put(request, networkResponse.clone());
    }
    return networkResponse;
  } catch (err) {
    console.warn('[SW] Network request failed:', request.url, err);
    // If offline and it's an HTML page request, return offlineHTML, NEVER a stale cached HTML page
    if (isHtmlRequest) {
      return new Response(offlineHTML(), {
        headers: { 'Content-Type': 'text/html' }
      });
    }
    // For non-HTML static requests, try cache
    const cached = await caches.match(request);
    if (cached) {
      return cached;
    }
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
    if (networkResponse.ok && request.url.startsWith('http')) {
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
    data: {
      url: data.url || '/',
      symbol: data.symbol || '',
    },
    actions: [
      { action: 'view', title: 'View Alert' },
      { action: 'dismiss', title: 'Dismiss' }
    ]
  };

  // vibrate is unsupported on iOS Safari — only add on Android
  if ('vibrate' in navigator) {
    options.vibrate = [200, 100, 200, 100, 200];
  }

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
      // [BUG FIX] Don't use client.url.includes(self.location.origin) — it's unreliable on iOS PWA.
      // Instead: try to focus any existing visible window, then navigate it, else open new window.
      for (const client of clientList) {
        if ('focus' in client) {
          return client.focus().then(c => c.navigate(targetUrl)).catch(() => {
            if (clients.openWindow) return clients.openWindow(targetUrl);
          });
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
