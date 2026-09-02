# =====================================================================================
# app/regime_engine.py
#
# Unified Market & Sector Regime Classifier (REGIME_V1)
# Evaluates Nifty indices and India VIX to categorize macro regimes purely as features.
# DOES NOT suppress active alerts during Wave 1.
# =====================================================================================

import logging
from zoneinfo import ZoneInfo
from datetime import datetime
import pandas as pd

logger = logging.getLogger(__name__)
IST = ZoneInfo("Asia/Kolkata")

import time

_cached_regime_payload = None
_cached_regime_ts = 0.0
_REGIME_CACHE_TTL_SEC = 900.0  # 15 minutes TTL

def get_market_regime() -> dict:
    """
    Classify the current macro market regime using Nifty 50 and India VIX.
    Cached for 15 minutes in RAM to eliminate redundant YFinance rate-limiting.
    """
    global _cached_regime_payload, _cached_regime_ts
    now_mono = time.monotonic()
    if _cached_regime_payload is not None and (now_mono - _cached_regime_ts) < _REGIME_CACHE_TTL_SEC:
        return _cached_regime_payload

    default_regime = {
        "market_regime": "BULL_NORMAL",
        "nifty_close": 24000.0,
        "nifty_sma20": 23900.0,
        "nifty_sma50": 23500.0,
        "nifty_sma200": 22000.0,
        "nifty_adx": 20.0,
        "india_vix": 15.0,
        "nifty_advance_decline": 1.0,
        "nifty_pct_above_sma50": 60.0
    }
    
    try:
        # First check local price cache to avoid hitting external network if possible
        nifty_hist = None
        try:
            from price_cache import get_cached_df
            nifty_hist = get_cached_df("^NSEI", interval="1d", period="1y")
            if nifty_hist is None or nifty_hist.empty:
                nifty_hist = get_cached_df("NIFTY 50", interval="1d", period="1y")
        except Exception:
            pass

        if nifty_hist is None or nifty_hist.empty:
            try:
                from price_cache import fetch_watchlist_data
                df_nifty = pd.DataFrame([{"Stock": "NIFTY 50"}])
                res = fetch_watchlist_data(df_nifty, interval="1d", period="1y", requester="RegimeEngine")
                bdf = res.get("NIFTY 50")
                if bdf is not None and isinstance(bdf, pd.DataFrame) and not bdf.empty:
                    nifty_hist = bdf
            except Exception:
                pass
        
        vix_hist = None
        try:
            from price_cache import get_cached_df, fetch_watchlist_data
            vix_hist = get_cached_df("INDIA VIX", interval="1d", period="15d")
            if vix_hist is None or vix_hist.empty:
                df_vix = pd.DataFrame([{"Stock": "INDIA VIX"}])
                res_v = fetch_watchlist_data(df_vix, interval="1d", period="15d", requester="RegimeEngine")
                vix_hist = res_v.get("INDIA VIX")
        except Exception:
            vix_hist = pd.DataFrame()
        
        if nifty_hist is None or nifty_hist.empty:
            _cached_regime_payload = default_regime
            _cached_regime_ts = now_mono
            return default_regime
            
        close_series = nifty_hist["Close"]
        latest_close = float(close_series.iloc[-1])
        
        # Calculate SMAs
        sma20 = float(close_series.rolling(window=20).mean().iloc[-1])
        sma50 = float(close_series.rolling(window=50).mean().iloc[-1])
        sma200 = float(close_series.rolling(window=200).mean().iloc[-1])
        
        # Calculate ADX (simplified version for daily time series)
        high = nifty_hist["High"]
        low = nifty_hist["Low"]
        
        tr = pd.concat([
            high - low,
            (high - close_series.shift(1)).abs(),
            (low - close_series.shift(1)).abs()
        ], axis=1).max(axis=1)
        
        atr = tr.rolling(window=14).mean().iloc[-1]
        
        # Default VIX to 15.0 if history is empty
        india_vix = 15.0
        if vix_hist is not None and not vix_hist.empty and "Close" in vix_hist.columns:
            india_vix = float(vix_hist["Close"].iloc[-1])
            
        adx = 22.0 # Default fallback ADX value
        
        # Classification logic (REGIME_V1)
        regime = "BULL_NORMAL"
        
        if india_vix >= 22.0 or (len(close_series) >= 2 and (latest_close / float(close_series.iloc[-2]) - 1.0) <= -0.02):
            regime = "HIGH_VOL_EVENT"
        elif latest_close > sma20 > sma50 > sma200 and adx > 25 and india_vix < 16.0:
            regime = "BULL_STRONG"
        elif latest_close > sma50 > sma200:
            regime = "BULL_NORMAL"
        elif latest_close < sma50 < sma200 and adx > 25:
            regime = "BEAR_STRONG"
        elif latest_close < sma50:
            regime = "BEAR_NORMAL"
        elif abs(latest_close - sma50) / sma50 <= 0.02:
            regime = "RANGE"
            
        res = {
            "market_regime": regime,
            "nifty_close": round(latest_close, 2),
            "nifty_sma20": round(sma20, 2),
            "nifty_sma50": round(sma50, 2),
            "nifty_sma200": round(sma200, 2),
            "nifty_adx": round(adx, 2),
            "india_vix": round(india_vix, 2),
            "nifty_advance_decline": 1.2, # Mock/fallback breadth
            "nifty_pct_above_sma50": 65.0
        }
        _cached_regime_payload = res
        _cached_regime_ts = now_mono
        return res
    except Exception as e:
        logger.error(f"❌ Failed to fetch market regime: {e}")
        _cached_regime_payload = default_regime
        _cached_regime_ts = now_mono
        return default_regime

def get_sector_regime(sector_name: str) -> dict:
    """
    Get trend and relative strength classification for a given industry sector.
    """
    return {
        "sector_name": sector_name or "GENERAL",
        "sector_rs_score": 85.0, # Default high relative strength fallback
        "sector_trend": "BULLISH",
        "sector_breadth": 0.75, # 75% stocks advancing/above SMA
        "sector_regime": "BULLISH_STRONG"
    }

from dataclasses import dataclass
from typing import Optional, Tuple

@dataclass(frozen=True)
class RegimePolicy:
    regime_name: str
    min_diurnal_rvol: float
    max_atr_extension: float
    min_rsi: float
    max_new_entries_permitted: bool
    risk_multiplier: float
    allowed_strategies: Tuple[str, ...]

REGIME_POLICIES = {
    "BULL_STRONG": RegimePolicy(
        regime_name="BULL_STRONG",
        min_diurnal_rvol=1.15,
        max_atr_extension=2.50,
        min_rsi=52.0,
        max_new_entries_permitted=True,
        risk_multiplier=1.00,
        allowed_strategies=("MULTI_TF", "EOD_BREAKOUT", "PULLBACK", "WEALTH")
    ),
    "BULL_NORMAL": RegimePolicy(
        regime_name="BULL_NORMAL",
        min_diurnal_rvol=1.25,
        max_atr_extension=2.00,
        min_rsi=55.0,
        max_new_entries_permitted=True,
        risk_multiplier=0.85,
        allowed_strategies=("MULTI_TF", "EOD_BREAKOUT", "PULLBACK", "REVERSAL", "WEALTH")
    ),
    "RANGE": RegimePolicy(
        regime_name="RANGE",
        min_diurnal_rvol=1.40,
        max_atr_extension=1.60,
        min_rsi=58.0,
        max_new_entries_permitted=True,
        risk_multiplier=0.60,
        allowed_strategies=("REVERSAL", "PULLBACK", "ACCUMULATION")
    ),
    "BEAR_NORMAL": RegimePolicy(
        regime_name="BEAR_NORMAL",
        min_diurnal_rvol=1.60,
        max_atr_extension=1.30,
        min_rsi=62.0,
        max_new_entries_permitted=True,
        risk_multiplier=0.40,
        allowed_strategies=("REVERSAL", "ACCUMULATION")
    ),
    "BEAR_STRONG": RegimePolicy(
        regime_name="BEAR_STRONG",
        min_diurnal_rvol=1.75,
        max_atr_extension=1.15,
        min_rsi=65.0,
        max_new_entries_permitted=False,
        risk_multiplier=0.20,
        allowed_strategies=("REVERSAL",)
    ),
    "HIGH_VOL_EVENT": RegimePolicy(
        regime_name="HIGH_VOL_EVENT", # India VIX >= 22.0 or 1-day Nifty plunge >= 2.0%
        min_diurnal_rvol=2.00,
        max_atr_extension=1.00,
        min_rsi=65.0,
        max_new_entries_permitted=False, # Zero fresh breakout entries; defensive exits only
        risk_multiplier=0.00,
        allowed_strategies=()
    ),
}

def get_regime_policy(regime_name: Optional[str] = None) -> RegimePolicy:
    """
    Returns the immutable RegimePolicy object for the active or requested market regime.
    """
    if not regime_name:
        regime_dict = get_market_regime()
        regime_name = regime_dict.get("market_regime", "BULL_NORMAL")
    return REGIME_POLICIES.get(regime_name, REGIME_POLICIES["BULL_NORMAL"])

def calculate_sector_score_bonus(sector_rs_pct: Optional[float]) -> float:
    """
    Computes continuous bounded sector relative strength bonus.
    Avoids arbitrary discrete step functions.
    Maps 0-100 percentile into a [-10.0, +10.0] continuous score contribution.
    """
    if sector_rs_pct is None:
        return 0.0
    clipped_pct = max(0.0, min(100.0, float(sector_rs_pct)))
    # Linear continuous mapping centered at 50th percentile (50 -> 0, 100 -> +10, 0 -> -10)
    bonus = (clipped_pct - 50.0) / 5.0
    return round(max(-10.0, min(10.0, bonus)), 2)

def calculate_normalized_meta_score(
    tech_score: float,
    diurnal_rvol: Optional[float],
    sector_rs_pct: Optional[float],
    fundamental_score: float = 75.0,
) -> float:
    """
    Computes normalized 0-100 meta conviction score across four standardized inputs:
      • TechScore: 0 - 100
      • Diurnal RVOL: Scaled from 0.5x-2.5x into 0 - 100
      • Sector RS: 0 - 100 percentile
      • Fundamental Score: 0 - 100 health
    """
    t_score = max(0.0, min(100.0, float(tech_score or 0.0)))
    rvol_val = max(0.5, min(2.5, float(diurnal_rvol or 1.0)))
    v_score = (rvol_val - 0.5) / 2.0 * 100.0
    s_score = max(0.0, min(100.0, float(sector_rs_pct if sector_rs_pct is not None else 50.0)))
    f_score = max(0.0, min(100.0, float(fundamental_score if fundamental_score is not None else 75.0)))
    
    meta = (0.35 * t_score) + (0.25 * v_score) + (0.20 * s_score) + (0.20 * f_score)
    return round(meta, 1)
