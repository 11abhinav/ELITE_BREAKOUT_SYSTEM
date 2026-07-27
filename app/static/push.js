// ── SERVICE WORKER & PUSH REGISTRATION ──────────────────────────────
if ('serviceWorker' in navigator) {
  window.addEventListener('load', () => {
    navigator.serviceWorker.register('/service-worker.js', { scope: '/' })
      .then(reg => {
        console.log('[PWA] Service worker registered:', reg.scope);
        reg.update();
      })
      .catch(err => {
        console.error('[PWA] Service worker registration failed:', err);
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

  if (Notification.permission === 'granted') {
    await subscribeToPush();
    return;
  }

  if (Notification.permission !== 'denied') {
    const permission = await Notification.requestPermission();
    if (permission === 'granted') {
      await subscribeToPush();
    }
  }
}

// Global window handle for UI buttons / manual trigger
window.enablePushNotifications = requestPushPermission;

// Request push permission on user click interaction
document.addEventListener('click', () => {
  requestPushPermission();
});
