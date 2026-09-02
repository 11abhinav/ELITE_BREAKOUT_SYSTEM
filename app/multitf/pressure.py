# =====================================================================================
# app/multitf/pressure.py
# MULTI_TF V2 — 5m Pressure & Live Execution Trigger Engine (REDESIGNED)
#
# Responsibility: Monitors live and recently-closed 5m candles against the 15m base.
# Emits ATTEMPT and CONFIRMED signals based on range expansion, volume, and position.
#
# Models Supported:
#   - Model A (Direct Breakout): 5m close > resistance with RVOL >= 1.25x and strong close.
#   - Model B (Breakout Retest Defense): Retest of breakout level / EMA9 with bullish bounce.
#   - Anti-Fake-Breakout Guards: Close confirmation (no wicks), Over-extension cap (0.5x ATR).
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
    is_overextended: bool = False
    trigger_model: str = ""  # MODEL_A_DIRECT, MODEL_B_RETEST
    
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
    config: Dict[str, Any],
    daily_atr: float = 0.0
) -> PressureResult:
    """
    Evaluates both the live candle (for ATTEMPT) and the last closed candle (for CONFIRMED).
    """
    res = PressureResult()
    if df_5m_closed is None or df_5m_closed.empty or not box_high:
        return res

    # 1. Compute Baselines (from closed bars)
    ranges = df_5m_closed["High"] - df_5m_closed["Low"]
    median_range = float(ranges.median()) if len(ranges) > 0 else atr_5m
    if median_range <= 0:
        median_range = atr_5m if atr_5m > 0 else 1.0

    # 2. Check for CONFIRMED Breakout (using strictly closed candle)
    last_closed = df_5m_closed.iloc[-1]
    _evaluate_confirmed(last_closed, box_high, atr_5m, median_range, df_5m_closed, res, config, daily_atr)

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
    config: Dict[str, Any],
    daily_atr: float = 0.0
):
    """
    Evaluates confirmed breakout using Model A (Direct Breakout) or Model B (Retest Defense).
    """
    c = float(last_closed["Close"])
    o = float(last_closed["Open"])
    h = float(last_closed["High"])
    l = float(last_closed["Low"])
    v = float(last_closed["Volume"])

    candle_range = h - l
    if candle_range <= 0:
        return

    range_ratio = candle_range / median_range if median_range > 0 else 1.0
    close_pos = (c - l) / candle_range

    # Calculate time-of-day normalized slot volume baseline
    bar_ts = last_closed.name if isinstance(last_closed.name, pd.Timestamp) else pd.to_datetime(last_closed.get("Date", datetime.now()))
    slot_vol_avg = _calc_slot_volume_baseline(bar_ts, df_5m_closed, config)
    vol_ratio = v / slot_vol_avg if slot_vol_avg > 0 else 1.0

    # [ANTI-FAKE-BREAKOUT GUARD]: Over-extension Cap
    # If price already exploded > 0.50x Daily ATR past resistance, reject chasing
    ref_daily_atr = daily_atr if daily_atr > 0 else (atr_5m * 17.0 if atr_5m > 0 else c * 0.02)
    max_ext_mult = config.get("MAX_EXTENSION_DAILY_ATR", 0.50)
    if c > box_high + (max_ext_mult * ref_daily_atr):
        res.is_overextended = True
        return

    # Parameters for validation
    buffer = config.get("BREAKOUT_BUFFER_ATR_MULT", 0.10) * (atr_5m if atr_5m > 0 else 1.0)
    min_vol = config.get("MIN_VOLUME_EXPANSION_CONFIRM", 1.25)
    min_pos = config.get("MIN_CLOSE_POSITION_CONFIRMED", 0.60)

    # Differentiate Model A vs Model B based on whether price was already above box_high prior to this candle
    prev_close = float(df_5m_closed.iloc[-2]["Close"]) if len(df_5m_closed) >= 2 else 0.0

    # ── MODEL B: Breakout Retest & Defense ───────────────────────────────────
    # Price was already above/at resistance on prior candle, tests support zone and bounces
    retest_tol = config.get("PULLBACK_RETEST_TOL_ATR", 0.15) * (atr_5m if atr_5m > 0 else 1.0)
    tested_level = l <= (box_high + retest_tol)
    bullish_reversal = (c > o) and (c >= box_high) and (close_pos >= min_pos)
    retest_vol_ok = vol_ratio >= max(1.15, min_vol * 0.90)

    if prev_close >= (box_high - 0.05 * atr_5m) and tested_level and bullish_reversal and retest_vol_ok:
        res.is_confirmed = True
        res.trigger_model = "MODEL_B_RETEST"
        res.volume_ratio = vol_ratio
        res.range_ratio = range_ratio
        res.live_position = close_pos
        res.distance_to_box_high = c - box_high
        res.momentum_score = min(25, int((range_ratio + vol_ratio) * 7.0))
        return

    # ── MODEL A: Direct Breakout ──────────────────────────────────────────────
    # 5m Close strictly above resistance + buffer, strong close position (>= 0.60), and RVOL >= 1.25x
    if c >= box_high + buffer and close_pos >= min_pos and vol_ratio >= min_vol:
        res.is_confirmed = True
        res.trigger_model = "MODEL_A_DIRECT"
        res.volume_ratio = vol_ratio
        res.range_ratio = range_ratio
        res.live_position = close_pos
        res.distance_to_box_high = c - box_high
        res.momentum_score = min(25, int((range_ratio + vol_ratio) * 7.5))
        return


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
    triggering candidate arming and immediate target mapping.
    """
    c = float(live["Close"])
    h = float(live["High"])
    l = float(live["Low"])
    v = float(live["Volume"])

    # Distance requirement: must be near or above box high
    approach_limit = box_high - (config.get("APPROACH_ATR_MULT", 0.10) * (atr_5m if atr_5m > 0 else 1.0))
    if c < approach_limit:
        return

    candle_range = h - l
    if candle_range <= 0:
        return

    range_ratio = candle_range / median_range if median_range > 0 else 1.0
    live_pos = (c - l) / candle_range

    # Volume run-rate projection
    live_ts = live.name if isinstance(live.name, pd.Timestamp) else pd.to_datetime(live.get("Date", ist_now))
    proj_vol, vol_ratio = _project_live_volume(v, live_ts, ist_now, df_5m_closed, config)

    req_range = config.get("MIN_RANGE_EXPANSION", 1.15)
    req_vol = config.get("MIN_VOLUME_EXPANSION_ATTEMPT", 1.20)
    req_pos = config.get("MIN_LIVE_POSITION_ATTEMPT", 0.60)

    if range_ratio >= req_range and vol_ratio >= req_vol and live_pos >= req_pos:
        res.is_attempt = True
        res.volume_ratio = vol_ratio
        res.range_ratio = range_ratio
        res.live_position = live_pos
        res.distance_to_box_high = c - box_high
        res.attempt_bar_boundary = len(df_5m_closed)
        res.momentum_score = min(20, int((range_ratio + vol_ratio) * 6.0))


def _calc_slot_volume_baseline(bar_ts: pd.Timestamp, df: pd.DataFrame, config: Dict[str, Any]) -> float:
    """Calculates the average volume for the same 5m slot over prior sessions, or prior bars baseline."""
    slot_time = bar_ts.time() if hasattr(bar_ts, "time") else pd.to_datetime(bar_ts).time()
    bar_date = bar_ts.date() if hasattr(bar_ts, "date") else pd.to_datetime(bar_ts).date()

    # Prior sessions
    if isinstance(df.index, pd.DatetimeIndex):
        prior_df = df[df.index.date < bar_date]
        if not prior_df.empty:
            same_slots = prior_df[prior_df.index.time == slot_time]
            lookback = config.get("SLOT_BASELINE_SESSIONS", 10)
            if len(same_slots) >= 3:
                avg = same_slots["Volume"].tail(lookback).median()
                if slot_time.strftime("%H:%M") == config.get("FIRST_CANDLE_SLOT", "09:15"):
                    avg *= config.get("FIRST_CANDLE_VOLUME_MULT", 0.80)
                return float(avg) if avg > 0 else 1.0

    # Fallback to median volume of strictly prior bars in current slice
    prior_bars = df.iloc[:-1] if len(df) > 1 else df
    if not prior_bars.empty and "Volume" in prior_bars:
        mean_vol = prior_bars["Volume"].tail(20).median()
        return float(mean_vol) if mean_vol > 0 else 1.0

    return 1.0


def _project_live_volume(
    live_vol: float,
    live_start_ts: pd.Timestamp,
    ist_now: datetime,
    df: pd.DataFrame,
    config: Dict[str, Any]
) -> tuple[float, float]:
    """Projects forming candle volume to full 5m equivalent."""
    slot_avg = _calc_slot_volume_baseline(live_start_ts, df, config)
    if slot_avg <= 0:
        return 0.0, 1.0

    try:
        now_ts = pd.Timestamp(ist_now)
        if now_ts.tzinfo is None:
            now_ts = now_ts.tz_localize("Asia/Kolkata")
        else:
            now_ts = now_ts.tz_convert("Asia/Kolkata")

        elapsed_sec = (now_ts - live_start_ts).total_seconds()
        elapsed_frac = elapsed_sec / 300.0

        min_frac = config.get("MIN_VOLUME_PROJECTION_FRAC", 0.25)
        elapsed_frac = max(min_frac, min(1.0, elapsed_frac))

        proj_vol = live_vol / elapsed_frac
        ratio = proj_vol / slot_avg
        return proj_vol, ratio

    except Exception as exc:
        logger.debug("[project_vol] exception: %s", exc)
        return live_vol, (live_vol / slot_avg)
