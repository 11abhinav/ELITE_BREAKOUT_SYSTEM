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
import os
import time
import threading
from datetime import datetime
from zoneinfo import ZoneInfo
import requests

logger = logging.getLogger(__name__)

IST = ZoneInfo("Asia/Kolkata")

# In-memory debounce cache to prevent notification floods during cascading failures
_EMERGENCY_DEBOUNCE: dict[str, float] = {}
_EMERGENCY_LOCK = threading.Lock()

def _get_credentials():
    token = os.getenv("TELEGRAM_BOT_TOKEN") or os.getenv("BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID") or os.getenv("CHAT_ID") or "-1002341999976"
    return token, chat_id

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
    token, chat_id = _get_credentials()
    if not token or not chat_id:
        logger.warning("⚠️ Telegram credentials not configured (TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID missing). Message suppressed.")
        return False

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": message,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }

    thread_id = THREAD_MAP.get(scan_type) if scan_type else None
    if thread_id is not None:
        payload["message_thread_id"] = thread_id

    for attempt in range(1, retries + 1):
        try:
            resp = requests.post(url, json=payload, timeout=8)
            if resp.status_code == 200:
                return True
            logger.warning(f"⚠️ Telegram API returned {resp.status_code}: {resp.text[:200]}")
        except Exception as e:
            logger.warning(f"⚠️ Telegram send attempt {attempt}/{retries} failed: {e}")
        if attempt < retries:
            time.sleep(0.5 * attempt)

    return False


def send_emergency_admin_alert(subject: str, message: str, severity: str = "CRITICAL", debounce_seconds: int = 300) -> bool:
    """
    Direct, out-of-band admin alert via Telegram API.
    Zero dependency on PostgreSQL, local filesystem, or worker queues.
    Throttled by subject to prevent notification storms when hundreds of threads fail.
    """
    now = time.time()
    with _EMERGENCY_LOCK:
        last_sent = _EMERGENCY_DEBOUNCE.get(subject, 0.0)
        if (now - last_sent) < debounce_seconds:
            logger.info(f"ℹ️ Emergency alert '{subject}' debounced ({int(now - last_sent)}s < {debounce_seconds}s)")
            return True
        _EMERGENCY_DEBOUNCE[subject] = now

    now_ist = datetime.now(IST).strftime("%Y-%m-%d %H:%M:%S IST")
    truncated_msg = message[:1200] if message else "No details provided"
    
    formatted_text = (
        f"🚨 <b>[ELITE SYSTEM {severity.upper()} ALERT]</b>\n\n"
        f"<b>Subject:</b> {subject}\n"
        f"<b>Severity:</b> {severity}\n"
        f"<b>Timestamp:</b> <code>{now_ist}</code>\n"
        f"<b>Host PID:</b> <code>{os.getpid()}</code>\n\n"
        f"<b>Details:</b>\n"
        f"<pre>{truncated_msg}</pre>"
    )

    def _async_send():
        try:
            token, chat_id = _get_credentials()
            if not token or not chat_id:
                logger.error(f"❌ Cannot dispatch emergency alert '{subject}': Telegram credentials missing from environment.")
                return
            url = f"https://api.telegram.org/bot{token}/sendMessage"
            payload = {
                "chat_id": chat_id,
                "text": formatted_text,
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
            }
            resp = requests.post(url, json=payload, timeout=5)
            if resp.status_code == 200:
                logger.info(f"✅ Dispatched emergency admin alert to Telegram: '{subject}'")
            else:
                logger.error(f"❌ Emergency Telegram dispatch returned HTTP {resp.status_code}: {resp.text[:200]}")
        except Exception as e:
            logger.error(f"❌ Failed to deliver emergency admin alert: {e}")

    # Launch daemon thread so we never block callers (even in connection checkout or signal handlers)
    t = threading.Thread(target=_async_send, name="EmergencyTelegramDispatch", daemon=True)
    t.start()
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
    """Queue a message for asynchronous delivery, falling back to direct send."""
    try:
        from database import queue_telegram_alert
        return queue_telegram_alert(message, symbol=symbol, alert_id=alert_id)
    except Exception:
        # Fallback to direct background dispatch if DB is down
        t = threading.Thread(target=send_telegram_message, args=(message,), daemon=True)
        t.start()
        return True
