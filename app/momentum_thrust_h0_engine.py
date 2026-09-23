"""
app/momentum_thrust_h0_engine.py
IMMUTABLE PRODUCTION CANDIDATE: MOMENTUM_THRUST_REVERSAL_H0

Specification Version: v1.2.0-CANONICAL-GOVERNANCE
Rules:
  1. Entry (Day t):
     - Close >= SMA(20) (Stock-level uptrend)
     - Close[t-1] < Close[t-2] (1-2 day pullback)
     - Close[t] > Open[t] and Price Change >= +2.0% (Green thrust candle)
     - Volume[t] >= 2.0 * Volume_SMA(20) (Institutional volume surge)
     - Execution Entry: Next session Open (Day t+1 Open)
  2. Stop Loss (SL):
     - Low of Pullback Bar (Low[t-1], 0.0% buffer)
     - Clamped between -1.0% and -7.0% for risk containment
  3. Targets:
     - Target 1 (T1): Entry * 1.025 (+2.50%, active Session D1)
     - Target 2 (T2): Entry * 1.040 (+4.00%, active Session D2)
  4. Time Exit:
     - Session D2 Market Close (48-hour timeout exit)
  5. Execution Mechanics:
     - Conservative tie-breaking: SL hit first if both SL and Target are within daily range
     - Gap-through-stop: If session Open < Stop Price, filled at Open (no favorable stop assumptions)
"""

from __future__ import annotations
import math
from dataclasses import dataclass, asdict
from typing import Optional, Dict, Any, List
import numpy as np
import pandas as pd


@dataclass
class H0Signal:
    signal_id: str
    symbol: str
    signal_date: str          # Date of thrust candle (t)
    entry_date: str           # Date of execution (t+1)
    entry_price: float        # Open price at t+1
    pullback_low: float       # Low of t-1
    stop_price: float         # Execution SL
    risk_pct: float           # (entry_price - stop_price) / entry_price * 100
    risk_per_share: float     # entry_price - stop_price
    t1_price: float           # entry_price * 1.025
    t2_price: float           # entry_price * 1.040
    volume_surge_ratio: float # vol / vol_sma20
    price_thrust_pct: float   # close change % on day t


@dataclass
class H0TradeOutcome:
    signal_id: str
    symbol: str
    entry_date: str
    entry_price: float
    stop_price: float
    t1_price: float
    t2_price: float
    risk_per_share: float
    risk_pct: float

    # Future Bar Observations
    d1_open: float
    d1_high: float
    d1_low: float
    d1_close: float
    d2_open: float
    d2_high: float
    d2_low: float
    d2_close: float

    # Outcome
    exit_date: str
    exit_price: float
    exit_reason: str          # "T1_HIT", "T2_HIT", "SL_HIT", "GAP_SL_HIT", "TIMEOUT"
    exit_day: str             # "D1" or "D2"
    pnl_per_share: float
    pnl_pct: float
    r_multiple: float
    is_win: bool
    ambiguous: bool           # True if both SL and Target were touched on the same bar
    gap_through_stop: bool    # True if open < stop_price

    # Telemetry
    mae_pct: float            # Maximum Adverse Excursion %
    mfe_pct: float            # Maximum Favorable Excursion %
    timeout_flag: bool
    premature_exit_flag: bool # True if stopped out but subsequent MFE touched T1


class MomentumThrustH0Engine:
    """
    DECOMMISSIONED RESEARCH ARTIFACT: MOMENTUM_THRUST_REVERSAL_H0
    Status: PERMANENTLY REJECTED FOR PRODUCTION
    Decommission Date: September 23, 2026
    Reason: Clean PIT recertification proved entry has negative forward drift (-0.15% to -0.30%).
            All 10 exit architectures produce negative expectancy (-0.017R to -0.048R).
    Reopen Condition: New independently defined entry hypothesis only.
    """

    DECOMMISSIONED: bool = True
    TARGET_1_PCT: float = 2.50
    TARGET_2_PCT: float = 4.00
    SL_MIN_PCT: float = 1.00
    SL_MAX_PCT: float = 7.00

    @classmethod
    def scan_symbol(cls, symbol: str, df: pd.DataFrame, allow_research: bool = True) -> List[H0Signal]:
        """
        Scans a historical dataframe for research evaluation.
        Production execution is permanently blocked.
        """
        if not allow_research:
            raise RuntimeError(
                f"MOMENTUM_THRUST_REVERSAL is DECOMMISSIONED. "
                f"Cannot generate production signals for {symbol}."
            )
        if df.empty or len(df) < 55:
            return []

        signals: List[H0Signal] = []
        if 'Date_str' in df.columns:
            dates = df['Date_str'].astype(str).values
        elif 'Date' in df.columns:
            dates = pd.to_datetime(df['Date']).dt.strftime('%Y-%m-%d').values
        elif 'Datetime' in df.columns:
            dates = pd.to_datetime(df['Datetime']).dt.strftime('%Y-%m-%d').values
        else:
            dates = pd.to_datetime(df.index).strftime('%Y-%m-%d').values

        highs = df['High'].astype(float).values
        lows = df['Low'].astype(float).values
        closes = df['Close'].astype(float).values
        volumes = df['Volume'].astype(float).values
        opens = df['Open'].astype(float).values if 'Open' in df.columns else closes

        for t in range(50, len(df) - 1):
            cur_c = closes[t]
            cur_o = opens[t]
            cur_v = volumes[t]
            if cur_c <= 0 or cur_v <= 0:
                continue

            vol_sma20 = float(np.mean(volumes[t-20:t]))
            if vol_sma20 <= 0:
                continue

            sma20 = float(np.mean(closes[t-20:t]))
            pct_chg = (cur_c - closes[t-1]) / closes[t-1] * 100.0 if closes[t-1] > 0 else 0.0

            # 1. Trend Filter: Close >= SMA20
            uptrend = (cur_c >= sma20)
            # 2. Pullback Condition: Yesterday Close < Day Before Yesterday Close
            pullback = (closes[t-1] < closes[t-2])
            # 3. Thrust Bar Condition: Green candle, >= +2.0% change, Volume >= 2.0x SMA20
            thrust = (cur_c > cur_o and pct_chg >= 2.0 and cur_v >= 2.0 * vol_sma20)

            if uptrend and pullback and thrust:
                entry_date = dates[t+1]
                entry_price = float(opens[t+1]) if opens[t+1] > 0 else cur_c
                if entry_price <= 0:
                    continue

                pullback_low = float(lows[t-1])
                # Calculate stop price (0.0% buffer) clamped between 1.0% and 7.0%
                raw_sl_pct = (entry_price - pullback_low) / entry_price * 100.0
                clamped_sl_pct = max(cls.SL_MIN_PCT, min(cls.SL_MAX_PCT, raw_sl_pct))
                stop_price = round(entry_price * (1.0 - clamped_sl_pct / 100.0), 2)
                risk_per_share = entry_price - stop_price

                t1_price = round(entry_price * (1.0 + cls.TARGET_1_PCT / 100.0), 2)
                t2_price = round(entry_price * (1.0 + cls.TARGET_2_PCT / 100.0), 2)

                sig_id = f"H0_{symbol}_{entry_date}"
                signals.append(H0Signal(
                    signal_id=sig_id,
                    symbol=symbol,
                    signal_date=dates[t],
                    entry_date=entry_date,
                    entry_price=round(entry_price, 2),
                    pullback_low=round(pullback_low, 2),
                    stop_price=stop_price,
                    risk_pct=round(clamped_sl_pct, 2),
                    risk_per_share=round(risk_per_share, 2),
                    t1_price=t1_price,
                    t2_price=t2_price,
                    volume_surge_ratio=round(cur_v / vol_sma20, 2),
                    price_thrust_pct=round(pct_chg, 2)
                ))

        return signals

    @classmethod
    def evaluate_forward_outcome(cls, signal: H0Signal,
                                 d1_bar: Dict[str, float],
                                 d2_bar: Dict[str, float],
                                 tie_breaker: str = "CONSERVATIVE_SL_FIRST") -> H0TradeOutcome:
        """
        Evaluates a signal's forward 2-day trade lifecycle with conservative tie-breaking
        and strict gap-through-stop fill pricing.
        """
        entry = signal.entry_price
        stop = signal.stop_price
        t1 = signal.t1_price
        t2 = signal.t2_price
        risk = signal.risk_per_share

        d1_o, d1_h, d1_l, d1_c = d1_bar["open"], d1_bar["high"], d1_bar["low"], d1_bar["close"]
        d2_o, d2_h, d2_l, d2_c = d2_bar["open"], d2_bar["high"], d2_bar["low"], d2_bar["close"]

        mae = min(0.0, (d1_l - entry) / entry * 100.0, (d2_l - entry) / entry * 100.0)
        mfe = max(0.0, (d1_h - entry) / entry * 100.0, (d2_h - entry) / entry * 100.0)

        exit_reason = ""
        exit_price = 0.0
        exit_day = ""
        ambiguous = False
        gap_through_stop = False

        # --- SESSION D1 ---
        # Gap-down check at Day 1 Open
        if d1_o <= stop:
            exit_reason = "GAP_SL_HIT"
            exit_price = d1_o
            exit_day = "D1"
            gap_through_stop = True
        else:
            d1_hit_sl = (d1_l <= stop)
            d1_hit_t1 = (d1_h >= t1)

            if d1_hit_sl and d1_hit_t1:
                ambiguous = True
                if tie_breaker == "CONSERVATIVE_SL_FIRST":
                    exit_reason = "SL_HIT"
                    exit_price = stop
                    exit_day = "D1"
                else:
                    exit_reason = "T1_HIT"
                    exit_price = t1
                    exit_day = "D1"
            elif d1_hit_sl:
                exit_reason = "SL_HIT"
                exit_price = stop
                exit_day = "D1"
            elif d1_hit_t1:
                exit_reason = "T1_HIT"
                exit_price = t1
                exit_day = "D1"

        # --- SESSION D2 (if not exited in D1) ---
        if not exit_reason:
            # Gap-down check at Day 2 Open
            if d2_o <= stop:
                exit_reason = "GAP_SL_HIT"
                exit_price = d2_o
                exit_day = "D2"
                gap_through_stop = True
            else:
                d2_hit_sl = (d2_l <= stop)
                d2_hit_t2 = (d2_h >= t2)

                if d2_hit_sl and d2_hit_t2:
                    ambiguous = True
                    if tie_breaker == "CONSERVATIVE_SL_FIRST":
                        exit_reason = "SL_HIT"
                        exit_price = stop
                        exit_day = "D2"
                    else:
                        exit_reason = "T2_HIT"
                        exit_price = t2
                        exit_day = "D2"
                elif d2_hit_sl:
                    exit_reason = "SL_HIT"
                    exit_price = stop
                    exit_day = "D2"
                elif d2_hit_t2:
                    exit_reason = "T2_HIT"
                    exit_price = t2
                    exit_day = "D2"
                else:
                    # Timeout at D2 Close
                    exit_reason = "TIMEOUT"
                    exit_price = d2_c
                    exit_day = "D2"

        pnl_per_share = exit_price - entry
        pnl_pct = (pnl_per_share / entry) * 100.0
        r_multiple = pnl_per_share / risk if risk > 0 else 0.0
        is_win = (pnl_per_share > 0)
        timeout_flag = (exit_reason == "TIMEOUT")
        premature_exit = ("SL" in exit_reason and mfe >= cls.TARGET_1_PCT)

        return H0TradeOutcome(
            signal_id=signal.signal_id,
            symbol=signal.symbol,
            entry_date=signal.entry_date,
            entry_price=entry,
            stop_price=stop,
            t1_price=t1,
            t2_price=t2,
            risk_per_share=risk,
            risk_pct=signal.risk_pct,
            d1_open=d1_o, d1_high=d1_h, d1_low=d1_l, d1_close=d1_c,
            d2_open=d2_o, d2_high=d2_h, d2_low=d2_l, d2_close=d2_c,
            exit_date=d1_bar.get("date", signal.entry_date) if exit_day == "D1" else d2_bar.get("date", signal.entry_date),
            exit_price=round(exit_price, 2),
            exit_reason=exit_reason,
            exit_day=exit_day,
            pnl_per_share=round(pnl_per_share, 2),
            pnl_pct=round(pnl_pct, 2),
            r_multiple=round(r_multiple, 4),
            is_win=is_win,
            ambiguous=ambiguous,
            gap_through_stop=gap_through_stop,
            mae_pct=round(mae, 2),
            mfe_pct=round(mfe, 2),
            timeout_flag=timeout_flag,
            premature_exit_flag=premature_exit
        )
