# =====================================================================================
# app/main.py  — SELF-HEALING ORCHESTRATOR
#
# RAILWAY FIX: Flask (dashboard) runs in the MAIN thread so Railway's health check
# gets a response immediately. The watchdog loop and all scanners run as daemon
# threads in the background. This is the correct pattern for Railway deployments.
#
# EOD / REVERSAL run ONCE at 18:30 IST. They are NOT auto-restarted on crash.
# Instead, any crash or zero-alert result sends a Telegram notification.
# =====================================================================================
import sys
import os
import time
import threading
import logging
import traceback
import signal
import socket
from datetime import datetime, time as dt_time
from zoneinfo import ZoneInfo

APP_DIR = os.path.dirname(os.path.abspath(__file__))
if APP_DIR not in sys.path:
    sys.path.insert(0, APP_DIR)

IST = ZoneInfo("Asia/Kolkata")

def ist_converter(*args):
    timestamp = args[-1] if args else None
    if timestamp is None:
        import time
        timestamp = time.time()
    return datetime.fromtimestamp(timestamp, IST).timetuple()

logging.Formatter.converter = ist_converter
logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s", datefmt="%Y-%m-%d %H:%M:%S")

from db_logger import install_db_logger
install_db_logger()

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# STARTUP DIAGNOSTICS — Verify all required functions are available (2026-06-26)
# This catches version mismatches between local/container code early
# ─────────────────────────────────────────────────────────────────────────────
try:
    from diagnostics import run_startup_diagnostics
    if not run_startup_diagnostics():
        logger.critical("🚨 CRITICAL: Startup diagnostics FAILED. Aborting boot.")
        sys.exit(1)
except ImportError:
    logger.warning("⚠️ diagnostics module not found (new deployment). Continuing without diagnostics.")
except Exception as e:
    logger.warning(f"⚠️ Diagnostics check failed: {e}. Continuing anyway.")
# ─────────────────────────────────────────────────────────────────────────────

# Map watchdog thread names to dashboard database keys
THREAD_TO_SCANNER = {
    "EODScanner":         "EOD",
    "ReversalScanner":    "REVERSAL",
    "MultiTFScanner":     "MULTI_TF",
    "PerformanceTracker": "PERFORMANCE_TRACKER",
}

# Lazy import — dashboard_server may not be ready yet at module load
def _notify_down(name: str, err: str):
    try:
        scanner_name = THREAD_TO_SCANNER.get(name, name)
        from dashboard_server import notify_scanner_down
        notify_scanner_down(scanner_name, err)
    except Exception:
        pass

def _clear_down(name: str):
    try:
        scanner_name = THREAD_TO_SCANNER.get(name, name)
        from dashboard_server import clear_scanner_down
        clear_scanner_down(scanner_name)
    except Exception:
        pass

# ── Scan windows (start_time, end_time) ─────────────────────────────────────────────
WINDOWS = {
    "live":     (dt_time(10, 17), dt_time(15, 30)),
    "eod":      (dt_time(18, 30), dt_time(23, 59, 59)),
    "reversal": (dt_time(18, 30), dt_time(23, 59, 59)),
}


# =====================================================================================
# HELPERS
# =====================================================================================

def _cleanup_old_scanner_names():
    from database import get_connection
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM scanner_health WHERE scanner_name ILIKE '%worker%' OR scanner_name ILIKE '%wealthengine%';")
                cur.execute("DELETE FROM scanner_health WHERE scanner_name = 'Multibagger';")
                cur.execute("DELETE FROM scanner_health WHERE scanner_name = 'WealthEngine';")
                # Reset stale DOWN status from previous crashes for main scanners.
                # On boot, every scanner starts fresh — it will set its own status
                # once it completes its first cycle. This prevents old DOWN entries
                # from a previous deploy from showing RED on the dashboard.
                cur.execute("""
                    UPDATE scanner_health 
                    SET status='OK', error_msg=NULL, is_acknowledged=TRUE
                    WHERE scanner_name IN ('EOD', 'REVERSAL', 'Wealth Engine', 'DAILY_BUILDER')
                      AND status = 'DOWN';
                """)
            conn.commit()
    except Exception as e:
        logger.warning(f"Failed to cleanup old scanner names: {e}")

def wait_for_window(name: str):
    """Block until the scan window opens (weekday only)."""
    start_time, end_time = WINDOWS[name]
    while True:
        now = datetime.now(IST)
        if now.weekday() >= 5:
            logger.info(f"[{name}] 📅 Weekend — sleeping 1 hour...")
            time.sleep(3600)
            continue
        if now.time() > end_time:
            logger.info(f"[{name}] 🕒 Past window end ({end_time}) — waiting for tomorrow...")
            time.sleep(1800)  # Sleep 30 minutes before checking again
            continue
        if now.time() >= start_time:
            logger.info(f"[{name}] ✅ Window open | {now.strftime('%H:%M:%S')} | Launching scanner")
            return
        time.sleep(60)


# =====================================================================================
# WATCHLIST PRE-FLIGHT
# =====================================================================================
from config import WATCHLIST_PATH
import threading as _threading

_watchlist_ready = _threading.Event()

def _build_watchlist_background():
    if os.path.exists(WATCHLIST_PATH):
        logger.info(f"✅ Watchlist found | {WATCHLIST_PATH}")
        _watchlist_ready.set()
        return
    logger.info("📋 Watchlist missing | Attempting to restore or build in background thread...")
    try:
        from watchlist_cache import get_watchlist
        get_watchlist()
        if os.path.exists(WATCHLIST_PATH):
            _watchlist_ready.set()
    except Exception:
        logger.exception("❌ Daily builder failed — scanners will rebuild at first scan cycle")

_threading.Thread(target=_build_watchlist_background, name="WatchlistBuilder", daemon=True).start()


# =====================================================================================
# THREAD RUNNERS — intraday / live  (self-healing via watchdog)
# =====================================================================================

active_threads = {}

def _run(name, fn):
    try:
        _clear_down(name)
        fn()
        threading.current_thread().completed_cleanly = True
    except Exception as exc:
        logger.exception(f"❌ Unhandled exception in {name}")
        threading.current_thread().completed_cleanly = False
        _notify_down(name, str(exc)[:200])
        try:
            from database import insert_notification
            insert_notification(
                notif_type="scanner_down",
                title=f"🚨 Scanner Crash: {name}",
                message=f"Thread crashed due to unhandled exception: {str(exc)[:400]}"
            )
        except Exception:
            pass

# GLOBAL LOCK to prevent concurrent scanner execution (fixes Fyers/Yahoo rate limits)
scanner_execution_lock = threading.Lock()

def run_reversal_scanner():
    """
    REVERSAL Scanner:
    - Wait for 6:30 PM window
    - Run scan
    - On SUCCESS: Mark completed and EXIT cleanly
    - On ERROR: Retry every minute until midnight, then force stop
    """
    retry_count = 0
    while True:
        block_until_watchlist_ready()
        wait_for_window("reversal")
        now = datetime.now(IST)
        today_str = now.strftime("%Y-%m-%d")
        
        # Check database if we already succeeded today
        try:
            from database import get_all_scanner_health
            health_records = get_all_scanner_health()
            already_ran = False
            for rec in health_records:
                if rec.get("scanner_name") == "REVERSAL" and rec.get("status") == "OK" and rec.get("last_success"):
                    last_success_str = str(rec["last_success"])
                    if last_success_str.startswith(today_str):
                        already_ran = True
                        break
            
            if already_ran:
                logger.info("🔄 REVERSAL SCAN | Already successfully executed today. Sleeping until tomorrow...")
                time.sleep(3600)  # Sleep 1 hour
                continue
        except Exception as e:
            logger.warning(f"Could not verify REVERSAL previous run status: {e}")
        
        try:
            logger.info(f"🔄 REVERSAL SCAN | Starting scan for {today_str}...")
            import reversal_scanner
            with scanner_execution_lock:
                total = reversal_scanner.start()   # returns int
                time.sleep(15)
            if total == 0:
                msg = (
                    f"🔄 REVERSAL SCAN — {today_str}\n"
                    f"ℹ️ No mean-reversion setups found today.\n"
                    f"All stocks screened — none passed the filters."
                )
                logger.info("🔄 REVERSAL | Zero alerts — no Telegram notification (removed 2026-06-17)")
            else:
                logger.info(f"🔄 REVERSAL | Completed — {total} alert(s) sent")
            
            # Successfully finished Reversal scan for today — MARK COMPLETED AND EXIT
            from database import upsert_scanner_health
            upsert_scanner_health(
                "REVERSAL",
                status="OK",
                last_success=datetime.now(IST).isoformat(),
                today_alerts=total,
                scheduled_for="18:30 IST"
            )
            # Rebuild performance data on scanner completion (debounced, async)
            try:
                from performance_tracker import trigger_performance_rebuild
                trigger_performance_rebuild()
            except Exception as pe:
                logger.error(f"Failed to trigger performance rebuild post-REVERSAL: {pe}")
            logger.info("✅ REVERSAL SCANNER | Completed successfully for today — waiting for tomorrow.")
            retry_count = 0  # reset on successful completion
            continue
            
        except Exception as exc:
            retry_count += 1
            now = datetime.now(IST)
            
            # Force stop at midnight (between 00:00 and 06:00)
            if 0 <= now.hour < 6:
                logger.critical(f"⏰ MIDNIGHT PASSED — REVERSAL scanner force-stopping after {retry_count} retries")
                upsert_scanner_health(
                    "REVERSAL",
                    status="DOWN",
                    error_msg=f"Stopped at midnight after {retry_count} failed attempts",
                    scheduled_for="18:30 IST"
                )
                retry_count = 0
                continue
            
            # Retry logic
            tb = traceback.format_exc()
            msg = (
                f"🚨 REVERSAL SCAN FAILED — {now.strftime('%Y-%m-%d')} (Retry #{retry_count})\n"
                f"Error: {exc}\n\n"
                f"{tb[-500:]}"
            )
            logger.critical(f"💀 REVERSAL scanner crashed (attempt {retry_count}): {exc}. Retrying in 1 minute...")
            
            from database import upsert_scanner_health, insert_notification
            upsert_scanner_health(
                "REVERSAL",
                status="DOWN",
                error_msg=str(exc)[:500],
                retry_count=retry_count,
                scheduled_for="18:30 IST"
            )
            
            # Notify admin on first failure only
            if retry_count == 1:
                try:
                    from telegram_engine import queue_telegram_message
                    queue_telegram_message(
                        f"🚨 <b>REVERSAL SCANNER CRASHED</b>\n\n"
                        f"❌ <b>Error:</b> {str(exc)[:300]}\n"
                        f"🕐 <b>Time:</b> {now.strftime('%H:%M:%S IST')}\n"
                        f"🔄 Will auto-retry with backoff until midnight."
                    )
                except Exception:
                    pass
                try:
                    insert_notification(
                        notif_type="scanner_down",
                        title="🚨 REVERSAL Scanner CRASHED",
                        message=f"Error: {str(exc)[:400]}. Auto-retrying."
                    )
                except Exception:
                    pass
            
            wait_time = min(300, (2 ** retry_count) * random.uniform(0.5, 1.5))
            logger.info(f"⏳ Sleeping for {wait_time:.1f}s before next REVERSAL retry...")
            time.sleep(wait_time)




def run_eod_scanner():
    """
    EOD Scanner:
    - Wait for 6:30 PM window
    - Run scan
    - On SUCCESS: Mark completed and EXIT cleanly
    - On ERROR: Retry every minute until midnight, then force stop
    """
    retry_count = 0
    while True:
        block_until_watchlist_ready()
        wait_for_window("eod")
        now = datetime.now(IST)
        today_str = now.strftime("%Y-%m-%d")
        
        # Check database if we already succeeded today
        try:
            from database import get_all_scanner_health
            health_records = get_all_scanner_health()
            already_ran = False
            for rec in health_records:
                if rec.get("scanner_name") == "EOD" and rec.get("status") == "OK" and rec.get("last_success"):
                    last_success_str = str(rec["last_success"])
                    if last_success_str.startswith(today_str):
                        already_ran = True
                        break
            
            if already_ran:
                logger.info("📊 EOD SCAN | Already successfully executed today. Sleeping until tomorrow...")
                time.sleep(3600)  # Sleep 1 hour
                continue
        except Exception as e:
            logger.warning(f"Could not verify EOD previous run status: {e}")
        
        try:
            logger.info(f"📊 EOD SCAN | Starting scan for {today_str}...")
            import eod_scanner
            with scanner_execution_lock:
                total = eod_scanner.start()   # returns int
                time.sleep(15)
            if total == 0:
                msg = (
                    f"📊 EOD SCAN — {today_str}\n"
                    f"ℹ️ No breakout setups found today.\n"
                    f"All stocks screened — none passed the filters."
                )
                logger.info("📊 EOD | Zero alerts — no Telegram notification (removed 2026-06-17)")
            else:
                logger.info(f"📊 EOD | Completed — {total} alert(s) sent")
            
            # Successfully finished EOD scan for today — MARK COMPLETED AND EXIT
            from database import upsert_scanner_health
            upsert_scanner_health(
                "EOD",
                status="OK",
                last_success=datetime.now(IST).isoformat(),
                today_alerts=total,
                scheduled_for="18:30 IST"
            )
            # Rebuild performance data on scanner completion (debounced, async)
            try:
                from performance_tracker import trigger_performance_rebuild
                trigger_performance_rebuild()
            except Exception as pe:
                logger.error(f"Failed to trigger performance rebuild post-EOD: {pe}")
            logger.info("✅ EOD SCANNER | Completed successfully for today — waiting for tomorrow.")
            retry_count = 0  # reset on successful completion
            continue
            
        except Exception as exc:
            retry_count += 1
            now = datetime.now(IST)
            
            # Force stop at midnight (between 00:00 and 06:00)
            if 0 <= now.hour < 6:
                logger.critical(f"⏰ MIDNIGHT PASSED — EOD scanner force-stopping after {retry_count} retries")
                upsert_scanner_health(
                    "EOD",
                    status="DOWN",
                    error_msg=f"Stopped at midnight after {retry_count} failed attempts",
                    scheduled_for="18:30 IST"
                )
                retry_count = 0
                continue
            
            # Retry logic
            tb = traceback.format_exc()
            msg = (
                f"🚨 EOD SCAN FAILED — {now.strftime('%Y-%m-%d')} (Retry #{retry_count})\n"
                f"Error: {exc}\n\n"
                f"{tb[-500:]}"
            )
            logger.critical(f"💀 EOD scanner crashed (attempt {retry_count}): {exc}. Retrying in 1 minute...")
            
            from database import upsert_scanner_health, insert_notification
            upsert_scanner_health(
                "EOD",
                status="DOWN",
                error_msg=str(exc)[:500],
                retry_count=retry_count,
                scheduled_for="18:30 IST"
            )
            
            # Notify admin on first failure only (avoid spam on retries)
            if retry_count == 1:
                try:
                    from telegram_engine import queue_telegram_message
                    queue_telegram_message(
                        f"🚨 <b>EOD SCANNER CRASHED</b>\n\n"
                        f"❌ <b>Error:</b> {str(exc)[:300]}\n"
                        f"🕐 <b>Time:</b> {now.strftime('%H:%M:%S IST')}\n"
                        f"🔄 Will auto-retry with backoff until midnight."
                    )
                except Exception:
                    pass
                try:
                    insert_notification(
                        notif_type="scanner_down",
                        title="🚨 EOD Scanner CRASHED",
                        message=f"Error: {str(exc)[:400]}. Auto-retrying."
                    )
                except Exception:
                    pass
            
            wait_time = min(300, (2 ** retry_count) * random.uniform(0.5, 1.5))
            logger.info(f"⏳ Sleeping for {wait_time:.1f}s before next EOD retry...")
            time.sleep(wait_time)




def run_multibagger_scanner():
    """
    Multibagger Scanner:
    - Runs Daily at 7:00 PM IST (19:00 IST).
    - Scans dynamically fetched index constituents.
    - Updates watchlist and buy alerts.
    """
    multibagger_ran = False
    while True:
        try:
            now = datetime.now(IST)
            # Daily at 7:00 PM IST
            if now.hour == 19 and now.minute >= 0 and not multibagger_ran:
                logger.info(f"🚀 MULTIBAGGER SCAN | Starting daily scan at {now.strftime('%H:%M:%S IST')}...")
                import multibagger
                with scanner_execution_lock:
                    stats = multibagger.start() or {}
                    time.sleep(15)
                multibagger_ran = True
                
                # Mark success in health table
                from database import upsert_scanner_health
                upsert_scanner_health(
                    "MULTIBAGGER",
                    status="OK",
                    last_success=datetime.now(IST).isoformat(),
                    scheduled_for="Daily 19:00 IST",
                    total_count=stats.get("total_count"),
                    processed_count=stats.get("processed_count"),
                    today_alerts=stats.get("today_alerts", 0)
                )
                # Rebuild performance data on scanner completion (debounced, async)
                try:
                    from performance_tracker import trigger_performance_rebuild
                    trigger_performance_rebuild()
                except Exception as pe:
                    logger.error(f"Failed to trigger performance rebuild post-MULTIBAGGER: {pe}")
                logger.info("✅ MULTIBAGGER SCAN | Completed successfully.")
            
            # Reset flag outside 7 PM window
            if now.hour != 19:
                multibagger_ran = False
                
        except Exception as e:
            logger.exception("❌ MULTIBAGGER SCAN | Failed")
            try:
                from database import upsert_scanner_health
                upsert_scanner_health(
                    "MULTIBAGGER",
                    status="DOWN",
                    error_msg=str(e)[:500],
                    scheduled_for="Daily 19:00 IST"
                )
            except Exception:
                pass
                
        time.sleep(30)


RESTARTABLE_THREADS = {
    # Intraday and Live scanners disabled per ops request to reduce API load during market hours.
    # "IntradayScanner":    run_intraday_scanner,
    # "LiveScanner":        run_live_scanner,
    "MultiTFScanner":     run_multi_tf_scanner,
    "PerformanceTracker": run_performance_tracker,
    # [VERSION: TRIGGER_AI_WORKER_v1.3] Uncomment AI Worker thread
    "AI Worker":          run_worker_loop,
    "Pledge Worker":      run_pledge_loop,
    # "BayesianUpdater":    run_bayesian_loop,
    "SystemScheduler":    run_system_scheduler,
    "EODScanner":         run_eod_scanner,
    "ReversalScanner":    run_reversal_scanner,
    "MultibaggerScanner": run_multibagger_scanner,
}

# EOD and Reversal are now restartable since they run continuously
ONE_SHOT_THREADS = {}

ALL_THREADS = {**RESTARTABLE_THREADS, **ONE_SHOT_THREADS}




def start_thread(name, target):
    t = threading.Thread(target=lambda: _run(name, target), name=name, daemon=True)
    t.completed_cleanly = False
    t.start()
    active_threads[name] = t
    return t


def run_watchdog():
    """Watchdog loop — background daemon thread; Flask owns the main thread."""
    _missing_env = [v for v in ("BOT_TOKEN", "CHAT_ID") if not os.getenv(v)]
    if _missing_env:
        logger.error(f"❌ FATAL: Missing env vars: {_missing_env}")

    # Start Telegram Queue Flusher background thread
    try:
        from telegram_engine import flush_telegram_queue
        flusher_thread = threading.Thread(target=flush_telegram_queue, name="TelegramQueueFlusher", daemon=True)
        flusher_thread.start()
        logger.info("📨 Background Telegram queue flusher thread started.")
    except Exception as e:
        logger.exception(f"❌ Failed to start Telegram queue flusher")

    for name, target in ALL_THREADS.items():
        start_thread(name, target)

    logger.info("=" * 70)
    logger.info("🛡️  SELF-HEALING WATCHDOG ACTIVE | All Scanners Initialized")
    logger.info("🌐  Dashboard: https://elitebreakoutsystem-production.up.railway.app/")
    logger.info("=" * 70)

    _logged_ready = False
    while True:
        if not _logged_ready and _watchlist_ready.is_set():
            logger.info("✅ Watchlist build complete — all scanners can proceed")
            _logged_ready = True

        for name, thread in list(active_threads.items()):
            if not thread.is_alive():
                if getattr(thread, "completed_cleanly", False):
                    logger.info(f"✅ THREAD COMPLETED CLEANLY: {name} — removing from watchdog.")
                    del active_threads[name]

                elif name in ONE_SHOT_THREADS:
                    # EOD/Reversal crashed without completing cleanly — already sent
                    # Telegram alert inside the runner.  Just drop from tracking.
                    logger.warning(f"⚠️ ONE-SHOT THREAD EXITED UNCLEANLY: {name} — NOT restarting (Telegram already notified).")
                    del active_threads[name]

                else:
                    # Restartable scanner crashed — revive it
                    logger.critical(f"💀 THREAD CRASH: {name} — restarting in 10s...")
                    _notify_down(name, "Thread crashed — restarting")
                    time.sleep(10)
                    start_thread(name, RESTARTABLE_THREADS[name])
                    logger.info(f"🔄 THREAD REVIVED: {name}")

        time.sleep(30)


# =====================================================================================
# ADMIN MANUAL SCANNER TRIGGER  (bypasses market-hour checks)
# =====================================================================================

def trigger_scanner_manual(scanner_key: str) -> dict:
    """Run a scanner once in a background thread, bypassing all market-hour checks.
    
    Returns a dict with 'status' and 'message'.
    Called from the admin dashboard API endpoint.
    """
    from database import upsert_scanner_health
    
    TRIGGER_MAP = {
        # [VERSION: TRIGGER_AI_WORKER_v1.0] Add AI Worker trigger mapping and lock resolution
        "DAILY_BUILDER": _trigger_daily_builder,
        "MULTI_TF":      _trigger_multi_tf,
        "EOD":           _trigger_eod,
        "REVERSAL":      _trigger_reversal,
        "Wealth Engine": _trigger_wealth_engine,
        "INTRADAY":      _trigger_intraday,
        "MULTIBAGGER":    _trigger_multibagger,
        "AI Worker":     _trigger_ai_worker,
        "PERFORMANCE_TRACKER": _trigger_performance_tracker,
    }
    
    fn = TRIGGER_MAP.get(scanner_key)
    if fn is None:
        return {"status": "error", "message": f"Unknown scanner: {scanner_key}"}
        
    # Check locks synchronously to return immediate HTTP JSON error
    LOCK_MAP = {
        "DAILY_BUILDER": lambda: __import__('daily_builder')._build_lock,
        "MULTI_TF":      lambda: __import__('multi_tf_scanner')._scan_lock,
        "EOD":           lambda: __import__('eod_scanner')._scan_lock,
        "REVERSAL":      lambda: __import__('reversal_scanner')._scan_lock,
        "Wealth Engine": lambda: __import__('wealth_engine')._scan_lock,
        "MULTIBAGGER":   lambda: __import__('multibagger')._scan_lock,
        "AI Worker":     lambda: __import__('ai_worker')._scan_lock,
        "PERFORMANCE_TRACKER": lambda: scanner_execution_lock,
    }
    
    lock_fn = LOCK_MAP.get(scanner_key)
    if lock_fn:
        try:
            lock = lock_fn()
            if lock.locked():
                return {"status": "error", "message": f"{scanner_key} is already actively running!"}
        except Exception:
            pass
    
    # Mark as running
    upsert_scanner_health(scanner_key, status="RUNNING", error_msg="⏳ Manual trigger in progress...")
    
    # Run in background thread so the API returns immediately
    def _run():
        try:
            logger.info(f"🔧 ADMIN MANUAL TRIGGER | Waiting for global lock for {scanner_key}...")
            with scanner_execution_lock:
                logger.info(f"🔧 ADMIN MANUAL TRIGGER | Starting {scanner_key}...")
                stats = fn() or {}
                time.sleep(15)
            now_str = datetime.now(IST).isoformat()
            upsert_scanner_health(scanner_key, status="OK", last_success=now_str,
                                  error_msg=None,
                                  total_count=stats.get("total_count") if isinstance(stats, dict) else None,
                                  processed_count=stats.get("processed_count") if isinstance(stats, dict) else None,
                                  today_alerts=stats.get("today_alerts") if isinstance(stats, dict) else None)
            
            try:
                from database import insert_notification
                # We format a nice summary for the admin notification
                summary = f"Total Scanned: {stats.get('total_count', 'N/A')}" if isinstance(stats, dict) else "Completed."
                # Skip duplicate notification for scanners that emit their own detailed completion notifications
                if scanner_key not in ["DAILY_BUILDER", "EOD", "MULTIBAGGER", "REVERSAL", "MULTI_TF", "Wealth Engine"]:
                    insert_notification("info", f"✅ {scanner_key} Manual Scan Complete", summary)
            except Exception:
                pass
                # Rebuild performance data on scan completion (debounced, async)
            try:
                from performance_tracker import trigger_performance_rebuild
                trigger_performance_rebuild()
            except Exception as pe:
                logger.error(f"Failed to trigger performance rebuild post-manual-scan for {scanner_key}: {pe}")
                
            logger.info(f"✅ ADMIN MANUAL TRIGGER | {scanner_key} completed successfully")
        except RuntimeError as e:
            if "already actively running" in str(e).lower():
                logger.warning(f"⚠️ ADMIN MANUAL TRIGGER | {scanner_key} skipped (already running)")
            else:
                logger.exception(f"❌ ADMIN MANUAL TRIGGER | {scanner_key} FAILED")
                upsert_scanner_health(scanner_key, status="DOWN",
                                      error_msg=f"Manual trigger failed: {str(e)[:400]}")
                try:
                    from database import insert_notification
                    insert_notification("scanner_down", f"🚨 {scanner_key} Manual Scan Failed", f"Error: {str(e)[:200]}")
                except Exception:
                    pass
        except Exception as e:
            logger.exception(f"❌ ADMIN MANUAL TRIGGER | {scanner_key} FAILED")
            upsert_scanner_health(scanner_key, status="DOWN",
                                  error_msg=f"Manual trigger failed: {str(e)[:400]}")
            try:
                from database import insert_notification
                insert_notification("scanner_down", f"🚨 {scanner_key} Manual Scan Failed", f"Error: {str(e)[:200]}")
            except Exception:
                pass
    
    t = threading.Thread(target=_run, name=f"ManualTrigger-{scanner_key}", daemon=True)
    t.start()
    return {"status": "ok", "message": f"{scanner_key} triggered — running in background"}


def _trigger_daily_builder():
    import os
    import json
    try:
        from database import save_system_state
        save_system_state("daily_builder_checkpoint", json.dumps({}))
        if os.path.exists("data/temp_universe.parquet"):
            os.remove("data/temp_universe.parquet")
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning(f"Could not clear daily builder checkpoint: {e}")
        
    from daily_builder import main as build_watchlist
    build_watchlist(force_rebuild=True)
    from watchlist_cache import get_watchlist
    get_watchlist()

def _trigger_multi_tf():
    import multi_tf_scanner
    multi_tf_scanner.start(run_once=True)

def _trigger_eod():
    import eod_scanner
    eod_scanner.start(force=True)

def _trigger_reversal():
    import reversal_scanner
    reversal_scanner.start(force=True)

def _trigger_wealth_engine():
    from wealth_engine import run_wealth_scan
    run_wealth_scan()

def _trigger_intraday():
    import intraday
    intraday.start(run_once=True)

def _trigger_live_scanner():
    import live_scanner
    live_scanner.start(run_once=True)

def _trigger_multibagger():
    import multibagger
    return multibagger.start()

# [VERSION: TRIGGER_AI_WORKER_v1.1] Define _trigger_ai_worker
def _trigger_ai_worker():
    from ai_worker import run_ai_worker_scan_once
    return run_ai_worker_scan_once()

def _trigger_performance_tracker():
    from performance_tracker import build_performance_data
    build_performance_data(force_live_fetch=True)
    return {"total_count": 1, "processed_count": 1}


# ENTRY POINT
# =====================================================================================

if __name__ == "__main__":
    _cleanup_old_scanner_names()
    def handle_sigterm(*args):
        logger.info("🛑 SIGTERM received — container shutting down. Closing gracefuly...")
        # Telegram notification removed (2026-06-17)
        sys.exit(0)

    signal.signal(signal.SIGTERM, handle_sigterm)
    signal.signal(signal.SIGINT, handle_sigterm)


    watchdog_thread = threading.Thread(target=run_watchdog, name="Watchdog", daemon=True)
    watchdog_thread.start()

    if "--worker" in sys.argv:
        logger.info("🛠️ Running in WORKER mode — decoupling Flask dashboard.")
        while True:
            time.sleep(86400)
    else:
        try:
            from dashboard_server import start_dashboard_server
            port = int(os.getenv("PORT", 8080))
            logger.info(f"🌐 Dashboard server binding to port {port} (main thread)")
            start_dashboard_server()
        except ImportError:
            logger.error("❌ dashboard_server.py not found — Railway will show 'failed to respond'")
            watchdog_thread.join()
        except Exception:
            logger.exception("❌ Dashboard server crashed")
            watchdog_thread.join()
