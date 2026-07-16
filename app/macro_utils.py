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

class MarketRegimeEngine:
    @staticmethod
    def _compute_state_for_row(df, idx):
        import pandas as pd
        
        try:
            price = float(df["Close"].iloc[idx])
            sma20 = float(df["Close"].rolling(window=20).mean().iloc[idx])
            sma50 = float(df["Close"].rolling(window=50).mean().iloc[idx])
            sma200 = float(df["Close"].rolling(window=200).mean().iloc[idx])
            
            nifty_ago = float(df["Close"].iloc[idx - 20])
            n_ret = ((price - nifty_ago) / nifty_ago) * 100.0 if nifty_ago > 0 else 0.0
            
            try:
                import ta
                adx_series = ta.trend.ADXIndicator(df['High'], df['Low'], df['Close'], window=14).adx()
                adx_val = float(adx_series.iloc[idx]) if pd.notna(adx_series.iloc[idx]) else 0.0
            except Exception:
                adx_val = 0.0
                
            trend = "NEUTRAL"
            if n_ret > 2.0: trend = "BULL"
            elif n_ret < -2.0: trend = "BEAR"
                
            strength = "WEAK"
            if adx_val >= 25.0: strength = "STRONG"
            elif adx_val >= 15.0: strength = "MODERATE"
            
            bull_signals = sum([
                1 if price > sma20 else 0,
                1 if price > sma50 else 0,
                1 if price > sma200 else 0,
                1 if strength == "STRONG" and trend == "BULL" else 0
            ])
            bear_signals = sum([
                1 if price < sma20 else 0,
                1 if price < sma50 else 0,
                1 if price < sma200 else 0,
                1 if strength == "STRONG" and trend == "BEAR" else 0
            ])
            
            total_signals = 4
            agreement_count = bull_signals if trend == "BULL" else (bear_signals if trend == "BEAR" else total_signals - abs(bull_signals - bear_signals))
            conf_pct = max(0, min(100, int((agreement_count / total_signals) * 100)))
            
            trend_score = 100 if trend == "BULL" else (0 if trend == "BEAR" else 50)
            strength_score = 100 if strength == "STRONG" else (50 if strength == "MODERATE" else 0)
            if trend == "BEAR" and strength == "STRONG": strength_score = 0
            elif trend == "BEAR" and strength == "MODERATE": strength_score = 25
            
            conf_score = conf_pct if trend == "BULL" else (100 - conf_pct if trend == "BEAR" else 50)
            
            # Simplified score for history tracking (Volatility excluded for historical row)
            score = (trend_score * 0.40) + (strength_score * 0.30) + (conf_score * 0.20)
            
            return {
                "score": score,
                "price": price,
                "sma20": sma20,
                "sma50": sma50,
                "sma200": sma200,
                "n_ret": n_ret,
                "adx_val": adx_val,
                "trend": trend,
                "strength": strength,
                "conf_pct": conf_pct,
                "agreement_count": agreement_count,
                "total_signals": total_signals
            }
        except Exception:
            return None

    @staticmethod
    def get_regime_context(nifty_ret: float = None) -> dict:
        import logging
        logger = logging.getLogger(__name__)

        try:
            from macro_utils import _get_daily_nifty, get_nifty_intraday_drop
            df = _get_daily_nifty()
            
            if df is not None and not df.empty and len(df) >= 200:
                state_today = MarketRegimeEngine._compute_state_for_row(df, -1)
                state_yesterday = MarketRegimeEngine._compute_state_for_row(df, -2)
                
                if state_today:
                    price = state_today["price"]
                    sma20 = state_today["sma20"]
                    sma50 = state_today["sma50"]
                    sma200 = state_today["sma200"]
                    n_ret = state_today["n_ret"]
                    adx_val = state_today["adx_val"]
                    trend = state_today["trend"]
                    strength = state_today["strength"]
                    conf_pct = state_today["conf_pct"]
                    agreement_count = state_today["agreement_count"]
                    base_total = state_today["total_signals"]
                    
                    intraday_drop = get_nifty_intraday_drop()
                    volatility = "NORMAL"
                    if intraday_drop >= 1.5: volatility = "HIGH"
                    elif intraday_drop <= 0.5: volatility = "LOW"
                    
                    if volatility == "LOW": 
                        if trend == "BULL": agreement_count += 1
                        elif trend == "BEAR": agreement_count -= 1
                    elif volatility == "HIGH":
                        if trend == "BULL": agreement_count -= 1
                        elif trend == "BEAR": agreement_count += 1
                        
                    total_signals = base_total + 1
                    agreement_count = max(0, min(total_signals, agreement_count))
                    conf_pct = int((agreement_count / total_signals) * 100)
                    
                    trend_score = 100 if trend == "BULL" else (0 if trend == "BEAR" else 50)
                    strength_score = 100 if strength == "STRONG" else (50 if strength == "MODERATE" else 0)
                    if trend == "BEAR" and strength == "STRONG": strength_score = 0
                    elif trend == "BEAR" and strength == "MODERATE": strength_score = 25
                    vol_score = 100 if volatility == "LOW" else (50 if volatility == "NORMAL" else 0)
                    conf_score = conf_pct if trend == "BULL" else (100 - conf_pct if trend == "BEAR" else 50)
                    
                    market_score = (trend_score * 0.40) + (strength_score * 0.30) + (conf_score * 0.20) + (vol_score * 0.10)
                    market_score = max(0, min(100, int(market_score)))
                    
                    trend_direction = "STABLE"
                    if state_yesterday:
                        y_score = state_yesterday["score"] + (vol_score * 0.10) # Assume same vol for approximation
                        if market_score > y_score + 2:
                            trend_direction = "IMPROVING"
                        elif market_score < y_score - 2:
                            trend_direction = "WEAKENING"
                            
                    phase = "CONSOLIDATION"
                    if trend == "BULL":
                        if price > sma20 and sma20 > sma50 and sma50 > sma200: phase = "EXPANSION"
                        elif price < sma20 and price > sma50: phase = "PULLBACK"
                    elif trend == "BEAR":
                        if price < sma20 and sma20 < sma50 and sma50 < sma200: phase = "CAPITULATION"
                        elif price < sma20 and price > sma200: phase = "DISTRIBUTION"

                    return {
                        "engine_version": "MARKET_CONTEXT_V1",
                        "trend": trend,
                        "strength": strength,
                        "volatility": volatility,
                        "market_phase": phase,
                        "trend_direction": trend_direction,
                        "market_score": market_score,
                        "confidence": {
                            "agreement": agreement_count,
                            "signals": total_signals,
                            "score": conf_pct
                        },
                        "metrics": {
                            "return20d": round(n_ret, 2),
                            "adx": round(adx_val, 2),
                            "atr_pct": round(intraday_drop, 2), # Approximating ATR pct with drop
                            "price_vs_20dma": round(((price - sma20)/sma20)*100, 2) if sma20 > 0 else 0,
                            "price_vs_50dma": round(((price - sma50)/sma50)*100, 2) if sma50 > 0 else 0,
                            "price_vs_200dma": round(((price - sma200)/sma200)*100, 2) if sma200 > 0 else 0
                        }
                    }
        except Exception as e:
            logger.warning(f"Failed to compute context inputs: {e}")
            
        return {
            "engine_version": "MARKET_CONTEXT_V1",
            "trend": "NEUTRAL",
            "strength": "WEAK",
            "volatility": "NORMAL",
            "market_phase": "CONSOLIDATION",
            "trend_direction": "STABLE",
            "market_score": 50,
            "confidence": {"agreement": 0, "signals": 5, "score": 0},
            "metrics": {}
        }


def get_macro_regime(nifty_ret: Optional[float] = None) -> str:
    ctx = MarketRegimeEngine.get_regime_context(nifty_ret=nifty_ret)
    return ctx.get("trend", "NEUTRAL")


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
            
            df_safe = df.copy()
            # Normalize index to avoid RangeIndex date coercion issues
            datetime_col = next((c for c in ["Datetime", "Date", "index"] if c in df_safe.columns), None)
            if datetime_col:
                df_safe = df_safe.set_index(datetime_col)
                
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
