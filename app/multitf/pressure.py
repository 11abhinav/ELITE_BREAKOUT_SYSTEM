# =====================================================================================
# app/multitf/pressure.py
# MULTI_TF V2 — 5m Pressure & Expansion Engine
#
# Responsibility: Monitors live and recently-closed 5m candles against the 15m box.
# Emits ATTEMPT and CONFIRMED signals based on range expansion, volume, and position.
# =====================================================================================

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Dict, Any, Optional

import pandas as pd

logger = logging.getLogger("multitf.pressure")


@dataclass
class PressureResult:
    """The outcome of evaluating 5m pressure."""
    is_attempt: bool = False
    is_confirmed: bool = False
    
    volume_ratio: float = 0.0
    range_ratio: float = 0.0
    live_position: float = 0.0
    distance_to_box_high: float = 0.0
    
    attempt_bar_boundary: int = 0
    momentum_score: int = 0


def evaluate_5m_pressure(
    live_candle: Optional[pd.Series],
    df_5m_closed: Optional[pd.DataFrame],
    box_high: float,
    atr_5m: float,
    ist_now: datetime,
    config: Dict[str, Any]
) -> PressureResult:
    """
    Evaluates both the live candle (for ATTEMPT) and the last closed candle (for CONFIRMED).
    """
    res = PressureResult()
    if df_5m_closed is None or df_5m_closed.empty or not box_high:
        return res

    # 1. Compute Baselines (from closed bars)
    median_range = (df_5m_closed["High"] - df_5m_closed["Low"]).median()
    if median_range <= 0:
        median_range = atr_5m

    # 2. Check for CONFIRMED Breakout (using strictly closed candle)
    last_closed = df_5m_closed.iloc[-1]
    _evaluate_confirmed(last_closed, box_high, atr_5m, median_range, df_5m_closed, res, config)

    # 3. Check for ATTEMPT (using forming live candle)
    if live_candle is not None and not res.is_confirmed:
        _evaluate_attempt(live_candle, box_high, atr_5m, median_range, ist_now, df_5m_closed, res, config)

    return res


def _evaluate_confirmed(
    last_closed: pd.Series,
    box_high: float,
    atr_5m: float,
    median_range: float,
    df_5m_closed: pd.DataFrame,
    res: PressureResult,
    config: Dict[str, Any]
):
    """
    A Confirmed breakout means a closed 5m bar printed definitively outside the box
    with momentum and volume.
    """
    c, o, h, l, v = last_closed["Close"], last_closed["Open"], last_closed["High"], last_closed["Low"], last_closed["Volume"]
    
    # Needs to clear box + buffer
    buffer = config.get("BREAKOUT_BUFFER_ATR_MULT", 0.10) * atr_5m
    if c <= box_high + buffer:
        return
        
    candle_range = h - l
    if candle_range <= 0: return
    
    range_ratio = candle_range / median_range
    close_pos = (c - l) / candle_range
    
    # Calculate volume baseline for this specific time slot
    slot_vol_avg = _calc_slot_volume_baseline(last_closed.name, df_5m_closed, config)
    vol_ratio = v / slot_vol_avg if slot_vol_avg > 0 else 1.0
    
    req_range = config.get("MIN_RANGE_EXPANSION", 1.25)
    req_vol = config.get("MIN_VOLUME_EXPANSION_CONFIRM", 1.50)
    req_pos = config.get("MIN_CLOSE_POSITION_CONFIRMED", 0.75)
    
    if range_ratio >= req_range and vol_ratio >= req_vol and close_pos >= req_pos:
        res.is_confirmed = True
        res.volume_ratio = vol_ratio
        res.range_ratio = range_ratio
        res.live_position = close_pos
        res.distance_to_box_high = c - box_high
        res.momentum_score = min(30, int((range_ratio + vol_ratio) * 10))


def _evaluate_attempt(
    live: pd.Series,
    box_high: float,
    atr_5m: float,
    median_range: float,
    ist_now: datetime,
    df_5m_closed: pd.DataFrame,
    res: PressureResult,
    config: Dict[str, Any]
):
    """
    An Attempt is an early-warning signal that price is pressuring the ceiling right now,
    triggering transition to CANDIDATE state and immediate target mapping.
    """
    c, h, l, v = live["Close"], live["High"], live["Low"], live["Volume"]
    
    # Distance requirement: must be "near" or above the box high
    approach_limit = box_high - (config.get("APPROACH_ATR_MULT", 0.10) * atr_5m)
    if c < approach_limit:
        return
        
    candle_range = h - l
    if candle_range <= 0: return
    
    range_ratio = candle_range / median_range
    live_pos = (c - l) / candle_range
    
    # Volume run-rate projection
    proj_vol, vol_ratio = _project_live_volume(v, live.name, ist_now, df_5m_closed, config)
    
    req_range = config.get("MIN_RANGE_EXPANSION", 1.25)
    req_vol = config.get("MIN_VOLUME_EXPANSION_ATTEMPT", 1.40)
    req_pos = config.get("MIN_LIVE_POSITION_ATTEMPT", 0.70)
    
    if range_ratio >= req_range and vol_ratio >= req_vol and live_pos >= req_pos:
        res.is_attempt = True
        res.volume_ratio = vol_ratio
        res.range_ratio = range_ratio
        res.live_position = live_pos
        res.distance_to_box_high = c - box_high
        res.attempt_bar_boundary = len(df_5m_closed)  # Records the boundary count at start of attempt
        res.momentum_score = min(25, int((range_ratio + vol_ratio) * 8))


def _calc_slot_volume_baseline(bar_ts: pd.Timestamp, df: pd.DataFrame, config: Dict[str, Any]) -> float:
    """Calculates the average volume for the same 5m slot over the last N sessions."""
    slot_time = bar_ts.time()
    same_slots = df[df.index.time == slot_time]
    lookback = config.get("SLOT_BASELINE_SESSIONS", 10)
    
    if len(same_slots) > 0:
        avg = same_slots["Volume"].tail(lookback).mean()
        # Opening candle adjustment
        if slot_time.strftime("%H:%M") == config.get("FIRST_CANDLE_SLOT", "09:15"):
            avg *= config.get("FIRST_CANDLE_VOLUME_MULT", 0.80)
        return float(avg)
    return float(df["Volume"].tail(50).mean())


def _project_live_volume(
    live_vol: float,
    live_start_ts: pd.Timestamp,
    ist_now: datetime,
    df: pd.DataFrame,
    config: Dict[str, Any]
) -> tuple[float, float]:
    """Projects forming candle volume to full 5m equivalent."""
    slot_avg = _calc_slot_volume_baseline(live_start_ts, df, config)
    if slot_avg <= 0: return 0.0, 1.0
    
    try:
        now_ts = pd.Timestamp(ist_now)
        if now_ts.tzinfo is None:
            now_ts = now_ts.tz_localize("Asia/Kolkata")
        else:
            now_ts = now_ts.tz_convert("Asia/Kolkata")
            
        elapsed_sec = (now_ts - live_start_ts).total_seconds()
        elapsed_frac = elapsed_sec / 300.0
        
        # Floor the fraction to prevent wild projections in the first seconds
        min_frac = config.get("MIN_VOLUME_PROJECTION_FRAC", 0.25)
        elapsed_frac = max(min_frac, min(1.0, elapsed_frac))
        
        proj_vol = live_vol / elapsed_frac
        ratio = proj_vol / slot_avg
        return proj_vol, ratio
        
    except Exception as exc:
        logger.debug("[project_vol] exception: %s", exc)
        return live_vol, (live_vol / slot_avg)
