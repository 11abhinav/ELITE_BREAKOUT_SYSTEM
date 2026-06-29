import os
import fcntl
import logging

logger = logging.getLogger(__name__)

class ProcessLock:
    """
    Cross-process file-based lock. 
    Prevents multiple Gunicorn workers or standalone scripts from running the same scanner concurrently.
    """
    def __init__(self, lock_name: str):
        self.lock_name = lock_name
        self.lock_file = f"data/{lock_name}.lock"
        self.lock_fd = None

    def acquire(self, blocking: bool = False) -> bool:
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
