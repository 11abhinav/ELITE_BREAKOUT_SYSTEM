# =====================================================================================
# app/main.py  — SELF-HEALING ORCHESTRATOR
#
# RAILWAY FIX: Flask (dashboard) runs in the MAIN thread so Railway's health check
# gets a response immediately. The watchdog loop and all scanners run as daemon
# threads in the background. This is the correct pattern for Railway deployments.
#
# EOD / REVERSAL run ONCE at 21:00 IST. They are NOT auto-restarted on crash.
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
import random
from typing import Optional, Dict, Any, List
import pandas as pd
from memory_profiler import MemoryProfiler
from forensics import forensics

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
# [VERSION: LOGGING_STDOUT_FIX_v1.0] Route logs to stdout to prevent Railway interpreting all INFO as ERROR
logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s", datefmt="%Y-%m-%d %H:%M:%S", stream=sys.stdout)

from db_logger import install_db_logger
install_db_logger()

# [VERSION: PERF_PROFILER_v1.0] Capture process startup timestamp for boot latency telemetry.
# This lets us log how long the full boot sequence takes (imports, DB init, diagnostics).
import time as _time
_PROCESS_START_TIME = _time.monotonic()

logger = logging.getLogger(__name__)

# Print high-visibility deployment version banner on startup
try:
    from config import SYSTEM_DEPLOYMENT_VERSION
    logger.info("======================================================================")
    logger.info(f"🚀 DEPLOYMENT VERSION: {SYSTEM_DEPLOYMENT_VERSION}")
    logger.info(f"📅 Server Startup Time: {datetime.now(IST).strftime('%Y-%m-%d %H:%M:%S IST')}")
    logger.info("======================================================================")
except Exception:
    pass

# ── Phase 2 Dataset Registry: Self-Register Consumers ────────────────────────
try:
    from data_registry import registry
    registry.register_consumer("watchlist", "WealthEngine")
    registry.register_consumer("price_1d", "WealthEngine")
    registry.register_consumer("fundamentals_quarterly", "WealthEngine")
    
    registry.register_consumer("watchlist", "MultiTFScanner")
    registry.register_consumer("price_1m", "MultiTFScanner")
    registry.register_consumer("price_15m", "MultiTFScanner")
    registry.register_consumer("price_1d", "MultiTFScanner")
    
    registry.register_consumer("watchlist", "EODScanner")
    registry.register_consumer("price_1d", "EODScanner")
    
    registry.register_consumer("watchlist", "PullbackScanner")
    registry.register_consumer("price_1d", "PullbackScanner")
    
    registry.register_consumer("watchlist", "ReversalScanner")
    registry.register_consumer("price_1d", "ReversalScanner")
    
    # Run graph validation at startup
    registry.validate()
    logger.info("✅ Dataset Registry graph validation passed.")
except Exception as e:
    logger.critical(f"🚨 Dataset Registry initialization failed: {e}")
    sys.exit(1)

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

# [VERSION: FYERS_SCOPE_CHECK_v1.0] Perform startup verification of Fyers Historical Data API scope.
# If code -403 is returned, opens circuit breaker immediately & records PERMISSION_DENIED status in DB.
try:
    from data_providers.fyers_fetcher import verify_fyers_startup_scope
    verify_fyers_startup_scope()
except Exception as _fyers_scope_err:
    logger.warning(f"⚠️ Fyers startup scope verification failed: {_fyers_scope_err}")

# [VERSION: PERF_PROFILER_v1.0] Log total boot latency from process start to first
# scanner-ready checkpoint. This is a passive metric — no behavior is changed.
_boot_elapsed = _time.monotonic() - _PROCESS_START_TIME
logger.info(f"⏱  [STARTUP] Boot sequence complete in {_boot_elapsed:.1f}s (imports + DB init + diagnostics)")
try:
    from symbol_router import symbol_router
    symbol_router.load_persisted_routes()
except Exception as _router_err:
    logger.warning(f"⚠️ Failed to restore symbol router state on boot: {_router_err}")
# ─────────────────────────────────────────────────────────────────────────────

# Map watchdog thread names to dashboard database keys
THREAD_TO_SCANNER = {
    "EODScanner":         "EOD",
    "PullbackScanner":    "PULLBACK",
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

# [VERSION: SCHEDULER_REFINEMENT_v1.0]
# ── Scan windows (start_time, end_time) ─────────────────────────────────────────────
WINDOWS = {
    "multi_tf": (dt_time(9, 30), dt_time(15, 0)),
    "eod":      (dt_time(18, 0), dt_time(23, 59, 59)),
    "reversal": (dt_time(18, 0), dt_time(23, 59, 59)),
}


# =====================================================================================
# HELPERS
# =====================================================================================



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
        
        target_dt = datetime.combine(now.date(), start_time).replace(tzinfo=IST)
        rem_secs = max(0, int((target_dt - now).total_seconds()))
        rem_m, rem_s = divmod(rem_secs, 60)
        logger.info(f"⏳ [{name.upper()}] Scan window opens at {start_time.strftime('%H:%M')} IST (in {rem_m}m {rem_s}s)... Checking again in 60s")
        time.sleep(60)

def wait_for_bhavcopy_or_fallback(name: str):
    """Block until today's Bhavcopy is available, or fallback if it's past 11 PM."""
    from delivery_data import fetch_delivery_data
    from database import upsert_scanner_health
    first_wait = True
    while True:
        now = datetime.now(IST)
        if now.weekday() >= 5:
            return  # Weekend, no bhavcopy published
            
        try:
            # fetch_delivery_data handles caching and retries internally
            delivery_map = fetch_delivery_data(now.date())
            if delivery_map:
                logger.info(f"[{name}] ✅ Today's Bhavcopy is available!")
                return
        except Exception as e:
            logger.warning(f"[{name}] Failed to fetch bhavcopy: {e}")
            
        if now.hour >= 21 or (now.hour == 20 and now.minute >= 30):
            logger.warning(f"[{name}] ⚠️ It's {now.strftime('%H:%M')} and today's Bhavcopy is still missing. Using fallback (yesterday).")
            return
            
        logger.info(f"[{name}] ⏳ Today's Bhavcopy not yet available. Waiting 5 mins...")
        
        # [VERSION: BHAVCOPY_UI_STATUS] Expose the blocking state to the UI so users don't think the scanner is dead
        if first_wait and name in ("EVENING_SCANNERS", "PULLBACK"):
            for scanner_name in ["EOD", "REVERSAL", "PULLBACK"]:
                upsert_scanner_health(
                    scanner_name, 
                    status="IDLE", 
                    error_msg="Blocked: Waiting for NSE to publish today's Bhavcopy (Delivery Data)..."
                )
            first_wait = False
            
        time.sleep(300)


# =====================================================================================
# WATCHLIST PRE-FLIGHT
# =====================================================================================
from config import WATCHLIST_PATH
import threading as _threading

_watchlist_ready = _threading.Event()

def _build_watchlist_background():
    t_name = threading.current_thread().name
    logger.info(f"🚀 [BACKGROUND WORKER START] Worker='{t_name}' | InitiatedBy='MainOrchestrator' | Action='Building or restoring fundamental watchlist'")
    _t_start = time.perf_counter()
    with MemoryProfiler("Startup - Watchlist", force_gc_cleanup=True):
        if os.path.exists(WATCHLIST_PATH):
            logger.info(f"✅ Watchlist found | {WATCHLIST_PATH}")
            _watchlist_ready.set()
            dur_s = time.perf_counter() - _t_start
            logger.info(f"✅ [BACKGROUND WORKER COMPLETE] Worker='{t_name}' | Action='Watchlist check complete' | Duration={dur_s:.2f}s")
            return
        logger.info("📋 Watchlist missing | Attempting to restore or build in background thread...")
        try:
            from watchlist_cache import get_watchlist
            get_watchlist()
            if os.path.exists(WATCHLIST_PATH):
                _watchlist_ready.set()
            dur_s = time.perf_counter() - _t_start
            logger.info(f"✅ [BACKGROUND WORKER COMPLETE] Worker='{t_name}' | Action='Watchlist build complete' | Duration={dur_s:.2f}s")
        except Exception as ex:
            logger.exception(f"❌ [BACKGROUND WORKER FAIL] Worker='{t_name}' | Action='Daily builder failed' | Error={ex}")

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

class InstrumentedLock:
    """
    Central process-level mutex protecting scanner execution.
    
    GUARANTEES:
      1. Protects critical sections that mutate shared scanner state or persist scanner results,
         ensuring those operations are not executed concurrently.
      2. Excludes long non-mutating wait loops (e.g. Bhavcopy wait, cool-down sleeps).
    """
    def __init__(self, name="scanner_execution_lock"):
        from lock_utils import ProcessLock
        self.lock = ProcessLock("global_scanner_lock") if name == "scanner_execution_lock" else ProcessLock(name)
        self.name = name
        self.acquisitions_count = 0
        self.total_wait_seconds = 0.0
        self.max_wait_seconds = 0.0
        self.total_hold_seconds = 0.0
        self.max_hold_seconds = 0.0
        self.contention_events_count = 0
        self._stats_lock = threading.Lock()
        self._acquire_time = 0.0

    def acquire(self, blocking: bool = True, timeout: float = -1) -> bool:
        wait_start = time.time()
        acquired = self.lock.acquire(blocking=blocking, timeout=timeout)
        if acquired:
            wait_time = time.time() - wait_start
            self._acquire_time = time.time()
            
            from config import LOCK_WAIT_WARNING_SECONDS
            with self._stats_lock:
                self.acquisitions_count += 1
                self.total_wait_seconds += wait_time
                if wait_time > self.max_wait_seconds:
                    self.max_wait_seconds = wait_time
                if wait_time > LOCK_WAIT_WARNING_SECONDS:
                    self.contention_events_count += 1
                    logger.warning(f"⚠️ [LOCK_CONTENTION] {self.name} wait time exceeded threshold: {wait_time:.2f}s (Thread: {threading.current_thread().name})")
                else:
                    logger.info(f"[LOCK] {self.name} acquired by {threading.current_thread().name} (Wait: {wait_time:.3f}s)")
        return acquired

    def release(self):
        hold_time = time.time() - getattr(self, "_acquire_time", time.time())
        self.lock.release()
        
        from config import LOCK_HOLD_WARNING_SECONDS
        with self._stats_lock:
            self.total_hold_seconds += hold_time
            if hold_time > self.max_hold_seconds:
                self.max_hold_seconds = hold_time
            if hold_time > LOCK_HOLD_WARNING_SECONDS:
                logger.warning(f"⚠️ [LOCK_LONG_HOLD] {self.name} hold time exceeded threshold: {hold_time:.2f}s (Thread: {threading.current_thread().name})")
            else:
                logger.info(f"[LOCK] {self.name} released by {threading.current_thread().name} (Hold: {hold_time:.3f}s)")

    def locked(self) -> bool:
        return self.lock.locked()

    def __enter__(self):
        self.acquire(blocking=True)
        return self
        
    def __exit__(self, exc_type, exc_val, exc_tb):
        self.release()

    def get_stats(self) -> dict:
        with self._stats_lock:
            avg_wait = self.total_wait_seconds / self.acquisitions_count if self.acquisitions_count > 0 else 0.0
            avg_hold = self.total_hold_seconds / self.acquisitions_count if self.acquisitions_count > 0 else 0.0
            return {
                "acquisitions_count": self.acquisitions_count,
                "contention_events_count": self.contention_events_count,
                "avg_wait_seconds": round(avg_wait, 3),
                "max_wait_seconds": round(self.max_wait_seconds, 3),
                "avg_hold_seconds": round(avg_hold, 3),
                "max_hold_seconds": round(self.max_hold_seconds, 3),
            }

# GLOBAL LOCK to prevent concurrent scanner execution (fixes Fyers/Yahoo rate limits)
scanner_execution_lock = InstrumentedLock("scanner_execution_lock")
wealth_execution_lock = InstrumentedLock("wealth_execution_lock")

def format_duration(seconds: Optional[float]) -> str:
    if seconds is None:
        return "0.0s"
    if seconds < 60:
        return f"{seconds:.1f}s"
    minutes = int(seconds // 60)
    secs = int(seconds % 60)
    return f"{minutes}m {secs}s"

def _run_performance_tracker_single():
    """Runs a single pass of the performance tracker dashboard refresh."""
    from performance_tracker import build_performance_data
    from database import upsert_scanner_health, is_scanner_stopped
    if is_scanner_stopped("PERFORMANCE_TRACKER"):
        logger.info("⏭️ PERFORMANCE_TRACKER is PAUSED by Admin. Skipping Alerts Exit Monitor pass.")
        return
    start_time = time.time()
    try:
        from telemetry_manager import telemetry
        telemetry.log_scheduler_event("PERFORMANCE_TRACKER", "CYCLE_START")
        build_performance_data()
        duration_sec = round(time.time() - start_time, 1)
        logger.info(f"✅ PERFORMANCE TRACKER | Refresh completed in {format_duration(duration_sec)}")
        upsert_scanner_health(
            "PERFORMANCE_TRACKER", status="OK",
            last_success=datetime.now(IST).isoformat(),
            scheduled_for="Every 5min (market hours)",
            duration_seconds=duration_sec
        )
        telemetry.log_scheduler_event("PERFORMANCE_TRACKER", "CYCLE_COMPLETE")
    except Exception as e:
        if "actively running" not in str(e).lower():
            logger.exception("❌ PERFORMANCE TRACKER | Refresh failed")
            from telemetry_manager import telemetry
            telemetry.log_scheduler_event("PERFORMANCE_TRACKER", "CYCLE_FAILED", error=str(e))
            try:
                upsert_scanner_health(
                    "PERFORMANCE_TRACKER", status="DOWN",
                    error_msg=str(e)[:500],
                    scheduled_for="Every 5min (market hours)"
                )
            except Exception:
                pass

def _run_multibagger_exit_single():
    """Runs a single pass of the Multibagger Exit Monitor."""
    from database import upsert_scanner_health, is_scanner_stopped
    if is_scanner_stopped("MULTIBAGGER_EXIT"):
        logger.info("⏭️ MULTIBAGGER_EXIT is PAUSED by Admin. Skipping Multibagger Exit Monitor pass.")
        return
    start_time = time.time()
    try:
        logger.info("🕒 SCHEDULER | Triggering Multibagger Exit Monitor (Single Pass)")
        from telemetry_manager import telemetry
        telemetry.log_scheduler_event("MULTIBAGGER_EXIT", "CYCLE_START")
        from multibagger import run_standalone_exit_monitor
        run_standalone_exit_monitor()
        duration_sec = round(time.time() - start_time, 1)
        logger.info(f"✅ MULTIBAGGER EXIT | Completed in {format_duration(duration_sec)}")
        upsert_scanner_health(
            "MULTIBAGGER_EXIT", status="OK",
            last_success=datetime.now(IST).isoformat(),
            scheduled_for="Every 15min (market hours)",
            duration_seconds=duration_sec
        )
        telemetry.log_scheduler_event("MULTIBAGGER_EXIT", "CYCLE_COMPLETE")
    except Exception as e:
        logger.exception(f"❌ SCHEDULER | Multibagger Exit Monitor crashed: {e}")
        from telemetry_manager import telemetry
        telemetry.log_scheduler_event("MULTIBAGGER_EXIT", "CYCLE_FAILED", error=str(e))
        if "actively running" not in str(e):
            try:
                upsert_scanner_health("MULTIBAGGER_EXIT", status="DOWN", error_msg=str(e)[:500], scheduled_for="Every 15min (market hours)")
            except Exception:
                pass

def run_multi_tf_scanner():
    wait_for_window("multi_tf")
    import multi_tf_scanner
    multi_tf_scanner.start()
    time.sleep(15)

def run_performance_tracker():
    """Refreshes dashboard data every 5 minutes all day on weekdays."""
    from performance_tracker import build_performance_data
    from database import upsert_scanner_health
    
    # Always run once on boot to ensure fresh dashboard data, even on weekends
    try:
        from telemetry_manager import telemetry
        telemetry.log_scheduler_event("PERFORMANCE_TRACKER_BOOT", "CYCLE_START")
        start_pt_boot = time.time()
        build_performance_data()
        dur_pt_boot = round(time.time() - start_pt_boot, 1)
        upsert_scanner_health(
            "PERFORMANCE_TRACKER", status="OK",
            last_success=datetime.now(IST).isoformat(),
            scheduled_for="Every 5min (market hours)",
            duration_seconds=dur_pt_boot
        )
        telemetry.log_scheduler_event("PERFORMANCE_TRACKER_BOOT", "CYCLE_COMPLETE")
    except Exception as e:
        if "actively running" in str(e).lower():
            pass
        else:
            logger.exception("❌ PERFORMANCE TRACKER | Initial boot refresh failed")
            upsert_scanner_health(
                "PERFORMANCE_TRACKER", status="DOWN",
            error_msg="Boot refresh failed",
            scheduled_for="Every 5min (market hours)"
        )
        telemetry.log_scheduler_event("PERFORMANCE_TRACKER_BOOT", "CYCLE_FAILED", error=str(e))
        
    from market_utils import is_market_open
    
    while True:
        if is_market_open():
            try:
                from telemetry_manager import telemetry
                telemetry.log_scheduler_event("PERFORMANCE_TRACKER", "CYCLE_START")
                start_pt_loop = time.time()
                build_performance_data()
                dur_pt_loop = round(time.time() - start_pt_loop, 1)
                upsert_scanner_health(
                    "PERFORMANCE_TRACKER", status="OK",
                    last_success=datetime.now(IST).isoformat(),
                    scheduled_for="Every 5min (market hours)",
                    duration_seconds=dur_pt_loop
                )
                telemetry.log_scheduler_event("PERFORMANCE_TRACKER", "CYCLE_COMPLETE")
            except Exception as e:
                if "actively running" in str(e).lower():
                    continue
                logger.exception("❌ PERFORMANCE TRACKER | Refresh failed")
                from telemetry_manager import telemetry
                telemetry.log_scheduler_event("PERFORMANCE_TRACKER", "CYCLE_FAILED", error=str(e))
                try:
                    upsert_scanner_health(
                        "PERFORMANCE_TRACKER", status="DOWN",
                        error_msg=str(e)[:500],
                        scheduled_for="Every 5min (market hours)"
                    )
                except Exception:
                    pass
        
        time.sleep(900)  # [ARCHITECTURAL FIX] Reduced from 5m (300) to 15m (900) to lower API strain

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
            if "actively running" in str(e).lower():
                logger.info("⏳ Daily Builder is actively running.")
                return False
            logger.exception(f"❌ Daily Builder rebuild FAILED (full traceback above): {e}")
            from database import upsert_scanner_health, insert_notification
            upsert_scanner_health("DAILY_BUILDER", status="DOWN", error_msg=str(e)[:500], scheduled_for="05:00 IST")
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
                    status="IDLE",
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
#   • Runs between 21:00 IST and midnight.
#   • If the scan raises an exception  → send Telegram crash alert, and RETRY in 5 minutes.
#   • Once it finishes successfully    → do NOT run again until the next day's window.
# =====================================================================================

# [VERSION: SCHEDULER_CORRECTNESS_v1.0]
# PRODUCTION CONTRACT: These _run_*_with_retries functions are called exclusively
# by the production scheduler after it has already:
#   (1) waited for Bhavcopy to be available, and
#   (2) determined that the correct execution window has been reached.
#
# Therefore, scanners are called with force=True so they treat this as a
# production run regardless of the wall-clock time. The scheduler owns the
# decision of WHEN to run; the scanner owns the decision of HOW to scan.
#
# force=True must NOT be removed — doing so causes the scanners to silently
# enter test_mode and discard all alert results whenever they run before 21:00.
def _run_eod_with_retries(today_str, session=None):
    retry_count = 0
    while True:
        # [VERSION: SCHEDULER_CORRECTNESS_v1.0] already_ran check: any successful run
        # today (regardless of time-of-day) counts as the authoritative production run.
        # The prior 21:00 time-gate is removed because the real production run now
        # happens at ~18:30-19:00 (when Bhavcopy arrives), not at 21:00.
        try:
            from database import get_all_scanner_health
            health_records = get_all_scanner_health()
            already_ran = False
            for rec in health_records:
                if rec.get("scanner_name") == "EOD" and rec.get("status") == "OK" and rec.get("last_success"):
                    last_success_str = str(rec["last_success"])
                    if last_success_str.startswith(today_str):
                        try:
                            from dateutil.parser import isoparse
                            ls_dt = isoparse(last_success_str)
                            start_time, _ = WINDOWS["eod"]
                            if ls_dt.time() >= start_time:
                                already_ran = True
                                break
                            else:
                                logger.info("📊 EOD SCAN | Previous run today was BEFORE 18:00 (manual trigger). Will execute scheduled run.")
                        except Exception as e:
                            logger.warning(f"Could not parse last_success: {e}")
                            already_ran = True
                            break
            
            if already_ran:
                logger.info("📊 EOD SCAN | Already successfully executed today.")
                return
        except Exception as e:
            logger.warning(f"Could not verify EOD previous run status: {e}")
        
        try:
            logger.info(f"📊 EOD SCAN | Starting scan for {today_str}...")
            from database import upsert_scanner_health
            upsert_scanner_health("EOD", status="RUNNING", error_msg="EOD scan in progress...")
            import eod_scanner
            start_time = time.time()
            with MemoryProfiler("EOD_SCANNER", force_gc_cleanup=True):
                total = eod_scanner.start(force=True, session=session, trigger_type="SCHEDULED", scheduler_name="CRON")
            duration_sec = round(time.time() - start_time, 1)
            time.sleep(15)
            if total == 0:
                logger.info(f"📊 EOD | Completed in {format_duration(duration_sec)} — Zero alerts")
            else:
                logger.info(f"📊 EOD | Completed in {format_duration(duration_sec)} — {total} alert(s) sent")
                
                upsert_scanner_health(
                    "EOD",
                    status="OK",
                    last_success=datetime.now(IST).isoformat(),
                    today_alerts=total,
                    scheduled_for="18:00 IST (After Bhavcopy)",
                    duration_seconds=duration_sec
                )
                try:
                    from performance_tracker import trigger_performance_rebuild
                    trigger_performance_rebuild()
                except Exception as pe:
                    logger.error(f"Failed to trigger performance rebuild post-EOD: {pe}")
                logger.info("✅ EOD SCANNER | Completed successfully for today.")
                with MemoryProfiler("Cleanup - EOD", force_gc_cleanup=True):
                    pass
            return
            
        except Exception as exc:
            if "actively running" in str(exc).lower():
                logger.info("⏳ EOD scanner is already running in another process. Waiting...")
                time.sleep(60)
                continue
                
            retry_count += 1
            now = datetime.now(IST)
            
            if 0 <= now.hour < 6:
                logger.critical(f"⏰ MIDNIGHT PASSED — EOD scanner force-stopping after {retry_count} retries")
                upsert_scanner_health("EOD", status="DOWN", error_msg=f"Stopped at midnight after {retry_count} failed attempts", scheduled_for="18:00 IST (After Bhavcopy)")
                return
            
            logger.critical(f"💀 EOD scanner crashed (attempt {retry_count}): {exc}. Retrying in 1 minute...")
            from database import upsert_scanner_health, insert_notification
            upsert_scanner_health("EOD", status="DOWN", error_msg=str(exc)[:500], retry_count=retry_count, scheduled_for="18:00 IST (After Bhavcopy)")
            
            if retry_count == 1:
                try:
                    insert_notification(notif_type="scanner_down", title="🚨 EOD Scanner CRASHED", message=f"Error: {str(exc)[:400]}. Auto-retrying.")
                except Exception:
                    pass
            
            wait_time = min(300, (2 ** retry_count) * random.uniform(0.5, 1.5))
            time.sleep(wait_time)


def _run_reversal_with_retries(today_str, session=None):
    retry_count = 0
    while True:
        # [VERSION: SCHEDULER_CORRECTNESS_v1.0] already_ran check: any successful run
        # today counts. The prior 21:00 time-gate is removed — see _run_eod_with_retries.
        try:
            from database import get_all_scanner_health
            health_records = get_all_scanner_health()
            already_ran = False
            for rec in health_records:
                if rec.get("scanner_name") == "REVERSAL" and rec.get("status") == "OK" and rec.get("last_success"):
                    last_success_str = str(rec["last_success"])
                    if last_success_str.startswith(today_str):
                        try:
                            from dateutil.parser import isoparse
                            ls_dt = isoparse(last_success_str)
                            start_time, _ = WINDOWS["reversal"]
                            if ls_dt.time() >= start_time:
                                already_ran = True
                                break
                            else:
                                logger.info("🔄 REVERSAL SCAN | Previous run today was BEFORE 18:00 (manual trigger). Will execute scheduled run.")
                        except Exception as e:
                            logger.warning(f"Could not parse last_success: {e}")
                            already_ran = True
                            break
            
            if already_ran:
                logger.info("🔄 REVERSAL SCAN | Already successfully executed today.")
                return
        except Exception as e:
            logger.warning(f"Could not verify REVERSAL previous run status: {e}")
        
        try:
            logger.info(f"🔄 REVERSAL SCAN | Starting scan for {today_str}...")
            from database import upsert_scanner_health
            upsert_scanner_health("REVERSAL", status="RUNNING", error_msg="Reversal scan in progress...")
            import reversal_scanner
            start_time = time.time()
            with MemoryProfiler("REVERSAL", force_gc_cleanup=True):
                total = reversal_scanner.start(force=True, session=session, trigger_type="SCHEDULED", scheduler_name="CRON")
            duration_sec = round(time.time() - start_time, 1)
            time.sleep(15)
            if total == 0:
                logger.info(f"🔄 REVERSAL | Completed in {format_duration(duration_sec)} — Zero alerts")
            else:
                logger.info(f"🔄 REVERSAL | Completed in {format_duration(duration_sec)} — {total} alert(s) sent")
                
                upsert_scanner_health(
                    "REVERSAL",
                    status="OK",
                    last_success=datetime.now(IST).isoformat(),
                    today_alerts=total,
                    scheduled_for="18:00 IST (After Bhavcopy)",
                    duration_seconds=duration_sec
                )
                try:
                    from performance_tracker import trigger_performance_rebuild
                    trigger_performance_rebuild()
                except Exception as pe:
                    logger.error(f"Failed to trigger performance rebuild post-REVERSAL: {pe}")
                logger.info("✅ REVERSAL SCANNER | Completed successfully for today.")
                with MemoryProfiler("Cleanup - REVERSAL", force_gc_cleanup=True):
                    pass
            return
            
        except Exception as exc:
            if "actively running" in str(exc).lower():
                logger.info("⏳ REVERSAL scanner is already running in another process. Waiting...")
                time.sleep(60)
                continue
                
            retry_count += 1
            now = datetime.now(IST)
            
            if 0 <= now.hour < 6:
                logger.critical(f"⏰ MIDNIGHT PASSED — REVERSAL scanner force-stopping after {retry_count} retries")
                upsert_scanner_health("REVERSAL", status="DOWN", error_msg=f"Stopped at midnight after {retry_count} failed attempts", scheduled_for="18:00 IST (After Bhavcopy)")
                return
            
            logger.critical(f"💀 REVERSAL scanner crashed (attempt {retry_count}): {exc}. Retrying in 1 minute...")
            from database import upsert_scanner_health, insert_notification
            upsert_scanner_health("REVERSAL", status="DOWN", error_msg=str(exc)[:500], retry_count=retry_count, scheduled_for="18:00 IST (After Bhavcopy)")
            
            if retry_count == 1:
                try:
                    insert_notification(notif_type="scanner_down", title="🚨 REVERSAL Scanner CRASHED", message=f"Error: {str(exc)[:400]}. Auto-retrying.")
                except Exception:
                    pass
            
            wait_time = min(300, (2 ** retry_count) * random.uniform(0.5, 1.5))
            time.sleep(wait_time)


def _run_pullback_with_retries(today_str, session=None):
    retry_count = 0
    while True:
        # [VERSION: SCHEDULER_CORRECTNESS_v1.0] already_ran check: any successful run
        # today counts. The prior 21:00 time-gate is removed — see _run_eod_with_retries.
        try:
            from database import get_all_scanner_health
            health_records = get_all_scanner_health()
            already_ran = False
            for rec in health_records:
                if rec.get("scanner_name") == "PULLBACK" and rec.get("status") == "OK" and rec.get("last_success"):
                    last_success_str = str(rec["last_success"])
                    if last_success_str.startswith(today_str):
                        try:
                            from dateutil.parser import isoparse
                            ls_dt = isoparse(last_success_str)
                            start_time, _ = WINDOWS["eod"]
                            if ls_dt.time() >= start_time:
                                already_ran = True
                                break
                            else:
                                logger.info("📊 PULLBACK SCAN | Previous run today was BEFORE 18:00 (manual trigger). Will execute scheduled run.")
                        except Exception as e:
                            logger.warning(f"Could not parse last_success: {e}")
                            already_ran = True
                            break
            if already_ran:
                logger.info("📊 PULLBACK SCAN | Already successfully executed today.")
                return
        except Exception as e:
            logger.warning(f"Could not verify PULLBACK previous run status: {e}")
        
        try:
            logger.info(f"📊 PULLBACK SCAN | Starting scan for {today_str}...")
            from database import upsert_scanner_health
            upsert_scanner_health("PULLBACK", status="RUNNING", error_msg="Pullback scan in progress...")
            import pullback_pipeline
            start_time = time.time()
            with MemoryProfiler("PULLBACK_SCANNER", force_gc_cleanup=True):
                total = pullback_pipeline.start(force=True, session=session, trigger_type="SCHEDULED", scheduler_name="CRON")
            duration_sec = round(time.time() - start_time, 1)
            time.sleep(5)
            logger.info(f"📊 PULLBACK | Completed in {format_duration(duration_sec)} — {total} alert(s) generated")
            alerts_num = total.get("today_alerts", 0) if isinstance(total, dict) else (total if isinstance(total, int) else 0)
            upsert_scanner_health("PULLBACK", status="OK", last_success=datetime.now(IST).isoformat(), today_alerts=alerts_num, scheduled_for="18:00 IST (After Bhavcopy)", duration_seconds=duration_sec)
            logger.info("✅ PULLBACK SCANNER | Completed successfully for today.")
            return
        except Exception as exc:
            if "actively running" in str(exc).lower():
                logger.info("⏳ PULLBACK scanner is already running in another process. Waiting...")
                time.sleep(60)
                continue
            retry_count += 1
            now = datetime.now(IST)
            if 0 <= now.hour < 6:
                logger.critical(f"⏰ MIDNIGHT PASSED — PULLBACK scanner force-stopping after {retry_count} retries")
                upsert_scanner_health("PULLBACK", status="DOWN", error_msg=f"Stopped at midnight after {retry_count} failed attempts", scheduled_for="18:00 IST (After Bhavcopy)")
                return
            logger.critical(f"💀 PULLBACK scanner crashed (attempt {retry_count}): {exc}. Retrying in 1 minute...")
            from database import upsert_scanner_health
            upsert_scanner_health("PULLBACK", status="DOWN", error_msg=str(exc)[:500], retry_count=retry_count, scheduled_for="18:00 IST (After Bhavcopy)")
            wait_time = min(300, (2 ** retry_count) * random.uniform(0.5, 1.5))
            time.sleep(wait_time)

# [VERSION: PULLBACK_MANUAL_TRIGGER_FIX_v1.0] Pass force=True for manual trigger
def _trigger_pullback():
    import pullback_pipeline
    from watchlist_cache import get_watchlist
    from datetime import datetime
    from zoneinfo import ZoneInfo
    IST = ZoneInfo("Asia/Kolkata")
    wl = get_watchlist()
    all_symbols = wl["Stock"].tolist() if wl is not None and not wl.empty else []
    session = None
    if all_symbols:
        from market_data_session import MarketDataSession
        try:
            session = MarketDataSession.build(all_symbols, ist_date=datetime.now(IST).date(), requester="ManualPullback")
        except Exception as e:
            import logging
            logging.getLogger(__name__).warning(f"Failed to build session for manual Pullback: {e}")
    with MemoryProfiler("PULLBACK_SCANNER", force_gc_cleanup=True):
        return pullback_pipeline.start(force=True, session=session)



def run_evening_scanners():
    while True:
        block_until_watchlist_ready()
        wait_for_window("eod")
        wait_for_bhavcopy_or_fallback("EVENING_SCANNERS")
        now = datetime.now(IST)
        today_str = now.strftime("%Y-%m-%d")
        
        from telemetry_manager import telemetry
        telemetry.log_scheduler_event("EVENING_SCANNERS", "CYCLE_START")
        telemetry.log_session_timeline("Started Evening Scanners Cycle (EOD, Reversal, Pullback)")
        
        # ── [VERSION: MARKET_DATA_SESSION_v1.0] ─────────────────────────────────
        # Build the shared MarketDataSession ONCE before any scanner runs.
        # All scanners (EOD, Reversal, Pullback) consume pre-fetched, pre-computed
        # data via session.get(symbol) instead of independently fetching OHLCV.
        # This eliminates: duplicate downloads, duplicate indicator computation,
        # and serialized Bhavcopy proxy retries per scanner.
        # ────────────────────────────────────────────────────────────────────────
        evening_session = None
        try:
            from market_data_session import build_evening_session
            from watchlist_cache import get_watchlist
            import pandas as pd
            wl = get_watchlist()
            all_symbols = wl["Stock"].tolist() if wl is not None and not wl.empty else []
            if all_symbols:
                logger.info(f"🏗️  Building MarketDataSession for {len(all_symbols)} symbols...")
                t_session_start = time.time()
                evening_session = build_evening_session(all_symbols, ist_date=now.date())
                t_session_dur = round(time.time() - t_session_start, 1)
                if evening_session:
                    logger.info(
                        f"✅ MarketDataSession ready in {format_duration(t_session_dur)} "
                        f"| {evening_session.summary()}"
                    )
                else:
                    logger.warning(
                        "⚠️ MarketDataSession build returned None — "
                        "scanners will fall back to independent data fetching."
                    )
            else:
                logger.warning("⚠️ Watchlist is empty — skipping session build.")
        except Exception as session_err:
            logger.exception(f"❌ MarketDataSession build crashed: {session_err}. "
                             f"Scanners will run with independent fetching as fallback.")
            evening_session = None

        logger.info("🚀 Bhavcopy is ready! Spawning EOD, Reversal, and Pullback sequentially.")
        
        # Run EOD Scanner (receives session; falls back to independent fetch if session=None)
        _run_eod_with_retries(today_str, session=evening_session)
        
        # Run Reversal Scanner
        _run_reversal_with_retries(today_str, session=evening_session)

        # Run Pullback Scanner (after EOD & Reversal finish)
        _run_pullback_with_retries(today_str, session=evening_session)

        # Verify actual execution outcome from database health records before declaring status
        from database import get_all_scanner_health
        health_records = {r.get("scanner_name"): r for r in get_all_scanner_health()}
        
        def _check_scanner_ok(name):
            rec = health_records.get(name, {})
            last_success = str(rec.get("last_success", ""))
            return rec.get("status") == "OK" and last_success.startswith(today_str)
            
        eod_ok = _check_scanner_ok("EOD")
        rev_ok = _check_scanner_ok("REVERSAL")
        pb_ok  = _check_scanner_ok("PULLBACK")

        if eod_ok and rev_ok and pb_ok:
            logger.info("✅ All Evening Scanners (EOD, Reversal, & Pullback) completed successfully for today.")
            telemetry.log_scheduler_event("EVENING_SCANNERS", "CYCLE_COMPLETE")
            telemetry.log_session_timeline("Completed Evening Scanners Cycle Successfully")
        else:
            status_str = f"EOD={'OK' if eod_ok else 'FAILED'}, REVERSAL={'OK' if rev_ok else 'FAILED'}, PULLBACK={'OK' if pb_ok else 'FAILED'}"
            logger.error(f"⚠️ Evening Scanners batch finished with incomplete/failed status: [{status_str}]")
            telemetry.log_scheduler_event("EVENING_SCANNERS", "CYCLE_FAILED", error=status_str)
            telemetry.log_session_timeline(f"Evening Scanners Cycle Failed: {status_str}")

        # Execute 4-step defensive purge telemetry post evening batch
        try:
            from memory_profiler import run_purge_with_telemetry
            run_purge_with_telemetry("Post-Evening Batch")
        except Exception as pe:
            logger.warning(f"Could not run purge telemetry post evening batch: {pe}")

        # Sleep for 6 hours to avoid retriggering until the window closes
        time.sleep(3600 * 6)


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
    - 2:00 AM: Wealth Engine (initial setup with fresh watchlist)
    - 8:30 AM: Verify file readiness
    - Market hours (9:15 AM - 3:30 PM): Wealth Engine hourly at :05 to generate new buy signals
    """
    from wealth_engine import run_wealth_scan
    from config import WATCHLIST_PATH, DATA_DIR
    # [VERSION: LOG_ERROR_FIXES_v1.0] Hoist is_scanner_stopped import to top of run_system_scheduler scope to fix NameError in nested functions
    from database import upsert_scanner_health, is_scanner_stopped
    
    WEALTH_PATH = os.path.join(DATA_DIR, "elite_wealth_system.parquet")
    
    # Track which tasks have run today
    daily_builder_ran = False
    wealth_initial_ran = False
    verify_scans_ran = False
    last_wealth_market_run = None  # Track last market-hours wealth run
    last_wealth_full_scan_run = None  # Track last market-hours full scan (15m BUY alert cycle)

    def safe_run_daily_builder():
        """Helper to run the builder and update the memory cache."""
        start_time = time.time()
        try:
            import os
            import pandas as pd
            
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
                logger.info("🕒 SCHEDULER | [5:00 AM] Watchlist already fresh for today. Skipping redundant build.")
            else:
                logger.info("🕒 SCHEDULER | [5:00 AM] Triggering Daily Builder")
                from telemetry_manager import telemetry
                from database import start_scanner_execution_run, complete_scanner_execution_run, upsert_scanner_health
                upsert_scanner_health("DAILY_BUILDER", status="RUNNING", error_msg="Building watchlist...")
                telemetry.log_scheduler_event("DAILY_BUILDER", "CYCLE_START")
                # Pre-Daily Builder 4-step defensive memory purge
                try:
                    from memory_profiler import run_purge_with_telemetry
                    run_purge_with_telemetry("Pre-Daily Builder")
                except Exception:
                    pass
                from daily_builder import main as build_watchlist
                run_ctx = None
                try:
                    with MemoryProfiler("DAILY_BUILDER", force_gc_cleanup=True):
                        with scanner_execution_lock:
                            run_ctx = start_scanner_execution_run(scanner_name="DAILY_BUILDER", trigger_type="SCHEDULED", scheduler_name="CRON")
                            try:
                                build_watchlist()
                                complete_scanner_execution_run(run_ctx)
                            except Exception as db_err:
                                complete_scanner_execution_run(run_ctx, exception=db_err)
                                raise db_err
                except Exception as db_err:
                    if run_ctx:
                        complete_scanner_execution_run(run_ctx, exception=db_err)
                    raise db_err

            
            # Update memory cache
            from watchlist_cache import get_watchlist
            get_watchlist()
            
            # Mark success
            now_str = datetime.now(IST).isoformat()
            dur_db = round(time.time() - start_time, 1)
            try:
                upsert_scanner_health(
                    "DAILY_BUILDER",
                    status="OK",
                    last_success=now_str,
                    scheduled_for="05:00 IST",
                    duration_seconds=dur_db
                )
            except Exception:
                logger.warning("⚠️ Could not update Daily Builder health status")
            logger.info("✅ Daily Builder completed successfully")
            if not already_fresh:
                from telemetry_manager import telemetry
                telemetry.log_scheduler_event("DAILY_BUILDER", "CYCLE_COMPLETE")
            return True
        except Exception as e:
            err_str = str(e).lower()
            if "actively running" in err_str:
                logger.info("⏳ DAILY_BUILDER is already running. Skipping scheduler trigger.")
                return False
                
            logger.exception("❌ SCHEDULER | Daily Builder crashed")
            from telemetry_manager import telemetry
            telemetry.log_scheduler_event("DAILY_BUILDER", "CYCLE_FAILED", error=str(e))
            # Telegram notifications disabled (2026-06-17)
            try:
                upsert_scanner_health(
                    "DAILY_BUILDER",
                    status="DOWN",
                    error_msg=str(e)[:500],
                    scheduled_for="05:00 IST"
                )
            except Exception:
                pass
            return False

    def safe_run_wealth_scan_initial():
        """Run Wealth Engine at 2:00 AM with fresh watchlist."""
        start_time = time.time()
        from database import upsert_scanner_health
        upsert_scanner_health("Wealth Engine", status="RUNNING", error_msg="Wealth Engine scan in progress...")
        try:
            logger.info("🕒 SCHEDULER | [6:00 AM] Triggering Wealth Engine (initial setup)")
            from telemetry_manager import telemetry
            telemetry.log_scheduler_event("WEALTH_ENGINE_INIT", "CYCLE_START")
            telemetry.log_session_timeline("Started Wealth Engine Initial Setup Cycle")
            with MemoryProfiler("WEALTH_ENGINE_INIT", force_gc_cleanup=True):
                from wealth_engine import run_wealth_scan
                run_wealth_scan(trigger_type="SCHEDULED", scheduler_name="CRON")
                duration_sec = round(time.time() - start_time, 1)
            
            # Mark success
            now_str = datetime.now(IST).isoformat()
            upsert_scanner_health(
                "Wealth Engine",
                status="OK",
                last_success=now_str,
                scheduled_for="06:00 IST",
                duration_seconds=duration_sec
            )
            logger.info(f"✅ Wealth Engine (initial) completed successfully in {format_duration(duration_sec)}")
            telemetry.log_scheduler_event("WEALTH_ENGINE_INIT", "CYCLE_COMPLETE")
            telemetry.log_session_timeline("Completed Wealth Engine Initial Setup Cycle Successfully")
            with MemoryProfiler("Cleanup - WEALTH", force_gc_cleanup=True):
                pass
            return True
        except Exception as e:
            if "actively running" in str(e).lower():
                logger.info("⏳ Wealth Engine is actively running.")
                return False
            logger.exception("❌ SCHEDULER | Wealth Engine (initial) crashed")
            from telemetry_manager import telemetry
            telemetry.log_scheduler_event("WEALTH_ENGINE_INIT", "CYCLE_FAILED", error=str(e))
            telemetry.log_session_timeline(f"Wealth Engine Initial Setup Cycle Failed: {str(e)}")
            upsert_scanner_health(
                "Wealth Engine",
                status="DOWN",
                error_msg=str(e)[:500],
                scheduled_for="06:00 IST"
            )
            return False

    def safe_run_wealth_market_hours():
        """Run Wealth Engine during market hours (5-min position CMP/Exit update, 15-min full BUY alert scan)."""
        nonlocal last_wealth_market_run, last_wealth_full_scan_run
        start_time = time.time()
        try:
            now = datetime.now(IST)
            start_time = time.time()
            # Only run once per 5 minutes (300 seconds)
            if last_wealth_market_run and (now - last_wealth_market_run).total_seconds() < 300:
                return False
            
            # Check if 1 hour (3600s) has elapsed since last full BUY alert scan
            should_run_full_scan = False
            if last_wealth_full_scan_run is None or (now - last_wealth_full_scan_run).total_seconds() >= 3600:
                should_run_full_scan = True

            if should_run_full_scan:
                if not is_scanner_stopped("Wealth Engine"):
                    logger.info(f"🕒 SCHEDULER | [{now.strftime('%H:%M')}] Triggering FULL Wealth Engine Scan (1-hour BUY alert cycle)")
                    from telemetry_manager import telemetry
                    upsert_scanner_health("Wealth Engine", status="RUNNING", error_msg="Wealth Engine scan in progress...")
                    telemetry.log_scheduler_event("WEALTH_ENGINE_15M", "CYCLE_START")
                    _scan_start_t = time.time()
                    try:
                        with MemoryProfiler("WEALTH_ENGINE_15M", force_gc_cleanup=True):
                            from wealth_engine import run_wealth_scan
                            run_wealth_scan(trigger_type="SCHEDULED", scheduler_name="CRON")
                            duration_sec = round(time.time() - _scan_start_t, 1)
                        now_str = datetime.now(IST).isoformat()
                        upsert_scanner_health(
                            "Wealth Engine",
                            status="OK",
                            last_success=now_str,
                            scheduled_for="Every 1h (09:15 AM - 03:30 PM IST)",
                            duration_seconds=duration_sec
                        )
                        logger.info(f"✅ Wealth Engine (market hours) FULL SCAN completed in {format_duration(duration_sec)}")
                    except Exception as run_err:
                        if run_ctx:
                            complete_scanner_execution_run(run_ctx, exception=run_err)
                        logger.exception(f"❌ [CRITICAL SCANNER FAILURE] Wealth Engine scan failed: {run_err}")
                        complete_scanner_execution_run(run_ctx, exception=run_err)
                        raise run_err
                else:
                    logger.info("⏭️ Wealth Engine BUY scan is PAUSED by Admin. Skipping 1-hour BUY scan.")
                last_wealth_full_scan_run = now

            if not is_scanner_stopped("WEALTH_EXIT"):
                logger.info(f"🕒 SCHEDULER | [{now.strftime('%H:%M')}] Triggering Wealth Engine Intraday Update (5-min exit loop)")
                from telemetry_manager import telemetry
                telemetry.log_scheduler_event("WEALTH_ENGINE_5M", "CYCLE_START")
                _exit_start_t = time.time()
                with MemoryProfiler("WEALTH_ENGINE_5M", force_gc_cleanup=True):
                    from wealth_engine import run_wealth_intraday_update
                    run_wealth_intraday_update(write_health=not should_run_full_scan)
                duration_sec = round(time.time() - _exit_start_t, 1)
                logger.info(f"✅ Wealth Engine (market hours) exit update completed in {format_duration(duration_sec)}")
                upsert_scanner_health(
                    "WEALTH_EXIT",
                    status="OK",
                    last_success=datetime.now(IST).isoformat(),
                    scheduled_for="Every 5min (9:15 AM - 3:30 PM)",
                    duration_seconds=duration_sec
                )
            else:
                logger.info("⏭️ WEALTH_EXIT is PAUSED by Admin. Skipping 5-min Wealth exit update.")
            
            last_wealth_market_run = now
            return True
        except Exception as e:
            if "actively running" in str(e).lower():
                logger.info("⏳ Wealth Engine is actively running.")
                return False
            logger.exception("❌ SCHEDULER | Wealth Engine (market hours) crashed")
            upsert_scanner_health(
                "Wealth Engine",
                status="DOWN",
                error_msg=str(e)[:500],
                scheduled_for="Every 5min (9:15 AM - 3:30 PM)"
            )
            return False

    def verify_scans(run_test_scans: bool = False):
        """Verify file readiness at 8:30 AM or boot."""
        logger.info("🕒 SCHEDULER | Verifying file readiness for today's scan")
        now = datetime.now(IST)
        today_str = now.strftime("%Y-%m-%d")

        # 0. Restore Historical Parquet Cache from DB (<0.5s cold boot restoration)
        try:
            from database import restore_history_bundle_from_db
            for _tf in ("1d", "1h", "30m", "15m", "5m"):
                restore_history_bundle_from_db(_tf)
        except Exception as hb_err:
            logger.debug(f"History bundle restore check at boot: {hb_err}")

        # 1. Verify Watchlist (with full date-aware cache/DB/rebuild logic)
        logger.info(f"🕒 SCHEDULER | Step 1: Verifying watchlist freshness for {today_str}")
        block_until_watchlist_ready()

        # 2. Verify Wealth Engine
        try:
            if not os.path.exists(WEALTH_PATH):
                logger.warning(f"⚠️ Wealth system missing from disk. Attempting DB restore for {today_str}...")
                try:
                    from database import download_parquet_from_db_today, download_parquet_from_db
                    restored = download_parquet_from_db_today("wealth_engine", WEALTH_PATH)
                    if not restored:
                        # [VERSION: DB_PARQUET_RESTORE_FALLBACK_v1.0] Fallback to latest DB parquet
                        # RATIONALE: If today's scan hasn't uploaded yet, fetch the most recent available Wealth Parquet
                        # from DB (from previous session) so the dashboard has instant state available on startup.
                        restored = download_parquet_from_db("wealth_engine", WEALTH_PATH)

                    if restored and os.path.exists(WEALTH_PATH):
                        logger.info("✅ Wealth system restored from DB.")
                    elif run_test_scans:
                        logger.warning("⚠️ Wealth system missing from DB too. Non-market hours: running test setup scan.")
                        if not is_scanner_stopped("Wealth Engine"):
                            safe_run_wealth_scan_initial()
                except Exception as e:
                    logger.exception(f"Failed to restore wealth from DB: {e}")
            else:
                mtime_ts = os.path.getmtime(WEALTH_PATH)
                mtime = datetime.fromtimestamp(mtime_ts, IST)
                if mtime.date() < now.date():
                    logger.warning(f"⚠️ Wealth system is from {mtime.date()}, not today ({today_str}). Attempting DB restore...")
                    try:
                        from database import download_parquet_from_db_today, download_parquet_from_db
                        restored = download_parquet_from_db_today("wealth_engine", WEALTH_PATH)
                        if not restored:
                            restored = download_parquet_from_db("wealth_engine", WEALTH_PATH)

                        if restored and os.path.exists(WEALTH_PATH):
                            logger.info("✅ Wealth system restored from DB.")
                        elif run_test_scans:
                            logger.warning("⚠️ Wealth system not in today DB. Non-market hours: running test setup scan.")
                            if not is_scanner_stopped("Wealth Engine"):
                                safe_run_wealth_scan_initial()
                    except Exception as e:
                        logger.exception(f"Failed to restore wealth: {e}")
        except Exception as e:
            logger.exception(f"Failed to verify wealth system: {e}")

        # 3. Verify Multi-TF System
        try:
            MULTI_TF_PATH = os.path.join(DATA_DIR, "multi_tf_system.parquet")
            if not os.path.exists(MULTI_TF_PATH):
                logger.warning(f"⚠️ Multi-TF system missing from disk. Attempting DB restore for {today_str}...")
                try:
                    from database import download_parquet_from_db_today, download_parquet_from_db
                    restored = download_parquet_from_db_today("multi_tf_system", MULTI_TF_PATH)
                    if not restored:
                        restored = download_parquet_from_db("multi_tf_system", MULTI_TF_PATH)

                    if restored and os.path.exists(MULTI_TF_PATH):
                        logger.info("✅ Multi-TF system restored from DB.")
                except Exception as e:
                    logger.warning(f"Failed to restore multi_tf_system from DB: {e}")
        except Exception as e:
            logger.warning(f"Failed to verify Multi-TF system: {e}")

        logger.info("✅ SCHEDULER | File readiness verification complete")

    def safe_run_multibagger_scan_initial():
        """Run Multibagger Scanner Cold Start at 4:00 AM with fresh watchlist."""
        start_time = time.time()
        try:
            logger.info("🕒 SCHEDULER | [4:00 AM] Triggering Multibagger Scanner (initial cold start)")
            from telemetry_manager import telemetry
            telemetry.log_scheduler_event("MULTIBAGGER_INIT", "CYCLE_START")
            telemetry.log_session_timeline("Started Multibagger Scanner Initial Setup Cycle")
            _run_multibagger_scanner_single()
            telemetry.log_scheduler_event("MULTIBAGGER_INIT", "CYCLE_COMPLETE")
            telemetry.log_session_timeline("Completed Multibagger Scanner Initial Setup Cycle Successfully")
            return True
        except Exception as e:
            logger.exception(f"❌ SCHEDULER | Multibagger Scanner (initial cold start) crashed: {e}")
            from telemetry_manager import telemetry
            telemetry.log_scheduler_event("MULTIBAGGER_INIT", "CYCLE_FAILED", error=str(e))
            return False

    logger.info("🕒 SCHEDULER | Started (custom time-based scheduler)")
    
    # [VERSION: BOOT_TEST_SCAN_MARKET_HOURS_SKIP_v1.0] Skip post-deployment / startup test scans if within market hours (9:00 AM - 3:45 PM IST)
    from market_utils import is_within_custom_hours
    from datetime import time as dt_time
    now_boot = datetime.now(IST)
    is_market_hours_boot = is_within_custom_hours(dt_time(9, 0), dt_time(15, 45), now_boot)

    if is_market_hours_boot:
        logger.info("⏰ Startup / Deployment during MARKET HOURS (9:00 AM - 3:45 PM IST) — Skipping initial boot test scans.")
        verify_scans(run_test_scans=False)
    else:
        logger.info("🌙 Startup during NON-MARKET HOURS — Running boot test scans to validate system integrity.")
        verify_scans(run_test_scans=True)
        try:
            from telemetry_manager import telemetry
            telemetry.log_scheduler_event("PERFORMANCE_TRACKER_BOOT", "CYCLE_START")
            _run_performance_tracker_single()
            telemetry.log_scheduler_event("PERFORMANCE_TRACKER_BOOT", "CYCLE_COMPLETE")
        except Exception as e:
            logger.error(f"Boot perf tracker failed: {e}")

    # Main scheduler loop state variables
    last_mb_exit = None
    last_perf = None
    last_multi_tf = None          # [VERSION: SCHEDULER_CORRECTNESS_v1.0] Tracks last 15-min candle-aligned Multi-TF execution
    daily_builder_ran = False
    wealth_initial_ran = False
    multibagger_initial_ran = False
    verify_scans_ran = False
    multibagger_ran = False
    last_multibagger_date = None
    last_rotation_date = None
    evening_scanners_ran = False
    evening_batch_deadline_logged = False
    warmup_ran = False

    last_earnings_date = None
    saturday_mb_refresh_ran = False
    
    try:
        from stock_analyzer import refresh_master_symbols_universe
        refresh_master_symbols_universe()
    except Exception as _msb:
        logger.warning(f"Boot master symbols refresh warning: {_msb}")

    from database import is_scanner_stopped

    while True:
        now = datetime.now(IST)
        
        # Weekdays only
        if now.weekday() < 5:  # Mon-Fri
            # 1:00 AM - Daily Builder → then create a fresh SessionContext
            if now.hour == 5 and now.minute >= 0 and not daily_builder_ran:
                daily_builder_ran = True
                if not is_scanner_stopped("DAILY_BUILDER"):
                    safe_run_daily_builder()
                else:
                    logger.info("⏭️ DAILY_BUILDER is STOPPED by Admin. Skipping scheduled 1:00 AM run.")
                # [VERSION: SESSION_ARCH_v2A_0] Create session after Daily Builder so
                # the watchlist is ready when SessionContext managers initialise.
                try:
                    from application_context import ApplicationContext
                    ApplicationContext.get_instance().create_session()
                except Exception as _se:
                    logger.warning(f"⚠️ [SESSION_ARCH] Failed to create SessionContext: {_se}")
            elif now.hour != 5:
                daily_builder_ran = False
            
            # Refresh now in case daily builder blocked for a long time
            now = datetime.now(IST)
            
            # 2:00 AM - Wealth Engine (initial)
            if now.hour == 6 and now.minute >= 0 and not wealth_initial_ran:
                wealth_initial_ran = True
                if not is_scanner_stopped("Wealth Engine"):
                    safe_run_wealth_scan_initial()
                else:
                    logger.info("⏭️ Wealth Engine is STOPPED by Admin. Skipping scheduled 6:00 AM run.")
            elif now.hour != 6:
                wealth_initial_ran = False
            
            now = datetime.now(IST)

            # Multibagger cold start removed; runs at 7:00 PM (19:00 IST) Daily
            pass

            # 7:00 AM - Master Symbols Universe Refresh (active NSE/BSE equities refresh)
            if now.hour == 7 and now.minute >= 0 and not verify_scans_ran:
                try:
                    from stock_analyzer import refresh_master_symbols_universe
                    refresh_master_symbols_universe()
                except Exception as _mse:
                    logger.warning(f"⚠️ [07:00 AM IST] Master symbols refresh warning: {_mse}")

            now = datetime.now(IST)

            now = datetime.now(IST)

            # 8:30 AM - Verify Scans
            if now.hour == 8 and now.minute >= 30 and not verify_scans_ran:
                verify_scans_ran = True
                verify_scans()
            elif now.hour != 8:
                verify_scans_ran = False
                
            # 09:14:30 - Precision Warmup for Intraday Scanners
            if now.hour == 9 and now.minute == 14 and now.second >= 30 and not warmup_ran:
                warmup_ran = True
                logger.info("🚀 SCHEDULER | [09:14:30] Executing Precision Warmup Sequence (15m + 1H Cache Initialization)")
                try:
                    from price_cache import fetch_watchlist_data
                    from config import WATCHLIST_PATH
                    import pandas as pd
                    from concurrent.futures import ThreadPoolExecutor as _WarmupExec
                    wl_df = pd.read_parquet(WATCHLIST_PATH)

                    def _warmup_15m():
                        # [VERSION: WARMUP_1H_v1.0] Pre-warm 15m cache for Multi-TF Phase B/C/D
                        fetch_watchlist_data(wl_df, interval="15m", period="10d", requester="SCHEDULER_WARMUP_15M")
                        logger.info("✅ SCHEDULER | 15m Warmup Complete")

                    def _warmup_1h():
                        # [VERSION: WARMUP_1H_v1.0] Pre-warm 1H cache for Multi-TF Phase A (1H Trend Scanner).
                        # Phase A runs on first 15-min boundary at 09:30. Without this pre-warm,
                        # the 1H cache is cold → evaluate_data_staleness() marks data stale →
                        # symbols are silently skipped in the 09:30 Phase A cycle.
                        fetch_watchlist_data(wl_df, interval="1h", period="15d", requester="SCHEDULER_WARMUP_1H")
                        logger.info("✅ SCHEDULER | 1H Warmup Complete")

                    # [VERSION: PARALLEL_WARMUP_v1.0] Run both warmups concurrently — each has its own
                    # requester-scoped lock in price_cache so they do NOT serialize each other.
                    with _WarmupExec(max_workers=2, thread_name_prefix="WarmupFetch") as wp:
                        f15 = wp.submit(_warmup_15m)
                        f1h = wp.submit(_warmup_1h)
                        for f in (f15, f1h):
                            try:
                                f.result()
                            except Exception as e:
                                logger.error(f"❌ SCHEDULER | Warmup fetch failed: {e}")
                except Exception as e:
                    logger.error(f"❌ SCHEDULER | Warmup sequence failed: {e}")
            elif now.hour == 9 and now.minute == 15 and not warmup_ran:
                logger.error("🚨 CRITICAL: 09:15 reached but Warmup did not complete! Scans will suffer severe cache misses.")
                # We do not set warmup_ran = True here so we know it failed, but we avoid re-triggering.
                # It will naturally reset at 10:00.
            elif now.hour != 9 or now.minute > 15:
                warmup_ran = False
            
            from market_utils import is_market_open
            # Market hours strict sequential loop (9:15 AM - 3:30 PM)
            if is_market_open(now):
                with scanner_execution_lock:
                    # [VERSION: EXIT_MONITORS_UNCONDITIONAL_v1.0] Exit monitors for open alerts/positions MUST ALWAYS run during market hours, irrespective of scanner pause/start
                    # 1. Multibagger Exit Monitor (every 15 mins)
                    if not last_mb_exit or (now - last_mb_exit).total_seconds() >= 900:
                        _run_multibagger_exit_single()
                        last_mb_exit = datetime.now(IST)

                    # 2. Performance Tracker / Alert Exit Monitor (every 5 mins)
                    if not last_perf or (now - last_perf).total_seconds() >= 300:
                        _run_performance_tracker_single()
                        last_perf = datetime.now(IST)

                    # 3. Wealth Engine Market Hours Loop (5-min Exit Monitor runs always; 15-min BUY scan is gated internally)
                    safe_run_wealth_market_hours()
                
                # [VERSION: SCHEDULER_CORRECTNESS_v1.0] Multi-TF: 15-min candle-aligned cadence
                # Runs on completed 15-minute bar boundaries (09:30, 09:45, 10:00 … 14:45).
                # Stops at 15:00 — Phase D (5m trigger) signals generated past 14:45 would
                # [VERSION: SCHEDULER_CORRECTNESS_v2.0] Multi-TF: 5-min candle-aligned cadence for fast 5m triggers
                # Phase A (full universe scan) runs on 15m boundaries inside multi_tf_scanner; Phase B/C/D runs every 5m.
                if now.hour < 15:  # Do not start new cycles after 14:59
                    current_slot = now.replace(second=0, microsecond=0)
                    current_slot = current_slot.replace(minute=(now.minute // 5) * 5)
                    if last_multi_tf is None or current_slot > last_multi_tf:
                        last_multi_tf = current_slot
                        if not is_scanner_stopped("MULTI_TF"):
                            logger.info(f"🚀 MULTI_TF SCAN | Starting 5m candle-aligned cycle at {now.strftime('%H:%M:%S IST')}...")
                            _trigger_multi_tf()
                        else:
                            logger.info("⏭️ MULTI_TF is STOPPED by Admin. Skipping candle-aligned cycle.")
                
                check_scanner_staleness(now)
                
            # 18:00 - Evening Scanners (EOD, Reversal, Pullback)
            if now.hour >= 18 and not evening_scanners_ran:
                from main import wait_for_bhavcopy_or_fallback, _run_eod_with_retries, _run_reversal_with_retries, _run_pullback_with_retries
                evening_scanners_ran = True


                def _run_evening_batch_async():
                    import concurrent.futures
                    import pandas as pd
                    with scanner_execution_lock:
                        wait_for_bhavcopy_or_fallback("EVENING_SCANNERS")
                        logger.info("🚀 Bhavcopy is ready! Spawning EOD, Reversal, and Pullback sequentially under scanner_execution_lock.")
                        today_str = datetime.now(IST).strftime("%Y-%m-%d")
                        
                        try:
                            from market_data_session import MarketDataSession
                            from watchlist_cache import get_watchlist
                            wl_df = get_watchlist()
                            symbols = wl_df["Stock"].dropna().tolist() if isinstance(wl_df, pd.DataFrame) and "Stock" in wl_df.columns else list(wl_df)
                            session = MarketDataSession.build(symbols=symbols, ist_date=datetime.now(IST).date(), requester="EVENING_BATCH")
                        except Exception as e:
                            logger.error(f"Failed to build MarketDataSession for Evening Batch: {e}")
                            session = None

                        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
                            try:
                                if not is_scanner_stopped("EOD"):
                                    logger.info("Starting EOD Scanner...")
                                    future_eod = executor.submit(_run_eod_with_retries, today_str, session)
                                    future_eod.result()
                                else:
                                    logger.info("⏭️ EOD Scanner is STOPPED by Admin. Skipping.")

                                if not is_scanner_stopped("REVERSAL"):
                                    logger.info("Starting Reversal Scanner...")
                                    future_rev = executor.submit(_run_reversal_with_retries, today_str, session)
                                    future_rev.result()
                                else:
                                    logger.info("⏭️ Reversal Scanner is STOPPED by Admin. Skipping.")

                                if not is_scanner_stopped("PULLBACK"):
                                    logger.info("Starting Pullback Pipeline...")
                                    future_pb = executor.submit(_run_pullback_with_retries, today_str, session)
                                    future_pb.result()
                                else:
                                    logger.info("⏭️ Pullback Pipeline is STOPPED by Admin. Skipping.")

                            except Exception as e:
                                logger.error(f"🚨 CRITICAL: Evening Batch error: {e}")

                import threading
                threading.Thread(target=_run_evening_batch_async, name="EveningBatch", daemon=True).start()
            elif now.hour < 18:
                evening_scanners_ran = False
                evening_batch_deadline_logged = False

                
            # 19:00 - Multibagger Scanner (Independent top-level branch)
            if now.hour >= 19 and last_multibagger_date != now.date():
                last_multibagger_date = now.date()
                if not is_scanner_stopped("MULTIBAGGER"):
                    _run_multibagger_scanner_single()
                else:
                    logger.info("⏭️ MULTIBAGGER is STOPPED by Admin. Skipping 19:00 IST run.")

            # 12:01 AM - 04:00 AM - Earnings Calendar Refresh (Daily off-peak window)
            if 0 <= now.hour < 4 and last_earnings_date != now.date():
                last_earnings_date = now.date()
                if not is_scanner_stopped("Earnings Calendar"):
                    def _run_earnings_post_market():
                        try:
                            logger.info("📅 SCHEDULER | [12:01 AM - 04:00 AM IST] Earnings Calendar off-peak refresh starting...")
                            from earnings_calendar import run_earnings_calendar_refresh
                            run_earnings_calendar_refresh()
                        except RuntimeError as re:
                            logger.info(f"⏭️ SCHEDULER | Earnings Calendar refresh skipped: {re}")
                        except Exception as e:
                            logger.error(f"❌ SCHEDULER | Earnings Calendar off-peak refresh failed: {e}")
                    import threading as _t
                    _t.Thread(target=_run_earnings_post_market, name="EarningsCalendar-PostMarket", daemon=True).start()
                else:
                    logger.info("⏭️ Earnings Calendar is STOPPED by Admin. Skipping 12:01 AM refresh.")

            # Midnight session rotation — triggered once on date boundary
            if last_rotation_date != now.date():
                last_rotation_date = now.date()
                try:
                    from application_context import ApplicationContext
                    ApplicationContext.get_instance().new_trading_day()
                    logger.info("🌙 [SESSION_ARCH] Midnight rotation complete — old session destroyed.")
                except Exception as _me:
                    logger.warning(f"⚠️ [SESSION_ARCH] Midnight session rotation failed: {_me}")

        # Saturday Morning (06:00 AM IST) - Fundamental Refresh for data >= 7 days old
        if now.weekday() == 5:
            if now.hour == 6 and now.minute >= 0 and not saturday_mb_refresh_ran:
                saturday_mb_refresh_ran = True
                if not is_scanner_stopped("MULTIBAGGER"):
                    logger.info("🕒 SCHEDULER | [Saturday 06:00 AM] Triggering Multibagger 7-day fundamental refresh...")
                    _run_multibagger_scanner_single()
                else:
                    logger.info("⏭️ MULTIBAGGER is STOPPED by Admin. Skipping Saturday 6:00 AM refresh.")
            elif now.hour != 6:
                saturday_mb_refresh_ran = False

        # Sleep tight, loop runs approximately every 15 seconds for precision timing
        time.sleep(15)


def check_scanner_staleness(now):
    """Check if any active scanner has gone stale (no heartbeat in expected cadence × 3).
    
    Runs during market hours only. If a scanner's last_success is too old,
    marks it DOWN and sends a Telegram + in-app notification.
    """
    # Expected max gap (in minutes) for each scanner before it's considered stale
    SCANNER_CADENCE = {
        "MULTI_TF":            20,   # runs every 5 min (ends 2:55 PM) → stale if no heartbeat in 20 min
        "PERFORMANCE_TRACKER": 20,   # runs every 5 min → stale if no heartbeat in 20 min
        "Wealth Engine":       45,   # [VERSION: WEALTH_HEALTH_FIX_v1.0] health only updates on 15-min full scan → stale if no heartbeat in 45 min
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
                    
                    # In-app notification and Push
                    try:
                        from push_service import send_push_to_all
                        insert_notification(
                            notif_type="scanner_stale",
                            title=f"🕐 {sc} is STALE",
                            message=stale_msg
                        )
                        send_push_to_all(f"❌ {sc} STALE/DOWN", stale_msg)
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

from ai_worker import run_worker_loop as run_ai_loop
from pledge_worker import worker_loop as run_pledge_loop
from earnings_calendar import run_worker_loop as run_earnings_loop

def run_multibagger_exit_monitor():
    """Independent background daemon to monitor multibagger exits every 15 minutes."""
    from database import upsert_scanner_health
    from market_utils import is_market_open
    from multibagger import run_standalone_exit_monitor
    iteration = 0
    
    logger.info("🛑 [MULTIBAGGER_EXIT] Monitor daemon started")
    while True:
        if is_market_open():
            iteration += 1
            cycle_start = time.time()
            logger.info(f"🕒 [MULTIBAGGER_EXIT] Cycle #{iteration} | {datetime.now(IST).strftime('%H:%M:%S IST')} | Checking open multibagger positions...")
            try:
                from telemetry_manager import telemetry
                telemetry.log_scheduler_event("MULTIBAGGER_EXIT", "CYCLE_START")
                run_standalone_exit_monitor()
                elapsed = round(time.time() - cycle_start, 1)
                logger.info(f"✅ [MULTIBAGGER_EXIT] Cycle #{iteration} complete in {elapsed}s")
                upsert_scanner_health(
                    "MULTIBAGGER_EXIT", status="OK",
                    last_success=datetime.now(IST).isoformat(),
                    scheduled_for="Every 15min (market hours)"
                )
                telemetry.log_scheduler_event("MULTIBAGGER_EXIT", "CYCLE_COMPLETE")
            except Exception as e:
                elapsed = round(time.time() - cycle_start, 1)
                logger.exception(f"❌ [MULTIBAGGER_EXIT] Cycle #{iteration} crashed after {elapsed}s: {e}")
                from telemetry_manager import telemetry
                telemetry.log_scheduler_event("MULTIBAGGER_EXIT", "CYCLE_FAILED", error=str(e))
                if "actively running" not in str(e):
                    try:
                        upsert_scanner_health("MULTIBAGGER_EXIT", status="DOWN", error_msg=str(e)[:500], scheduled_for="Every 15min (market hours)")
                    except Exception:
                        pass
        else:
            logger.debug("⏸️ [MULTIBAGGER_EXIT] Market closed — skipping exit check")
        time.sleep(900)


def _run_multibagger_scanner_single():
    """Runs a single pass of the Multibagger Scanner."""
    try:
        now = datetime.now(IST)
        logger.info(f"🚀 MULTIBAGGER SCAN | Starting daily scan at {now.strftime('%H:%M:%S IST')}...")
        from database import upsert_scanner_health
        import multibagger
        if multibagger._scan_lock.locked():
            logger.info("🛑 Multibagger scanner is already running in thread lock. Skipping duplicate trigger...")
            return
        upsert_scanner_health("MULTIBAGGER", status="RUNNING", error_msg="Multibagger scan in progress...")
        import multibagger
        from telemetry_manager import telemetry
        telemetry.log_scheduler_event("MULTIBAGGER", "CYCLE_START")
        lock_acquired = False
        max_retries = 30
        for i in range(max_retries):
            if scanner_execution_lock.acquire(blocking=False):
                lock_acquired = True
                break
            
            logger.info(f"⏳ MULTIBAGGER SCAN | Waiting for scanner_execution_lock (attempt {i+1}/{max_retries}). Another scanner is currently running.")
            try:
                from database import upsert_scanner_health
                upsert_scanner_health("MULTIBAGGER", status="DEFERRED", error_msg=f"Deferred: Waiting for scanner lock (attempt {i+1}/{max_retries})")
            except Exception:
                pass
            time.sleep(60)
            
        if not lock_acquired:
            raise RuntimeError(f"Could not acquire scanner_execution_lock after {max_retries} minutes. Evening batch might be stuck.")
            
        try:
            start_mb_single = time.time()
            upsert_scanner_health("MULTIBAGGER", status="RUNNING", error_msg="Multibagger scan in progress...")
            from database import start_scanner_execution_run, complete_scanner_execution_run
            run_ctx = start_scanner_execution_run(scanner_name="MULTIBAGGER", trigger_type="SCHEDULED", scheduler_name="CRON")
            try:
                from market_data_session import MarketDataSession
                from constituent_service import fetch_constituents
                from watchlist_cache import get_watchlist
                import pandas as pd
                symbols = fetch_constituents()
                if not symbols:
                    wl_df = get_watchlist()
                    symbols = wl_df["Stock"].dropna().tolist() if isinstance(wl_df, pd.DataFrame) and "Stock" in wl_df.columns else list(wl_df)
                session = MarketDataSession.build(symbols=symbols, ist_date=datetime.now(IST).date(), requester="MULTIBAGGER")
            except Exception as e:
                logger.error(f"Failed to build MarketDataSession for MULTIBAGGER: {e}")
                session = None

            with MemoryProfiler("MULTIBAGGER", force_gc_cleanup=True):
                stats = multibagger.start(session=session, run_ctx=run_ctx, trigger_type="SCHEDULED", scheduler_name="CRON") or {}
            dur_mb_single = round(time.time() - start_mb_single, 1)
            time.sleep(15)

            # Mark success in health table INSIDE the lock
            upsert_scanner_health(
                "MULTIBAGGER",
                status="OK",
                last_success=datetime.now(IST).isoformat(),
                scheduled_for="Daily 19:00 IST",
                total_count=stats.get("total_count"),
                processed_count=stats.get("processed_count"),
                today_alerts=stats.get("today_alerts", 0),
                duration_seconds=dur_mb_single
            )
            # Rebuild performance data on scanner completion (debounced, async)
            try:
                from performance_tracker import trigger_performance_rebuild
                trigger_performance_rebuild()
            except Exception as pe:
                logger.error(f"Failed to trigger performance rebuild post-MULTIBAGGER: {pe}")
            telemetry.log_scheduler_event("MULTIBAGGER", "CYCLE_COMPLETE")
            telemetry.log_session_timeline("Completed Multibagger Scanner Cycle Successfully")
            logger.info("✅ MULTIBAGGER SCANNER | Completed successfully for today.")
        finally:
            scanner_execution_lock.release()
        logger.info("✅ MULTIBAGGER SCAN | Completed successfully.")
            
    except Exception as e:
        if "actively running" in str(e).lower():
            logger.info("⏳ MULTIBAGGER scanner is already running. Skipping...")
            return
            
        logger.exception("❌ MULTIBAGGER SCAN | Failed")
        from telemetry_manager import telemetry
        telemetry.log_scheduler_event("MULTIBAGGER", "CYCLE_FAILED", error=str(e))
        telemetry.log_session_timeline(f"Multibagger Scanner Cycle Failed: {str(e)}")
        try:
            from database import upsert_scanner_health
            upsert_scanner_health(
                "MULTIBAGGER",
                status="DOWN",
                error_msg=str(e)[:500],
                scheduled_for="Daily 19:00 IST"
            )
            from push_service import send_push_to_all
            send_push_to_all("❌ MULTIBAGGER Scanner DOWN", f"Crash: {str(e)[:100]}", bypass_throttle=True)
        except Exception:
            pass


RESTARTABLE_THREADS = {
    "AI Worker":          run_ai_loop,
    "Pledge Worker":      run_pledge_loop,
    "Earnings Calendar":  run_earnings_loop,
    "SystemScheduler":    run_system_scheduler,
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
    logger.info("🔐 [BOOT] Initializing Fyers API session on startup...")
    try:
        from data_provider import get_fetcher
        fetcher = get_fetcher()
        if getattr(fetcher, "_should_use_fyers", lambda: False)():
            # Perform a live 1-symbol probe query to verify Fyers Historical Data API permissions
            try:
                probe_res = fetcher.fyers_fetcher.get_ohlcv("SBIN", "1d", "5d")
                if probe_res and probe_res.dataframe is not None and not probe_res.dataframe.empty:
                    logger.info("✅ [BOOT] Fyers API session authenticated & historical data verified live on startup!")
                else:
                    err = getattr(probe_res, 'error', 'Unknown')
                    logger.warning(f"⚠️ [BOOT] Fyers token loaded, but historical data probe returned: {err}")
            except Exception as probe_err:
                logger.warning(f"⚠️ [BOOT] Fyers historical data live probe failed: {probe_err}")
        else:
            logger.warning("⚠️ [BOOT] Fyers API auto-login skipped or incomplete on startup.")
    except Exception as boot_fyers_err:
        logger.warning(f"⚠️ [BOOT] Fyers API boot initialization warning: {boot_fyers_err}")

    for name, target in ALL_THREADS.items():
        start_thread(name, target)

    logger.info("=" * 70)
    logger.info("🛡️  SELF-HEALING WATCHDOG ACTIVE | All Scanners Initialized")
    logger.info("🌐  Dashboard: http://localhost:8080/")
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
    from database import upsert_scanner_health, is_scanner_stopped
    
    if is_scanner_stopped(scanner_key):
        return {
            "status": "error",
            "message": f"❌ Cannot trigger {scanner_key}: Scanner is currently STOPPED by Admin. Please RESUME the scanner first."
        }
    
    TRIGGER_MAP = {
        # [VERSION: TRIGGER_AI_WORKER_v1.0] Add AI Worker trigger mapping and lock resolution
        "DAILY_BUILDER": _trigger_daily_builder,
        "MULTI_TF":      _trigger_multi_tf,
        "EOD":           _trigger_eod,
        "REVERSAL":      _trigger_reversal,
        "PULLBACK":      _trigger_pullback,
        "Wealth Engine": _trigger_wealth_engine,
        "MULTIBAGGER":    _trigger_multibagger,
        "AI Worker":     _trigger_ai_worker,
        "PERFORMANCE_TRACKER": _trigger_performance_tracker,
        "MULTIBAGGER_EXIT": _trigger_multibagger_exit,
        "WEALTH_EXIT": _trigger_wealth_exit,
        "Earnings Calendar": _trigger_earnings_calendar,
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
        "PULLBACK":      lambda: __import__('pullback_pipeline')._scan_lock,
        "Wealth Engine": lambda: __import__('wealth_engine')._scan_lock,
        "MULTIBAGGER":   lambda: __import__('multibagger')._scan_lock,
        "AI Worker":     lambda: __import__('ai_worker')._scan_lock,
        "PERFORMANCE_TRACKER": lambda: scanner_execution_lock,
        "MULTIBAGGER_EXIT": lambda: scanner_execution_lock,
        "WEALTH_EXIT": lambda: scanner_execution_lock,
        "Earnings Calendar": lambda: __import__('earnings_calendar')._scan_lock,
    }
    
    # Check in-memory thread lock first — if not locked, no scan is running in this process
    lock_fn = LOCK_MAP.get(scanner_key)
    if lock_fn:
        try:
            lock = lock_fn()
            if lock.locked():
                return {"status": "error", "message": f"❌ {scanner_key} is already actively running!"}
        except Exception:
            pass

    # Let the individual scanner thread handle its own global lock acquisition
    # and database status updates (QUEUED vs RUNNING). This prevents race conditions.

    # Invalidate dashboard status cache so next poll returns fresh DB state immediately
    try:
        import dashboard_server
        dashboard_server._scanner_status_cache["payload"] = None
    except Exception:
        pass

    # Run in background thread so the API returns immediately
    def _run():
        try:
            start_time = time.time()
            logger.info(f"🔧 ADMIN MANUAL TRIGGER | Starting {scanner_key}...")
            try:
                import inspect
                sig = inspect.signature(fn)
                if "trigger_type" in sig.parameters:
                    stats = fn(trigger_type="MANUAL", scheduler_name="MANUAL") or {}
                else:
                    stats = fn() or {}
                duration_sec = round(time.time() - start_time, 1)
                logger.info(f"✅ ADMIN MANUAL TRIGGER | {scanner_key} completed in {format_duration(duration_sec)}.")
            except Exception as run_err:
                raise run_err

                now_str = datetime.now(IST).isoformat()
                upsert_scanner_health(scanner_key, status="OK", last_success=now_str,
                                      error_msg=None,
                                      duration_seconds=duration_sec,
                                      total_count=stats.get("total_count") if isinstance(stats, dict) else None,
                                      processed_count=stats.get("processed_count") if isinstance(stats, dict) else None,
                                      today_alerts=stats.get("today_alerts") if isinstance(stats, dict) else None)
                
                try:
                    from database import insert_notification
                    dur_str = f"Time: {format_duration(duration_sec)}"
                    summary = f"Total Scanned: {stats.get('total_count', 'N/A')} | {dur_str}" if isinstance(stats, dict) else f"Completed in {dur_str}."
                    if scanner_key not in ["DAILY_BUILDER", "EOD", "MULTIBAGGER", "REVERSAL", "MULTI_TF", "Wealth Engine", "PULLBACK"]:
                        insert_notification("info", f"✅ {scanner_key} Manual Scan Complete", summary)
                except Exception:
                    pass

            # Perform post-lock background tasks outside the global execution lock
            time.sleep(5)
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

def _trigger_multi_tf(trigger_type="SCHEDULED", scheduler_name="CRON"):
    import multi_tf_scanner
    return multi_tf_scanner.start(run_once=True, trigger_type=trigger_type, scheduler_name=scheduler_name)


def _trigger_eod():
    import eod_scanner
    from watchlist_cache import get_watchlist
    import pandas as pd
    from datetime import datetime
    from zoneinfo import ZoneInfo
    IST = ZoneInfo("Asia/Kolkata")
    wl = get_watchlist()
    all_symbols = wl["Stock"].tolist() if wl is not None and not wl.empty else []
    session = None
    if all_symbols:
        from market_data_session import MarketDataSession
        try:
            session = MarketDataSession.build(all_symbols, ist_date=datetime.now(IST).date(), requester="ManualEOD")
        except Exception as e:
            import logging
            logging.getLogger(__name__).warning(f"Failed to build session for manual EOD: {e}")
    eod_scanner.start(force=True, trigger_type="MANUAL", scheduler_name="MANUAL", session=session)

def _trigger_reversal():
    import reversal_scanner
    from watchlist_cache import get_watchlist
    import pandas as pd
    from datetime import datetime
    from zoneinfo import ZoneInfo
    IST = ZoneInfo("Asia/Kolkata")
    wl = get_watchlist()
    all_symbols = wl["Stock"].tolist() if wl is not None and not wl.empty else []
    session = None
    if all_symbols:
        from market_data_session import MarketDataSession
        try:
            session = MarketDataSession.build(all_symbols, ist_date=datetime.now(IST).date(), requester="ManualReversal")
        except Exception as e:
            import logging
            logging.getLogger(__name__).warning(f"Failed to build session for manual Reversal: {e}")
    reversal_scanner.start(force=True, session=session)

def _trigger_wealth_engine():
    from wealth_engine import run_wealth_scan
    run_wealth_scan()

def _trigger_multibagger():
    import multibagger
    return multibagger.start()

# [VERSION: TRIGGER_AI_WORKER_v1.1] Define _trigger_ai_worker
def _trigger_ai_worker():
    from ai_worker import run_ai_worker_scan_once
    return run_ai_worker_scan_once()


def _trigger_earnings_calendar():
    from earnings_calendar import run_earnings_calendar_refresh
    result = run_earnings_calendar_refresh()
    return {"total_count": result.get("total_count", 0), "processed_count": result.get("updated_count", 0)}

def _trigger_performance_tracker():
    from performance_tracker import build_performance_data
    build_performance_data(force_live_fetch=True)
    return {"total_count": 1, "processed_count": 1}

def _trigger_multibagger_exit():
    _run_multibagger_exit_single()
    return {"total_count": 1, "processed_count": 1}

def _trigger_wealth_exit():
    from wealth_engine import run_wealth_intraday_update
    run_wealth_intraday_update()
    return {"total_count": 1, "processed_count": 1}


# ENTRY POINT
# =====================================================================================

if __name__ == "__main__":
    forensics.take_snapshot("startup")

    # [VERSION: SESSION_ARCH_v2A_0] Instantiate ApplicationContext at process boot.
    # This is the single process-lifetime owner of all services and sessions.
    from application_context import ApplicationContext
    _app_ctx = ApplicationContext.get_instance()
    logger.info("✅ [SESSION_ARCH] ApplicationContext ready (Phase 2A — wiring only).")

    def handle_sigterm(*args):
        logger.info("🛑 SIGTERM received — container shutting down. Closing gracefuly...")
        # Destroy session cleanly on SIGTERM so memory is released before exit
        try:
            _app_ctx.destroy_session()
        except Exception:
            pass
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
            logger.error("❌ dashboard_server.py not found — Container will show 'failed to respond'")
            watchdog_thread.join()
        except Exception:
            logger.exception("❌ Dashboard server crashed")
            watchdog_thread.join()
