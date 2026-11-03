const CACHE = 'viralcut-pages-v2';
const ASSETS = ['./', './index.html', './manifest.webmanifest', './icon-192.png', './icon-512.png'];
self.addEventListener('install', event => {
 event.waitUntil(caches.open(CACHE).then(cache => cache.addAll(ASSETS)).then(() => self.skipWaiting()));
});
self.addEventListener('activate', event => {
 event.waitUntil(caches.keys().then(keys => Promise.all(keys.filter(key => key !== CACHE).map(key => caches.delete(key)))).then(() => self.clients.claim()));
});
self.addEventListener('fetch', event => {
 const u = new URL(event.request.url);
 if(event.request.method !== 'GET' || u.origin !== location.origin || u.pathname.includes('/api/'))return;
 event.respondWith(fetch(event.request).then(res => {
   if(res.ok && (event.request.mode==='navigate' || ASSETS.some(a => new URL(a, self.registration.scope).pathname===u.pathname))){
     const copy=res.clone(); caches.open(CACHE).then(cache=>cache.put(event.request,copy));
   }
   return res;
 }).catch(() => caches.match(event.request).then(r => r || caches.match('./index.html'))));
});
