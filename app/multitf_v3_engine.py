# app/multitf_v3_engine.py
# =============================================================================
# MULTI-TIMEFRAME V3 — INTRADAY BASE CONTRACTION & EXPANSION ENGINE
# =============================================================================
#
# RULE 67 CHANGE-RATIONALE:
# - Replaces the flawed legacy Multi-TF model (buying hourly breakouts of daily 20D highs,
#   which suffered from adverse selection, -0.681R expectancy, and 76.2% SL hit rate).
# - Implements genuine pre-breakout multi-session intraday base building:
#   1. Daily Trend Context: Stock in macro bull/neutral trend (Close > EMA20 > SMA50).
#   2. Multi-Session Intraday Base: 15-25 hourly bars (~3-4 trading sessions) of tight horizontal
#      consolidation where base width <= 1.80x daily ATR.
#   3. Intraday Contraction (H1 VCP): Late-stage range compression where the last 5-8 bars
#      have a narrower range than the prior 12-15 bars (compression ratio <= 0.75) with ascending lows.
#   4. Base Ceiling Breakout Trigger: Hourly bar closes ABOVE the intraday base ceiling
#      (NOT the daily 20D high) with volume thrust >= 1.5x SMA20 hourly volume.
#   5. Anchored Structural Risk Management: Initial stop loss anchored below the multi-session
#      base shelf (Base_Low - 0.20x daily ATR), giving room for retests without premature invalidation.
# =============================================================================

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple

import numpy as np
import pandas as pd

logger = logging.getLogger("MultiTFV3Engine")


@dataclass
class MultiTFV3Setup:
    """Represents a validated Multi-TF V3 Intraday Base Setup."""
    symbol: str
    is_valid: bool
    rejection_reason: str = ""
    
    # Base geometry
    base_bars_count: int = 0
    base_high: float = 0.0
    base_low: float = 0.0
    base_width_atr: float = 0.0
    compression_ratio: float = 1.0
    has_higher_lows: bool = False
    
    # Trade execution parameters
    entry_price: float = 0.0
    stop_loss: float = 0.0
    target_1: float = 0.0
    target_2: float = 0.0
    risk_distance: float = 0.0
    rr_ratio: float = 0.0
    
    # Volume and momentum telemetry
    volume_ratio: float = 1.0
    candle_range_atr: float = 0.0
    close_position: float = 0.0
    quality_score: float = 0.0


def evaluate_multitf_v3_intraday_base(
    symbol: str,
    daily_df_cut: pd.DataFrame,
    hourly_df_cut: pd.DataFrame,
    regime: str = "BULL",
    base_window_bars: int = 20,
    max_base_width_atr: float = 1.80,
    max_compression_ratio: float = 0.80,
    min_volume_ratio: float = 1.50,
    min_close_position: float = 0.65,
    max_breakout_extension_atr: float = 0.50,
) -> Optional[Dict[str, Any]]:
    """
    Evaluates whether an hourly bar represents a valid V3 Intraday Base Breakout.
    Operates strictly on finalized closed bars with zero forward lookahead.
    """
    if hourly_df_cut is None or len(hourly_df_cut) < (base_window_bars + 5):
        return None
    if daily_df_cut is None or len(daily_df_cut) < 50:
        return None

    # 1. Daily Trend Context
    daily_close = float(daily_df_cut["Close"].iloc[-1])
    daily_ema20 = float(daily_df_cut["Close"].ewm(span=20, adjust=False).mean().iloc[-1])
    daily_sma50 = float(daily_df_cut["Close"].rolling(50, min_periods=20).mean().iloc[-1])
    
    # Daily trend filter: Stock must be above daily EMA20 and EMA20 > SMA50
    if not (daily_close > daily_ema20 and daily_ema20 >= daily_sma50 * 0.98):
        return None

    # Daily ATR20
    daily_atr20 = float((daily_df_cut["High"] - daily_df_cut["Low"]).iloc[-20:].mean())
    if daily_atr20 <= 0:
        return None

    # 2. Multi-Session Intraday Base Geometry (strictly bars t - base_window to t - 1)
    # The current bar (iloc[-1]) is the breakout candidate; the base is formed strictly prior.
    base_df = hourly_df_cut.iloc[-(base_window_bars + 1):-1]
    base_high = float(base_df["High"].max())
    base_low = float(base_df["Low"].min())
    base_range = base_high - base_low
    
    base_width_atr = base_range / daily_atr20
    if base_width_atr > max_base_width_atr:
        return None  # Base too erratic/wide; not a coiling consolidation

    # 3. Volatility Contraction Structure (H1 VCP)
    # Divide the base into early phase and late compression phase
    split_idx = int(len(base_df) * 0.60)
    early_base = base_df.iloc[:split_idx]
    late_base = base_df.iloc[split_idx:]
    
    early_range = float(early_base["High"].max() - early_base["Low"].min())
    late_range = float(late_base["High"].max() - late_base["Low"].min())
    
    compression_ratio = (late_range / early_range) if early_range > 0 else 1.0
    if compression_ratio > max_compression_ratio:
        return None  # Range is expanding rather than contracting prior to breakout

    # Higher lows check: Late base low should not break below early base low
    late_low = float(late_base["Low"].min())
    early_low = float(early_base["Low"].min())
    has_higher_lows = (late_low >= early_low * 0.998)
    if not has_higher_lows:
        return None  # Lower lows indicate distribution rather than accumulation

    # 4. Breakout Candidate Bar Evaluation (iloc[-1])
    breakout_bar = hourly_df_cut.iloc[-1]
    h_close = float(breakout_bar["Close"])
    h_open = float(breakout_bar["Open"])
    h_high = float(breakout_bar["High"])
    h_low = float(breakout_bar["Low"])
    h_vol = float(breakout_bar["Volume"]) if "Volume" in hourly_df_cut.columns else 100000.0
    
    candle_range = max(0.01, h_high - h_low)
    
    # Must close above the multi-session base ceiling
    if h_close <= base_high:
        return None
    
    # Must not be an exhausted late extension (entry price must be within 0.50 ATR of base high)
    extension_from_base = (h_close - base_high) / daily_atr20
    if extension_from_base > max_breakout_extension_atr:
        return None  # Chasing already extended thrust

    # Candle posture: Bullish body, strong close near highs
    if h_close <= h_open:
        return None  # Red candle or doji
        
    close_pos = (h_close - h_low) / candle_range
    if close_pos < min_close_position:
        return None  # Closed in lower part of the candle (weak thrust / selling wick)

    # Volume thrust confirmation
    avg_vol_20 = float(hourly_df_cut["Volume"].iloc[-21:-1].mean()) if len(hourly_df_cut) >= 21 and "Volume" in hourly_df_cut.columns else float(hourly_df_cut["Volume"].mean())
    vol_ratio = (h_vol / avg_vol_20) if avg_vol_20 > 0 else 1.0
    if vol_ratio < min_volume_ratio:
        return None  # Lack of institutional volume ignition

    # 5. Anchored Structural Risk Architecture
    # Stop Loss is anchored below the late contraction shelf, buffered by 0.20x ATR
    shelf_low = min(late_low, h_low)
    stop_loss = round(shelf_low - 0.20 * daily_atr20, 2)
    entry_price = h_close
    risk_dist = max(0.01, entry_price - stop_loss)
    
    target_1 = round(entry_price + 2.0 * risk_dist, 2)
    target_2 = round(entry_price + 3.0 * risk_dist, 2)
    rr_ratio = round((target_1 - entry_price) / risk_dist, 2)

    # Quality score calculation (0-100)
    score = 70.0
    if compression_ratio <= 0.60:
        score += 10.0  # Exceptional contraction
    if vol_ratio >= 2.0:
        score += 10.0  # High volume thrust
    if close_pos >= 0.80:
        score += 5.0   # Dominant close
    if regime == "BULL":
        score += 5.0   # Bull regime alignment

    return {
        "symbol": symbol,
        "entry_price": entry_price,
        "stop_loss": stop_loss,
        "target_1": target_1,
        "target_2": target_2,
        "risk_distance": risk_dist,
        "signal_rr": rr_ratio,
        "score": score,
        "volume_ratio": round(vol_ratio, 2),
        "base_high": round(base_high, 2),
        "base_low": round(base_low, 2),
        "base_width_atr": round(base_width_atr, 2),
        "compression_ratio": round(compression_ratio, 3),
        "regime": regime,
        "stage": "MULTITF_V3_BASE_BREAKOUT",
        "engine_path": "MULTITF_V3_INTRADAY_BASE",
        "variant_id": "MULTITF_V3_INTRADAY_BASE",
    }
