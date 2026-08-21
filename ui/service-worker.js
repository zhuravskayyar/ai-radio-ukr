const CACHE_NAME = 'vector-radio-shell-v2';
const APP_SHELL = [
  'index.html',
  'style.css',
  'library.css',
  'radio-copy.css',
  'vector.css',
  'boombox.css',
  'online-config.js',
  'online-bridge.js',
  'app.js',
  'assets/vector-radio-logo.png',
  'manifest.webmanifest',
].map(path => new URL(path, self.location.href).href);

self.addEventListener('install', event => {
  event.waitUntil(caches.open(CACHE_NAME).then(cache => cache.addAll(APP_SHELL)));
  self.skipWaiting();
});

self.addEventListener('activate', event => {
  event.waitUntil(
    caches.keys().then(keys => Promise.all(
      keys.filter(key => key !== CACHE_NAME).map(key => caches.delete(key)),
    )),
  );
  self.clients.claim();
});

self.addEventListener('fetch', event => {
  const url = new URL(event.request.url);
  if (event.request.method !== 'GET' || url.origin !== self.location.origin) return;
  if (url.pathname.includes('/api/')
      || url.pathname.includes('/media/')
      || url.pathname.includes('/cover/')) return;
  event.respondWith(
    fetch(event.request)
      .then(response => {
        if (response.ok) {
          const copy = response.clone();
          caches.open(CACHE_NAME).then(cache => cache.put(event.request, copy));
        }
        return response;
      })
      .catch(() => caches.match(event.request).then(cached => (
        cached || caches.match(new URL('index.html', self.location.href).href)
      ))),
  );
});
