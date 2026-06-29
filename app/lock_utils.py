import os
import fcntl
import logging

logger = logging.getLogger(__name__)

import threading

class ProcessLock:
    """
    Hybrid lock combining threading.Lock and fcntl.flock.
    Protects against BOTH multiple threads in the same process AND multiple processes.
    """
    def __init__(self, lock_name: str):
        self.lock_name = lock_name
        self.lock_file = f"data/{lock_name}.lock"
        self.lock_fd = None
        self.thread_lock = threading.Lock()

    def acquire(self, blocking: bool = False) -> bool:
        if not self.thread_lock.acquire(blocking=blocking):
            return False
            
        try:
            os.makedirs("data", exist_ok=True)
            if self.lock_fd is None:
                self.lock_fd = os.open(self.lock_file, os.O_CREAT | os.O_RDWR)
                
            flags = fcntl.LOCK_EX
            if not blocking:
                flags |= fcntl.LOCK_NB
                
            fcntl.flock(self.lock_fd, flags)
            return True
        except (BlockingIOError, IOError):
            self.thread_lock.release()
            return False
        except Exception as e:
            logger.error(f"Error acquiring lock {self.lock_name}: {e}")
            return True

    def release(self):
        if self.lock_fd is not None:
            try:
                fcntl.flock(self.lock_fd, fcntl.LOCK_UN)
            except Exception:
                pass
        try:
            self.thread_lock.release()
        except RuntimeError:
            pass
