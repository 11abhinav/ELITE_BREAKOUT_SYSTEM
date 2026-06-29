import os
import json
import logging
from pywebpush import webpush, WebPushException
import database

logger = logging.getLogger(__name__)

def send_push_to_all(title: str, body: str, url: str = "/", symbol: str = ""):
    """Send a web push notification to all subscribed users."""
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
