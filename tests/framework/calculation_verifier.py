# =====================================================================================
# tests/framework/calculation_verifier.py
# LEVEL 3: TECHNICAL INDICATORS & CALCULATION PARITY VERIFIER
# =====================================================================================
import logging
from typing import Dict, List, Tuple
import pandas as pd
import numpy as np

logger = logging.getLogger("CALCULATION_VERIFIER")


class CalculationError(Exception):
    """Raised when an indicator or calculated metric is out of bounds or mathematically impossible."""
    pass


def verify_indicator_bounds(ind_df: pd.DataFrame, symbol: str, timeframe: str) -> Tuple[bool, List[str]]:
    """Level 3 Verification: Asserts mathematical bounds for technical indicators."""
    errors = []
    
    if ind_df is None or ind_df.empty:
        errors.append(f"[{symbol} {timeframe}] Indicator DataFrame is empty/None!")
        return False, errors

    # 1. RSI (14) Contract: 0.0 <= RSI <= 100.0
    if "RSI" in ind_df.columns:
        rsi_series = ind_df["RSI"].dropna()
        if not rsi_series.empty:
            invalid_rsi = rsi_series[(rsi_series < 0.0) | (rsi_series > 100.0)]
            if not invalid_rsi.empty:
                errors.append(f"[{symbol} {timeframe}] RSI out of 0-100 range! Bad values: {invalid_rsi.tolist()[:3]}")

    # 2. ADX (14) Contract: 0.0 <= ADX <= 100.0
    if "ADX" in ind_df.columns:
        adx_series = ind_df["ADX"].dropna()
        if not adx_series.empty:
            invalid_adx = adx_series[(adx_series < 0.0) | (adx_series > 100.0)]
            if not invalid_adx.empty:
                errors.append(f"[{symbol} {timeframe}] ADX out of 0-100 range! Bad values: {invalid_adx.tolist()[:3]}")

    # 3. Volume Ratio Contract: Volume_Ratio >= 0.0
    if "Volume_Ratio" in ind_df.columns:
        vr_series = ind_df["Volume_Ratio"].dropna()
        if not vr_series.empty:
            invalid_vr = vr_series[vr_series < 0.0]
            if not invalid_vr.empty:
                errors.append(f"[{symbol} {timeframe}] Negative Volume Ratio! Bad values: {invalid_vr.tolist()[:3]}")

    # 4. Moving Averages: Positivity Check (EMA20, SMA50, SMA200 > 0)
    for ma in ["EMA20", "SMA50", "SMA200", "sma_50", "sma_200"]:
        if ma in ind_df.columns:
            ma_series = ind_df[ma].dropna()
            if not ma_series.empty:
                invalid_ma = ma_series[ma_series <= 0.0]
                if not invalid_ma.empty:
                    errors.append(f"[{symbol} {timeframe}] Invalid Moving Average {ma} <= 0! Bad values: {invalid_ma.tolist()[:3]}")

    is_valid = len(errors) == 0
    return is_valid, errors


def verify_sl_target_engine(entry_price: float, sl_price: float, target_price: float, symbol: str) -> Tuple[bool, List[str]]:
    """Level 3 Verification: Asserts stop loss and target price geometry."""
    errors = []
    
    if entry_price <= 0.0:
        errors.append(f"[{symbol}] Entry price <= 0: {entry_price}")
        return False, errors

    if sl_price >= entry_price:
        errors.append(f"[{symbol}] Stop Loss ({sl_price}) >= Entry Price ({entry_price})!")

    if target_price <= entry_price:
        errors.append(f"[{symbol}] Target Price ({target_price}) <= Entry Price ({entry_price})!")

    if sl_price > 0 and target_price > 0:
        risk = entry_price - sl_price
        reward = target_price - entry_price
        rr_ratio = reward / risk if risk > 0 else 0.0
        if rr_ratio < 1.0:
            errors.append(f"[{symbol}] Sub-unity Risk/Reward ratio: {rr_ratio:.2f} (Risk: ₹{risk:.2f}, Reward: ₹{reward:.2f})")

    return len(errors) == 0, errors
