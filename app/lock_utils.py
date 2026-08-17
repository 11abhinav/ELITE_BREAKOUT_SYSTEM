import os
import fcntl
import logging
import time

logger = logging.getLogger(__name__)

import threading
import psycopg2
import zlib

from zoneinfo import ZoneInfo
from datetime import datetime
IST = ZoneInfo("Asia/Kolkata")

# ─────────────────────────────────────────────────────────────────────────────
# SCANNER IDENTITY CONFIG  — unique emoji + display name per scanner
# ─────────────────────────────────────────────────────────────────────────────
SCANNER_CONFIG = {
    "wealth_engine":      {"emoji": "💰", "display": "WEALTH ENGINE",      "db_name": "Wealth Engine"},
    "multi_tf_scanner":   {"emoji": "📊", "display": "MULTI-TF SCANNER",    "db_name": "MULTI_TF"},
    "eod_scanner":        {"emoji": "🌙", "display": "EOD SCANNER",          "db_name": "EOD"},
    "reversal_scanner":   {"emoji": "🔄", "display": "REVERSAL SCANNER",     "db_name": "REVERSAL"},
    "pullback_scanner":   {"emoji": "📉", "display": "PULLBACK SCANNER",     "db_name": "PULLBACK"},
    "multibagger":        {"emoji": "🚀", "display": "MULTIBAGGER SCANNER",   "db_name": "MULTIBAGGER"},
}

_BAR_LEN = 30


def print_scanner_start_banner(scanner_key: str, queued_at: float = None) -> float:
    """
    Print a vivid START banner and immediately mark scanner as RUNNING in DB.
    This fixes the QUEUED-stuck UI bug — status transitions QUEUED → RUNNING
    the instant the global lock is acquired, before any scan logic runs.
    Returns the current monotonic timestamp so callers can compute runtime.
    """
    cfg = SCANNER_CONFIG.get(scanner_key, {"emoji": "⚙️", "display": scanner_key.upper(), "db_name": None})
    emoji, display = cfg["emoji"], cfg["display"]
    db_name = cfg.get("db_name")
    bar = emoji * _BAR_LEN
    ts = datetime.now(IST).strftime("%Y-%m-%d %H:%M:%S IST")
    
    queue_wait_str = ""
    if queued_at is not None:
        queue_wait_secs = round(time.monotonic() - queued_at, 1)
        queue_wait_str = f" | Queue Wait: {queue_wait_secs}s"
    
    logger.info(bar)
    logger.info(f"&&&&& {display} STARTED — {ts}{queue_wait_str} &&&&&")
    logger.info(bar)
    
    # ✅ CRITICAL FIX: Immediately transition QUEUED → RUNNING in DB so UI reflects reality
    if db_name:
        try:
            from database import upsert_scanner_health
            upsert_scanner_health(db_name, "RUNNING", error_msg="Scan in progress...")
            logger.info(f"🟢 [{display}] Status updated: RUNNING (was QUEUED{queue_wait_str})")
        except Exception as _e:
            logger.warning(f"⚠️ Could not update scanner status to RUNNING: {_e}")
    
    return time.monotonic()


def print_scanner_end_banner(scanner_key: str, start_mono: float) -> None:
    """
    Print a vivid END banner for the given scanner.
    Must be called BEFORE releasing any locks so log order is guaranteed.
    """
    cfg = SCANNER_CONFIG.get(scanner_key, {"emoji": "⚙️", "display": scanner_key.upper()})
    emoji, display = cfg["emoji"], cfg["display"]
    bar = emoji * _BAR_LEN
    ts = datetime.now(IST).strftime("%Y-%m-%d %H:%M:%S IST")
    runtime = time.monotonic() - start_mono
    logger.info(bar)
    logger.info(f"##### {display} ENDED — {ts} | Runtime: {runtime:.0f}s #####")
    logger.info(bar)

_process_locks = {}
_process_locks_guard = threading.Lock()

def ProcessLock(lock_name: str):
    """Factory returning a reentrant Singleton ProcessLock per lock_name."""
    with _process_locks_guard:
        if lock_name not in _process_locks:
            _process_locks[lock_name] = ProcessLockImpl(lock_name)
        return _process_locks[lock_name]


class ProcessLockImpl:
    """
    True Reentrant Distributed Lock using PostgreSQL Advisory Locks + local threading.RLock.
    Protects against BOTH multiple threads AND multiple distributed containers on Railway.
    """
    def __init__(self, lock_name: str):
        self.lock_name = lock_name
        self.lock_file = f"data/{lock_name}.lock"
        self.lock_fd = None
        self.thread_lock = threading.RLock()
        self.db_conn = None
        # Generate a stable 32-bit integer for the Postgres lock key based on the name
        self.lock_key = zlib.crc32(lock_name.encode('utf-8'))
        self.is_acquired = False
        self._owner_thread = None
        self._recursion_depth = 0
        self._internal_lock = threading.Lock()

    def locked(self) -> bool:
        """Check if the local thread lock is held."""
        return self._recursion_depth > 0

    def acquire(self, blocking: bool = False, timeout: float = -1, **kwargs) -> bool:
        current_thread = threading.current_thread().name
        with self._internal_lock:
            if self._owner_thread == current_thread and self._recursion_depth > 0:
                self._recursion_depth += 1
                return True

        timeout_val = float(timeout) if timeout is not None else -1.0
        if blocking:
            if not self.thread_lock.acquire(blocking=True, timeout=timeout_val if timeout_val > 0 else -1):
                return False
        else:
            if not self.thread_lock.acquire(blocking=False):
                return False
            
        try:
            # 1. Fallback local file lock for non-distributed edge cases
            os.makedirs("data", exist_ok=True)
            if self.lock_fd is None:
                self.lock_fd = os.open(self.lock_file, os.O_CREAT | os.O_RDWR)
                
            flags = fcntl.LOCK_EX
            if not blocking:
                flags |= fcntl.LOCK_NB
                
            fcntl.flock(self.lock_fd, flags)

            # 2. True distributed PostgreSQL lock (vital for Railway autodeploys/multi-containers)
            db_url = os.environ.get("DATABASE_URL")
            if db_url:
                if self.db_conn is None:
                    # Create a raw unpooled connection dedicated to holding this lock
                    self.db_conn = psycopg2.connect(db_url)
                    self.db_conn.autocommit = True
                
                with self.db_conn.cursor() as cur:
                    if blocking:
                        wait_start = time.monotonic()
                        last_logged_s = 0
                        while True:
                            cur.execute("SELECT pg_try_advisory_lock(%s)", (self.lock_key,))
                            locked = cur.fetchone()[0]
                            if locked:
                                break
                            elapsed = time.monotonic() - wait_start
                            if timeout_val > 0 and elapsed >= timeout_val:
                                fcntl.flock(self.lock_fd, fcntl.LOCK_UN)
                                self.thread_lock.release()
                                return False
                            if int(elapsed) >= last_logged_s + 15:
                                last_logged_s = int(elapsed)
                                logger.info(f"⏳ [{self.lock_name.upper()}] Lock busy — waiting for active scanner to release... (elapsed: {last_logged_s}s)")
                            time.sleep(1.0)
                    else:
                        cur.execute("SELECT pg_try_advisory_lock(%s)", (self.lock_key,))
                        locked = cur.fetchone()[0]
                    
                    if not locked:
                        raise BlockingIOError("Could not acquire Postgres distributed lock")

            with self._internal_lock:
                self.is_acquired = True
                self._owner_thread = current_thread
                self._recursion_depth = 1
            return True
        except (BlockingIOError, IOError):
            if self.db_conn:
                try:
                    self.db_conn.close()
                    self.db_conn = None
                except Exception:
                    pass
            self.thread_lock.release()
            return False
        except Exception as e:
            # [VERSION: PROCESS_LOCK_EXC_FIX_v1.0] On DB or system exception, release thread lock and return False
            logger.error(f"Error acquiring distributed lock {self.lock_name}: {e}")
            if self.db_conn:
                try:
                    self.db_conn.close()
                    self.db_conn = None
                except Exception:
                    pass
            try:
                self.thread_lock.release()
            except Exception:
                pass
            return False

    def release(self):
        with self._internal_lock:
            current_thread = threading.current_thread().name
            if self._owner_thread != current_thread:
                return
            
            self._recursion_depth -= 1
            if self._recursion_depth > 0:
                return

            self.is_acquired = False
            self._owner_thread = None

        # 1. Release Postgres lock by simply closing the dedicated connection
        if self.db_conn is not None:
            try:
                self.db_conn.close()
                self.db_conn = None
            except Exception:
                pass

        # 2. Release local file lock
        if self.lock_fd is not None:
            try:
                fcntl.flock(self.lock_fd, fcntl.LOCK_UN)
                os.close(self.lock_fd)
                self.lock_fd = None
            except Exception:
                pass

        try:
            self.thread_lock.release()
        except Exception:
            pass
