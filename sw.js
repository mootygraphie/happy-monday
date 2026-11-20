// Happy Monday : service worker, UNIQUEMENT pour les notifications push.
// Aucun cache : le site est toujours chargé depuis le réseau, donc toujours à jour.
self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", e => e.waitUntil(self.clients.claim()));

self.addEventListener("push", e => {
  let d = {};
  try { d = e.data ? e.data.json() : {}; } catch (err) { d = { body: e.data ? e.data.text() : "" }; }
  e.waitUntil(self.registration.showNotification(d.title || "Happy Monday", {
    body: d.body || "",
    icon: "icons/icon-192.png",
    badge: "icons/favicon-64.png",
    tag: d.tag || undefined,
    data: { url: d.url || "./" },
  }));
});

// Toucher la notification : ouvre (ou ramène) le site sur le bon écran
self.addEventListener("notificationclick", e => {
  e.notification.close();
  const url = new URL((e.notification.data && e.notification.data.url) || "./", self.registration.scope).href;
  e.waitUntil(self.clients.matchAll({ type: "window", includeUncontrolled: true }).then(list => {
    for (const c of list) {
      if (c.url.startsWith(self.registration.scope) && "focus" in c) {
        return c.focus().then(w => (w || c).navigate(url)).catch(() => self.clients.openWindow(url));
      }
    }
    return self.clients.openWindow(url);
  }));
});
