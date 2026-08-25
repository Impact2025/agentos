/* Impact OS Remote — service worker: web-push + een offline app-shell.
 *
 * Bewust twee verschillende strategieën:
 *  - de schil (HTML/CSS/JS/fonts) komt uit de cache, want die verandert alleen
 *    bij een deploy en moet in de trein direct laden;
 *  - /api/* gaat ALTIJD naar het netwerk en wordt nooit gecachet. Een besluit
 *    nemen op een gecachete inbox betekent goedkeuren wat je niet meer ziet —
 *    dan liever een eerlijke foutmelding.
 */
const VERSION = 'v2';
const SHELL = `shell-${VERSION}`;
const ASSETS = [
  '/', '/index.html', '/app.js', '/style.css', '/tailwind.css', '/fonts.css',
  '/manifest.json',
];

self.addEventListener('install', (event) => {
  event.waitUntil((async () => {
    const cache = await caches.open(SHELL);
    // Individueel toevoegen: één ontbrekend bestand mag niet de hele
    // installatie laten mislukken en de app zonder schil achterlaten.
    await Promise.all(ASSETS.map((u) => cache.add(u).catch(() => {})));
    await self.skipWaiting();
  })());
});

self.addEventListener('activate', (event) => {
  event.waitUntil((async () => {
    const keys = await caches.keys();
    await Promise.all(keys.filter((k) => k !== SHELL).map((k) => caches.delete(k)));
    await self.clients.claim();
  })());
});

self.addEventListener('fetch', (event) => {
  const { request } = event;
  if (request.method !== 'GET') return;
  const url = new URL(request.url);
  if (url.origin !== self.location.origin) return;
  if (url.pathname.startsWith('/api/')) return;   // nooit uit de cache

  // Fonts hebben een inhoudshash in de naam: cache-first, nooit verversen.
  if (url.pathname.startsWith('/fonts/')) {
    event.respondWith(caches.match(request).then((hit) => hit || fetchAndStore(request)));
    return;
  }

  // Schil: netwerk eerst zodat een deploy meteen doorkomt, cache als vangnet.
  event.respondWith((async () => {
    try {
      const fresh = await fetch(request);
      if (fresh.ok) (await caches.open(SHELL)).put(request, fresh.clone());
      return fresh;
    } catch {
      const hit = await caches.match(request);
      return hit || caches.match('/index.html');
    }
  })());
});

async function fetchAndStore(request) {
  const res = await fetch(request);
  if (res.ok) (await caches.open(SHELL)).put(request, res.clone());
  return res;
}

self.addEventListener('push', (event) => {
  let data = {};
  try { data = event.data ? event.data.json() : {}; } catch { /* leeg */ }
  event.waitUntil(self.registration.showNotification(data.title || 'Impact OS Remote', {
    body: data.body || '',
    data: { url: data.url || '/' },
    tag: 'iris-remote',
  }));
});

self.addEventListener('notificationclick', (event) => {
  event.notification.close();
  const url = (event.notification.data && event.notification.data.url) || '/';
  event.waitUntil(clients.matchAll({ type: 'window', includeUncontrolled: true }).then((list) => {
    for (const c of list) { if ('focus' in c) return c.focus(); }
    return clients.openWindow(url);
  }));
});
