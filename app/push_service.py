import os
import json
import time
import logging
from pywebpush import webpush, WebPushException
import database

logger = logging.getLogger(__name__)

# Simple in-memory cache to throttle duplicate push notifications (especially DEGRADED/DOWN errors)
_push_throttle_cache = {}
_THROTTLE_SECONDS = 3600  # 1 hour

def send_push_to_all(title: str, body: str, url: str = "/", symbol: str = "", bypass_throttle: bool = False):
    """Send a web push notification to all subscribed users. Throttles duplicates by default."""
    
    global _push_throttle_cache
    now = time.time()
    
    # 1. Clean up expired throttles
    expired_keys = [k for k, v in _push_throttle_cache.items() if now - v > _THROTTLE_SECONDS]
    for k in expired_keys:
        del _push_throttle_cache[k]
        
    # Throttle identical titles (to prevent MULTI_TF from spamming every 5 minutes during outages)
    if not bypass_throttle and title in _push_throttle_cache:
        # We already removed expired keys above, so if it's still here, it is within throttle window
        logger.info(f"🔕 Throttling duplicate push notification: '{title}' | Details: {body}")
        return
    
    _push_throttle_cache[title] = now
    
    # 2. Limit maximum cache size
    MAX_PUSH_CACHE_SIZE = 1000
    if len(_push_throttle_cache) > MAX_PUSH_CACHE_SIZE:
        excess = len(_push_throttle_cache) - MAX_PUSH_CACHE_SIZE
        oldest_keys = list(_push_throttle_cache.keys())[:excess]
        for k in oldest_keys:
            del _push_throttle_cache[k]
        logger.info(f"🧹 Evicted {len(expired_keys)} expired and {len(oldest_keys)} oldest entries from _push_throttle_cache.")

    vapid_private_key = os.getenv("VAPID_PRIVATE_KEY")
    vapid_public_key = os.getenv("VAPID_PUBLIC_KEY")

    if not vapid_private_key or not vapid_public_key:
        logger.warning("VAPID keys not configured. Cannot send push notifications.")
        return

    subscriptions = database.get_all_push_subscriptions()
    if not subscriptions:
        return

    payload = json.dumps({
        "title": title,
        "body": body,
        "url": url,
        "symbol": symbol
    })

    for sub in subscriptions:
        try:
            webpush(
                subscription_info={
                    "endpoint": sub["endpoint"],
                    "keys": {
                        "p256dh": sub["p256dh"],
                        "auth": sub["auth"]
                    }
                },
                data=payload,
                vapid_private_key=vapid_private_key,
                vapid_claims={"sub": "mailto:admin@elitebreakout.com"}
            )
        except WebPushException as ex:
            # If the subscription expired, user revoked permission, or VAPID keys changed (400/403)
            status_code = getattr(ex.response, "status_code", None) if hasattr(ex, "response") and ex.response else None
            is_invalid = (status_code in (400, 403, 404, 410)) or any(code in str(ex) for code in ["400", "403", "404", "410"])
            if is_invalid:
                logger.info(f"🧹 Removing invalid/expired subscription: {sub['endpoint']} (Status: {status_code or 'from string'})")
                database.remove_push_subscription(sub['endpoint'])
            else:
                logger.error(f"WebPush error: {repr(ex)}")
        except Exception as e:
            logger.exception(f"Failed to send push")
