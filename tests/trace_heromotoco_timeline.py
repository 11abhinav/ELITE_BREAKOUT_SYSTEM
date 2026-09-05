#!/usr/bin/env python3
"""
tests/trace_heromotoco_timeline.py
Per-bar chronological trace of HEROMOTOCO across the 2026-08-18 trading session.

Tracks every 5-minute decision point and outputs the exact diagnostic table:
TIME | STATE | IGNITION | BASE | DIST | TESTS | COMP | LIVE_POS | VOL_RATIO | 1H | 30M | CONF | RR | OUTCOME
"""

import os
import sys
from datetime import datetime
from zoneinfo import ZoneInfo
import pandas as pd
import numpy as np

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "app")))

from multitf.consolidation import detect_15m_consolidation
from multitf.context import evaluate_1h_context, evaluate_30m_context
from multitf.pressure import evaluate_5m_pressure, compute_ignition_score
from multitf.confluence import evaluate_breakout_confluence
from multitf.breakout_strength import (
    compute_breakout_strength,
    classify_alert_severity,
    evaluate_trade_eligibility
)
from sl_target_helper import compute_sl_and_target
from config import MULTI_TF_V2_CONFIG

IST = ZoneInfo("Asia/Kolkata")

def _get_atr(df: pd.DataFrame, period: int = 14) -> float:
    if df is None or len(df) < period + 1:
        return 0.0
    h = df["High"].astype(float).values
    l = df["Low"].astype(float).values
    c = df["Close"].astype(float).values
    tr = np.maximum(h[1:] - l[1:], np.maximum(np.abs(h[1:] - c[:-1]), np.abs(l[1:] - c[:-1])))
    return float(pd.Series(tr).rolling(period).mean().iloc[-1])


def _ensure_dt_index(df: pd.DataFrame):
    if df is None or df.empty:
        return None
    res = df.copy()
    if not isinstance(res.index, pd.DatetimeIndex):
        for col in ["Date", "Datetime", "timestamp"]:
            if col in res.columns:
                res = res.set_index(pd.to_datetime(res[col]))
                break
        else:
            try:
                res.index = pd.to_datetime(res.index)
            except Exception:
                return None
    if not isinstance(res.index, pd.DatetimeIndex):
        return None
    if res.index.tz is None:
        res.index = res.index.tz_localize(IST)
    else:
        res.index = res.index.tz_convert(IST)
    return res.sort_index()


def trace_symbol(symbol: str = "HEROMOTOCO", target_date: str = "2026-08-18", market_status: str = "BEAR"):
    base_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "data", "history"))
    config = MULTI_TF_V2_CONFIG.copy()

    d15 = _ensure_dt_index(pd.read_parquet(os.path.join(base_dir, "15m", f"{symbol}.parquet")))
    d5 = _ensure_dt_index(pd.read_parquet(os.path.join(base_dir, "5m", f"{symbol}.parquet")))
    d1h = _ensure_dt_index(pd.read_parquet(os.path.join(base_dir, "1h", f"{symbol}.parquet")))
    d1d = _ensure_dt_index(pd.read_parquet(os.path.join(base_dir, "1d", f"{symbol}.parquet")))

    timestamps = pd.date_range(
        start=f"{target_date} 09:30:00",
        end=f"{target_date} 15:15:00",
        freq="5min",
        tz=IST
    )

    print("=" * 130)
    print(f"📊 PER-BAR CHRONOLOGICAL TIMELINE: {symbol} on {target_date} (Regime: {market_status})")
    print("=" * 130)
    header = f"{'TIME':<6} {'STATE':<18} {'IGN':<4} {'BASE':<4} {'DIST_ATR':<8} {'TESTS':<5} {'COMP':<5} {'POS':<5} {'VOL':<6} {'1H':<3} {'30M':<4} {'CONF':<4} {'RR':<5} {'RESULT / NOTES'}"
    print(header)
    print("─" * 130)

    current_state = "WATCHING"

    for ts in timestamps:
        ts_str = ts.strftime("%H:%M")
        d15_cut = d15[d15.index <= ts]
        d5_cut = d5[d5.index <= ts]
        d1h_cut = d1h[d1h.index <= ts]
        d30m_cut = None

        if len(d15_cut) < 10 or len(d5_cut) < 10:
            continue

        atr_15m = _get_atr(d15_cut)
        atr_5m = _get_atr(d5_cut)
        daily_atr = _get_atr(d1d)

        cons = detect_15m_consolidation(d15_cut, atr_15m, ts, config, symbol=symbol)
        if not cons.is_valid:
            continue

        c_5m = float(d5_cut["Close"].iloc[-1])
        live_5m = d5_cut.iloc[-1]
        df_5m_closed = d5_cut.iloc[:-1] if len(d5_cut) > 1 else d5_cut

        pressure = evaluate_5m_pressure(
            live_candle=live_5m,
            df_5m_closed=df_5m_closed,
            box_high=cons.box_high,
            atr_5m=atr_5m,
            ist_now=ts,
            config=config,
            daily_atr=daily_atr,
            atr_15m=atr_15m
        )

        dist_to_high = cons.box_high - c_5m
        dist_atr = dist_to_high / atr_15m if atr_15m > 0 else 0.0

        ctx_1h = evaluate_1h_context(d1h_cut, config)
        ctx_30m = evaluate_30m_context(None, cons.box_high, config)
        market_ctx = {"status": market_status, "regime": market_status}

        ign_res = compute_ignition_score(
            consolidation=cons,
            pressure=pressure,
            distance_to_box_high_atr=dist_atr,
            ctx_1h=ctx_1h,
            config=config
        )
        ign = ign_res["ignition_score"]

        confluence = evaluate_breakout_confluence(
            consolidation=cons,
            pressure=pressure,
            ctx_1h=ctx_1h,
            ctx_30m=ctx_30m,
            market_ctx=market_ctx,
            config=config
        )

        planned_entry = cons.box_high + (0.05 * atr_5m)
        proj_sl = compute_sl_and_target(
            entry_price=planned_entry,
            atr=atr_5m,
            ticker=d1h_cut,
            mode="MULTI_TF_V2",
            box_low=cons.box_low
        )
        rr = float(proj_sl.get("rr_ratio", 0.0))

        # Determine State
        note = ""
        if pressure.is_confirmed:
            c_close = float(df_5m_closed["Close"].iloc[-1])
            sl_target = compute_sl_and_target(
                entry_price=c_close,
                atr=atr_5m,
                ticker=d1h_cut,
                mode="MULTI_TF_V2",
                box_low=cons.box_low
            )
            rr_actual = float(sl_target.get("rr_ratio", 0.0))
            brkout_strength = compute_breakout_strength(
                pressure_result=pressure,
                consolidation_result=cons,
                df_5m_closed=df_5m_closed,
                nifty_5m=None,
                ist_now=ts,
                config=config
            )
            is_eligible, reject_reason = evaluate_trade_eligibility(
                base_score=cons.setup_score,
                breakout_score=brkout_strength.breakout_score,
                volume_ratio=pressure.volume_ratio,
                confluence_score=int(confluence.total_score),
                rr_ratio=rr_actual,
                market_status=market_status,
                config=config
            )
            if is_eligible and not pressure.is_overextended and confluence.is_approved and rr_actual >= 1.5:
                current_state = "EARLY_BREAKOUT"
                note = f"ALERT (GOOD) Entry ₹{c_close:.2f} T1 ₹{sl_target.get('target_1'):.1f}"
            else:
                current_state = "BREAKOUT_ATTEMPT"
                note = f"Rejected ({reject_reason or 'CONFLUENCE_FAIL'})"
        elif dist_atr <= 0.40 and cons.setup_score >= 75:
            if ign_res.get("is_ignition_ready") and rr >= 1.5:
                current_state = "ARMED_PRE_BREAKOUT"
                note = f"ARMED (Proj T1 ₹{proj_sl.get('target_1'):.1f} [{proj_sl.get('t1_source')}])"
            elif pressure.live_position >= 0.60 or pressure.is_attempt:
                current_state = "PRESSURE_BUILDING"
                note = f"Coiling near ceiling (Pos {pressure.live_position:.2f})"
            else:
                current_state = "NEAR_RESISTANCE"
                note = f"Proximity OK ({dist_atr:.2f} ATR), awaiting pressure"
        else:
            current_state = "WATCHING"
            note = f"Inside base (Dist {dist_atr:.2f} ATR)"

        # Print only relevant bars near action (e.g. 12:30 onwards or when near resistance)
        if dist_atr <= 0.80 or current_state != "WATCHING" or ts.hour >= 12:
            row = (
                f"{ts_str:<6} {current_state:<18} {ign:<4} {cons.setup_score:<4} "
                f"{dist_atr:<8.2f} {cons.resistance_test_count:<5} {cons.compression_ratio:<5.2f} "
                f"{pressure.live_position:<5.2f} {pressure.volume_ratio:<6.2f} "
                f"{ctx_1h.get('score', 0):<3} {ctx_30m.get('score', 0):<4} "
                f"{confluence.total_score:<4} {rr:<5.2f} {note}"
            )
            print(row)

    print("=" * 130)


if __name__ == "__main__":
    trace_symbol()
