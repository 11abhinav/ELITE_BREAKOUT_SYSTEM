// ── PUSH NOTIFICATIONS — iOS/Safari Compatible ──────────────────
// IMPORTANT: iOS 16.4+ only supports Web Push when the app is installed
// as a PWA (standalone mode). Push permission prompts in regular Safari
// are silently ignored.

(function() {
  'use strict';

  const isIOS = /iPad|iPhone|iPod/.test(navigator.userAgent) && !window.MSStream;
  const isStandalone = window.matchMedia('(display-mode: standalone)').matches ||
                       window.navigator.standalone === true;

  // ── Cache cleanup: delete all caches EXCEPT the currently active one ──
  const CURRENT_CACHE = 'elite-breakout-v10';
  if ('caches' in window) {
    caches.keys().then(keys => {
      keys.forEach(key => {
        if (key !== CURRENT_CACHE) {
          console.log('[PWA] Purging old cache bucket:', key);
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
        // Use sub.toJSON() for reliable cross-browser PushSubscription serialization.
        // JSON.stringify(sub) relies on implicit toJSON() which is not consistent across all browsers.
        body: JSON.stringify(typeof sub.toJSON === 'function' ? sub.toJSON() : sub)
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

  function showPushPromptBanner() {
    if (document.getElementById('push-prompt-banner')) return;
    if (sessionStorage.getItem('dismissed_push_banner')) return;

    const banner = document.createElement('div');
    banner.id = 'push-prompt-banner';
    banner.style.cssText = `
      position: fixed; bottom: 20px; left: 50%; transform: translateX(-50%);
      width: calc(100% - 32px); max-width: 460px; z-index: 99999;
      background: linear-gradient(135deg, #131b26 0%, #0d131d 100%);
      border: 1px solid rgba(0, 229, 160, 0.4); border-radius: 16px;
      padding: 12px 16px; box-shadow: 0 12px 32px rgba(0,0,0,0.7);
      display: flex; align-items: center; justify-content: space-between;
      gap: 10px; font-family: -apple-system, BlinkMacSystemFont, 'Inter', sans-serif;
      backdrop-filter: blur(12px); -webkit-backdrop-filter: blur(12px);
      animation: pushSlideUp 0.3s cubic-bezier(0.16, 1, 0.3, 1);
    `;

    let contentHTML = '';
    if (isIOS && !isStandalone) {
      contentHTML = `
        <div style="display:flex; align-items:center; gap:10px; flex:1;">
          <span style="font-size:22px;">📲</span>
          <div style="font-size:12px; color:#e2e8f0; line-height:1.4;">
            <strong>Enable iPhone Push Notifications:</strong><br>
            Tap <span style="color:#00e5a0; font-weight:700;">Share ➔ Add to Home Screen</span>
          </div>
        </div>
        <button id="close-push-banner" style="background:transparent; border:none; color:#94a3b8; font-size:18px; cursor:pointer; padding:4px;">✕</button>
      `;
    } else {
      contentHTML = `
        <div style="display:flex; align-items:center; gap:10px; flex:1;">
          <span style="font-size:22px;">🔔</span>
          <div style="font-size:12px; color:#e2e8f0; line-height:1.3;">
            <strong style="color:#00e5a0;">Get Live Push Alerts</strong><br>
            Receive instant signals on your phone
          </div>
        </div>
        <button id="enable-push-btn" style="background:#00e5a0; color:#0b0e14; border:none; padding:8px 14px; border-radius:8px; font-size:12px; font-weight:700; cursor:pointer; white-space:nowrap;">Enable Now</button>
        <button id="close-push-banner" style="background:transparent; border:none; color:#94a3b8; font-size:16px; cursor:pointer; padding:4px;">✕</button>
      `;
    }

    banner.innerHTML = contentHTML;

    if (!document.getElementById('push-banner-style')) {
      const style = document.createElement('style');
      style.id = 'push-banner-style';
      style.textContent = `@keyframes pushSlideUp { from { transform: translate(-50%, 100%); opacity: 0; } to { transform: translate(-50%, 0); opacity: 1; } }`;
      document.head.appendChild(style);
    }

    document.body.appendChild(banner);

    const enableBtn = document.getElementById('enable-push-btn');
    if (enableBtn) {
      enableBtn.addEventListener('click', async () => {
        banner.remove();
        await requestPushPermission();
      });
    }

    const closeBtn = document.getElementById('close-push-banner');
    if (closeBtn) {
      closeBtn.addEventListener('click', () => {
        banner.remove();
        sessionStorage.setItem('dismissed_push_banner', 'true');
      });
    }
  }

  // ── Auto-subscribe if already granted, or show prompt banner if default ──
  if ('Notification' in window) {
    if (Notification.permission === 'granted') {
      if ('serviceWorker' in navigator) {
        window.addEventListener('load', () => {
          setTimeout(() => {
            navigator.serviceWorker.ready.then(() => {
              subscribeToPush();
            }).catch(err => {
              console.warn('[PWA] SW not ready for auto-subscribe:', err);
            });
          }, 1000);
        });
      }
    } else if (Notification.permission === 'default') {
      if (document.readyState === 'complete' || document.readyState === 'interactive') {
        setTimeout(showPushPromptBanner, 2000);
      } else {
        window.addEventListener('DOMContentLoaded', () => {
          setTimeout(showPushPromptBanner, 2000);
        });
      }
    }
  }

  // ── Expose global handle for UI buttons ──
  window.enablePushNotifications = requestPushPermission;
  window.showPushPromptBanner = showPushPromptBanner;

})();

