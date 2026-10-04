// Minimal service worker so Android offers "Install app". Always uses the network (data must be fresh).
self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", e => e.waitUntil(self.clients.claim()));
self.addEventListener("fetch", () => {});
