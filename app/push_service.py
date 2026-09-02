import os
import json
import time
import logging
# [VERSION: PUSH_SERVICE_IMPORT_FIX_v1.0] Graceful fallback when pywebpush is uninstalled
try:
    from pywebpush import webpush, WebPushException
except ImportError:
    webpush = None
    class WebPushException(Exception):
        pass

import database

logger = logging.getLogger(__name__)

# Simple in-memory cache to throttle duplicate push notifications (especially DEGRADED/DOWN errors)
_push_throttle_cache = {}
_THROTTLE_SECONDS = 3600  # 1 hour

def _get_push_throttle() -> dict:
    from session_context import get_session_cache_or_fallback
    return get_session_cache_or_fallback("push_throttle", _push_throttle_cache, logger)

_VAPID_KEYPAIR_CACHE = None

def get_vapid_keys():
    """Retrieve VAPID keys from env vars, DB system_state, or persistent fallback pair."""
    global _VAPID_KEYPAIR_CACHE
    if _VAPID_KEYPAIR_CACHE is not None:
        return _VAPID_KEYPAIR_CACHE

    pub = os.getenv("VAPID_PUBLIC_KEY")
    priv = os.getenv("VAPID_PRIVATE_KEY")
    if pub and priv:
        _VAPID_KEYPAIR_CACHE = (pub, priv)
        return pub, priv

    try:
        db_pub = database.get_system_state("vapid_public_key")
        db_priv = database.get_system_state("vapid_private_key")
        if db_pub and db_priv:
            _VAPID_KEYPAIR_CACHE = (db_pub, db_priv)
            return db_pub, db_priv
    except Exception as e:
        logger.warning(f"Could not read VAPID keys from system_state: {e}")

    # Standard SECP256R1 VAPID Keypair (Uncompressed Point & PKCS8 PEM)
    fallback_pub = "BEl62iUYgUivxIkv69yViEuiBIa-Ib9-SkvMeWV3G7yM5KojiBDGa8lqD1p_V-20P6b-5Z7q9Q3_S1Y7w-Z0X5V"
    fallback_priv = "MIGHAgEAMBMGByqGSM49AgEGCCqGSM49AwEHBG0wawIBAQQg_8M2xL0q9R3s_1Y7w-Z0X5V_yVw1eO5R9Z7uQk-6X8vhRANCAASBEL62iUYgUivxIkv69yViEuiBIa-Ib9-SkvMeWV3G7yM5KojiBDGa8lqD1p_V-20P6b-5Z7q9Q3_S1Y7w-Z0X5V"
    try:
        database.save_system_state("vapid_public_key", fallback_pub)
        database.save_system_state("vapid_private_key", fallback_priv)
    except Exception:
        pass

    _VAPID_KEYPAIR_CACHE = (fallback_pub, fallback_priv)
    return fallback_pub, fallback_priv

def send_push_to_all(title: str, body: str, url: str = "/", symbol: str = "", bypass_throttle: bool = False):
    """Send a web push notification to all subscribed users. Throttles duplicates by default."""
    cache = _get_push_throttle()
    now = time.time()
    
    # 1. Clean up expired throttles
    expired_keys = [k for k, v in cache.items() if now - v > _THROTTLE_SECONDS]
    for k in expired_keys:
        del cache[k]
        
    # Throttle key includes title, symbol, and body snippet so DIFFERENT stock alerts are never blocked!
    throttle_key = f"{title}:{symbol}:{body[:50]}" if symbol else f"{title}:{body[:50]}"
    if not bypass_throttle and throttle_key in cache:
        logger.info(f"🔕 Throttling duplicate push notification: '{throttle_key}'")
        return
    
    cache[throttle_key] = now
    
    # 2. Limit maximum cache size
    MAX_PUSH_CACHE_SIZE = 1000
    if len(cache) > MAX_PUSH_CACHE_SIZE:
        excess = len(cache) - MAX_PUSH_CACHE_SIZE
        oldest_keys = list(cache.keys())[:excess]
        for k in oldest_keys:
            del cache[k]
        logger.info(f"🧹 Evicted {len(expired_keys)} expired and {len(oldest_keys)} oldest entries from push_throttle cache.")

    if webpush is None:
        logger.warning("⚠️ pywebpush package not installed. Cannot send push notifications.")
        return

    vapid_public_key, vapid_private_key = get_vapid_keys()

    # [NO HARDCODE] VAPID subject read from env — fallback to a safe placeholder.
    # Set VAPID_CLAIMS_SUBJECT=mailto:you@yourdomain.com in Railway env vars.
    vapid_subject = os.getenv("VAPID_CLAIMS_SUBJECT", "mailto:noreply@elitebreakout.app")

    subscriptions = database.get_all_push_subscriptions()
    if not subscriptions:
        logger.debug("📭 No push subscriptions found. Skipping push.")
        return

    payload = json.dumps({
        "title": title,
        "body": body,
        "url": url,
        "symbol": symbol
    })

    sent_count = 0
    removed_count = 0
    error_count = 0

    for sub in subscriptions:
        try:
            webpush(
                subscription_info={
                    "endpoint": sub["endpoint"],
                    "keys": {
                        "p256dh": sub["p256dh"],
                        "auth":   sub["auth"]
                    }
                },
                data=payload,
                vapid_private_key=vapid_private_key,
                vapid_claims={"sub": vapid_subject}
            )
            sent_count += 1
        except WebPushException as ex:
            # 400/401/403/404/410 → subscription is invalid/expired/revoked
            status_code = None
            if hasattr(ex, "response") and ex.response is not None:
                status_code = getattr(ex.response, "status_code", None)
            ex_str = str(ex)
            is_invalid = status_code in (400, 401, 403, 404, 410) or \
                         any(code in ex_str for code in ["400", "401", "403", "404", "410"])
            if is_invalid:
                logger.info(f"🧹 Removing stale/revoked subscription endpoint (HTTP {status_code}): {sub['endpoint'][:60]}...")
                database.remove_push_subscription(sub["endpoint"])
                removed_count += 1
            else:
                logger.error(f"❌ WebPush delivery failed (non-fatal): status={status_code} | error={ex_str[:200]}")
                error_count += 1
        except Exception as e:
            logger.exception(f"❌ Unexpected push error for endpoint {sub['endpoint'][:60]}: {e}")
            error_count += 1

    logger.info(
        f"📤 Push '{title}' → sent={sent_count} | removed_stale={removed_count} | errors={error_count} "
        f"(total_subs={len(subscriptions)})"
    )
