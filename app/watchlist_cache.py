# =====================================================================================
# app/watchlist_cache.py
# =====================================================================================
import pandas as pd
import logging
from datetime import datetime
from zoneinfo import ZoneInfo
from config import WATCHLIST_PATH

logger = logging.getLogger(__name__)
IST = ZoneInfo("Asia/Kolkata")

_watchlist_cache = None
_watchlist_date = None

class StaleWatchlistError(Exception):
    """Raised when watchlist parquet file modification date is older than today's 00:00 IST."""
    pass

def validate_watchlist_freshness(require_fresh: bool = False) -> bool:
    """
    Verifies that the watchlist parquet file on disk was modified today (after 00:00 IST).
    Returns True if fresh, False if stale.
    Raises StaleWatchlistError if require_fresh is True and file is stale.
    """
    import os
    if not os.path.exists(WATCHLIST_PATH):
        if require_fresh:
            raise StaleWatchlistError("Watchlist parquet file missing from disk.")
        return False
        
    mtime = os.path.getmtime(WATCHLIST_PATH)
    file_date = datetime.fromtimestamp(mtime, tz=IST).date()
    today_date = datetime.now(IST).date()
    
    if file_date < today_date:
        msg = f"⚠️ STALE WATCHLIST DETECTED: {WATCHLIST_PATH} modified date ({file_date}) is older than today ({today_date})"
        logger.warning(msg)
        if require_fresh:
            from database import insert_notification
            try:
                insert_notification("error", "⚠️ STALE WATCHLIST PREVENTED SCAN", msg)
            except Exception:
                pass
            raise StaleWatchlistError(msg)
        return False
    return True

def get_watchlist(require_fresh: bool = False) -> pd.DataFrame:
    global _watchlist_cache, _watchlist_date
    current_date = datetime.now(IST).date()
    
    if require_fresh:
        validate_watchlist_freshness(require_fresh=True)

    if _watchlist_cache is not None and _watchlist_date == current_date:
        return _watchlist_cache.copy()

    try:
        df = pd.read_parquet(WATCHLIST_PATH)
        _watchlist_cache = df
        _watchlist_date = current_date
        logger.info(f"📁 Watchlist loaded into memory cache ({len(df)} symbols)")
        return _watchlist_cache.copy()
    except Exception:
        # Try to restore from database first to avoid 2-minute rebuilding on server restarts
        try:
            from database import download_parquet_from_db
            import os
            
            # If downloaded successfully, we can just read it normally
            if download_parquet_from_db("daily_builder", WATCHLIST_PATH) and os.path.exists(WATCHLIST_PATH):
                # Restore the exclusion log as well
                try:
                    exclusion_path = WATCHLIST_PATH.replace(".parquet", "_excluded.csv")
                    download_parquet_from_db("daily_builder_excluded", exclusion_path)
                    logger.info("☁️ [WATCHLIST CACHE] Restored exclusion log from Postgres cache.")
                except Exception as ex_err:
                    logger.warning(f"Failed to restore exclusion log from DB: {ex_err}")
                
                df = pd.read_parquet(WATCHLIST_PATH)
                _watchlist_cache = df
                _watchlist_date = current_date
                logger.info(f"☁️ [WATCHLIST CACHE] Restored watchlist from Postgres cache ({len(df)} symbols)")
                return _watchlist_cache.copy()
        except Exception as e:
            logger.warning(f"Failed to restore watchlist from DB: {e}")

        # Fallback if missing: do NOT build here, let main.py watchdog handle it
        logger.error("❌ Watchlist missing from both disk and DB. Scanners must wait for Watchdog to build it.")
        return pd.DataFrame()
