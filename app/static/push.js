// ── PUSH NOTIFICATIONS — iOS/Safari Compatible ──────────────────
// IMPORTANT: iOS 16.4+ only supports Web Push when the app is installed
// as a PWA (standalone mode). Push permission prompts in regular Safari
// are silently ignored.

(function() {
  'use strict';

  const isIOS = /iPad|iPhone|iPod/.test(navigator.userAgent) && !window.MSStream;
  const isStandalone = window.matchMedia('(display-mode: standalone)').matches ||
                       window.navigator.standalone === true;

  // ── Cache cleanup ──
  if ('caches' in window) {
    caches.keys().then(keys => {
      keys.forEach(key => {
        if (key !== 'elite-breakout-v5-no-html-cache') {
          console.log('[PWA] Purging legacy cache bucket:', key);
          caches.delete(key);
        }
      });
    });
  }

  function urlB64ToUint8Array(base64String) {
    const padding = '='.repeat((4 - base64String.length % 4) % 4);
    const base64 = (base64String + padding).replace(/\-/g, '+').replace(/_/g, '/');
    const rawData = window.atob(base64);
    const outputArray = new Uint8Array(rawData.length);
    for (let i = 0; i < rawData.length; ++i) {
      outputArray[i] = rawData.charCodeAt(i);
    }
    return outputArray;
  }

  async function sendSubscriptionToServer(sub) {
    if (!sub) return;
    try {
      const saveRes = await fetch('/api/push/subscribe', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(sub)
      });
      if (saveRes.ok) {
        console.log('[PWA] ✅ Push subscription saved on server.');
      } else {
        console.warn('[PWA] Server returned status for push subscription:', saveRes.status);
      }
    } catch (err) {
      console.error('[PWA] Failed to send subscription to server:', err);
    }
  }

  async function subscribeToPush() {
    if (!('serviceWorker' in navigator) || !('PushManager' in window)) {
      console.warn('[PWA] Push messaging is not supported in this browser environment.');
      return;
    }

    try {
      const reg = await navigator.serviceWorker.ready;

      // Fetch the current server VAPID public key
      const vapidRes = await fetch('/api/push/vapid_public_key');
      if (!vapidRes.ok) {
        console.warn('[PWA] Could not fetch VAPID key from server.');
        return;
      }
      const resp = await vapidRes.json();
      const serverPublicKey = resp.public_key || resp.vapid_public_key;
      if (!serverPublicKey) {
        console.warn('[PWA] VAPID key not configured on server. Push disabled.');
        return;
      }

      let sub = await reg.pushManager.getSubscription();

      if (sub) {
        try {
          if (sub.options && sub.options.applicationServerKey) {
            const serverKeyBytes = urlB64ToUint8Array(serverPublicKey);
            const existingKeyBytes = new Uint8Array(sub.options.applicationServerKey);
            const keysMatch = serverKeyBytes.length === existingKeyBytes.length &&
              serverKeyBytes.every((b, i) => b === existingKeyBytes[i]);
            if (!keysMatch) {
              console.warn('[PWA] VAPID key changed. Unsubscribing old subscription...');
              await sub.unsubscribe();
              sub = null;
            }
          }
        } catch (keyErr) {
          console.warn('[PWA] Key validation check skipped (Safari/mobile compatibility):', keyErr);
        }
      }

      if (!sub) {
        sub = await reg.pushManager.subscribe({
          userVisibleOnly: true,
          applicationServerKey: urlB64ToUint8Array(serverPublicKey)
        });
      }

      // Always ensure the subscription is saved/refreshed in PostgreSQL DB
      await sendSubscriptionToServer(sub);

    } catch (err) {
      console.error('[PWA] Failed to subscribe to push:', err);
    }
  }

  async function requestPushPermission() {
    if (!('serviceWorker' in navigator)) return;
    if (!('Notification' in window)) {
      console.warn('[PWA] Notification API not available in window.');
      return;
    }

    // iOS Safari only supports push in standalone PWA mode (iOS 16.4+)
    // Don't even try to request permission in regular Safari — it will silently fail
    if (isIOS && !isStandalone) {
      console.warn('[PWA] iOS requires the app to be installed as PWA for push notifications. Use Safari Share → Add to Home Screen.');
      return;
    }

    if (Notification.permission === 'granted') {
      await subscribeToPush();
      return;
    }

    if (Notification.permission !== 'denied') {
      // IMPORTANT on iOS: Notification.requestPermission() must resolve within the
      // user gesture context. Do NOT await any async work before calling it.
      const permission = await Notification.requestPermission();
      if (permission === 'granted') {
        await subscribeToPush();
      }
    }
  }

  // ── Auto-subscribe if already granted ──
  // Only auto-subscribe when already granted (user previously accepted).
  // Never auto-request permission — that must come from explicit user gesture.
  if ('Notification' in window && Notification.permission === 'granted') {
    if ('serviceWorker' in navigator) {
      window.addEventListener('load', () => {
        // Small delay to let SW registration settle
        setTimeout(() => {
          navigator.serviceWorker.ready.then(() => {
            subscribeToPush();
          }).catch(err => {
            console.warn('[PWA] SW not ready for auto-subscribe:', err);
          });
        }, 1000);
      });
    }
  }

  // ── Expose global handle for UI buttons ──
  // Pages should call window.enablePushNotifications() from a direct
  // click handler (onclick) to preserve the user gesture context for iOS.
  window.enablePushNotifications = requestPushPermission;

})();
