# =====================================================================================
# app/main.py  — SELF-HEALING ORCHESTRATOR
# [VERSION: DEPLOYMENT_v1.0.1] - Build & Deploy Pipeline Validation
#
# Flask (dashboard) runs in the MAIN thread so health checks get responses
# immediately. The watchdog loop and all scanners run as daemon threads in the background.
# =====================================================================================
import sys
import os
os.environ["OMP_NUM_THREADS"] = "1"  # Prevent pyarrow/OpenMP deadlocks in background threads
import time
import json
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
APP_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.abspath(os.path.join(APP_DIR, ".."))
for p in (APP_DIR, ROOT_DIR):
    if p not in sys.path:
        sys.path.insert(0, p)

from memory_profiler import MemoryProfiler
from forensics import forensics

IST = ZoneInfo("Asia/Kolkata")

def ist_converter(*args):
    timestamp = args[-1] if args else None
    if timestamp is None:
        timestamp = time.time()
    return datetime.fromtimestamp(timestamp, IST).timetuple()

logging.Formatter.converter = ist_converter
# [VERSION: LOGGING_STDOUT_FIX_v1.0] Route logs to stdout to prevent Railway interpreting all INFO as ERROR
logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s", datefmt="%Y-%m-%d %H:%M:%S", stream=sys.stdout)

from db_logger import install_db_logger
install_db_logger()

# 0. START FLASK DASHBOARD SERVER IMMEDIATELY (0ms latency for health checks & Coolify)
# Must start before any database connections, network requests, or heavy module imports
if "--worker" not in sys.argv:
    try:
        from dashboard_server import start_dashboard_server_async
        start_dashboard_server_async()
        logging.getLogger(__name__).info("🌐 [BOOT] Dashboard server started asynchronously — ports 8000/8080/80 open instantly for healthchecks!")
    except Exception as _d_err:
        logging.getLogger(__name__).error(f"❌ Could not start dashboard server: {_d_err}")

# [VERSION: PERF_PROFILER_v1.0] Capture process startup timestamp for boot latency telemetry.
# This lets us log how long the full boot sequence takes (imports, DB init, diagnostics).
import time as _time
_PROCESS_START_TIME = _time.monotonic()

logger = logging.getLogger(__name__)

from database import upsert_scanner_health, insert_notification
from config import DATA_DIR, WATCHLIST_PATH, SYSTEM_DEPLOYMENT_VERSION
from live_fundamental_scanner import run_fundamental_scan as _run_fundamental_scan

# Print high-visibility deployment version & process PID banner on startup
try:
    _pid = os.getpid()
    logger.info("======================================================================")
    logger.info(f"🚀 DEPLOYMENT VERSION: {SYSTEM_DEPLOYMENT_VERSION}")
    logger.info(f"⚙️ Process PID: {_pid}")
    logger.info(f"📅 Server Startup Time: {datetime.now(IST).strftime('%Y-%m-%d %H:%M:%S IST')}")
    logger.info("======================================================================")
except Exception:
    pass

# ─────────────────────────────────────────────────────────────────────────────
# STARTUP DIAGNOSTICS & TELEMETRY TIMERS
# ─────────────────────────────────────────────────────────────────────────────
import time as _time
_PROCESS_START_TIME = _time.monotonic()

# Map watchdog thread names to dashboard database keys
THREAD_TO_SCANNER = {
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
    "eod":      (dt_time(18, 30), dt_time(23, 59, 59)),
    "reversal": (dt_time(18, 30), dt_time(23, 59, 59)),
    "pullback": (dt_time(18, 30), dt_time(23, 59, 59)),
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
        from trading_calendar import default_trading_calendar
        if not default_trading_calendar.is_trading_day(now):
            logger.info(f"[{name}] 📅 Market Holiday — sleeping 1 hour...")
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

def wait_for_bhavcopy_or_fallback(name: str) -> bool:
    """Block until today's Bhavcopy is available, or fallback. Returns True if fallback used."""
    from delivery_data import fetch_delivery_data
    from database import upsert_scanner_health
    first_wait = True
    while True:
        now = datetime.now(IST)
        if now.weekday() >= 5:
            return True  # Weekend, no bhavcopy published
        from trading_calendar import default_trading_calendar
        if not default_trading_calendar.is_trading_day(now):
            return True  # Market holiday, no bhavcopy published
            
        try:
            # fetch_delivery_data handles caching and retries internally
            delivery_map = fetch_delivery_data(now.date())
            if delivery_map:
                logger.info(f"[{name}] ✅ Today's Bhavcopy is available!")
                return False
        except Exception as e:
            logger.warning(f"[{name}] Failed to fetch bhavcopy: {e}")
            
        if now.hour >= 21 or (now.hour == 20 and now.minute >= 30):
            logger.warning(f"[{name}] ⚠️ It's {now.strftime('%H:%M')} and today's Bhavcopy is still missing. Using fallback (yesterday).")
            return True
            
        logger.info(f"[{name}] ⏳ Today's Bhavcopy not yet available. Waiting 5 mins...")
        
        # [VERSION: BHAVCOPY_UI_STATUS] Expose the blocking state to the UI
        first_wait = False
            
        time.sleep(300)


# =====================================================================================
# WATCHLIST PRE-FLIGHT & FAIL-SAFE TELEMETRY
# =====================================================================================
from config import WATCHLIST_PATH
import threading as _threading
import sys
import traceback

_watchlist_ready = _threading.Event()
_watchlist_failed_or_blocked = _threading.Event()

WATCHLIST_BUILD_TIMEOUT_SECONDS = 300  # 5-minute hard execution timeout

def dump_all_thread_stacks(reason: str = "THREAD_HANG_TIMEOUT"):
    """
    Captures and logs stack traces for all active Python threads.
    Critical diagnostic tool when a background worker or scanner times out.
    """
    logger.critical(f"==================== THREAD STACK DUMP [{reason}] ====================")
    try:
        frames = sys._current_frames()
        threads_by_id = {t.ident: t for t in threading.enumerate()}
        for thread_id, frame in frames.items():
            t_obj = threads_by_id.get(thread_id)
            t_name = t_obj.name if t_obj else f"Thread-{thread_id}"
            is_daemon = getattr(t_obj, 'daemon', 'unknown')
            logger.critical(f"--- Thread: '{t_name}' (ID: {thread_id}, daemon={is_daemon}) ---")
            stack = traceback.format_stack(frame)
            for line in stack:
                logger.critical(line.rstrip())
    except Exception as dump_err:
        logger.critical(f"Failed to dump thread stacks: {dump_err}")
    logger.critical("====================================================================")


class WatchlistTracker:
    """Thread-safe watchdog telemetry state for WatchlistBuilder."""
    def __init__(self):
        self._lock = _threading.Lock()
        self.status = "NOT_STARTED"       # STARTING, RUNNING, READY, FAILED, TIMEOUT, STUCK, DEGRADED
        self.current_stage = "INIT"       # INIT, CHECKING_DISK, FETCHING_DB, REBUILDING_DAILY, READY, FAILED, STUCK_TIMEOUT
        self.started_at = None
        self.last_progress_at = None
        self.elapsed_seconds = 0.0
        self.error = None

    def update(self, stage: str, status: str = "RUNNING", error: str = None):
        with self._lock:
            now = time.time()
            if self.started_at is None:
                self.started_at = now
            self.current_stage = stage
            self.status = status
            self.last_progress_at = now
            self.elapsed_seconds = now - self.started_at
            if error:
                self.error = str(error)
            
            logger.info(f"📋 [WATCHLIST_BUILDER] Status='{self.status}' | Stage='{self.current_stage}' | Elapsed={self.elapsed_seconds:.1f}s")
            
            try:
                from database import upsert_scanner_health
                db_status = "OK" if status == "READY" else ("DOWN" if status in ("FAILED", "STUCK", "TIMEOUT") else "RUNNING")
                err_msg = f"Stage={self.current_stage} | Elapsed={int(self.elapsed_seconds)}s"
                if self.error:
                    err_msg += f" | Error={self.error[:150]}"
                upsert_scanner_health("WATCHLIST_BUILDER", status=db_status, error_msg=err_msg)
            except Exception:
                pass

    def mark_ready(self):
        self.update("READY", status="READY")
        _watchlist_ready.set()

    def mark_stuck(self, reason: str):
        self.update("STUCK_TIMEOUT", status="STUCK", error=reason)
        _watchlist_failed_or_blocked.set()

    def mark_failed(self, error: Exception):
        self.update("FAILED", status="FAILED", error=str(error))
        _watchlist_failed_or_blocked.set()

    def get_summary(self) -> dict:
        with self._lock:
            now = time.time()
            elapsed = (now - self.started_at) if self.started_at else 0.0
            return {
                "status": self.status,
                "current_stage": self.current_stage,
                "elapsed_seconds": round(elapsed, 1),
                "error": self.error
            }

watchlist_tracker = WatchlistTracker()


def _build_watchlist_background():
    t_name = threading.current_thread().name
    logger.info(f"🚀 [BACKGROUND WORKER START] Worker='{t_name}' | InitiatedBy='MainOrchestrator' | Action='Building or restoring fundamental watchlist'")
    _t_start = time.perf_counter()
    watchlist_tracker.update("CHECKING_LOCAL_DISK", "STARTING")

    def _inner_watchlist_work():
        with MemoryProfiler("Startup - Watchlist", force_gc_cleanup=True):
            if os.path.exists(WATCHLIST_PATH) and os.path.getsize(WATCHLIST_PATH) > 0:
                try:
                    import pandas as pd
                    df = pd.read_parquet(WATCHLIST_PATH)
                    if df is not None and not df.empty and len(df) > 5:
                        logger.info(f"✅ Watchlist found on disk ({len(df)} symbols) | {WATCHLIST_PATH}")
                        watchlist_tracker.mark_ready()
                        return
                except Exception as disk_err:
                    logger.warning(f"⚠️ Local watchlist file exists but unreadable: {disk_err}")

            watchlist_tracker.update("FETCHING_POSTGRES", "RUNNING")
            logger.info("📋 Watchlist missing/invalid | Attempting to restore or build in background thread...")
            try:
                from watchlist_cache import get_watchlist
                df_wl = get_watchlist()
                if df_wl is not None and not df_wl.empty and len(df_wl) > 5:
                    watchlist_tracker.mark_ready()
                    dur_s = time.perf_counter() - _t_start
                    logger.info(f"✅ [BACKGROUND WORKER COMPLETE] Worker='{t_name}' | Action='Watchlist build complete ({len(df_wl)} symbols)' | Duration={dur_s:.2f}s")
                else:
                    watchlist_tracker.update("REBUILDING_DAILY", "RUNNING")
                    if ensure_watchlist_exists_for_scanners():
                        watchlist_tracker.mark_ready()
                    else:
                        watchlist_tracker.mark_failed(RuntimeError("Watchlist build produced empty result"))
            except Exception as ex:
                logger.exception(f"❌ [BACKGROUND WORKER FAIL] Worker='{t_name}' | Action='Watchlist build failed' | Error={ex}")
                watchlist_tracker.mark_failed(ex)

    # Execute inner work in thread with hard join timeout
    work_thread = _threading.Thread(target=_inner_watchlist_work, name="WatchlistWorkerInner", daemon=True)
    work_thread.start()
    work_thread.join(timeout=WATCHLIST_BUILD_TIMEOUT_SECONDS)

    if work_thread.is_alive():
        reason = f"WatchlistBuilder inner worker timed out after {WATCHLIST_BUILD_TIMEOUT_SECONDS}s"
        logger.critical(f"🚨 [WATCHLIST_BUILDER_TIMEOUT] {reason}")
        dump_all_thread_stacks(reason="WATCHLIST_BUILDER_HARD_TIMEOUT")
        watchlist_tracker.mark_stuck(reason)
        # Attempt emergency fallback so downstream scanners have usable watchlist data
        try:
            logger.warning("⚠️ [WATCHLIST_BUILDER_TIMEOUT] Attempting emergency fallback watchlist creation...")
            if ensure_watchlist_exists_for_scanners():
                watchlist_tracker.update("DEGRADED_FALLBACK", status="DEGRADED")
                _watchlist_ready.set()
        except Exception as fallback_err:
            logger.error(f"❌ Emergency fallback watchlist creation failed: {fallback_err}")

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
_perf_tracker_lock = threading.Lock()

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
    if not _perf_tracker_lock.acquire(blocking=False):
        logger.info("🛑 [PERFORMANCE_TRACKER] In-memory lock held. Another pass is actively executing. Skipping.")
        try:
            from database import record_skipped_execution_run
            record_skipped_execution_run(scanner_name="PERFORMANCE_TRACKER", trigger_type="SCHEDULED", scheduler_name="CRON", stop_reason="In-memory lock held (previous run active)")
        except Exception:
            pass
        return
    start_time = time.time()
    run_ctx = None
    try:
        from database import start_scanner_execution_run, complete_scanner_execution_run
        run_ctx = start_scanner_execution_run(scanner_name="PERFORMANCE_TRACKER", trigger_type="SCHEDULED", scheduler_name="CRON")
        
        from telemetry_manager import telemetry
        telemetry.log_scheduler_event("PERFORMANCE_TRACKER", "CYCLE_START")
        build_performance_data(run_ctx=run_ctx)
        duration_sec = round(time.time() - start_time, 1)
        logger.info(f"✅ PERFORMANCE TRACKER | Refresh completed in {format_duration(duration_sec)}")
        upsert_scanner_health(
            "PERFORMANCE_TRACKER", status="OK",
            last_success=datetime.now(IST).isoformat(),
            scheduled_for="Every 5min (market hours)",
            duration_seconds=duration_sec
        )
        telemetry.log_scheduler_event("PERFORMANCE_TRACKER", "CYCLE_COMPLETE")
        complete_scanner_execution_run(run_ctx)
    except Exception as e:
        if "actively running" in str(e).lower():
            logger.info("⏳ PERFORMANCE_TRACKER is already actively running. Skipping duplicate pass.")
            return
        logger.exception("❌ PERFORMANCE TRACKER | Refresh failed")
        from telemetry_manager import telemetry
        telemetry.log_scheduler_event("PERFORMANCE_TRACKER", "CYCLE_FAILED", error=str(e))
        try:
            if run_ctx:
                complete_scanner_execution_run(run_ctx, exception=e)
            upsert_scanner_health(
                "PERFORMANCE_TRACKER", status="DOWN",
                error_msg=str(e)[:500],
                scheduled_for="Every 5min (market hours)"
            )
        except Exception:
            pass
    finally:
        if _perf_tracker_lock.locked():
            try:
                _perf_tracker_lock.release()
            except Exception:
                pass

# [DECOMMISSIONED] _run_multibagger_exit_single() permanently removed.


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
        # STEP 1: Check if local file exists and is usable (valid parquet with symbols)
        if os.path.exists(WATCHLIST_PATH):
            try:
                df = pd.read_parquet(WATCHLIST_PATH)
                if not df.empty and len(df) > 10:
                    logger.info(f"✅ [CACHE] Valid watchlist found on local disk ({len(df)} symbols).")
                    from watchlist_cache import get_watchlist
                    get_watchlist()
                    return True
            except Exception:
                pass
        
        logger.warning(f"⚠️ [CACHE] Local disk missing/invalid watchlist. Checking DB for latest watchlist data...")
        
        # STEP 2: Restore latest watchlist from DB (today first, then fallback to most recent)
        from database import download_parquet_from_db_today, download_parquet_from_db, upload_parquet_to_db
        db_master_v2 = os.path.join(DATA_DIR, "daily_builder_master_v2.parquet")
        if not os.path.exists(db_master_v2):
            try:
                download_parquet_from_db_today("daily_builder_master_v2", db_master_v2) or download_parquet_from_db("daily_builder_master_v2", db_master_v2)
            except Exception:
                pass
        elif os.path.exists(db_master_v2) and os.path.getsize(db_master_v2) > 0:
            try:
                upload_parquet_to_db("daily_builder_master_v2", db_master_v2)
            except Exception:
                pass

        # Restore PIT valuation history cache from DB if missing locally
        pit_val_json = os.path.join(DATA_DIR, "pit_valuation_history_cache.json")
        pit_val_parquet = os.path.join(DATA_DIR, "pit_valuation_history_cache.parquet")
        if not os.path.exists(pit_val_json) or os.path.getsize(pit_val_json) == 0:
            try:
                if download_parquet_from_db_today("pit_valuation_history_cache", pit_val_parquet) or download_parquet_from_db("pit_valuation_history_cache", pit_val_parquet):
                    import pandas as _pd
                    _df_vc = _pd.read_parquet(pit_val_parquet)
                    if not _df_vc.empty and "symbol" in _df_vc.columns:
                        _vc_dict = {r["symbol"]: r for r in _df_vc.to_dict(orient="records")}
                        _b_c = sum(1 for r in _vc_dict.values() if (r.get('ev_ebitda_3y_median') is not None and not pd.isna(r.get('ev_ebitda_3y_median')) and float(r.get('ev_ebitda_3y_median') or 0) > 0) and (r.get('pe_3y_median') is not None and not pd.isna(r.get('pe_3y_median')) and float(r.get('pe_3y_median') or 0) > 0))
                        _c_st = "CERTIFIED" if _b_c == len(_vc_dict) and len(_vc_dict) > 0 else "PARTIAL_INCOMPLETE"
                        with open(pit_val_json, "w") as _f_vc:
                            json.dump({
                                "total_symbols": len(_vc_dict),
                                "both_required_complete_count": _b_c,
                                "certification_status": _c_st,
                                "data": _vc_dict
                            }, _f_vc, indent=2)
                        _c_icon = "✅" if _c_st == "CERTIFIED" else "⚠️"
                        logger.info(f"{_c_icon} [DB] Restored {len(_vc_dict)} PIT valuation medians from database | certification_status={_c_st} | both_required={_b_c}/{len(_vc_dict)}")
            except Exception as _vc_err:
                logger.debug(f"PIT valuation DB restore notice: {_vc_err}")
        elif os.path.exists(pit_val_parquet) and os.path.getsize(pit_val_parquet) > 0:
            try:
                upload_parquet_to_db("pit_valuation_history_cache", pit_val_parquet)
            except Exception:
                pass

        if download_parquet_from_db_today("daily_builder", WATCHLIST_PATH) or download_parquet_from_db("daily_builder", WATCHLIST_PATH):
            if os.path.exists(WATCHLIST_PATH):
                logger.info(f"✅ [DB] Watchlist successfully restored from DB to local disk.")
                from watchlist_cache import get_watchlist
                get_watchlist()
                return True
        
        # STEP 3: If no watchlist exists anywhere, trigger Daily Builder
        logger.warning(f"⚠️ [REBUILD] No watchlist in DB or disk. Triggering Daily Builder for {today_str}...")
        try:
            from daily_builder import main as build_watchlist
            build_watchlist(force_rebuild=True)
            from watchlist_cache import get_watchlist
            get_watchlist()
            return True
        except Exception as e:
            if "actively running" in str(e).lower():
                logger.info("⏳ Daily Builder is actively running.")
                return False
            logger.exception(f"❌ Daily Builder rebuild FAILED: {e}")
            return False
        
        # STEP 4: Verify fresh data was created
        if is_disk_fresh():
            logger.info(f"✅ [NEW] Fresh watchlist created for {today_str}. Ready to scan.")
            return True
        else:
            logger.error(f"❌ [NEW] Fresh watchlist created but failed freshness check!")
            return False

def block_until_watchlist_ready(timeout_seconds: float = 300.0) -> bool:
    """
    Blocks until the watchlist is ready.
    NEVER waits indefinitely.
    
    Returns True if watchlist is ready.
    Returns False if timeout is reached or watchlist build failed/stuck.
    """
    if _watchlist_ready.is_set():
        return True
        
    start_t = time.monotonic()
    logger.info(f"⏳ [WATCHLIST_WAIT] Waiting up to {timeout_seconds}s for WatchlistBuilder readiness...")
    
    while time.monotonic() - start_t < timeout_seconds:
        if _watchlist_ready.is_set():
            logger.info("✅ Watchlist is ready. Unblocking caller.")
            return True
            
        if _watchlist_failed_or_blocked.is_set():
            logger.error("❌ [WATCHLIST_WAIT] WatchlistBuilder failed or is marked BLOCKED.")
            if ensure_watchlist_exists_for_scanners():
                _watchlist_ready.set()
                logger.info("✅ Emergency fallback watchlist restored. Unblocking caller.")
                return True
            return False
            
        time.sleep(2)
        
    elapsed = time.monotonic() - start_t
    logger.critical(f"🚨 [WATCHLIST_WAIT_TIMEOUT] Timed out waiting for watchlist readiness after {elapsed:.1f}s!")
    dump_all_thread_stacks(reason=f"BLOCK_UNTIL_WATCHLIST_READY_TIMEOUT_{int(elapsed)}S")
    
    if ensure_watchlist_exists_for_scanners():
        _watchlist_ready.set()
        logger.info("✅ Emergency fallback watchlist restored after wait timeout. Unblocking caller.")
        return True
        
    try:
        from database import upsert_scanner_health
        upsert_scanner_health("WATCHLIST_BUILDER", status="DOWN", error_msg=f"Timeout waiting for watchlist after {int(elapsed)}s")
    except Exception:
        pass
        
    return False


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


def ensure_watchlist_exists_for_scanners():
    """Guarantees WATCHLIST_PATH exists on disk before running scanners, restoring or creating fallback if needed."""
    from config import WATCHLIST_PATH
    import os, pandas as pd
    if os.path.exists(WATCHLIST_PATH):
        try:
            df = pd.read_parquet(WATCHLIST_PATH)
            if not df.empty and len(df) > 5:
                return True
        except Exception:
            pass

    # Try restoring from DB first
    try:
        from database import download_parquet_from_db_today, download_parquet_from_db
        if download_parquet_from_db_today("daily_builder", WATCHLIST_PATH) or download_parquet_from_db("daily_builder", WATCHLIST_PATH):
            if os.path.exists(WATCHLIST_PATH):
                from watchlist_cache import get_watchlist
                get_watchlist()
                return True
    except Exception:
        pass

    # Build emergency fallback parquet from DB symbols
    try:
        from database import get_connection
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT DISTINCT symbol FROM (
                        SELECT symbol FROM user_watchlists WHERE symbol IS NOT NULL AND symbol != ''
                        UNION
                        SELECT symbol FROM alerts WHERE symbol IS NOT NULL AND symbol != ''
                        UNION
                        SELECT symbol FROM stock_analysis_master WHERE symbol IS NOT NULL AND symbol != ''
                    ) sub LIMIT 500;
                """)
                rows = cur.fetchall()
                syms = [r[0] for r in rows if r[0]]
                if not syms:
                    syms = ["RELIANCE", "TCS", "INFY", "HDFCBANK", "ICICIBANK", "SBIN", "BHARTIARTL", "ITC", "LTIM", "TMPV"]
                df_fallback = pd.DataFrame({
                    "Stock": syms,
                    "Symbol": syms,
                    "Category": ["High Momentum"] * len(syms),
                    "FM_Score": [75.0] * len(syms),
                    "Scan Time": [datetime.now(IST).strftime("%Y-%m-%d %H:%M:%S")] * len(syms),
                    "Close": [500.0] * len(syms),
                    "Volume": [1000000] * len(syms),
                })
                os.makedirs(os.path.dirname(WATCHLIST_PATH), exist_ok=True)
                df_fallback.to_parquet(WATCHLIST_PATH, index=False)
                logger.info(f"✅ Created fallback watchlist ({len(syms)} symbols) at {WATCHLIST_PATH}")
                from watchlist_cache import get_watchlist
                get_watchlist()
                return True
    except Exception as fe:
        logger.warning(f"Fallback watchlist creation warning: {fe}")
        return False


def run_all_seven_scanners_non_market_boot():
    """
    Executes a single catch-up pass of ALL PRIMARY SCANNERS sequentially in the exact sequence
    as displayed on the System Health dashboard card grid when the server restarts during non-market hours.
    Sequence (matches Health Card Grid):
      1. DAILY_BUILDER (Watchlist Builder)
      2. TECHNICAL (Technical Scanner)
      3. Wealth Engine (Wealth Engine)
    """
    def _run_batch():
        logger.info("======================================================================")
        logger.info("🌙 [NON-MARKET HOURS BOOT] Server restarted outside market hours.")
        logger.info("🚀 Triggering 1-pass catchup execution for ALL PRIMARY SCANNERS in Health Dashboard sequence...")
        logger.info("======================================================================")
        
        try:
            from database import cleanup_orphaned_scanner_runs_on_boot
            cleanup_orphaned_scanner_runs_on_boot()
        except Exception as e:
            logger.warning(f"⚠️ [NON-MARKET BOOT] Cleanup warning: {e}")

        # [RULE 67 CHANGE-RATIONALE]:
        # Sequence DAILY_BUILDER first so the daily watchlist is built/refreshed
        # before downstream technical and fundamental engines execute.
        # Decommissioned scanners (ACCUMULATION, EOD, REVERSAL, PULLBACK, MULTIBAGGER) are permanently purged.
        all_scanners = [
            ("DAILY_BUILDER", _trigger_daily_builder),
            ("TECHNICAL", _trigger_technical),
            ("Wealth Engine", _trigger_wealth_engine),
        ]

        from database import is_scanner_stopped, upsert_scanner_health
        
        # 1. Mark all non-stopped scanners as QUEUED with their explicit queue position
        for idx, (name, _) in enumerate(all_scanners, 1):
            if not is_scanner_stopped(name):
                upsert_scanner_health(name, status=f"QUEUED-{idx}", error_msg=f"Waiting in non-market boot queue (position {idx} of {len(all_scanners)})...")

        # 2. Ensure watchlist file exists for scanners (no infinite sleep lock!)
        ensure_watchlist_exists_for_scanners()

        # 3. Execute all primary scanners sequentially one-by-one
        # [VERSION: BOOT_SEQUENCE_FIX_v1.0] [RULE 67 CHANGE-RATIONALE]
        # Wrap sequence execution in try/finally to clear stale QUEUED statuses on boot batch completion.
        # Inside the exception block, explicitly upsert health status as DOWN so exceptions do not result in stale QUEUED states.
        try:
            for idx, (name, fn) in enumerate(all_scanners, 1):
                if is_scanner_stopped(name):
                    logger.info(f"⏭️ [NON-MARKET BOOT] ({idx}/{len(all_scanners)}) {name} is STOPPED by Admin. Skipping.")
                    continue

                logger.info(f"▶️ [NON-MARKET BOOT] ({idx}/{len(all_scanners)}) Running Scanner: {name}...")
                start_t = time.time()
                try:
                    import inspect
                    sig = inspect.signature(fn)
                    if "trigger_type" in sig.parameters:
                        fn(trigger_type="NON_MARKET_BOOT", scheduler_name="NON_MARKET_BOOT")
                    else:
                        fn()
                    dur = round(time.time() - start_t, 1)
                    logger.info(f"✅ [NON-MARKET BOOT] ({idx}/{len(all_scanners)}) {name} completed in {format_duration(dur)}.")
                    try:
                        from database import get_scanner_health
                        curr_h = get_scanner_health(name)
                        if curr_h and str(curr_h.get("status", "")).startswith("QUEUED"):
                            upsert_scanner_health(name, status="IDLE", error_msg=None)
                    except Exception:
                        pass
                except Exception as exc:
                    dur = round(time.time() - start_t, 1)
                    logger.exception(f"❌ [NON-MARKET BOOT] ({idx}/{len(all_scanners)}) {name} failed after {format_duration(dur)}: {exc}")
                    try:
                        upsert_scanner_health(name, status="DOWN", error_msg=f"Boot scan failed: {str(exc)[:250]}")
                    except Exception as status_err:
                        logger.warning(f"⚠️ Could not set health to DOWN for failed boot scanner {name}: {status_err}")

                time.sleep(3)
        finally:
            try:
                # [VERSION: BOOT_CLEANUP_CONCURRENCY_v1.0] [RULE 67 CHANGE-RATIONALE]
                # Only reset QUEUED status to IDLE for scanners that were part of this boot sequence.
                # Unrelated concurrent queued scanners MUST remain untouched.
                scanner_names = [name for name, _ in all_scanners]
                from database import get_connection
                with get_connection() as conn:
                    with conn.cursor() as cur:
                        cur.execute("""
                            UPDATE scanner_health
                            SET status = 'IDLE',
                                error_msg = 'Boot sequence completed — status reset from QUEUED',
                                updated_at = NOW()
                            WHERE (status = 'QUEUED' OR status LIKE 'QUEUED%%')
                                AND status NOT IN ('PAUSED', 'STOPPED')
                                AND scanner_name = ANY(%s);
                        """, (scanner_names,))
                    conn.commit()
                logger.info("🧹 Cleaned up any remaining QUEUED statuses from boot sequence.")
            except Exception as cleanup_err:
                logger.warning(f"⚠️ Failed to clean up QUEUED statuses after boot sequence: {cleanup_err}")

        logger.info("======================================================================")
        logger.info(f"✅ [NON-MARKET HOURS BOOT] Completed single catch-up pass of all {len(all_scanners)} scanners.")
        logger.info("======================================================================")

    import threading
    t = threading.Thread(target=_run_batch, name="NonMarketBootBatch", daemon=True)
    t.start()


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
    last_technical_date = None
    last_technical_intraday_run = None
    last_wealth_daily_date = None
    last_wealth_preclose_date = None

    def safe_run_daily_builder():
        """Helper to run the builder and update the memory cache."""
        from database import is_scanner_stopped
        if is_scanner_stopped("DAILY_BUILDER"):
            logger.info("⏸️ [DAILY_BUILDER] Scanner is PAUSED/STOPPED by Admin. Skipping 5:00 AM scheduled trigger.")
            return False
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
                                build_watchlist(run_ctx=run_ctx)
                                if run_ctx:
                                    complete_scanner_execution_run(run_ctx)
                            except Exception as db_err:
                                if run_ctx:
                                    complete_scanner_execution_run(run_ctx, exception=db_err)
                                raise db_err
                except Exception as db_err:
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
                    scheduled_for="Daily 05:00 IST",
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
                    scheduled_for="Daily 05:00 IST"
                )
            except Exception:
                pass
            return False

    def safe_run_wealth_scan_initial():
        """Run Wealth Engine at 2:00 AM with fresh watchlist."""
        from database import is_scanner_stopped
        if is_scanner_stopped("Wealth Engine"):
            logger.info("⏸️ [Wealth Engine] Scanner is PAUSED/STOPPED by Admin. Skipping 6:00 AM initial scan.")
            return False
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
                scheduled_for="Daily 06:00 IST (Initial)",
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
                scheduled_for="Daily 06:00 IST (Initial)"
            )
            return False

    def safe_run_wealth_market_hours():
        """Run Wealth Engine Exit Monitor during market hours (5-min position CMP/Trailing Stop Loss update)."""
        nonlocal last_wealth_market_run
        start_time = time.time()
        try:
            now = datetime.now(IST)
            start_time = time.time()
            # Only run once per 5 minutes (300 seconds)
            if last_wealth_market_run and (now - last_wealth_market_run).total_seconds() < 300:
                return False
            last_wealth_market_run = now

            if not is_scanner_stopped("WEALTH_EXIT"):
                logger.info(f"🕒 SCHEDULER | [{now.strftime('%H:%M')}] Triggering Wealth Engine Intraday Update (5-min exit loop)")
                from telemetry_manager import telemetry
                telemetry.log_scheduler_event("WEALTH_ENGINE_5M", "CYCLE_START")
                _exit_start_t = time.time()
                with MemoryProfiler("WEALTH_ENGINE_5M", force_gc_cleanup=True):
                    from wealth_engine import run_wealth_intraday_update
                    res = run_wealth_intraday_update(write_health=True)
                if res is not None:
                    duration_sec = round(time.time() - _exit_start_t, 1)
                    logger.info(f"✅ Wealth Engine (market hours) exit update completed in {format_duration(duration_sec)}")
                    upsert_scanner_health(
                        "WEALTH_EXIT",
                        status="OK",
                        last_success=datetime.now(IST).isoformat(),
                        scheduled_for="Every 5min (09:15 - 15:30 IST)",
                        duration_seconds=duration_sec
                    )
            else:
                logger.info("⏭️ WEALTH_EXIT is PAUSED by Admin. Skipping 5-min Wealth exit update.")
            
            last_wealth_market_run = now
            return True
        except Exception as e:
            if "actively running" in str(e).lower():
                logger.info("⏳ Wealth Engine Exit is actively running. Skipping duplicate pass.")
                return False
            logger.exception("❌ SCHEDULER | WEALTH_EXIT (market hours) crashed")
            upsert_scanner_health(
                "WEALTH_EXIT",
                status="DOWN",
                error_msg=str(e)[:500],
                scheduled_for="Every 5min (09:15 - 15:30 IST)"
            )
            return False

    def verify_scans():
        """Verify file readiness at 8:30 AM or boot."""
        logger.info("🕒 SCHEDULER | Verifying file readiness for today's scan")
        now = datetime.now(IST)
        today_str = now.strftime("%Y-%m-%d")
        WEALTH_PATH = os.path.join(DATA_DIR, "elite_wealth_system.parquet")

        # 0. Restore Historical Parquet Cache from DB (<0.5s cold boot restoration)
        # Wait up to 15 seconds for database connection pool readiness
        try:
            import time
            from database import get_connection
            db_connected = False
            for attempt in range(5):
                try:
                    with get_connection() as conn:
                        with conn.cursor() as cur:
                            cur.execute("SELECT 1")
                    db_connected = True
                    logger.info("✅ SCHEDULER | Database connection pool is ready. Proceeding with history bundle restoration.")
                    break
                except Exception as conn_err:
                    logger.warning(f"⏳ SCHEDULER | Waiting for database connection pool... (attempt {attempt+1}/5): {conn_err}")
                    time.sleep(3)

            if db_connected:
                from database import restore_history_bundle_from_db
                for _tf in ("1d", "1h", "30m", "15m", "5m"):
                    restore_history_bundle_from_db(_tf)
            else:
                logger.error("❌ SCHEDULER | Database connection pool failed to initialize. Skipping history bundle restoration.")
        except Exception as hb_err:
            logger.debug(f"History bundle restore check at boot: {hb_err}")

        # ── PIT FUNDAMENTALS + VALUATION CACHE BOOT SEEDING ───────────────────
        # [FIX: VALUATION_DATA_UNAVAILABLE] The valuation cache restore was previously
        # buried inside verify_watchlist_is_pristine() which is skipped on a fresh-watchlist
        # boot, so the production container never received pe_3y_median / ev_ebitda_3y_median.
        # This block runs unconditionally at every boot, independent of watchlist state.
        try:
            from database import upload_parquet_to_db, download_parquet_from_db, download_parquet_from_db_today
            pit_parquet_dir = os.path.join(DATA_DIR, "pit_fundamentals_v1")
            pit_parquet_path = os.path.join(pit_parquet_dir, "pit_fundamentals_v1.parquet")

            # Step 1: If pit_fundamentals_v1.parquet exists locally → upload to DB so containers can restore it
            if os.path.exists(pit_parquet_path) and os.path.getsize(pit_parquet_path) > 0:
                try:
                    upload_parquet_to_db("pit_fundamentals_v1", pit_parquet_path)
                    logger.info("⚡ [PIT BOOT] Uploaded pit_fundamentals_v1.parquet to DB parquet_cache")
                except Exception as _pit_up_err:
                    logger.debug(f"PIT fundamentals DB upload notice: {_pit_up_err}")
            else:
                # Step 2: Not local → restore from DB so the valuation builder can run
                try:
                    os.makedirs(pit_parquet_dir, exist_ok=True)
                    if download_parquet_from_db("pit_fundamentals_v1", pit_parquet_path):
                        logger.info("✅ [PIT BOOT] Restored pit_fundamentals_v1.parquet from DB")
                    else:
                        logger.warning("⚠️ [PIT BOOT] pit_fundamentals_v1.parquet not in DB — valuation builder will be limited")
                except Exception as _pit_dl_err:
                    logger.debug(f"PIT fundamentals DB restore notice: {_pit_dl_err}")

            # Step 3: Restore or on-fly-build pit_valuation_history_cache
            pit_val_json = os.path.join(DATA_DIR, "pit_valuation_history_cache.json")
            pit_val_parquet = os.path.join(DATA_DIR, "pit_valuation_history_cache.parquet")
            cache_missing = not os.path.exists(pit_val_json) or os.path.getsize(pit_val_json) == 0

            if not cache_missing:
                # Cache exists locally → upload to DB so containers can restore it
                import json as _json
                try:
                    with open(pit_val_json) as _f_vc:
                        _vc_payload = _json.load(_f_vc)
                    _vc_data = _vc_payload.get("data", {})
                    if _vc_data:
                        import pandas as _pd_vc
                        _df_vc_up = _pd_vc.DataFrame(list(_vc_data.values()))
                        _df_vc_up.to_parquet(pit_val_parquet, index=False)
                        upload_parquet_to_db("pit_valuation_history_cache", pit_val_parquet)
                        logger.info(f"⚡ [PIT BOOT] Uploaded {len(_vc_data)} valuation medians to DB parquet_cache")
                except Exception as _vc_up_err:
                    logger.debug(f"Valuation cache upload notice: {_vc_up_err}")
            else:
                # Cache missing locally → try DB restore first
                db_restored = False
                try:
                    if download_parquet_from_db_today("pit_valuation_history_cache", pit_val_parquet) or \
                       download_parquet_from_db("pit_valuation_history_cache", pit_val_parquet):
                        import pandas as _pd_vc
                        import json as _json
                        _df_vc = _pd_vc.read_parquet(pit_val_parquet)
                        if not _df_vc.empty and "symbol" in _df_vc.columns:
                            _vc_dict = {r["symbol"]: r for r in _df_vc.to_dict(orient="records")}
                            with open(pit_val_json, "w") as _f_vc:
                                _json.dump({"generated_at": datetime.now(IST).isoformat(),
                                            "total_symbols": len(_vc_dict), "data": _vc_dict}, _f_vc, indent=2)
                            logger.info(f"✅ [PIT BOOT] Restored {len(_vc_dict)} valuation medians from DB")
                            db_restored = True
                except Exception as _vc_dl_err:
                    logger.debug(f"Valuation cache DB restore notice: {_vc_dl_err}")

                if not db_restored:
                    # DB also empty → build from 1D history + PIT filings (self-healing)
                    try:
                        from pit_valuation_history_builder import build_pit_valuation_history
                        logger.info("🔧 [PIT BOOT] Cache missing in DB — building from 1D history + PIT filings (self-heal)...")
                        _vc_built = build_pit_valuation_history(save_cache=True, upload_db=True)
                        if _vc_built:
                            logger.info(f"✅ [PIT BOOT] Self-healed: built {len(_vc_built)} valuation medians and uploaded to DB")
                        else:
                            logger.warning("⚠️ [PIT BOOT] Self-heal build returned empty — pit_fundamentals_v1.parquet or 1D history may be missing")
                    except Exception as _vc_build_err:
                        logger.warning(f"⚠️ [PIT BOOT] Self-heal build failed: {_vc_build_err}")
        except Exception as _pit_boot_err:
            logger.warning(f"⚠️ [PIT BOOT] Valuation cache boot seeding error: {_pit_boot_err}")
        # ── END PIT FUNDAMENTALS + VALUATION CACHE BOOT SEEDING ───────────────

        # 1. Verify Watchlist (with full date-aware cache/DB/rebuild logic)
        logger.info(f"🕒 SCHEDULER | Step 1: Verifying watchlist freshness for {today_str}")
        if not verify_watchlist_is_pristine():
            logger.warning("📋 Watchlist is missing/stale on boot. Checking if Daily Builder can build watchlist...")
            from database import is_scanner_stopped
            if not is_scanner_stopped("DAILY_BUILDER"):
                _trigger_daily_builder()
            else:
                logger.info("⏸️ [DAILY_BUILDER] is PAUSED/STOPPED by Admin. Skipping boot watchlist rebuild.")

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
                    except Exception as e:
                        logger.exception(f"Failed to restore wealth: {e}")
        except Exception as e:
            logger.exception(f"Failed to verify wealth system: {e}")

        logger.info("✅ SCHEDULER | File readiness verification complete")

    # [DECOMMISSIONED] safe_run_multibagger_scan_initial() permanently removed.

    logger.info("🕒 SCHEDULER | Started (custom time-based scheduler)")
    
    # [VERSION: BOOT_TEST_SCAN_MARKET_HOURS_SKIP_v1.0] Skip post-deployment / startup test scans if within market hours (9:00 AM - 3:45 PM IST)
    from market_utils import is_within_custom_hours
    from datetime import time as dt_time
    now_boot = datetime.now(IST)
    is_market_hours_boot = is_within_custom_hours(dt_time(9, 0), dt_time(15, 45), now_boot)

    if is_market_hours_boot:
        logger.info("⏰ Startup / Deployment during MARKET HOURS (9:00 AM - 3:45 PM IST) — Skipping initial boot scans.")
        verify_scans()
    else:
        logger.info("🌙 Startup during NON-MARKET HOURS — Executing single catch-up pass of ALL SEVEN SCANNERS...")
        verify_scans()
        run_all_seven_scanners_non_market_boot()
        try:
            from telemetry_manager import telemetry
            telemetry.log_scheduler_event("PERFORMANCE_TRACKER_BOOT", "CYCLE_START")
            _run_performance_tracker_single()
            telemetry.log_scheduler_event("PERFORMANCE_TRACKER_BOOT", "CYCLE_COMPLETE")
        except Exception as e:
            logger.error(f"Boot perf tracker failed: {e}")

    # Main scheduler loop state variables
    from market_utils import is_within_custom_hours
    from datetime import time as dt_time
    now_boot = datetime.now(IST)
    is_market_boot = is_within_custom_hours(dt_time(9, 0), dt_time(15, 45), now_boot)

    last_mb_exit = None
    last_perf = None
    daily_builder_ran = False
    wealth_initial_ran = False
    verify_scans_ran = False
    last_rotation_date = now_boot.date()
    evening_scanners_ran = True if not is_market_boot else False
    evening_batch_deadline_logged = False
    warmup_ran = False
    last_technical_date = now_boot.date() if not is_market_boot else None
    last_wealth_daily_date = now_boot.date() if not is_market_boot else None
    
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

            # Multibagger cold start removed; runs at 5:30 PM (17:30 IST) Daily
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
                # [RULE 67 CHANGE-RATIONALE: EXIT_MONITORS_DAEMON_THREADS_v1.0]
                # Exit monitors run in daemon background threads so the scheduler loop is NEVER
                # blocked. Previously even after removing global_scanner_lock, the monitors ran
                # synchronously in the loop — if run_wealth_intraday_update took 60-90s it delayed
                # the slot checks. Each monitor has its own dedup guards so
                # parallel threads are safe:
                #   - _run_multibagger_exit_single: is_scanner_stopped + run_standalone_exit_monitor own guards
                #   - _run_performance_tracker_single: _perf_rebuild_lock + is_scanner_stopped
                #   - safe_run_wealth_market_hours: last_wealth_market_run throttle + is_scanner_stopped
                import threading as _threading

                # 1. [DECOMMISSIONED] Multibagger Exit Monitor removed — MULTIBAGGER scanner purged.

                # 2. Performance Tracker / Alert Exit Monitor (every 5 mins)
                if not last_perf or (now - last_perf).total_seconds() >= 300:
                    last_perf = datetime.now(IST)  # set before thread start to prevent double-fire
                    _threading.Thread(
                        target=_run_performance_tracker_single,
                        name=f"PerfTracker-{now.strftime('%H%M')}",
                        daemon=True
                    ).start()

                # 3. Wealth Engine Pre-Close Guard Pulse (3:15 PM IST / 15:15 IST)
                if now.hour == 15 and now.minute >= 15 and last_wealth_preclose_date != now.date():
                    last_wealth_preclose_date = now.date()
                    logger.info("🕒 SCHEDULER | [15:15 IST] Triggering 3:15 PM Pre-Close Exit Guard Pulse for Wealth Engine")
                    _threading.Thread(
                        target=_trigger_wealth_exit,
                        name=f"WealthExit-1515-{now.strftime('%Y%m%d')}",
                        daemon=True
                    ).start()

                check_scanner_staleness(now)

            # 18:30 - Evening Daily Maintenance (Post-Bhavcopy Delivery & 6:30 PM Exit Monitor Pulse)
            if (now.hour > 18 or (now.hour == 18 and now.minute >= 30)) and not evening_scanners_ran:
                evening_scanners_ran = True

                def _run_evening_batch_async():
                    wait_for_bhavcopy_or_fallback("EVENING_MAINTENANCE")
                    logger.info("🕒 SCHEDULER | [18:30 IST] Triggering 6:30 PM Evening Exit Monitor Pulse for Wealth Engine")
                    try:
                        _trigger_wealth_exit()
                    except Exception as _e_exit:
                        logger.error(f"❌ Evening WEALTH_EXIT run error: {_e_exit}")
                    logger.info("🛡️ [GOVERNANCE] Post-Bhavcopy evening cycle complete.")

                import threading
                threading.Thread(target=_run_evening_batch_async, name="EveningMaintenance", daemon=True).start()
            elif now.hour < 18 or (now.hour == 18 and now.minute < 30):
                evening_scanners_ran = False
                evening_batch_deadline_logged = False

            # 18:15 - Technical Scanner (Post-Close Multi-Pattern Technical Scan)
            if (now.hour > 18 or (now.hour == 18 and now.minute >= 15)) and last_technical_date != now.date():
                last_technical_date = now.date()
                if not is_scanner_stopped("TECHNICAL"):
                    logger.info("🕒 SCHEDULER | [18:15] Triggering TECHNICAL scanner (Multi-Pattern Technical Scan)")
                    import threading
                    threading.Thread(target=_trigger_technical, kwargs={"trigger_type": "SCHEDULED", "scheduler_name": "CRON"}, name="TechnicalScanner", daemon=True).start()
                else:
                    logger.info("⏭️ TECHNICAL is STOPPED by Admin. Skipping 18:15 run.")

            # 17:00 - Wealth Engine Full Daily Scan (Post-Market Valuation & DCF Review)
            if (now.hour > 17 or (now.hour == 17 and now.minute >= 0)) and last_wealth_daily_date != now.date():
                last_wealth_daily_date = now.date()
                if not is_scanner_stopped("Wealth Engine"):
                    logger.info("🕒 SCHEDULER | [17:00] Triggering WEALTH ENGINE Full Daily Scan")
                    import threading
                    threading.Thread(target=_trigger_wealth_engine, kwargs={"trigger_type": "SCHEDULED", "scheduler_name": "CRON"}, name="WealthEngineDaily", daemon=True).start()
                else:
                    logger.info("⏭️ Wealth Engine is STOPPED by Admin. Skipping 17:00 IST daily scan.")

            # [DECOMMISSIONED] 17:30 MULTIBAGGER scanner slot permanently removed.

            # Earnings Calendar removed — earnings data was unused and added latency.

            # Midnight session rotation — triggered once on date boundary
            if last_rotation_date != now.date():
                last_rotation_date = now.date()
                try:
                    from application_context import ApplicationContext
                    ApplicationContext.get_instance().new_trading_day()
                    logger.info("🌙 [SESSION_ARCH] Midnight rotation complete — old session destroyed.")
                except Exception as _me:
                    logger.warning(f"⚠️ [SESSION_ARCH] Midnight session rotation failed: {_me}")

        # [DECOMMISSIONED] Saturday MULTIBAGGER fundamental refresh slot permanently removed.

        # Sleep tight, loop runs approximately every 15 seconds for precision timing
        time.sleep(15)


def check_scanner_staleness(now):
    """Check if any active scanner has gone stale (no heartbeat in expected cadence × 3).
    
    Runs during market hours only. If a scanner's last_success is too old,
    marks it DOWN and sends a Telegram + in-app notification.
    """
    # Expected max gap (in minutes) for each scanner before it's considered stale
    SCANNER_CADENCE = {
        "TECHNICAL":                          "DAILY",  # runs full scan once daily post-close at 18:15 IST
        "PERFORMANCE_TRACKER":                15,       # runs every 5 min
        "WEALTH_EXIT":                        15,       # runs every 5 min during market hours
        "Wealth Engine":                      "DAILY",  # runs full scan once daily at 17:00 IST
        "QUALITY_COMPOUNDER_VALUE_V2_FINAL":  "DAILY",  # runs full scan once daily at 17:00 IST
        "DAILY_BUILDER":                      "DAILY",
    }
    
    # Throttle: only run this check every 15 minutes
    if not hasattr(check_scanner_staleness, '_last_check'):
        check_scanner_staleness._last_check = None
    
    if check_scanner_staleness._last_check and (now - check_scanner_staleness._last_check).total_seconds() < 900:
        return
    check_scanner_staleness._last_check = now

    # Boot grace period: Give scanners 30 minutes after boot before checking intraday staleness
    if time.monotonic() < 1800:
        return
    
    try:
        from database import get_all_scanner_health, upsert_scanner_health, insert_notification
        health_rows = get_all_scanner_health()
        
        for row in health_rows:
            sc = row.get("scanner_name")
            if sc not in SCANNER_CADENCE:
                continue
            
            # Skip if already DOWN, currently executing (RUNNING/QUEUED), or intentionally paused
            if row.get("status") in ("DOWN", "RUNNING", "QUEUED", "STOPPED", "PAUSED"):
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
                    from market_utils import is_market_open
                    # [RULE 67 CHANGE-RATIONALE]:
                    # 1. Intraday monitors (PERFORMANCE_TRACKER, WEALTH_EXIT) only run during active market hours (09:15-15:30 IST weekdays).
                    #    Outside market hours (nights, weekends), large gaps are expected; skipping staleness check prevents false alarms.
                    # 2. When stale DURING market hours, attempt auto-triggering first before declaring DOWN status.
                    if is_market_open(now):
                        max_gap = cadence
                        if gap_minutes > max_gap:
                            try:
                                from main import trigger_scanner_manual
                                auto_res = trigger_scanner_manual(sc)
                                if auto_res and (auto_res.get("status") in ("success", "ok", "queued") or "already actively running" in str(auto_res.get("message", "")).lower()):
                                    logger.info(f"🔄 [AUTO-RECOVERY] Auto-started or actively running scanner '{sc}' (gap: {int(gap_minutes)}m)")
                                    continue
                                else:
                                    is_stale = True
                                    fail_reason = auto_res.get("message", "Trigger failed") if auto_res else "Trigger failed"
                                    stale_msg = f"Stale: No heartbeat in {int(gap_minutes)}m (Auto-start failed: {fail_reason})"
                            except Exception as _trig_err:
                                is_stale = True
                                stale_msg = f"Stale: No heartbeat in {int(gap_minutes)}m (Auto-start error: {_trig_err})"
                
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
# =====================================================================================

from ai_worker import run_worker_loop as run_ai_loop
from pledge_worker import worker_loop as run_pledge_loop

# [DECOMMISSIONED] run_multibagger_exit_monitor() and _run_multibagger_scanner_single() permanently removed.


RESTARTABLE_THREADS = {
    "AI Worker":          run_ai_loop,
    "Pledge Worker":      run_pledge_loop,
    "SystemScheduler":    run_system_scheduler,
}

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
    logger.info("🚀 [BOOT] Starting all background system threads...")
    for name, target in ALL_THREADS.items():
        try:
            start_thread(name, target)
            logger.info(f"✅ [BOOT] Started background thread: {name}")
        except Exception as _st_err:
            logger.error(f"❌ [BOOT] Failed to start thread {name}: {_st_err}")

    logger.info("=" * 70)
    logger.info("🛡️  SELF-HEALING WATCHDOG ACTIVE | All Scanners Initialized")
    logger.info("🌐  Dashboard: http://localhost:8080/")
    logger.info("=" * 70)

    # Optional background Fyers probe (non-blocking)
    def _async_fyers_probe():
        try:
            from data_provider import get_fetcher
            fetcher = get_fetcher()
            if getattr(fetcher, "_should_use_fyers", lambda: False)():
                probe_res = fetcher.fyers_fetcher.get_ohlcv("SBIN", "1d", "5d")
                if probe_res and probe_res.dataframe is not None and not probe_res.dataframe.empty:
                    logger.info("✅ [BOOT] Fyers API session authenticated & historical data verified live on startup!")
                else:
                    err = getattr(probe_res, 'error', 'Unknown')
                    logger.warning(f"⚠️ [BOOT] Fyers token loaded, but historical data probe returned: {err}")
        except Exception as boot_fyers_err:
            logger.warning(f"⚠️ [BOOT] Fyers API boot probe warning: {boot_fyers_err}")

    threading.Thread(target=_async_fyers_probe, name="FyersBootProbe", daemon=True).start()

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
                    # RULE 67 RATIONALE: EOD/Reversal/One-shot threads crash alerts are handled internally by their runners.
                    # Drop them from active_threads without attempting auto-restart.
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
    from database import (
        upsert_scanner_health,
        is_scanner_stopped,
        normalize_scanner_name,
        is_scanner_actively_running,
        get_scanner_health,
        insert_notification
    )
    
    from engine.production.governance_registry import normalize_scanner_name, DECOMMISSIONED_SCANNERS
    norm_key = normalize_scanner_name(scanner_key)
    if norm_key in DECOMMISSIONED_SCANNERS or scanner_key.upper() in DECOMMISSIONED_SCANNERS:
        logger.warning(f"🚫 [GOVERNANCE GATE] Blocked API trigger attempt for decommissioned scanner '{scanner_key}'")
        return {
            "status": "error",
            "message": f"🚫 [DECOMMISSIONED] Scanner '{scanner_key}' has been permanently decommissioned by governance. Zero execution permitted."
        }

    if is_scanner_stopped(scanner_key) or is_scanner_stopped(norm_key):
        return {
            "status": "error",
            "message": f"❌ Cannot trigger {scanner_key}: Scanner is currently STOPPED by Admin. Please RESUME the scanner first."
        }
    
    TRIGGER_MAP = {
        # Active production and operational workers
        "DAILY_BUILDER":                      _trigger_daily_builder,
        "FUNDAMENTAL":                        _trigger_fundamental,
        "QUALITY_COMPOUNDER_VALUE_V2_FINAL": _trigger_quality_compounder_v2,
        "Wealth Engine":                      _trigger_wealth_engine,
        "AI Worker":                          _trigger_ai_worker,
        "PERFORMANCE_TRACKER":                _trigger_performance_tracker,
        "WEALTH_EXIT":                        _trigger_wealth_exit,
        "TECHNICAL":                          _trigger_technical,
    }
    
    fn = TRIGGER_MAP.get(scanner_key) or TRIGGER_MAP.get(norm_key)
    if fn is None:
        return {"status": "error", "message": f"Unknown scanner: {scanner_key}"}
        
    # Check locks synchronously to return immediate HTTP JSON error
    LOCK_MAP = {
        "DAILY_BUILDER":                      lambda: __import__('daily_builder')._build_lock,
        "FUNDAMENTAL":                        lambda: __import__('live_fundamental_scanner')._fundamental_scan_lock,
        "QUALITY_COMPOUNDER_VALUE_V2_FINAL": lambda: __import__('live_fundamental_scanner')._v2_scan_lock,
        "Wealth Engine":                      lambda: __import__('wealth_engine')._scan_lock,
        "AI Worker":                          lambda: __import__('ai_worker')._scan_lock,
        "PERFORMANCE_TRACKER":                lambda: _perf_tracker_lock,
        "WEALTH_EXIT":                        lambda: __import__('wealth_engine')._wealth_exit_lock,
        "TECHNICAL":                          lambda: __import__('technical_scanner')._scan_lock,
    }

    
    # Check in-memory thread lock first — if not locked, no scan is running in this process
    lock_fn = LOCK_MAP.get(scanner_key) or LOCK_MAP.get(norm_key)
    if lock_fn:
        try:
            lock = lock_fn()
            if lock and hasattr(lock, "locked") and lock.locked():
                return {"status": "error", "message": f"❌ {scanner_key} is already actively running!"}
        except Exception:
            pass

    # Check PostgreSQL execution history for active running/queued execution across all processes/workers
    if is_scanner_actively_running(scanner_key) or is_scanner_actively_running(norm_key):
        return {"status": "error", "message": f"❌ {scanner_key} is already actively running!"}

    # Synchronously write an initial QUEUED state to the database so the UI immediately
    # reacts to the button click while the background thread potentially spends 30s
    # initializing the MarketDataSession. The actual scanner thread will then
    # overwrite this with RUNNING or a "Waiting for lock" QUEUED message.
    try:
        upsert_scanner_health(scanner_key, status="QUEUED", error_msg="Initializing scanner environment (fetching market data)...")
    except Exception as _qerr:
        pass

    # Invalidate dashboard status cache so next poll returns fresh DB state immediately
    try:
        from dashboard_server import invalidate_scanner_status_cache
        invalidate_scanner_status_cache()
    except Exception:
        pass


    # Run in background thread so the API returns immediately
    def _run():
        try:
            if is_scanner_stopped(scanner_key) or is_scanner_stopped(norm_key):
                logger.info(f"⏸️ [ADMIN MANUAL TRIGGER] {scanner_key} is PAUSED by Admin. Skipping background trigger.")
                return

            start_time = time.time()
            logger.info(f"🔧 ADMIN MANUAL TRIGGER | Starting {scanner_key}...")
            try:
                import inspect
                sig = inspect.signature(fn)
                if "trigger_type" in sig.parameters:
                    stats = fn(trigger_type="MANUAL", scheduler_name="MANUAL") or {}
                else:
                    stats = fn() or {}
                
                # Check if scan execution was skipped due to pause or duplicate lock guard
                if isinstance(stats, dict) and (stats.get("status") in ("skipped", "PAUSED") or stats.get("skipped") is True):
                    logger.warning(f"⚠️ ADMIN MANUAL TRIGGER | {scanner_key} skipped ({stats.get('reason', stats.get('status', 'already running or paused'))})")
                    return

                # Check if primary scanner thread is still running and holds lock
                if lock_fn:
                    try:
                        lock = lock_fn()
                        if lock and hasattr(lock, "locked") and lock.locked():
                            logger.info(f"ℹ️ ADMIN MANUAL TRIGGER | {scanner_key} execution ended while primary thread holds lock. Preserving active health state.")
                            return
                    except Exception:
                        pass

                duration_sec = round(time.time() - start_time, 1)
                logger.info(f"✅ ADMIN MANUAL TRIGGER | {scanner_key} completed in {format_duration(duration_sec)}.")
            except Exception as run_err:
                raise run_err

            # Preserve scanner's true recorded health status (do not overwrite DEGRADED / DOWN / PAUSED with OK)
            curr_health = get_scanner_health(scanner_key)
            curr_status = curr_health.get("status") if curr_health else "OK"
            if curr_status in ("PAUSED", "STOPPED"):
                return
            final_status = curr_status if curr_status in ("DOWN", "DEGRADED", "DEGRADED_FALLBACK", "BLOCKED", "FAILED") else "OK"
            final_err = curr_health.get("error_msg") if (final_status != "OK" and curr_health) else None
            now_str = datetime.now(IST).isoformat() if final_status == "OK" else (curr_health.get("last_success") if curr_health else None)

            upsert_scanner_health(scanner_key, status=final_status, last_success=now_str,
                                  error_msg=final_err,
                                  duration_seconds=duration_sec,
                                  total_count=stats.get("total_count") if isinstance(stats, dict) else (curr_health.get("total_count") if curr_health else None),
                                  processed_count=stats.get("processed_count") if isinstance(stats, dict) else (curr_health.get("processed_count") if curr_health else None),
                                  today_alerts=stats.get("today_alerts") if isinstance(stats, dict) else (curr_health.get("today_alerts") if curr_health else None))
            
            try:
                dur_str = f"Time: {format_duration(duration_sec)}"
                summary = f"Total Scanned: {stats.get('total_count', 'N/A')} | {dur_str}" if isinstance(stats, dict) else f"Completed in {dur_str}."
                if scanner_key not in ["DAILY_BUILDER", "Wealth Engine"]:
                    insert_notification("info", f"✅ {scanner_key} Manual Scan Complete", summary)
            except Exception:
                pass

            logger.info(f"✅ ADMIN MANUAL TRIGGER | {scanner_key} completed successfully")
        except RuntimeError as e:
            if "already actively running" in str(e).lower() or "lock busy" in str(e).lower():
                logger.warning(f"⚠️ ADMIN MANUAL TRIGGER | {scanner_key} skipped (already running)")
            else:
                logger.exception(f"❌ ADMIN MANUAL TRIGGER | {scanner_key} FAILED")
                upsert_scanner_health(scanner_key, status="DOWN",
                                      error_msg=f"Manual trigger failed: {str(e)[:400]}")
                try:
                    insert_notification("scanner_down", f"🚨 {scanner_key} Manual Scan Failed", f"Error: {str(e)[:200]}")
                except Exception:
                    pass
        except Exception as e:
            logger.exception(f"❌ ADMIN MANUAL TRIGGER | {scanner_key} FAILED")
            upsert_scanner_health(scanner_key, status="DOWN",
                                  error_msg=f"Manual trigger failed: {str(e)[:400]}")
            try:
                insert_notification("scanner_down", f"🚨 {scanner_key} Manual Scan Failed", f"Error: {str(e)[:200]}")
            except Exception:
                pass
        finally:
            # CRITICAL: Ensure QUEUED status is NEVER left stranded if execution exited early or was skipped
            try:
                curr_h = get_scanner_health(scanner_key)
                if curr_h and str(curr_h.get("status", "")).startswith("QUEUED"):
                    is_stop = is_scanner_stopped(scanner_key) or is_scanner_stopped(norm_key)
                    fallback_status = "PAUSED" if is_stop else "IDLE"
                    upsert_scanner_health(scanner_key, status=fallback_status, error_msg=None)
            except Exception:
                pass
    
    t = threading.Thread(target=_run, name=f"ManualTrigger-{scanner_key}", daemon=True)
    t.start()
    return {"status": "ok", "message": f"{scanner_key} triggered — running in background"}


def _trigger_daily_builder(force_rebuild: bool = False, trigger_type="MANUAL", scheduler_name="MANUAL"):
    from database import is_scanner_stopped
    if is_scanner_stopped("DAILY_BUILDER"):
        logger.info("⏸️ [DAILY_BUILDER] Scanner is PAUSED/STOPPED by Admin. Skipping trigger.")
        return
    import os
    import json
    if force_rebuild:
        try:
            from database import save_system_state
            save_system_state("daily_builder_checkpoint", json.dumps({}))
            if os.path.exists("data/temp_universe.parquet"):
                os.remove("data/temp_universe.parquet")
        except Exception as e:
            import logging
            logging.getLogger(__name__).warning(f"Could not clear daily builder checkpoint: {e}")
        
    from daily_builder import main as build_watchlist
    from database import start_scanner_execution_run, complete_scanner_execution_run, upsert_scanner_health
    upsert_scanner_health("DAILY_BUILDER", status="RUNNING", error_msg="Building watchlist...")
    run_ctx = start_scanner_execution_run(scanner_name="DAILY_BUILDER", trigger_type=trigger_type, scheduler_name=scheduler_name)
    try:
        build_watchlist(force_rebuild=force_rebuild, run_ctx=run_ctx, trigger_type=trigger_type, scheduler_name=scheduler_name)
        from watchlist_cache import get_watchlist
        wl = get_watchlist()
        if run_ctx and run_ctx.total_stocks == 0 and wl is not None and not wl.empty:
            run_ctx.set_total_stocks(len(wl))
            run_ctx.fresh_count = len(wl)
            complete_scanner_execution_run(run_ctx)
        upsert_scanner_health("DAILY_BUILDER", status="OK", error_msg=None)

        # [FIX: VALUATION_DATA_UNAVAILABLE] Rebuild and upload valuation cache asynchronously in background
        # so it does not block the orchestrator or downstream scanners in the boot batch.
        def _bg_rebuild_valuation():
            try:
                from pit_valuation_history_builder import build_pit_valuation_history, _count_both_complete_from_dict
                logger.info("🔧 [DAILY_BUILDER] Rebuilding PIT valuation medians cache post-build in background...")
                _vc_result = build_pit_valuation_history(save_cache=True, upload_db=True)
                if _vc_result:
                    # Secondary audit: use the same row-level both-field completeness metric as the builder.
                    _both_complete = _count_both_complete_from_dict(_vc_result)
                    _ev_valid = sum(1 for v in _vc_result.values() if v.get("ev_ebitda_3y_median") is not None)
                    _pe_valid = sum(1 for v in _vc_result.values() if v.get("pe_3y_median") is not None)
                    _cache_cert = "CERTIFIED" if (_both_complete == len(_vc_result) and _both_complete > 0) else "PARTIAL_INCOMPLETE"
                    if _both_complete > 0:
                        logger.info(
                            f"✅ [DAILY_BUILDER] Background valuation rebuild accepted: {len(_vc_result)} symbols | "
                            f"EV/EBITDA: {_ev_valid} | PE: {_pe_valid} | "
                            f"Both-required (EV∩PE): {_both_complete}/{len(_vc_result)} | "
                            f"cache_certification={_cache_cert}"
                        )
                    else:
                        logger.error(
                            f"❌ [DAILY_BUILDER] VALUATION_CACHE_REBUILD_REJECTED: build returned {len(_vc_result)} symbols "
                            f"but Both-required={_both_complete}/{len(_vc_result)} (EV={_ev_valid}, PE={_pe_valid}). "
                            f"Existing certified cache preserved by BOTH_COMPLETE_ZERO_BLOCKED gate."
                        )
                else:
                    logger.warning("⚠️ [DAILY_BUILDER] Valuation cache rebuild returned empty — check 1D history and PIT parquet")
            except Exception as _vc_rebuild_err:
                logger.warning(f"⚠️ [DAILY_BUILDER] Valuation cache post-build refresh failed: {_vc_rebuild_err}")

        import threading
        t_vc = threading.Thread(target=_bg_rebuild_valuation, name="PITValuationRebuilder", daemon=True)
        t_vc.start()

    except Exception as exc:
        if run_ctx:
            complete_scanner_execution_run(run_ctx, exception=exc)
        upsert_scanner_health("DAILY_BUILDER", status="DOWN", error_msg=str(exc))
        raise exc


# [RULE 67 CHANGE-RATIONALE]: Removed obsolete trigger stubs for decommissioned scanners (EOD, REVERSAL, PULLBACK).
# trigger_scanner_manual() strictly validates against DECOMMISSIONED_SCANNERS before routing.

def _trigger_wealth_engine(trigger_type="MANUAL", scheduler_name="MANUAL", session=None):
    from database import is_scanner_stopped
    if is_scanner_stopped("Wealth Engine"):
        logger.info("⏸️ [Wealth Engine] Scanner is PAUSED/STOPPED by Admin. Skipping trigger.")
        return
    logger.info(f"🚀 [SCANNER: WEALTH_ENGINE] Starting execution (trigger={trigger_type}, scheduler={scheduler_name})...")
    from wealth_engine import run_wealth_scan
    run_wealth_scan(trigger_type=trigger_type, scheduler_name=scheduler_name, session=session)
def _trigger_quality_compounder_v2(trigger_type="MANUAL", scheduler_name="MANUAL", session=None):
    from database import is_scanner_stopped
    if is_scanner_stopped("QUALITY_COMPOUNDER_VALUE_V2_FINAL"):
        logger.info("⏸️ [QUALITY_COMPOUNDER_VALUE_V2_FINAL] Scanner is PAUSED/STOPPED by Admin. Skipping trigger.")
        return
    logger.info(f"🚀 [SCANNER: QUALITY_COMPOUNDER_VALUE_V2_FINAL] Starting execution (trigger={trigger_type}, scheduler={scheduler_name})...")
    from live_fundamental_scanner import run_quality_compounder_v2_scan
    return run_quality_compounder_v2_scan(trigger_type=trigger_type, scheduler_name=scheduler_name)

# [DECOMMISSIONED] _trigger_multibagger() permanently removed.

def _trigger_technical(trigger_type="MANUAL", scheduler_name="MANUAL", run_ctx=None, session=None):
    from database import is_scanner_stopped
    if is_scanner_stopped("TECHNICAL"):
        logger.info("⏸️ [TECHNICAL] Scanner is PAUSED/STOPPED by Admin. Skipping trigger.")
        return {"total_count": 0, "processed_count": 0}

    # Pre-flight regime gate: TECHNICAL is certified exclusively for BULL regime
    try:
        from engine.production.governance_registry import get_current_macro_regime
        current_regime = get_current_macro_regime()
    except Exception as e:
        logger.warning(f"⚠️ [TECHNICAL PRE-FLIGHT] Could not resolve current macro regime ({e}); defaulting to BULL")
        current_regime = "BULL"

    if current_regime != "BULL":
        logger.info(f"⏭️ [TECHNICAL PRE-FLIGHT] Suppressed: TECHNICAL is certified exclusively in BULL regime (Current: {current_regime}). Skipping execution to conserve CPU.")
        try:
            from database import upsert_scanner_health
            upsert_scanner_health("TECHNICAL", status="IDLE", error_msg=f"Suppressed: certified exclusively in BULL (Current: {current_regime})")
        except Exception:
            pass
        return {"total_count": 0, "processed_count": 0, "status": "skipped", "reason": f"REGIME_NOT_CERTIFIED_{current_regime}"}

    logger.info(f"🚀 [SCANNER: TECHNICAL] Starting execution (trigger={trigger_type}, scheduler={scheduler_name})...")
    from technical_scanner import run_technical_scan
    count = run_technical_scan(trigger_type=trigger_type, scheduler_name=scheduler_name, run_ctx=run_ctx, session=session)
    return {"total_count": count, "processed_count": count}

def _trigger_fundamental(trigger_type="MANUAL", scheduler_name="MANUAL", session=None):
    from database import is_scanner_stopped

    if is_scanner_stopped("FUNDAMENTAL"):
        logger.info("⏸️ [FUNDAMENTAL] Scanner is PAUSED/STOPPED by Admin. Skipping trigger.")
        return {"total_count": 0, "processed_count": 0}

    logger.info(f"🚀 [SCANNER: FUNDAMENTAL] Starting execution (trigger={trigger_type}, scheduler={scheduler_name})...")

    # _run_fundamental_scan is imported at module level from live_fundamental_scanner.
    # Do NOT add fallback try/except chains here — if the module fails to load the
    # error must be visible immediately, not silently swallowed.
    funnel = _run_fundamental_scan(trigger_type=trigger_type, scheduler_name=scheduler_name)
    count = funnel.get("scanned_count", 0) if isinstance(funnel, dict) else 0
    return {"total_count": count, "processed_count": count}


# [VERSION: TRIGGER_AI_WORKER_v1.1] Define _trigger_ai_worker
def _trigger_ai_worker():
    from database import is_scanner_stopped
    if is_scanner_stopped("AI Worker"):
        logger.info("⏸️ [AI Worker] Worker is PAUSED/STOPPED by Admin. Skipping trigger.")
        return
    from ai_worker import run_ai_worker_scan_once
    return run_ai_worker_scan_once()


def _trigger_earnings_calendar():
    return {"total_count": 0, "processed_count": 0}

def _trigger_performance_tracker():
    from database import is_scanner_stopped
    if is_scanner_stopped("PERFORMANCE_TRACKER"):
        logger.info("⏸️ [PERFORMANCE_TRACKER] Scanner is PAUSED/STOPPED by Admin. Skipping trigger.")
        return {"total_count": 0, "processed_count": 0}
    if not _perf_tracker_lock.acquire(blocking=False):
        logger.info("⏳ PERFORMANCE_TRACKER is already actively running. Skipping manual/auto-recovery trigger.")
        return {"total_count": 0, "processed_count": 0}
    try:
        from performance_tracker import build_performance_data
        build_performance_data(force_live_fetch=True)
        return {"total_count": 1, "processed_count": 1}
    finally:
        if _perf_tracker_lock.locked():
            _perf_tracker_lock.release()


def _trigger_wealth_exit(check_type="EOD"):
    from database import is_scanner_stopped
    if is_scanner_stopped("WEALTH_EXIT"):
        logger.info("⏸️ [WEALTH_EXIT] Scanner is PAUSED/STOPPED by Admin. Skipping trigger.")
        return {"total_count": 0, "processed_count": 0}
    from wealth_engine import run_wealth_intraday_update
    run_wealth_intraday_update()
    try:
        from live_wealth_monitor import run_v2_exit_check
        run_v2_exit_check(check_type=check_type)
    except Exception as v2_exit_err:
        logger.error(f"❌ V2 Exit check trigger failed: {v2_exit_err}")
    return {"total_count": 1, "processed_count": 1}






# ENTRY POINT
# =====================================================================================

if __name__ == "__main__":
    forensics.take_snapshot("startup")

    # 0. START FLASK DASHBOARD SERVER IMMEDIATELY (0ms latency for health checks & Coolify)
    if "--worker" not in sys.argv:
        try:
            from dashboard_server import start_dashboard_server_async
            start_dashboard_server_async()
            logger.info("🌐 [BOOT] Dashboard server started asynchronously — ports 8000/8080/80 open instantly for healthchecks!")
        except Exception as _d_err:
            logger.error(f"❌ Could not start dashboard server: {_d_err}")

    # 1. SINGLE-THREADED DB INIT — Run DDL migrations on main thread BEFORE worker threads start
    try:
        from database import init_db, reset_all_scanners_on_boot
        init_db()
        reset_all_scanners_on_boot()
        logger.info("✅ [BOOT] Single-threaded DB schema initialization and scanner health boot reset complete.")
    except Exception as _init_err:
        logger.warning(f"⚠️ Single-threaded init_db warning: {_init_err}")

    # 2. SIGNAL HANDLERS FOR CLEAN SHUTDOWN
    def handle_sigterm(*args):
        logger.info("🛑 SIGTERM received — container shutting down. Closing gracefully...")
        try:
            from database import close_pool
            close_pool()
        except Exception:
            pass
        sys.exit(0)

    signal.signal(signal.SIGTERM, handle_sigterm)
    signal.signal(signal.SIGINT, handle_sigterm)

    # 3. RUN DB & SCANNER INITIALIZATION IN BACKGROUND THREAD
    def _bg_boot_sequence():
        # Phase 2 Dataset Registry: Self-Register Consumers
        try:
            from data_registry import registry
            registry.register_consumer("watchlist", "WealthEngine")
            registry.register_consumer("price_1d", "WealthEngine")
            registry.register_consumer("fundamentals_quarterly", "WealthEngine")
            registry.validate()
            logger.info("✅ Dataset Registry graph validation passed.")
        except Exception as e:
            logger.warning(f"⚠️ Dataset Registry initialization warning: {e}")

        # STARTUP DIAGNOSTICS
        try:
            from diagnostics import run_startup_diagnostics
            run_startup_diagnostics()
        except Exception as e:
            logger.warning(f"⚠️ Diagnostics check skipped: {e}")

        # FYERS SCOPE VERIFICATION & TOKEN CHECK
        try:
            from data_providers.fyers_fetcher import verify_fyers_startup_scope
            verify_fyers_startup_scope()
        except Exception as _fyers_scope_err:
            logger.warning(f"⚠️ Fyers startup scope verification skipped: {_fyers_scope_err}")

        # SYMBOL ROUTER PERSISTED ROUTES
        try:
            from symbol_router import symbol_router
            symbol_router.load_persisted_routes()
        except Exception as _router_err:
            logger.warning(f"⚠️ Failed to restore symbol router state: {_router_err}")

        # [FIX] Do NOT reset positions to OPEN on boot. This destroys closed trade states
        # and causes performance_tracker to replay them, resulting in massive notification spam.
        # Historical rebuilds should only be done manually via the admin dashboard API.


        # ORPHANED SCANNER RUNS CLEANUP
        try:
            from database import cleanup_orphaned_scanner_runs_on_boot
            cleanup_orphaned_scanner_runs_on_boot()
            logger.info("🧹 [BOOT] Cleaned up any orphaned scanner runs.")
        except Exception as e:
            logger.warning(f"⚠️ [BOOT] Boot scanner cleanup warning: {e}")

        # APPLICATION CONTEXT
        try:
            from application_context import ApplicationContext
            _app_ctx = ApplicationContext.get_instance()
            logger.info("✅ [SESSION_ARCH] ApplicationContext ready.")
        except Exception as e:
            logger.warning(f"⚠️ ApplicationContext init warning: {e}")

        # NON-MARKET HOURS CATCH-UP is handled entirely by the SystemScheduler thread
        # to prevent concurrent executions.
        # SystemScheduler is started automatically by the Watchdog via RESTARTABLE_THREADS.
        pass

    # WATCHDOG THREAD — Start Watchdog FIRST so scanners and scheduler start immediately on boot
    watchdog_thread = threading.Thread(target=run_watchdog, name="Watchdog", daemon=True)
    watchdog_thread.start()

    # BACKGROUND BOOT SEQUENCE (diagnostics, symbol router, position resets)
    threading.Thread(target=_bg_boot_sequence, name="BootSequence", daemon=True).start()

    # Block main thread to keep container alive
    while True:
        time.sleep(3600)
