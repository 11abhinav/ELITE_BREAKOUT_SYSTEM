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
    
    # Throttle identical titles (to prevent MULTI_TF from spamming every 5 minutes during outages)
    if not bypass_throttle and title in _push_throttle_cache:
        if now - _push_throttle_cache[title] < _THROTTLE_SECONDS:
            logger.info(f"🔕 Throttling duplicate push notification: '{title}'")
            return
    
    _push_throttle_cache[title] = now

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
            logger.error(f"WebPush error: {repr(ex)}")
            # If the subscription expired or the user revoked permission
            if ex.response and ex.response.status_code in (404, 410):
                logger.info(f"Removing expired subscription: {sub['endpoint']}")
                database.remove_push_subscription(sub['endpoint'])
        except Exception as e:
            logger.exception(f"Failed to send push")
