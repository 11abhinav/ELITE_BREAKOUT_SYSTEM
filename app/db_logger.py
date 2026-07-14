import logging
import traceback
import sys
import threading
from datetime import datetime
from zoneinfo import ZoneInfo
import os

APP_DIR = os.path.dirname(os.path.abspath(__file__))
if APP_DIR not in sys.path:
    sys.path.insert(0, APP_DIR)

from database import get_connection

IST = ZoneInfo("Asia/Kolkata")

class DatabaseLogHandler(logging.Handler):
    """
    Custom logging handler that intercepts ERROR and CRITICAL logs
    and stores them in the system_logs table.
    """
    def __init__(self):
        super().__init__()
        # Prevent infinite recursion if database insert fails and tries to log
        self._local = threading.local()

    def emit(self, record):
        if getattr(self._local, 'handling', False):
            return
            
        self._local.handling = True
        try:
            # We only care about ERROR and CRITICAL
            if record.levelno < logging.ERROR:
                return
                
            # Filter out known noisy third-party loggers (like yfinance missing symbols)
            if record.name.startswith(('yfinance', 'urllib3', 'requests', 'httpx')):
                return
                
            # Fallback for yfinance modules if record.name is just the file name
            if record.module in ('multi', 'history', 'quote', 'base', 'yfinance'):
                if "delisted" in str(record.msg) or "Failed download" in str(record.msg) or "HTTP Error" in str(record.msg):
                    return
                
            module_name = record.module
            msg = self.format(record)
            
            tb_text = None
            if record.exc_info:
                tb_text = "".join(traceback.format_exception(*record.exc_info))
            
            with get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute("""
                        INSERT INTO system_logs (level, module, message, traceback, created_at)
                        VALUES (%s, %s, %s, %s, %s)
                    """, (
                        record.levelname,
                        module_name,
                        msg[:5000],  # truncate to prevent excessive data size
                        tb_text[:10000] if tb_text else None,
                        datetime.now(IST)
                    ))
                conn.commit()
                
        except Exception:
            # Failsafe: if writing to the log DB fails, we can't log an error normally,
            # so we just quietly ignore or print to stderr
            pass
        finally:
            self._local.handling = False

def install_db_logger():
    """
    Attach the DatabaseLogHandler to the root logger and override sys.excepthook
    to catch any unhandled thread or application exceptions.
    """
    root_logger = logging.getLogger()
    
    # Check if we already added it
    for handler in root_logger.handlers:
        if isinstance(handler, DatabaseLogHandler):
            return
            
    db_handler = DatabaseLogHandler()
    db_handler.setLevel(logging.ERROR)
    
    formatter = logging.Formatter('%(asctime)s | %(levelname)s | %(message)s')
    db_handler.setFormatter(formatter)
    
    root_logger.addHandler(db_handler)
    
    # Override sys.excepthook to catch fully unhandled exceptions
    orig_excepthook = sys.excepthook
    def unhandled_exception_handler(exc_type, exc_value, exc_traceback):
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc_value, exc_traceback)
            return
            
        logging.getLogger("UNHANDLED").critical(
            "Uncaught exception", 
            exc_info=(exc_type, exc_value, exc_traceback)
        )
        orig_excepthook(exc_type, exc_value, exc_traceback)
        
    sys.excepthook = unhandled_exception_handler
