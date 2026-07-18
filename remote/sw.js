/* Iris Remote — service worker: alleen web-push, geen cache-magie. */
self.addEventListener('push', (event) => {
  let data = {};
  try { data = event.data ? event.data.json() : {}; } catch { /* leeg */ }
  event.waitUntil(self.registration.showNotification(data.title || 'Iris Remote', {
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
