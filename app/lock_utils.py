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
    "wealth_engine":      {"emoji": "💰", "display": "WEALTH ENGINE"},
    "multi_tf_scanner":   {"emoji": "📊", "display": "MULTI-TF SCANNER"},
    "eod_scanner":        {"emoji": "🌙", "display": "EOD SCANNER"},
    "reversal_scanner":   {"emoji": "🔄", "display": "REVERSAL SCANNER"},
    "pullback_scanner":   {"emoji": "📉", "display": "PULLBACK SCANNER"},
    "multibagger":        {"emoji": "🚀", "display": "MULTIBAGGER SCANNER"},
}

_BAR_LEN = 30


def print_scanner_start_banner(scanner_key: str) -> float:
    """
    Print a vivid START banner for the given scanner.
    Returns the current monotonic timestamp so callers can compute runtime.
    """
    cfg = SCANNER_CONFIG.get(scanner_key, {"emoji": "⚙️", "display": scanner_key.upper()})
    emoji, display = cfg["emoji"], cfg["display"]
    bar = emoji * _BAR_LEN
    ts = datetime.now(IST).strftime("%Y-%m-%d %H:%M:%S IST")
    logger.info(bar)
    logger.info(f"&&&&& {display} STARTED — {ts} &&&&&")
    logger.info(bar)
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

class ProcessLock:
    """
    True Distributed Lock using PostgreSQL Advisory Locks + local threading.Lock.
    Protects against BOTH multiple threads AND multiple distributed containers on Railway.
    """
    def __init__(self, lock_name: str):
        self.lock_name = lock_name
        self.lock_file = f"data/{lock_name}.lock"
        self.lock_fd = None
        self.thread_lock = threading.Lock()
        self.db_conn = None
        # Generate a stable 32-bit integer for the Postgres lock key based on the name
        self.lock_key = zlib.crc32(lock_name.encode('utf-8'))
        self.is_acquired = False

    def locked(self) -> bool:
        """
        Check if the local thread lock is held. 
        Note: This does not verify the Postgres distributed lock state, 
        but is sufficient for UI rejection of duplicate manual triggers on the same server.
        """
        return self.thread_lock.locked()

    def acquire(self, blocking: bool = False) -> bool:
        if not self.thread_lock.acquire(blocking=blocking):
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
                        cur.execute("SELECT pg_advisory_lock(%s)", (self.lock_key,))
                        locked = True
                    else:
                        cur.execute("SELECT pg_try_advisory_lock(%s)", (self.lock_key,))
                        locked = cur.fetchone()[0]
                    
                    if not locked:
                        raise BlockingIOError("Could not acquire Postgres distributed lock")

            self.is_acquired = True
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
        if self.is_acquired:
            self.is_acquired = False

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
            except Exception:
                pass
                
        # 3. Release thread lock
        try:
            self.thread_lock.release()
        except RuntimeError:
            pass
