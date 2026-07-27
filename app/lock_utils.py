import os
import fcntl
import logging

logger = logging.getLogger(__name__)

import threading
import psycopg2
import zlib

from zoneinfo import ZoneInfo
from datetime import datetime
IST = ZoneInfo("Asia/Kolkata")

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
            # [VERSION: SCANNER_LOCK_BANNERS_v1.0] Standardized scanner start banner
            name_display = self.lock_name.replace("_", " ").title()
            logger.info(f"********************* Starting {name_display} Scanner at {datetime.now(IST).strftime('%Y-%m-%d %H:%M:%S IST')} *********************")
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
            # [VERSION: SCANNER_LOCK_BANNERS_v1.0] Standardized scanner completion banner
            name_display = self.lock_name.replace("_", " ").title()
            logger.info(f"********************* {name_display} Scanner completed at {datetime.now(IST).strftime('%Y-%m-%d %H:%M:%S IST')} *********************")
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
