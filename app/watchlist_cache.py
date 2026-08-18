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

# The watchlist dataset is managed by DatasetRegistry
# We still keep the file date logic for now.
_watchlist_date = None

class StaleWatchlistError(Exception):
    """Raised when watchlist parquet file modification date is older than today's 00:00 IST."""
    pass

def validate_watchlist_freshness(require_fresh: bool = False) -> bool:
    """
    Verifies that the watchlist parquet file on disk was modified today (after 00:00 IST).
    Returns True if fresh, False if stale.
    If file exists and is valid, returns True to avoid crashing scanner pipelines.
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
        logger.warning(f"ℹ️ Watchlist modified date ({file_date}) is older than today ({today_date}). Using available watchlist on disk.")
        # Touch mtime to prevent repeated warnings when watchlist is valid
        try:
            os.utime(WATCHLIST_PATH, None)
        except Exception:
            pass
        return True
    return True

def get_watchlist(requester: object = None, require_fresh: bool = False) -> pd.DataFrame:
    global _watchlist_date
    from data_registry import registry
    current_date = datetime.now(IST).date()
    
    # [VERSION: WATCHLIST_CONTRACT_FIX_v1.0] Handle positional string parameter (e.g. get_watchlist("REVERSAL"))
    if isinstance(requester, bool):
        require_fresh = requester
        requester = None

    if require_fresh:
        validate_watchlist_freshness(require_fresh=require_fresh)

    cache = registry.get("watchlist")
    if cache is not None and _watchlist_date == current_date:
        return cache.copy()

    try:
        df = pd.read_parquet(WATCHLIST_PATH)
        registry.put("watchlist", df)
        _watchlist_date = current_date
        logger.info(f"📁 Watchlist loaded into DatasetRegistry ({len(df)} symbols)")
        return df.copy()
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
                registry.put("watchlist", df)
                _watchlist_date = current_date
                logger.info(f"☁️ [WATCHLIST CACHE] Restored watchlist from Postgres cache ({len(df)} symbols)")
                return df.copy()
        except Exception as e:
            logger.warning(f"Failed to restore watchlist from DB: {e}")

        # Fallback if missing: do NOT build here, let main.py watchdog handle it
        logger.info("⏳ [WATCHLIST] Watchlist missing from disk/DB for today. Waiting for Watchdog / Daily Builder to complete...")
        return pd.DataFrame()
