#!/usr/bin/env python3
"""
tests/simulate_chronological_replay.py
Chronological Intraday Event Replay for Multi-TF V2 Engine.

Simulates a complete trading session (e.g. 2026-08-18 09:30 to 15:15 IST) in 5-minute steps.
Maintains persistent watchlist and state transitions per symbol:
  CANDIDATE -> WATCHING -> PRESSURE_BUILDING -> ARMED_PRE_BREAKOUT -> EARLY_BREAKOUT

Answers User Questions:
  1. Did any of the 15 near-resistance stocks ever reach Ignition >= 75 before their breakout?
  2. For breakout candidates, what killed them (telemetry / confluence / RR)?
  3. Are ARMED states detected as building pressure before breakout?
  4. Does persistent watchlist state promote candidates seamlessly to EARLY_BREAKOUT?
"""

import os
import sys
import glob
from typing import Optional, Dict, Any, List
from collections import defaultdict
from datetime import datetime, time as dtime
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


def _ensure_dt_index(df: pd.DataFrame) -> Optional[pd.DataFrame]:
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


class SymbolWatchlistState:
    def __init__(self, symbol: str):
        self.symbol = symbol
        self.state = "NONE"           # NONE, WATCHING, PRESSURE_BUILDING, ARMED_PRE_BREAKOUT, EARLY_BREAKOUT, INVALIDATED
        self.active_box_high = 0.0
        self.active_box_low = 0.0
        self.base_score = 0
        self.last_ignition_score = 0
        self.armed_at = None
        self.alert_at = None
        self.alert_details = None


def run_chronological_replay(target_date: str = "2026-08-18", market_status: str = "BEAR"):
    base_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "data", "history"))
    config = MULTI_TF_V2_CONFIG.copy()

    # Discover symbols
    s15m = set(os.path.basename(f).replace(".parquet", "") for f in glob.glob(os.path.join(base_dir, "15m", "*.parquet")))
    s5m = set(os.path.basename(f).replace(".parquet", "") for f in glob.glob(os.path.join(base_dir, "5m", "*.parquet")))
    s1h = set(os.path.basename(f).replace(".parquet", "") for f in glob.glob(os.path.join(base_dir, "1h", "*.parquet")))
    s1d = set(os.path.basename(f).replace(".parquet", "") for f in glob.glob(os.path.join(base_dir, "1d", "*.parquet")))
    symbols = sorted(list(s15m & s5m & s1h & s1d))

    print("=" * 90, flush=True)
    print(f"⏱️ MULTI-TF CHRONOLOGICAL SESSION REPLAY: {target_date} (09:30 - 15:15 IST)", flush=True)
    print(f"Regime: {market_status} | Universe: {len(symbols)} stocks", flush=True)
    print("=" * 90, flush=True)

    # Pre-load and standardize data per symbol
    raw_data = {}
    for sym in symbols:
        try:
            d15 = _ensure_dt_index(pd.read_parquet(os.path.join(base_dir, "15m", f"{sym}.parquet")))
            d5 = _ensure_dt_index(pd.read_parquet(os.path.join(base_dir, "5m", f"{sym}.parquet")))
            d1h = _ensure_dt_index(pd.read_parquet(os.path.join(base_dir, "1h", f"{sym}.parquet")))
            d1d = _ensure_dt_index(pd.read_parquet(os.path.join(base_dir, "1d", f"{sym}.parquet")))
            if d15 is None or d5 is None or d1h is None or d1d is None:
                continue
            raw_data[sym] = {"15m": d15, "5m": d5, "1h": d1h, "1d": d1d}
        except Exception:
            continue

    print(f"Loaded and standardized {len(raw_data)} symbols with valid DatetimeIndex.", flush=True)

    # Generate 5-minute chronological evaluation timestamps from 09:30 to 15:15 IST
    day_dt = datetime.strptime(target_date, "%Y-%m-%d")
    timestamps = pd.date_range(
        start=f"{target_date} 09:30:00",
        end=f"{target_date} 15:15:00",
        freq="5min",
        tz=IST
    )

    # Watchlist state per symbol
    tracker = {sym: SymbolWatchlistState(sym) for sym in raw_data}
    event_log = []
    daily_funnel = defaultdict(int)

    print(f"\n📋 LIVE CHRONOLOGICAL EVENT STREAM:", flush=True)
    print("─" * 90, flush=True)
    print(f"{'STOCK':<12} {'TIME':<7} {'STATE':<22} {'IGNITION':<10} {'RESULT / NOTES'}", flush=True)
    print("─" * 90, flush=True)

    for ts in timestamps:
        ts_str = ts.strftime("%H:%M")
        for sym, data in raw_data.items():
            sym_state = tracker[sym]
            if sym_state.state == "EARLY_BREAKOUT":
                continue  # Already alerted for this box

            d15_cut = data["15m"][data["15m"].index <= ts]
            d5_cut = data["5m"][data["5m"].index <= ts]
            d1h_cut = data["1h"][data["1h"].index <= ts]

            if len(d15_cut) < 10 or len(d5_cut) < 10:
                continue

            atr_15m = _get_atr(d15_cut)
            atr_5m = _get_atr(d5_cut)
            daily_atr = _get_atr(data["1d"])
            if atr_15m <= 0 or atr_5m <= 0:
                continue

            c_5m = float(d5_cut["Close"].iloc[-1])

            # 15M base detection
            cons = detect_15m_consolidation(d15_cut, atr_15m, ts, config, symbol=sym)
            if not cons.is_valid:
                continue

            daily_funnel["BASE_VALID"] += 1
            sym_state.active_box_high = cons.box_high
            sym_state.active_box_low = cons.box_low
            sym_state.base_score = cons.setup_score

            # Context
            ctx_1h = evaluate_1h_context(d1h_cut, config)
            market_ctx = {"status": market_status, "regime": market_status}

            # 5M pressure
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
            dist_atr = dist_to_high / atr_15m if atr_15m > 0 else 999.0

            ign_res = compute_ignition_score(
                consolidation=cons,
                pressure=pressure,
                distance_to_box_high_atr=dist_atr,
                ctx_1h=ctx_1h,
                config=config
            )
            ign_score = ign_res["ignition_score"]
            sym_state.last_ignition_score = ign_score

            # ── STATE TRANSITIONS ──

            # 1. Check for Confirmed Early Breakout
            if pressure.is_confirmed:
                confluence = evaluate_breakout_confluence(
                    consolidation=cons,
                    pressure=pressure,
                    ctx_1h=ctx_1h,
                    ctx_30m=evaluate_30m_context(None, cons.box_high, config),
                    market_ctx=market_ctx,
                    config=config
                )

                c_close = float(df_5m_closed["Close"].iloc[-1])
                buffer_atr = config.get("BREAKOUT_BUFFER_ATR_MULT", 0.10) * (atr_5m if atr_5m > 0 else 1.0)
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

                severity = classify_alert_severity(
                    consolidation_result=cons,
                    breakout_result=brkout_strength,
                    config=config,
                    market_status=market_status
                )

                is_eligible, reject_reason = evaluate_trade_eligibility(
                    base_score=cons.setup_score,
                    breakout_score=brkout_strength.breakout_score,
                    volume_ratio=pressure.volume_ratio,
                    confluence_score=int(confluence.total_score),
                    rr_ratio=rr_actual,
                    market_status=market_status,
                    config=config,
                    is_late_session=(ts.time() >= dtime(14, 15))
                )

                if is_eligible and not pressure.is_overextended and confluence.is_approved and rr_actual >= 1.5:
                    sym_state.state = "EARLY_BREAKOUT"
                    sym_state.alert_at = ts
                    sym_state.alert_details = {
                        "severity": severity,
                        "entry": c_close,
                        "sl": sl_target.get("stop_loss"),
                        "t0": sl_target.get("target_0"),
                        "t1": sl_target.get("target_1"),
                        "t1_source": sl_target.get("t1_source"),
                        "rr": rr_actual,
                        "rvol": pressure.volume_ratio
                    }
                    daily_funnel["ALERT_TRIGGERED"] += 1
                    msg = f"ALERT ({severity}) Entry ₹{c_close:.2f} T1 ₹{sl_target.get('target_1'):.1f} RR {rr_actual:.2f}"
                    event_log.append((sym, ts_str, "EARLY_BREAKOUT", ign_score, msg))
                    print(f"{sym:<12} {ts_str:<7} {'EARLY_BREAKOUT':<22} {ign_score:<10} {msg}", flush=True)
                    continue
                else:
                    fail_cause = reject_reason or ("LOW_CONF" if not confluence.is_approved else ("LOW_RR" if rr_actual < 1.5 else "OVEREXT"))
                    msg = f"Rejected ({fail_cause})"
                    event_log.append((sym, ts_str, "BREAKOUT_ATTEMPT", ign_score, msg))
                    print(f"{sym:<12} {ts_str:<7} {'BREAKOUT_ATTEMPT':<22} {ign_score:<10} {msg}", flush=True)

            # 2. Pre-Breakout Path
            if dist_atr <= config.get("PRE_BREAKOUT_MAX_DISTANCE_ATR", 0.40) and cons.setup_score >= config.get("PRE_BREAKOUT_MIN_BASE_SCORE", 75):
                if ign_res.get("is_ignition_ready"):
                    planned_entry = cons.box_high + (0.05 * atr_5m)
                    proj_sl = compute_sl_and_target(
                        entry_price=planned_entry,
                        atr=atr_5m,
                        ticker=d1h_cut,
                        mode="MULTI_TF_V2",
                        box_low=cons.box_low
                    )
                    proj_rr = float(proj_sl.get("rr_ratio", 0.0))
                    if proj_rr >= 1.5 and not proj_sl.get("is_rejected"):
                        if sym_state.state != "ARMED_PRE_BREAKOUT":
                            sym_state.state = "ARMED_PRE_BREAKOUT"
                            sym_state.armed_at = ts
                            daily_funnel["ARMED_EVENT"] += 1
                            msg = f"ARMED (Dist {dist_atr:.2f}ATR, Proj RR {proj_rr:.2f})"
                            event_log.append((sym, ts_str, "ARMED_PRE_BREAKOUT", ign_score, msg))
                            print(f"{sym:<12} {ts_str:<7} {'ARMED_PRE_BREAKOUT':<22} {ign_score:<10} {msg}", flush=True)
                    else:
                        msg = f"Proj RR Fail ({proj_rr:.2f} < 1.5)"
                        event_log.append((sym, ts_str, "IGNITION_READY", ign_score, msg))
                        print(f"{sym:<12} {ts_str:<7} {'IGNITION_READY':<22} {ign_score:<10} {msg}", flush=True)
                elif pressure.live_position >= 0.60 or pressure.is_attempt:
                    if sym_state.state not in ("ARMED_PRE_BREAKOUT", "PRESSURE_BUILDING"):
                        sym_state.state = "PRESSURE_BUILDING"
                        msg = f"Coiling near high (Pos {pressure.live_position:.2f}, Dist {dist_atr:.2f}ATR)"
                        event_log.append((sym, ts_str, "PRESSURE_BUILDING", ign_score, msg))
                        print(f"{sym:<12} {ts_str:<7} {'PRESSURE_BUILDING':<22} {ign_score:<10} {msg}", flush=True)
                else:
                    if sym_state.state == "NONE":
                        sym_state.state = "WATCHING"
                        msg = f"Dist {dist_atr:.2f}ATR (Base {cons.setup_score})"
                        event_log.append((sym, ts_str, "NEAR_RESISTANCE", ign_score, msg))
                        print(f"{sym:<12} {ts_str:<7} {'NEAR_RESISTANCE':<22} {ign_score:<10} {msg}", flush=True)

    print("\n📋 CHRONOLOGICAL EVENT LOG:")
    print("─" * 90)
    print(f"{'STOCK':<12} {'TIME':<7} {'STATE':<22} {'IGNITION':<10} {'RESULT / NOTES'}")
    print("─" * 90)
    if not event_log:
        print("  No state transition events recorded during session.")
    else:
        for sym, t_str, st, ign, note in event_log:
            print(f"{sym:<12} {t_str:<7} {st:<22} {ign:<10} {note}")

    print("\n" + "=" * 90)
    print(f"📊 SESSION REPLAY SUMMARY ({target_date}):")
    print(f"  • Armed Events Reached        : {daily_funnel.get('ARMED_EVENT', 0)}")
    print(f"  • Early Breakout Alerts       : {daily_funnel.get('ALERT_TRIGGERED', 0)}")
    print("=" * 90)


if __name__ == "__main__":
    run_chronological_replay()
