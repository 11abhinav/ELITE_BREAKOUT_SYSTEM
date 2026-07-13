import time
import logging
import pandas as pd
from datetime import datetime
from zoneinfo import ZoneInfo
from threading import Lock
from typing import Optional

logger = logging.getLogger(__name__)
IST = ZoneInfo("Asia/Kolkata")

# TTL Cache settings
MACRO_CACHE_TTL_SECONDS = 300  # 5 minutes

class MacroCache:
    def __init__(self):
        self.lock = Lock()
        
        # 1-year daily cache (used for 20d return, 6m return, 52w distance, regime)
        self.daily_data = None
        self.daily_last_fetched = 0
        
        # 5-day 15m cache (used for intraday drop)
        self.intraday_data = None
        self.intraday_last_fetched = 0

_cache = MacroCache()

def _get_daily_nifty() -> pd.DataFrame:
    """Fetch 1-year daily NIFTY data with 5-minute caching."""
    now = time.time()
    with _cache.lock:
        if _cache.daily_data is not None and (now - _cache.daily_last_fetched) < MACRO_CACHE_TTL_SECONDS:
            return _cache.daily_data
            
    try:
        from price_cache import fetch_unified_historical
        # We need at least 1 year for the 6-month returns and 52W high
        fetched = fetch_unified_historical(["^NSEI"], period="1y", interval="1d", requester="macro_daily")
        df = fetched.get("^NSEI")
        if df is not None and not df.empty:
            with _cache.lock:
                _cache.daily_data = df
                _cache.daily_last_fetched = time.time()
            return df
    except Exception:
        logger.exception(f"Failed to fetch Nifty daily macro data")
        
    return _cache.daily_data

def _get_intraday_nifty() -> pd.DataFrame:
    """Fetch 5-day 15-minute NIFTY data with 5-minute caching."""
    now = time.time()
    with _cache.lock:
        if _cache.intraday_data is not None and (now - _cache.intraday_last_fetched) < MACRO_CACHE_TTL_SECONDS:
            return _cache.intraday_data
            
    try:
        from price_cache import fetch_unified_historical
        fetched = fetch_unified_historical(["^NSEI"], period="5d", interval="15m", requester="macro_intraday")
        df = fetched.get("^NSEI")
        if df is not None and not df.empty:
            with _cache.lock:
                _cache.intraday_data = df
                _cache.intraday_last_fetched = time.time()
            return df
    except Exception:
        logger.exception(f"Failed to fetch Nifty intraday macro data")
        
    return _cache.intraday_data

def get_macro_regime(nifty_ret: Optional[float] = None) -> str:
    """Calculate the market regime based on Nifty 20-day returns and ADX.
    
    [FIX] Thresholds raised: previous -2%/-5% BEAR triggers were too sensitive
    and caused scanners to go dark during normal pullbacks. New thresholds:
      Trending (ADX>=20): BEAR < -5%, BULL > +3%
      Rangebound (ADX<20): BEAR < -8%, BULL > +5%
    Default on failure: NEUTRAL (not BULL or BEAR).
    """
    try:
        df = _get_daily_nifty()
        if df is not None and not df.empty and len(df) >= 20:
            if nifty_ret is None:
                val_now = df["Close"].iloc[-1]
                nifty_now = float(val_now.iloc[0]) if hasattr(val_now, 'iloc') else float(val_now)
                val_ago = df["Close"].iloc[-20]
                nifty_ago = float(val_ago.iloc[0]) if hasattr(val_ago, 'iloc') else float(val_ago)
                
                if nifty_ago > 0:
                    nifty_ret = ((nifty_now - nifty_ago) / nifty_ago) * 100.0
            
            if nifty_ret is not None:
                ret = nifty_ret
                
                adx_val = 0.0
                try:
                    import ta
                    adx_series = ta.trend.ADXIndicator(df['High'], df['Low'], df['Close'], window=14).adx()
                    adx_val = float(adx_series.iloc[-1]) if pd.notna(adx_series.iloc[-1]) else 0.0
                except Exception as e:
                    logger.warning(f"Could not compute ADX for macro regime: {e}")
                
                # [FIX] Raised thresholds to prevent minor pullbacks from triggering BEAR
                if adx_val >= 20.0:
                    if ret < -5.0: return "BEAR"    # was -2.0 (too trigger-happy)
                    if ret > 3.0:  return "BULL"    # was 2.0
                    return "NEUTRAL"
                else:
                    if ret < -8.0: return "BEAR"    # was -5.0
                    if ret > 5.0:  return "BULL"
                    return "NEUTRAL"
    except Exception as e:
        logger.warning(f"Failed to compute macro regime: {e}")
    return "NEUTRAL"

def get_nifty_20d_return() -> float:
    """Returns the 20-day percentage return of Nifty. Defaults to 0.0% if unavailable."""
    try:
        df = _get_daily_nifty()
        if df is not None and not df.empty and len(df) >= 20:
            val_now = df["Close"].iloc[-1]
            nifty_now = float(val_now.iloc[0]) if hasattr(val_now, 'iloc') else float(val_now)
            val_ago = df["Close"].iloc[-20]
            nifty_ago = float(val_ago.iloc[0]) if hasattr(val_ago, 'iloc') else float(val_ago)
            if nifty_ago > 0:
                return (nifty_now - nifty_ago) / nifty_ago * 100.0
    except Exception as e:
        logger.warning(f"Failed to compute Nifty 20d return: {e}")
    return 0.0  # Fallback assumption

def get_nifty_6m_state() -> tuple[Optional[float], Optional[float]]:
    """
    Returns (ret_6m, dist_52w) for Nifty.
    Returns (None, None) if data is unavailable.
    """
    try:
        df = _get_daily_nifty()
        if df is not None and not df.empty and len(df) >= 2:
            hist_6m = df.tail(126) # Approx 6 months
            if len(hist_6m) >= 2:
                start_price = float(hist_6m['Close'].iloc[0])
                end_price = float(hist_6m['Close'].iloc[-1])
                ret_6m = ((end_price - start_price) / start_price) * 100.0 if start_price > 0 else 0.0
            else:
                ret_6m = None
                
            high_52w = float(df['High'].max())
            end_price_1y = float(df['Close'].iloc[-1])
            dist_52w = ((high_52w - end_price_1y) / high_52w) * 100.0 if high_52w > 0 else 0.0
            
            return ret_6m, dist_52w
    except Exception as e:
        logger.warning(f"Failed to compute Nifty 6m state: {e}")
    return None, None

def get_nifty_intraday_drop() -> float:
    """
    Returns the percentage drop from today's open to current price.
    If the market is up or data is unavailable, returns 0.0.
    """
    try:
        df = _get_intraday_nifty()
        if df is not None and not df.empty:
            today_str = datetime.now(IST).strftime('%Y-%m-%d')
            
            # Normalize index to avoid AttributeError if fetcher returns plain Index
            df_safe = df.copy()
            df_safe.index = pd.to_datetime(df_safe.index, errors="coerce")
            today_data = df_safe[df_safe.index.notna() & (df_safe.index.strftime("%Y-%m-%d") == today_str)]
            
            if not today_data.empty:
                nifty_open = float(today_data['Open'].iloc[0])
                nifty_current = float(today_data['Close'].iloc[-1])
                if nifty_open > 0:
                    drop = ((nifty_open - nifty_current) / nifty_open) * 100.0
                    return drop if drop > 0 else 0.0
    except Exception as e:
        logger.warning(f"Failed to compute Nifty intraday drop: {e}")
    return 0.0
