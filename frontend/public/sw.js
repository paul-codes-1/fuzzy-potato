/**
 * CivicLens Service Worker
 *
 * Strategies:
 *  - Cache-first for static assets (JS, CSS, images, fonts)
 *  - Network-first for API calls (falls back to cache when offline)
 *  - Offline fallback page when navigation fails
 *  - Push notification handler
 *  - Background sync for queued queries
 */

const CACHE_NAME = 'civiclens-v1';
const API_CACHE_NAME = 'civiclens-api-v1';

const STATIC_ASSETS = [
  '/',
  '/index.html',
  '/manifest.json',
];

// Offline fallback HTML (embedded so it works without a network fetch)
const OFFLINE_HTML = `<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>CivicLens - Offline</title>
  <style>
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body {
      font-family: 'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
      background: #f5f5f5;
      color: #1a1a2e;
      display: flex;
      align-items: center;
      justify-content: center;
      min-height: 100vh;
      padding: 2rem;
    }
    .offline-card {
      background: #fff;
      border-radius: 16px;
      padding: 3rem 2rem;
      max-width: 440px;
      text-align: center;
      box-shadow: 0 2px 8px rgba(0,26,58,0.08);
    }
    .offline-icon {
      font-size: 3rem;
      margin-bottom: 1rem;
      display: block;
    }
    h1 { font-size: 1.5rem; margin-bottom: 0.75rem; color: #0033A0; }
    p { color: #6b7280; line-height: 1.6; margin-bottom: 1.5rem; }
    button {
      background: #0033A0;
      color: #fff;
      border: none;
      padding: 0.75rem 2rem;
      border-radius: 9999px;
      font-size: 1rem;
      font-weight: 600;
      cursor: pointer;
    }
    button:hover { background: #002255; }
  </style>
</head>
<body>
  <div class="offline-card">
    <span class="offline-icon" aria-hidden="true">&#128268;</span>
    <h1>You're Offline</h1>
    <p>
      CivicLens needs an internet connection to load meeting data.
      Previously viewed pages may still be available.
    </p>
    <button onclick="window.location.reload()">Try Again</button>
  </div>
</body>
</html>`;


// ---------------------------------------------------------------------------
// Install -- pre-cache static assets and offline page
// ---------------------------------------------------------------------------

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) => {
      // Cache the offline fallback as a special key
      const offlineResponse = new Response(OFFLINE_HTML, {
        headers: { 'Content-Type': 'text/html; charset=utf-8' },
      });
      return cache.put('/_offline', offlineResponse).then(() => {
        return cache.addAll(STATIC_ASSETS).catch(() => {
          // Static assets may not exist yet during dev; non-fatal
        });
      });
    })
  );
  // Activate immediately without waiting for old SW to finish
  self.skipWaiting();
});


// ---------------------------------------------------------------------------
// Activate -- clean up old caches
// ---------------------------------------------------------------------------

self.addEventListener('activate', (event) => {
  const currentCaches = [CACHE_NAME, API_CACHE_NAME];
  event.waitUntil(
    caches.keys().then((names) =>
      Promise.all(
        names
          .filter((name) => !currentCaches.includes(name))
          .map((name) => caches.delete(name))
      )
    ).then(() => self.clients.claim())
  );
});


// ---------------------------------------------------------------------------
// Fetch -- strategy routing
// ---------------------------------------------------------------------------

self.addEventListener('fetch', (event) => {
  const { request } = event;
  const url = new URL(request.url);

  // Only handle same-origin requests
  if (url.origin !== self.location.origin) return;

  // API calls: network-first with cache fallback
  if (url.pathname.startsWith('/api/') || url.pathname.startsWith('/ask') || url.pathname.startsWith('/chat')) {
    event.respondWith(networkFirstStrategy(request));
    return;
  }

  // Static assets (JS, CSS, images, fonts): cache-first
  if (isStaticAsset(url.pathname)) {
    event.respondWith(cacheFirstStrategy(request));
    return;
  }

  // Navigation requests: network-first with offline fallback
  if (request.mode === 'navigate') {
    event.respondWith(navigationStrategy(request));
    return;
  }

  // Everything else: network-first
  event.respondWith(networkFirstStrategy(request));
});


function isStaticAsset(pathname) {
  return /\.(js|css|png|jpg|jpeg|gif|svg|ico|woff2?|ttf|eot)(\?.*)?$/.test(pathname)
    || pathname.startsWith('/assets/');
}


async function cacheFirstStrategy(request) {
  const cached = await caches.match(request);
  if (cached) return cached;

  try {
    const response = await fetch(request);
    if (response.ok) {
      const cache = await caches.open(CACHE_NAME);
      cache.put(request, response.clone());
    }
    return response;
  } catch {
    // Return a basic 503 if both cache and network fail
    return new Response('Service Unavailable', { status: 503 });
  }
}


async function networkFirstStrategy(request) {
  try {
    const response = await fetch(request);
    // Cache successful GET responses
    if (response.ok && request.method === 'GET') {
      const cache = await caches.open(API_CACHE_NAME);
      cache.put(request, response.clone());
    }
    return response;
  } catch {
    const cached = await caches.match(request);
    if (cached) return cached;
    return new Response(
      JSON.stringify({ error: 'You are offline and this data is not cached.' }),
      { status: 503, headers: { 'Content-Type': 'application/json' } }
    );
  }
}


async function navigationStrategy(request) {
  try {
    const response = await fetch(request);
    // Cache the HTML shell for offline use
    if (response.ok) {
      const cache = await caches.open(CACHE_NAME);
      cache.put(request, response.clone());
    }
    return response;
  } catch {
    // Try returning cached version of the page
    const cached = await caches.match(request);
    if (cached) return cached;

    // Last resort: offline fallback page
    const offline = await caches.match('/_offline');
    if (offline) return offline;

    return new Response(OFFLINE_HTML, {
      headers: { 'Content-Type': 'text/html; charset=utf-8' },
    });
  }
}


// ---------------------------------------------------------------------------
// Push notifications
// ---------------------------------------------------------------------------

self.addEventListener('push', (event) => {
  let data = { title: 'CivicLens', body: 'You have a new notification.' };

  if (event.data) {
    try {
      data = event.data.json();
    } catch {
      data.body = event.data.text();
    }
  }

  const options = {
    body: data.body || '',
    icon: '/icons/icon-192x192.png',
    badge: '/icons/icon-192x192.png',
    tag: data.tag || 'civiclens-notification',
    data: {
      url: data.url || '/',
      clip_id: data.clip_id || null,
    },
    actions: data.actions || [],
    vibrate: [100, 50, 100],
    requireInteraction: data.require_interaction || false,
  };

  event.waitUntil(
    self.registration.showNotification(data.title || 'CivicLens', options)
  );
});


// Notification click -- open the relevant meeting detail page
self.addEventListener('notificationclick', (event) => {
  event.notification.close();

  const targetUrl = event.notification.data?.url || '/';

  event.waitUntil(
    self.clients.matchAll({ type: 'window', includeUncontrolled: true }).then((clients) => {
      // Focus an existing tab if one is open on the same origin
      for (const client of clients) {
        if (new URL(client.url).origin === self.location.origin && 'focus' in client) {
          client.navigate(targetUrl);
          return client.focus();
        }
      }
      // Otherwise open a new window
      return self.clients.openWindow(targetUrl);
    })
  );
});


// ---------------------------------------------------------------------------
// Background sync -- replay queued queries when back online
// ---------------------------------------------------------------------------

self.addEventListener('sync', (event) => {
  if (event.tag === 'civiclens-query-sync') {
    event.waitUntil(replayQueuedQueries());
  }
});


async function replayQueuedQueries() {
  try {
    const db = await openSyncDB();
    const tx = db.transaction('queries', 'readwrite');
    const store = tx.objectStore('queries');
    const allRequest = store.getAll();

    return new Promise((resolve, reject) => {
      allRequest.onsuccess = async () => {
        const queries = allRequest.result || [];
        for (const entry of queries) {
          try {
            await fetch(entry.url, {
              method: 'POST',
              headers: { 'Content-Type': 'application/json', ...entry.headers },
              body: JSON.stringify(entry.body),
            });
            // Remove from queue on success
            store.delete(entry.id);
          } catch {
            // Leave in queue for next sync attempt
          }
        }
        resolve();
      };
      allRequest.onerror = () => reject(allRequest.error);
    });
  } catch {
    // IndexedDB may not be available; silently skip
  }
}


function openSyncDB() {
  return new Promise((resolve, reject) => {
    const request = indexedDB.open('civiclens-sync', 1);
    request.onupgradeneeded = () => {
      const db = request.result;
      if (!db.objectStoreNames.contains('queries')) {
        db.createObjectStore('queries', { keyPath: 'id', autoIncrement: true });
      }
    };
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error);
  });
}
