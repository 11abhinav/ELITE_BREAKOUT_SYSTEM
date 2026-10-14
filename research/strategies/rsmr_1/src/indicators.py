"""
RSMR-1 Technical & Momentum Indicators
Strictly Point-in-Time calculations using only prior sessions.
"""

from typing import Dict, List, Optional, Any
import pandas as pd
import numpy as np

def calculate_stock_metrics(
    df: pd.DataFrame,
    as_of_date: pd.Timestamp,
    momentum_lookback: int = 126,
    sma_period: int = 200,
    liquidity_lookback: int = 60
) -> Optional[Dict[str, Any]]:
    """
    Computes Point-In-Time 6-month momentum, 200-SMA, and median daily volume.
    Returns None if data requirements are not met.
    """
    # Slice strictly up to as_of_date
    sub = df.loc[:as_of_date]
    if len(sub) < max(sma_period, momentum_lookback + 1):
        return None
        
    # Ensure current bar matches exactly or is recent
    last_dt = sub.index[-1]
    if (as_of_date - last_dt).days > 10:
        # Stock hasn't traded in over 10 days - suspended or stale
        return None

    close_series = sub["Close"]
    volume_series = sub["Volume"]
    
    current_close = float(close_series.iloc[-1])
    if current_close <= 0:
        return None
        
    # 1. Momentum 6-Month: TR[t] / TR[t - 126] - 1
    # Using 126 bars back
    if len(close_series) <= momentum_lookback:
        return None
        
    past_close = float(close_series.iloc[-1 - momentum_lookback])
    if past_close <= 0:
        return None
        
    momentum_6m = (current_close / past_close) - 1.0

    # 2. 200-Day SMA
    sma_200 = float(close_series.iloc[-sma_period:].mean())
    trend_ok = current_close > sma_200

    # 3. Liquidity: 60-day median daily turnover (Close * Volume)
    liq_sub_close = close_series.iloc[-liquidity_lookback:]
    liq_sub_vol = volume_series.iloc[-liquidity_lookback:]
    daily_turnover = liq_sub_close * liq_sub_vol
    median_daily_turnover = float(daily_turnover.median())

    return {
        "as_of_date": as_of_date,
        "last_trade_date": last_dt,
        "close": current_close,
        "past_close_126": past_close,
        "momentum_6m": momentum_6m,
        "sma_200": sma_200,
        "trend_ok": trend_ok,
        "median_turnover_60d": median_daily_turnover,
        "total_bars_available": len(sub)
    }
