// ============================================================
// Elite Breakout System — Service Worker v9
// ──────────────────────────────────────────────────────────
// Rules (in priority order):
//   1. SSE / EventSource streams         → bypass (browser handles)
//   2. Navigation (page loads/redirects) → bypass (browser handles with cookies & auth)
//   3. Third-party non-CDN origins       → bypass (browser handles)
//   4. /api/* and /data/*                → network-first, 45s timeout, never cached
//   5. /static/* + approved CDNs         → cache-first (icons, fonts, chart.js)
//   6. Everything else                   → network-first, never cached
//
// KEY FIX v9: Navigation requests are NEVER intercepted by the service worker.
// Intercepting `mode === 'navigate'` caused Chrome to log:
//   "[SW] Network request failed: https://elitebreakout.duckdns.org/wealth"
// because the SW's fetch() lacked the browser's session cookies & auth context.
// ============================================================

const CACHE_NAME = 'elite-breakout-v11'; // v11: bypass non-GET API requests to preserve POST body
const STATIC_ASSETS = [
  '/static/manifest.json',
  '/static/icons/icon-192.png',
  '/static/icons/icon-512.png',
];

// ── INSTALL ──────────────────────────────────────────────────
self.addEventListener('install', event => {
  console.log('[SW] v9 Installing...');
  event.waitUntil(
    caches.open(CACHE_NAME).then(cache => {
      return cache.addAll(STATIC_ASSETS).catch(err => {
        console.warn('[SW] Some static assets failed to cache:', err);
      });
    }).then(() => self.skipWaiting())
  );
});

// ── ACTIVATE: Purge all old caches ───────────────────────────
self.addEventListener('activate', event => {
  console.log('[SW] v9 Activating, purging old caches...');
  event.waitUntil(
    caches.keys().then(keys =>
      Promise.all(
        keys.map(k => {
          if (k !== CACHE_NAME) {
            console.log('[SW] Deleting old cache:', k);
            return caches.delete(k);
          }
        })
      )
    ).then(() => self.clients.claim())
  );
});

// ── FETCH: Strict bypass / routing rules ─────────────────────
self.addEventListener('fetch', event => {
  const req = event.request;
  const url = new URL(req.url);

  // ① Bypass: SSE / EventSource streams
  if (url.pathname.includes('/stream/') || req.headers.get('accept')?.includes('text/event-stream')) {
    return; // browser handles natively
  }

  // ② Bypass: ALL navigation requests (page loads, back/forward, redirects)
  //    These need the browser's full cookie/auth stack.
  if (req.mode === 'navigate') {
    return; // browser handles natively
  }

  // ③ Bypass: third-party origins (except approved CDNs)
  if (url.origin !== self.location.origin &&
      !url.hostname.includes('fonts.googleapis.com') &&
      !url.hostname.includes('fonts.gstatic.com') &&
      !url.hostname.includes('cdn.jsdelivr.net')) {
    return; // browser handles natively
  }

  // ④ Bypass: ALL non-GET API & data requests (POST, PUT, DELETE, PATCH, etc.)
  //    A service worker cannot reliably clone a request body (ReadableStream is
  //    consumed once). Intercepting POST/PUT drops the body → server gets empty
  //    body → 400 Bad Request. Let the browser handle these natively.
  if ((url.pathname.startsWith('/api/') || url.pathname.startsWith('/data/')) &&
       req.method !== 'GET') {
    return; // browser handles natively — body, cookies, auth all preserved
  }

  // ⑤ GET /api/ and GET /data/ → network-first with 45s timeout (no caching)
  if (url.pathname.startsWith('/api/') || url.pathname.startsWith('/data/')) {
    event.respondWith(networkFirstNoCache(req));
    return;
  }

  // ⑥ Static assets & CDNs → cache-first
  if (url.pathname.startsWith('/static/') ||
      url.hostname.includes('fonts.googleapis.com') ||
      url.hostname.includes('fonts.gstatic.com') ||
      url.hostname.includes('cdn.jsdelivr.net')) {
    event.respondWith(cacheFirst(req));
    return;
  }

  // ⑦ Everything else → network-first, not cached
  event.respondWith(networkFirstNoCache(req));
});


// ── Network-first: 45s timeout, never caches ─────────────────
async function networkFirstNoCache(request) {
  try {
    const controller = new AbortController();
    const timeoutId = setTimeout(() => controller.abort(), 45000);
    const networkResponse = await fetch(new Request(request, { signal: controller.signal }));
    clearTimeout(timeoutId);
    return networkResponse;
  } catch (err) {
    if (err.name !== 'AbortError') {
      console.warn('[SW] API request failed (offline?):', request.url);
    }
    return new Response(JSON.stringify({ status: 'offline', message: 'Network unavailable' }), {
      status: 503,
      headers: { 'Content-Type': 'application/json' }
    });
  }
}

// ── Cache-first: for static assets with network fallback ──────
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

// ── NOTIFICATION CLICK ────────────────────────────────────────
self.addEventListener('notificationclick', event => {
  event.notification.close();

  if (event.action === 'dismiss') return;

  const targetUrl = event.notification.data?.url || '/';

  event.waitUntil(
    clients.matchAll({ type: 'window', includeUncontrolled: true }).then(clientList => {
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
