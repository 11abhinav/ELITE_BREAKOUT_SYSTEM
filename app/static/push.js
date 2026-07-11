// ── SERVICE WORKER & PUSH REGISTRATION ──────────────────────────────
if ('serviceWorker' in navigator) {
  window.addEventListener('load', () => {
    navigator.serviceWorker.register('/service-worker.js', { scope: '/' })
      .then(reg => {
        console.log('[PWA] Service worker registered:', reg.scope);
        // Check for updates every time page loads
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
    let sub = await reg.pushManager.getSubscription();
    if (sub) return; // already subscribed

    const vapidRes = await fetch('/api/push/vapid_public_key');
    if (!vapidRes.ok) throw new Error("Could not fetch VAPID key");
    const { public_key } = await vapidRes.json();
    if (!public_key) return;

    sub = await reg.pushManager.subscribe({
      userVisibleOnly: true,
      applicationServerKey: urlB64ToUint8Array(public_key)
    });

    await fetch('/api/push/subscribe', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(sub)
    });
    console.log('[PWA] Push subscription saved on server.');
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

// FIX: Desktop browsers aggressively block Notification.requestPermission() if triggered via setTimeout.
// It MUST be triggered by a user gesture. We attach a one-time click listener to the document
// so that the first time the user clicks anywhere on the dashboard, it prompts for permissions.
document.addEventListener('click', () => {
    requestPushPermission();
}, { once: true });
