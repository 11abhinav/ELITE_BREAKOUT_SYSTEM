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

async function subscribeToPush() {
  try {
    const reg = await navigator.serviceWorker.ready;

    // [BUG FIX] Don't silently skip if a subscription exists — verify it matches
    // the current VAPID key. If VAPID keys were rotated, the existing subscription
    // is dead. Unsubscribe first, then re-subscribe.
    let sub = await reg.pushManager.getSubscription();

    // Fetch the current server VAPID public key
    const vapidRes = await fetch('/api/push/vapid_public_key');
    if (!vapidRes.ok) {
      console.warn('[PWA] Could not fetch VAPID key from server.');
      return;
    }
    // [BUG FIX] API returns both { public_key, vapid_public_key } — use public_key
    const resp = await vapidRes.json();
    const serverPublicKey = resp.public_key || resp.vapid_public_key;
    if (!serverPublicKey) {
      console.warn('[PWA] VAPID key not configured on server. Push disabled.');
      return;
    }

    // If there's an existing subscription, validate it matches the server's current key
    if (sub) {
      // Convert the server key to Uint8Array for comparison
      const serverKeyBytes = urlB64ToUint8Array(serverPublicKey);
      const existingKeyBytes = new Uint8Array(sub.options.applicationServerKey);
      const keysMatch = serverKeyBytes.length === existingKeyBytes.length &&
        serverKeyBytes.every((b, i) => b === existingKeyBytes[i]);
      if (keysMatch) {
        console.log('[PWA] Existing push subscription is valid. No action needed.');
        return;
      }
      // Keys don't match (VAPID rotation) — unsubscribe old and re-subscribe
      console.warn('[PWA] VAPID key changed. Unsubscribing old subscription and re-subscribing...');
      await sub.unsubscribe();
      sub = null;
    }

    // Create a fresh subscription
    sub = await reg.pushManager.subscribe({
      userVisibleOnly: true,
      applicationServerKey: urlB64ToUint8Array(serverPublicKey)
    });

    // Save to server
    const saveRes = await fetch('/api/push/subscribe', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(sub)
    });

    if (saveRes.ok) {
      console.log('[PWA] ✅ Push subscription saved on server.');
    } else {
      console.error('[PWA] Server rejected subscription:', saveRes.status);
    }
  } catch (err) {
    console.error('[PWA] Failed to subscribe to push:', err);
  }
}

async function requestPushPermission() {
  if (!('Notification' in window) || !('serviceWorker' in navigator)) return;
  
  if (Notification.permission === 'granted') {
    subscribeToPush();
    return;
  }
  
  if (Notification.permission !== 'denied') {
    const permission = await Notification.requestPermission();
    if (permission === 'granted') {
      subscribeToPush();
    }
  }
}

// Push permission is requested on first user click (browsers block auto-permission prompts)
document.addEventListener('click', () => {
    requestPushPermission();
}, { once: true });
