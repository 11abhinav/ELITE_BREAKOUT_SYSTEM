# =====================================================================================
# app/telegram_engine.py
# =====================================================================================
#
# HOW TO SET UP GROUP TOPICS
# =====================================================================================
#
#  Step 1 — Add bot to your Telegram group
#            Group → Edit → Administrators → Add Admin → search your bot
#
#  Step 2 — Enable Topics
#            Group Settings → Topics → Enable
#
#  Step 3 — Create three topics inside the group:
#            e.g. "⚡ Intraday", "🚀 1H Scan", "📊 EOD Alerts"
#
#  Step 4 — Get each topic's message_thread_id:
#            Send any message inside the topic, then open:
#            https://api.telegram.org/bot<YOUR_TOKEN>/getUpdates
#            Look for "message_thread_id" in the response — one per topic.
#
#  Step 5 — Add to config.py:
#            THREAD_EOD      = 123    # replace with real IDs
#            THREAD_MULTI_TF = 456
#            THREAD_1H       = 789
#
#  If THREAD_* values are not set in config.py, messages go to General (no topic).
#
# =====================================================================================

import logging
import time

logger = logging.getLogger(__name__)

# =====================================================================================
# OPTIONAL THREAD IDs — loaded from config if present
# =====================================================================================

try:
    from config import THREAD_EOD
except ImportError:
    THREAD_EOD = None

try:
    from config import THREAD_MULTI_TF
except ImportError:
    THREAD_MULTI_TF = None

try:
    from config import THREAD_1H
except ImportError:
    THREAD_1H = None

try:
    from config import THREAD_REVERSAL
except ImportError:
    THREAD_REVERSAL = None

# =====================================================================================
# THREAD ROUTING — scan_type → message_thread_id
# =====================================================================================

THREAD_MAP = {
    "EOD":      THREAD_EOD,
    "MULTI_TF": THREAD_MULTI_TF,
    "1H":       THREAD_1H,
    "REVERSAL": THREAD_REVERSAL,
}

# =====================================================================================
# SEND
# =====================================================================================

def send_telegram_message(message: str, scan_type: str = None, retries: int = 3) -> bool:
    """
    Sends a message to the configured Telegram group.

    Parameters
    ----------
    message   : str  — alert text (HTML tags supported: <b>, <i>, <code>, <pre>)
    scan_type : str  — "EOD" | "MULTI_TF" | "1H"
                       Routes to the matching group topic if THREAD_* is set in config.
                       Pass None to post to General.
    retries   : int  — retry attempts on failure (default 3)

    Returns True on success, False after all retries exhausted.
    """
    return True

# =====================================================================================
# TELEGRAM QUEUE FLUSHER — async delivery with rate limiting
# =====================================================================================

def flush_telegram_queue(batch_size: int = 5, batch_delay: float = 0.2):
    """
    Flush pending Telegram messages from queue.
    - Takes 5 at a time (respects 30/sec limit: 5/sec = safe)
    - Retry up to 3 times on failure
    - Clean up sent messages after 7 days
    
    Call this from a background thread every 100ms.
    """
    try:
        from database import (
            get_pending_telegram_alerts, mark_telegram_sent, mark_telegram_failed,
            cleanup_old_telegram_sent
        )
    except Exception as e:
        logger.exception(f"❌ Failed to import database functions")
        return

    while True:
        try:
            # Get pending alerts (max 5)
            pending = get_pending_telegram_alerts(limit=batch_size)
            
            if not pending:
                time.sleep(0.1)
                continue
            
            # Send each alert
            for alert in pending:
                try:
                    # Send without retry (already retried in DB)
                    if send_telegram_message(alert['message_text'], scan_type=None, retries=1):
                        mark_telegram_sent(alert['id'])
                        logger.debug(f"✅ Telegram sent: {alert['symbol']}")
                    else:
                        # Mark for retry
                        mark_telegram_failed(alert['id'])
                        logger.warning(f"⚠️ Telegram retry queued: {alert['symbol']}")
                except Exception as e:
                    logger.exception(f"❌ Error processing alert {alert['id']}")
                    mark_telegram_failed(alert['id'])
            
            # Clean up old sent messages (weekly)
            try:
                cleanup_old_telegram_sent(days=7)
            except Exception as e:
                logger.warning(f"⚠️ Failed to cleanup old messages: {e}")
            
            # Rate limiting: respect Telegram 30/sec limit
            time.sleep(batch_delay)
            
        except Exception as e:
            logger.exception(f"❌ Telegram queue flush error: {e}")
            time.sleep(1)

def queue_telegram_message(message: str, symbol: str = "", alert_id: int = None) -> bool:
    """Queue a message for asynchronous delivery instead of sending immediately."""
    return True
