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
import random
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
    "IntradayScanner":    "INTRADAY",
    "LiveScanner":        "1H",
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
    "intraday": (dt_time(9, 32),  dt_time(15, 30)),
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
                    WHERE scanner_name IN ('INTRADAY', '1H', 'EOD', 'REVERSAL', 'Wealth Engine', 'DAILY_BUILDER')
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

def run_intraday_scanner():
    wait_for_window("intraday")
    with scanner_execution_lock:
        import intraday
        intraday.start()
        time.sleep(15)

def run_live_scanner():
    wait_for_window("live")
    with scanner_execution_lock:
        import live_scanner
        live_scanner.start()
        time.sleep(15)

def run_multi_tf_scanner():
    wait_for_window("live")
    with scanner_execution_lock:
        import multi_tf_scanner
        multi_tf_scanner.start()
        time.sleep(15)

def run_performance_tracker():
    """Refreshes dashboard data every 5 minutes all day on weekdays."""
    from performance_tracker import build_performance_data
    from database import upsert_scanner_health
    
    # Always run once on boot to ensure fresh dashboard data, even on weekends
    try:
        build_performance_data()
        upsert_scanner_health(
            "PERFORMANCE_TRACKER", status="OK",
            last_success=datetime.now(IST).isoformat(),
            scheduled_for="Every 5min (all day)"
        )
    except Exception:
        logger.exception("❌ PERFORMANCE TRACKER | Initial boot refresh failed")
        upsert_scanner_health(
            "PERFORMANCE_TRACKER", status="DOWN",
            error_msg="Boot refresh failed",
            scheduled_for="Every 5min (all day)"
        )
        
    from market_utils import is_market_open
    
    while True:
        if is_market_open():
            try:
                build_performance_data()
                upsert_scanner_health(
                    "PERFORMANCE_TRACKER", status="OK",
                    last_success=datetime.now(IST).isoformat(),
                    scheduled_for="Every 5min (all day)"
                )
            except Exception as e:
                logger.exception("❌ PERFORMANCE TRACKER | Refresh failed")
                try:
                    upsert_scanner_health(
                        "PERFORMANCE_TRACKER", status="DOWN",
                        error_msg=str(e)[:500],
                        scheduled_for="Every 5min (all day)"
                    )
                except Exception:
                    pass
        
        time.sleep(300)

_watchlist_build_lock = threading.Lock()

def verify_watchlist_is_pristine() -> bool:
    """
    Check if local disk has today's watchlist.
    Logic: Cache → DB (today) → Delete stale from DB → Fresh rebuild → Save to DB → Start scanner
    """
    from config import WATCHLIST_PATH
    import pandas as pd
    from database import download_parquet_from_db_today, delete_stale_parquet_from_db
    import os
    
    now = datetime.now(IST)
    today_str = now.strftime("%Y-%m-%d")
    
    def is_disk_fresh():
        """Returns True if local disk has watchlist from today."""
        if not os.path.exists(WATCHLIST_PATH): return False
        try:
            df = pd.read_parquet(WATCHLIST_PATH)
            if "Scan Time" in df.columns and not df.empty:
                scan_date_str = str(df["Scan Time"].iloc[0])[:10]
                scan_date = datetime.strptime(scan_date_str, "%Y-%m-%d").date()
                return scan_date >= now.date()
        except Exception:
            pass
        return False

    with _watchlist_build_lock:
        # STEP 1: Check local disk cache
        if is_disk_fresh():
            logger.info(f"✅ [CACHE] Watchlist from today ({today_str}) found on local disk.")
            return True
        
        logger.warning(f"⚠️ [CACHE] Local disk missing/stale watchlist. Checking DB for today's data ({today_str})...")
        
        # STEP 2: Try to restore from DB (TODAY ONLY)
        if download_parquet_from_db_today("daily_builder", WATCHLIST_PATH):
            if is_disk_fresh():
                logger.info(f"✅ [DB] Watchlist from today successfully restored from DB to local disk.")
                from watchlist_cache import get_watchlist
                get_watchlist()
                return True
        
        # STEP 3: DB has old/stale data. Delete it and trigger rebuild.
        logger.warning(f"⚠️ [DB] No today's data in cache or DB! Deleting stale entries from DB...")
        delete_stale_parquet_from_db("daily_builder")
        
        logger.warning(f"⚠️ [REBUILD] Triggering fresh Daily Builder rebuild for {today_str}...")
        try:
            from daily_builder import main as build_watchlist
            build_watchlist(force_rebuild=True)
            from watchlist_cache import get_watchlist
            get_watchlist()
        except Exception as e:
            logger.exception(f"❌ Daily Builder rebuild FAILED (full traceback above): {e}")
            from database import upsert_scanner_health, insert_notification
            upsert_scanner_health("DAILY_BUILDER", status="DOWN", error_msg=str(e)[:500], scheduled_for="01:00 IST")
            try:
                insert_notification(
                    notif_type="scanner_down",
                    title="🚨 Daily Builder FAILED to rebuild",
                    message=f"Watchlist is stale and rebuild failed: {str(e)[:400]}"
                )
            except Exception:
                pass
            return False
        
        # STEP 4: Verify fresh data was created
        if is_disk_fresh():
            logger.info(f"✅ [NEW] Fresh watchlist created for {today_str}. Ready to scan.")
            return True
        else:
            logger.error(f"❌ [NEW] Fresh watchlist created but failed freshness check!")
            return False

def block_until_watchlist_ready():
    """Blocks the thread until the watchlist is pristine."""
    from database import upsert_scanner_health
    first_block = True
    while not verify_watchlist_is_pristine():
        if first_block:
            logger.warning("⏳ Watchlist not ready. Updating dashboard to show scanners as WAITING...")
            for scanner in ["Wealth Engine", "MULTI-TF LADDER", "REVERSAL", "EOD"]:
                upsert_scanner_health(
                    scanner,
                    status="WAITING",
                    error_msg="Blocked: Waiting for Daily Builder to provide fresh fundamental data."
                )
            first_block = False
        logger.warning("⏳ Retrying watchlist check in 60 seconds...")
        time.sleep(60)
    if not first_block:
        logger.info("✅ Watchlist is pristine. Unblocking scanners.")


# =====================================================================================
# SINGLE-SHOT RUNNERS — EOD & Reversal
#
# Rules:
#   • Runs between 18:30 IST and midnight.
#   • If the scan raises an exception  → send Telegram crash alert, and RETRY in 5 minutes.
#   • Once it finishes successfully    → do NOT run again until the next day's window.
# =====================================================================================

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
                scheduled_for="06:30 IST"
            )
            # [VERSION: TRIGGER_PERF_REBUILD_v1.1] Rebuild performance data on scheduled EOD completion
            try:
                from market_utils import is_market_open
                if is_market_open():
                    from performance_tracker import build_performance_data
                    build_performance_data(fast_mode=True)
                    logger.info("📈 Completed post-EOD scheduled scan performance data rebuild")
                else:
                    logger.info("⏭️ Skipping post-EOD performance rebuild because market is strictly CLOSED (User enforced).")
            except Exception as pe:
                logger.error(f"Failed to rebuild performance data post-EOD: {pe}")
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
                    scheduled_for="06:30 IST"
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
                scheduled_for="06:30 IST"
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
                scheduled_for="06:30 IST"
            )
            # [VERSION: TRIGGER_PERF_REBUILD_v1.1] Rebuild performance data on scheduled REVERSAL completion
            try:
                from market_utils import is_market_open
                if is_market_open():
                    from performance_tracker import build_performance_data
                    build_performance_data(fast_mode=True)
                    logger.info("📈 Completed post-REVERSAL scheduled scan performance data rebuild")
                else:
                    logger.info("⏭️ Skipping post-REVERSAL performance rebuild because market is strictly CLOSED (User enforced).")
            except Exception as pe:
                logger.error(f"Failed to rebuild performance data post-REVERSAL: {pe}")
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
                    scheduled_for="06:30 IST"
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
                scheduled_for="06:30 IST"
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


def run_bayesian_loop():
    """Runs the Bayesian Updater loop. Triggers immediately on boot, then waits 24h."""
    from bayesian_updater import run_bayesian_updater
    while True:
        try:
            logger.info("🧠 BAYESIAN UPDATER | Waking up to process trades...")
            run_bayesian_updater()
        except Exception as e:
            logger.exception("❌ BAYESIAN UPDATER | Crashed")
            # Telegram notification removed (2026-06-17)
        
        # Run daily (86400 seconds)
        logger.info("🧠 BAYESIAN UPDATER | Sleeping for 24h")
        time.sleep(86400)


# =====================================================================================
# TIME-BASED SCHEDULER
# =====================================================================================
def run_system_scheduler():
    """
    Custom time-based scheduler (replaces schedule library for reliability).
    
    Timing:
    - 1:00 AM: Daily Builder (fresh watchlist)
    - 1:05 AM: Wealth Engine (initial setup with fresh watchlist)
    - 8:30 AM: Verify file readiness
    - Market hours (9:15 AM - 3:30 PM): Wealth Engine hourly at :05 to generate new buy signals
    """
    from daily_builder import build_watchlist
    from wealth_engine import run_wealth_scan
    from config import WATCHLIST_PATH, DATA_DIR
    from database import upsert_scanner_health
    
    WEALTH_PATH = os.path.join(DATA_DIR, "elite_wealth_system.parquet")
    
    # Track which tasks have run today
    daily_builder_ran = False
    wealth_initial_ran = False
    verify_scans_ran = False
    last_wealth_market_run = None  # Track last market-hours wealth run

    def safe_run_daily_builder():
        """Helper to run the builder and update the memory cache."""
        try:
            import os
            import pandas as pd
            from config import WATCHLIST_PATH
            
            already_fresh = False
            if os.path.exists(WATCHLIST_PATH):
                try:
                    df = pd.read_parquet(WATCHLIST_PATH)
                    if "Scan Time" in df.columns and not df.empty:
                        scan_date_str = str(df["Scan Time"].iloc[0])[:10]
                        if datetime.strptime(scan_date_str, "%Y-%m-%d").date() >= datetime.now(IST).date():
                            already_fresh = True
                except Exception:
                    pass
            
            if already_fresh:
                logger.info("🕒 SCHEDULER | [1:00 AM] Watchlist already fresh for today. Skipping redundant build.")
            else:
                logger.info("🕒 SCHEDULER | [1:00 AM] Triggering Daily Builder")
                from daily_builder import main as build_watchlist
                build_watchlist()
            
            # Update memory cache
            from watchlist_cache import get_watchlist
            get_watchlist()
            
            # Mark success
            now_str = datetime.now(IST).isoformat()
            try:
                upsert_scanner_health(
                    "DAILY_BUILDER",
                    status="OK",
                    last_success=now_str,
                    scheduled_for="01:00 IST"
                )
            except Exception:
                logger.warning("⚠️ Could not update Daily Builder health status")
            logger.info("✅ Daily Builder completed successfully")
            return True
        except Exception as e:
            logger.exception("❌ SCHEDULER | Daily Builder crashed")
            # Telegram notifications disabled (2026-06-17)
            try:
                upsert_scanner_health(
                    "DAILY_BUILDER",
                    status="DOWN",
                    error_msg=str(e)[:500],
                    scheduled_for="01:00 IST"
                )
            except Exception:
                pass
            return False

    def safe_run_wealth_scan_initial():
        """Run Wealth Engine at 1:05 AM with fresh watchlist."""
        try:
            logger.info("🕒 SCHEDULER | [1:05 AM] Triggering Wealth Engine (initial setup)")
            run_wealth_scan()
            
            # Mark success
            now_str = datetime.now(IST).isoformat()
            upsert_scanner_health(
                "Wealth Engine",
                status="OK",
                last_success=now_str,
                scheduled_for="01:05 IST"
            )
            logger.info("✅ Wealth Engine (initial) completed successfully")
            return True
        except Exception as e:
            logger.exception("❌ SCHEDULER | Wealth Engine (initial) crashed")
            upsert_scanner_health(
                "Wealth Engine",
                status="DOWN",
                error_msg=str(e)[:500],
                scheduled_for="01:05 IST"
            )
            return False

    def safe_run_wealth_market_hours():
        """Run Wealth Engine during market hours (5-min loop from 9:15 AM to 3:30 PM)."""
        nonlocal last_wealth_market_run
        try:
            now = datetime.now(IST)
            # Only run once per 5 minutes (300 seconds)
            if last_wealth_market_run and (now - last_wealth_market_run).total_seconds() < 300:
                return False
            
            logger.info(f"🕒 SCHEDULER | [{now.strftime('%H:%M')}] Triggering Wealth Engine (market hours - 5min loop)")
            run_wealth_scan()
            
            # Run exit monitor in isolated try/except so a crash here
            # does NOT mark Wealth Engine as DOWN (Issue #5 from audit)
            try:
                logger.info(f"🕒 SCHEDULER | [{now.strftime('%H:%M')}] Triggering Multibagger Exit Monitor (market hours - 5min loop)")
                from multibagger import run_standalone_exit_monitor
                run_standalone_exit_monitor()
            except Exception as exit_err:
                logger.exception(f"❌ SCHEDULER | Multibagger Exit Monitor crashed (Wealth Engine unaffected): {exit_err}")
            
            last_wealth_market_run = now
            # Mark success
            now_str = now.isoformat()
            upsert_scanner_health(
                "Wealth Engine",
                status="OK",
                last_success=now_str,
                scheduled_for="Every 5min (9:15 AM - 3:30 PM)"
            )
            logger.info("✅ Wealth Engine (market hours) completed successfully")
            return True
        except Exception as e:
            logger.exception("❌ SCHEDULER | Wealth Engine (market hours) crashed")
            upsert_scanner_health(
                "Wealth Engine",
                status="DOWN",
                error_msg=str(e)[:500],
                scheduled_for="Every 5min (9:15 AM - 3:30 PM)"
            )
            return False

    def verify_scans():
        """Verify file readiness at 8:30 AM."""
        logger.info("🕒 SCHEDULER | [8:30 AM] Verifying file readiness for today's scan")
        now = datetime.now(IST)
        today_str = now.strftime("%Y-%m-%d")

        # 1. Verify Watchlist (with full date-aware cache/DB/rebuild logic)
        logger.info(f"🕒 SCHEDULER | Step 1: Verifying watchlist freshness for {today_str}")
        block_until_watchlist_ready()

        # 2. Verify Wealth Engine
        try:
            if not os.path.exists(WEALTH_PATH):
                logger.warning(f"⚠️ Wealth system missing from disk. Attempting DB restore for {today_str}...")
                try:
                    from database import download_parquet_from_db_today
                    restored = download_parquet_from_db_today("wealth_engine", WEALTH_PATH)
                    if restored and os.path.exists(WEALTH_PATH):
                        logger.info("✅ Wealth system restored from DB (today's data).")
                    else:
                        logger.warning("⚠️ Wealth system missing from DB too! Forcing fresh run.")
                        safe_run_wealth_scan_initial()
                except Exception as e:
                    logger.exception(f"Failed to restore wealth from DB; forcing run: {e}")
                    safe_run_wealth_scan_initial()
            else:
                mtime_ts = os.path.getmtime(WEALTH_PATH)
                mtime = datetime.fromtimestamp(mtime_ts, IST)
                if mtime.date() < now.date():
                    logger.warning(f"⚠️ Wealth system is from {mtime.date()}, not today ({today_str}). Attempting DB restore...")
                    try:
                        from database import download_parquet_from_db_today, delete_stale_parquet_from_db
                        restored = download_parquet_from_db_today("wealth_engine", WEALTH_PATH)
                        if restored and os.path.exists(WEALTH_PATH):
                            logger.info("✅ Wealth system restored from DB (today's data).")
                        else:
                            logger.warning("⚠️ Wealth system not in today's DB data. Deleting old entries and forcing run.")
                            delete_stale_parquet_from_db("wealth_engine")
                            safe_run_wealth_scan_initial()
                    except Exception as e:
                        logger.exception(f"Failed to restore wealth; forcing run: {e}")
                        safe_run_wealth_scan_initial()
                else:
                    logger.info(f"✅ Wealth system from today ({mtime.date()}) is fresh.")
        except Exception as e:
            logger.exception(f"Failed to verify wealth system: {e}")

        logger.info("✅ SCHEDULER | [8:30 AM] File readiness verification complete")

    logger.info("🕒 SCHEDULER | Started (custom time-based scheduler)")
    
    # Run boot verification
    verify_scans()

    # Main scheduler loop
    while True:
        now = datetime.now(IST)
        
        # Weekdays only
        if now.weekday() < 5:  # Mon-Fri
            # 1:00 AM - Daily Builder
            if now.hour == 1 and now.minute >= 0 and not daily_builder_ran:
                daily_builder_ran = True
                safe_run_daily_builder()
            elif now.hour != 1:
                daily_builder_ran = False
            
            # Refresh now in case daily builder blocked for a long time
            now = datetime.now(IST)
            
            # 1:30 AM - Wealth Engine (initial)
            if now.hour == 1 and now.minute >= 30 and not wealth_initial_ran:
                wealth_initial_ran = True
                safe_run_wealth_scan_initial()
            elif now.hour != 1:
                wealth_initial_ran = False
            
            now = datetime.now(IST)
            
            # 8:30 AM - Verify Scans
            if now.hour == 8 and now.minute >= 30 and not verify_scans_ran:
                verify_scans_ran = True
                verify_scans()
            elif now.hour != 8:
                verify_scans_ran = False
            
            from market_utils import is_market_open
            # Market hours: Wealth Engine every 5 minutes from 9:15 AM - 3:30 PM
            if is_market_open(now):
                safe_run_wealth_market_hours()
                check_scanner_staleness(now)
                
        # Sunday only
        time.sleep(30)  # Check every 30 seconds


def check_scanner_staleness(now):
    """Check if any active scanner has gone stale (no heartbeat in expected cadence × 3).
    
    Runs during market hours only. If a scanner's last_success is too old,
    marks it DOWN and sends a Telegram + in-app notification.
    """
    # Expected max gap (in minutes) for each scanner before it's considered stale
    SCANNER_CADENCE = {
        "MULTI_TF":            20,   # runs every 5 min → stale if no heartbeat in 20 min
        "PERFORMANCE_TRACKER": 20,   # runs every 5 min → stale if no heartbeat in 20 min
        "Wealth Engine":       20,   # runs every 5 min during market hours
        "DAILY_BUILDER":       "DAILY",
        "EOD":                 "DAILY",
        "REVERSAL":            "DAILY"
    }
    
    # Throttle: only run this check every 15 minutes
    if not hasattr(check_scanner_staleness, '_last_check'):
        check_scanner_staleness._last_check = None
    
    if check_scanner_staleness._last_check and (now - check_scanner_staleness._last_check).total_seconds() < 900:
        return
    check_scanner_staleness._last_check = now
    
    try:
        from database import get_all_scanner_health, upsert_scanner_health, insert_notification
        health_rows = get_all_scanner_health()
        
        for row in health_rows:
            sc = row.get("scanner_name")
            if sc not in SCANNER_CADENCE:
                continue
            
            # Skip if already DOWN (don't spam)
            if row.get("status") == "DOWN":
                continue
                
            last_success = row.get("last_success")
            if not last_success:
                continue
            
            # Parse last_success timestamp
            try:
                if isinstance(last_success, str):
                    from datetime import datetime as dt
                    ls = dt.fromisoformat(last_success.replace('Z', '+00:00'))
                    if ls.tzinfo is None:
                        ls = ls.replace(tzinfo=IST)
                else:
                    ls = last_success
                    if ls.tzinfo is None:
                        ls = ls.replace(tzinfo=IST)
                
                cadence = SCANNER_CADENCE[sc]
                is_stale = False
                stale_msg = ""
                gap_minutes = (now - ls).total_seconds() / 60.0
                
                if cadence == "DAILY":
                    # Daily scanners must succeed at least once today by 11:30 PM
                    # (For DAILY_BUILDER, it should succeed by 2 AM, but we can just check if it succeeded today by 11:30 PM)
                    if now.hour == 23 and now.minute >= 30:
                        if ls.date() != now.date():
                            is_stale = True
                            stale_msg = f"Stale: Did not complete successfully today (last success: {ls.strftime('%Y-%m-%d')})"
                else:
                    max_gap = cadence
                    if gap_minutes > max_gap:
                        is_stale = True
                        stale_msg = f"Stale: No heartbeat in {int(gap_minutes)} minutes (expected every {max_gap // 3} min)"
                
                if is_stale:
                    logger.warning(f"🕐 STALENESS DETECTED | {sc} | {stale_msg}")
                    
                    upsert_scanner_health(sc, status="DOWN", error_msg=stale_msg)
                    
                    # Telegram alert
                    try:
                        from telegram_engine import queue_telegram_message
                        msg = (
                            f"🕐 <b>SCANNER STALE</b>\n\n"
                            f"📛 <b>Scanner:</b> {sc}\n"
                            f"⏱ <b>Last heartbeat:</b> {int(gap_minutes)} min ago\n"
                            f"🕐 <b>Time:</b> {now.strftime('%H:%M:%S IST')}"
                        )
                        queue_telegram_message(msg)
                    except Exception:
                        logger.exception(f"❌ Could not send staleness Telegram for {sc}")
                    
                    # In-app notification
                    try:
                        insert_notification(
                            notif_type="scanner_stale",
                            title=f"🕐 {sc} is STALE",
                            message=stale_msg
                        )
                    except Exception:
                        pass
                        
            except Exception:
                logger.warning(f"Could not parse last_success for {sc}: {last_success}")
                
    except Exception:
        logger.exception("❌ Staleness check failed")


# =====================================================================================
# SELF-HEALING WATCHDOG  (runs in background thread)
#
# EOD and REVERSAL are intentionally excluded from auto-restart — they run once and
# exit.  The watchdog will see completed_cleanly=True and simply drop them.
# =====================================================================================

# [VERSION: TRIGGER_AI_WORKER_v1.2] Uncomment AI Worker thread and imports to enable background concall worker daemon
from ai_worker import run_worker_loop
from pledge_worker import worker_loop as run_pledge_loop

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
                # [VERSION: TRIGGER_PERF_REBUILD_v1.1] Rebuild performance data on scheduled MULTIBAGGER completion
                try:
                    from performance_tracker import build_performance_data
                    build_performance_data(fast_mode=True)
                    logger.info("📈 Completed post-MULTIBAGGER scheduled scan performance data rebuild")
                except Exception as pe:
                    logger.error(f"Failed to rebuild performance data post-MULTIBAGGER: {pe}")
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
                    scheduled_for="Sunday 15:00 IST"
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
        "1H":            _trigger_live_scanner,
        "MULTIBAGGER":    _trigger_multibagger,
        "AI Worker":     _trigger_ai_worker,
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
        "INTRADAY":      lambda: __import__('intraday')._scan_lock,
        "1H":            lambda: __import__('live_scanner')._scan_lock,
        "MULTIBAGGER":   lambda: __import__('multibagger')._scan_lock,
        "AI Worker":     lambda: __import__('ai_worker')._scan_lock,
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
            
            # [VERSION: TRIGGER_PERF_REBUILD_v1.0] Rebuild performance data on manual trigger completion
            try:
                from market_utils import is_market_open
                if is_market_open():
                    from performance_tracker import build_performance_data
                    build_performance_data(fast_mode=True)
                    logger.info(f"📈 Completed post-scan performance data rebuild for {scanner_key}")
                else:
                    logger.info(f"⏭️ Skipping post-scan performance rebuild because market is strictly CLOSED (User enforced).")
            except Exception as pe:
                logger.error(f"Failed to rebuild performance data post-scan for {scanner_key}: {pe}")
            
            try:
                from database import insert_notification
                # We format a nice summary for the admin notification
                summary = f"Total Scanned: {stats.get('total_count', 'N/A')}" if isinstance(stats, dict) else "Completed."
                # Skip duplicate notification for scanners that emit their own detailed completion notifications
                if scanner_key not in ["DAILY_BUILDER", "EOD", "MULTIBAGGER", "REVERSAL", "MULTI_TF", "Wealth Engine"]:
                    insert_notification("info", f"✅ {scanner_key} Manual Scan Complete", summary)
            except Exception:
                pass
                
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
