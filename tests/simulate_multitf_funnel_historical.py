#!/usr/bin/env python3
"""
tests/simulate_multitf_funnel_historical.py
Multi-TF End-to-End Funnel Simulation on Real Historical Market Data

Validates the full candidate funnel from universe down to alert:
1. Fundamental / Available Universe
2. 15M Quality Bases (Base Score >= 70 / 75)
3. Near Resistance (Distance <= 0.40 ATR)
4. Ignition Readiness (Ignition Score >= 75) -> ARMED_PRE_BREAKOUT
5. 5M Early Breakout Expansion (Close > Res + Buffer, RVOL >= 1.25x)
6. Anti-Overextension (Penetration <= 1.20 ATR or Institutional Thrust)
7. R:R & Target Validation (T0 obstacle + T1 >= 1.5R)
8. Institutional Trade Eligibility (Normal / BEAR / Late Session)
9. Final Executable Alert Generation
"""

import os
import sys
import glob
from collections import defaultdict
from datetime import datetime
from zoneinfo import ZoneInfo
import pandas as pd
import numpy as np

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "app")))

from multitf.consolidation import detect_15m_consolidation
from multitf.context import evaluate_1h_context, evaluate_30m_context, evaluate_market_context
from multitf.pressure import evaluate_5m_pressure, compute_ignition_score
from multitf.confluence import evaluate_breakout_confluence
from multitf.breakout_strength import (
    compute_breakout_strength,
    classify_alert_severity,
    evaluate_trade_eligibility
)
from multitf.state import MtfSubstate
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


def run_funnel_simulation(target_time: str = "2026-08-18 13:30:00+05:30", market_status: str = "BEAR"):
    # 1. Discover common symbols across timeframes
    base_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "data", "history"))
    s1d = set(os.path.basename(f).replace(".parquet", "") for f in glob.glob(os.path.join(base_dir, "1d", "*.parquet")))
    s1h = set(os.path.basename(f).replace(".parquet", "") for f in glob.glob(os.path.join(base_dir, "1h", "*.parquet")))
    s30m = set(os.path.basename(f).replace(".parquet", "") for f in glob.glob(os.path.join(base_dir, "30m", "*.parquet")))
    s15m = set(os.path.basename(f).replace(".parquet", "") for f in glob.glob(os.path.join(base_dir, "15m", "*.parquet")))
    s5m = set(os.path.basename(f).replace(".parquet", "") for f in glob.glob(os.path.join(base_dir, "5m", "*.parquet")))

    common_symbols = sorted(list(s1d & s1h & s30m & s15m & s5m))
    if not common_symbols:
        print("❌ No common historical symbols found.")
        return

    sim_ts = pd.to_datetime(target_time)
    config = MULTI_TF_V2_CONFIG.copy()

    print("=" * 80)
    print(f"🚀 MULTI-TF END-TO-END FUNNEL SIMULATION ON HISTORICAL DATA")
    print("=" * 80)
    print(f"  • Target Evaluation Timestamp : {sim_ts} (IST)")
    print(f"  • Regime Mode                 : {market_status}")
    print(f"  • Common Historical Universe  : {len(common_symbols)} stocks")
    print("=" * 80)

    funnel = defaultdict(int)
    armed_candidates = []
    alert_candidates = []

    for sym in common_symbols:
        funnel["UNIVERSE_TOTAL"] += 1

        # Load data sliced up to simulation timestamp
        try:
            df_1d = pd.read_parquet(os.path.join(base_dir, "1d", f"{sym}.parquet"))
            df_1h = pd.read_parquet(os.path.join(base_dir, "1h", f"{sym}.parquet"))
            df_30m = pd.read_parquet(os.path.join(base_dir, "30m", f"{sym}.parquet"))
            df_15m = pd.read_parquet(os.path.join(base_dir, "15m", f"{sym}.parquet"))
            df_5m = pd.read_parquet(os.path.join(base_dir, "5m", f"{sym}.parquet"))

            # Filter up to simulation timestamp
            df_15m_cut = df_15m[df_15m.index <= sim_ts]
            df_5m_cut = df_5m[df_5m.index <= sim_ts]
            df_30m_cut = df_30m[df_30m.index <= sim_ts]
            df_1h_cut = df_1h[df_1h.index <= sim_ts]

            if len(df_15m_cut) < 10 or len(df_5m_cut) < 10:
                funnel["DATA_INSUFFICIENT"] += 1
                continue

            atr_15m = _get_atr(df_15m_cut)
            atr_5m = _get_atr(df_5m_cut)
            daily_atr = _get_atr(df_1d)

            if atr_15m <= 0 or atr_5m <= 0:
                funnel["INVALID_ATR"] += 1
                continue

            funnel["DATA_VALID"] += 1
            current_price = float(df_5m_cut["Close"].iloc[-1])

            # 2. 15m Base Detection
            cons = detect_15m_consolidation(df_15m_cut, atr_15m, sim_ts, config, symbol=sym)
            if not cons.is_valid:
                if "TESTS_TOO_LOW" in getattr(cons, "rejection_reason", ""):
                    funnel["15M_REJECT_RESISTANCE"] += 1
                else:
                    funnel["15M_REJECT_BASE"] += 1
                continue

            funnel["15M_QUALITY_BASE"] += 1

            # 3. Multi-TF Context
            ctx_1h = evaluate_1h_context(df_1h_cut, config)
            ctx_30m = evaluate_30m_context(df_30m_cut, cons.box_high, config)
            market_ctx = {"status": market_status, "regime": market_status}

            # 4. 5m Pressure
            live_5m = df_5m_cut.iloc[-1]
            df_5m_closed = df_5m_cut.iloc[:-1] if len(df_5m_cut) > 1 else df_5m_cut

            pressure = evaluate_5m_pressure(
                live_candle=live_5m,
                df_5m_closed=df_5m_closed,
                box_high=cons.box_high,
                atr_5m=atr_5m,
                ist_now=sim_ts,
                config=config,
                daily_atr=daily_atr,
                atr_15m=atr_15m
            )

            dist_to_high = cons.box_high - current_price
            dist_atr = dist_to_high / atr_15m if atr_15m > 0 else 999.0

            # 5. Pre-Breakout Ignition Path
            if dist_atr <= config.get("PRE_BREAKOUT_MAX_DISTANCE_ATR", 0.40):
                funnel["NEAR_RESISTANCE"] += 1

                ign_res = compute_ignition_score(
                    consolidation=cons,
                    pressure=pressure,
                    distance_to_box_high_atr=dist_atr,
                    ctx_1h=ctx_1h,
                    config=config
                )

                if ign_res.get("is_ignition_ready"):
                    funnel["IGNITION_READY"] += 1

                    # Projected Pre-breakout Tradeability
                    planned_entry = cons.box_high + (0.05 * atr_5m)
                    proj_sl = compute_sl_and_target(
                        entry_price=planned_entry,
                        atr=atr_5m,
                        ticker=df_1h_cut,
                        mode="MULTI_TF_V2",
                        box_low=cons.box_low
                    )
                    proj_rr = float(proj_sl.get("rr_ratio", 0.0))

                    if proj_rr >= config.get("MIN_RR_RATIO", 1.5) and not proj_sl.get("is_rejected"):
                        funnel["ARMED_PRE_BREAKOUT_ACTIVE"] += 1
                        armed_candidates.append({
                            "symbol": sym,
                            "base_score": cons.setup_score,
                            "ignition_score": ign_res["ignition_score"],
                            "dist_atr": round(dist_atr, 2),
                            "box_high": cons.box_high,
                            "planned_entry": round(planned_entry, 2),
                            "proj_t1": proj_sl.get("target_1"),
                            "t1_source": proj_sl.get("t1_source"),
                            "proj_rr": round(proj_rr, 2)
                        })
                    else:
                        funnel["PREBREAK_RR_FAIL"] += 1
                else:
                    funnel["PREBREAK_IGNITION_FAIL"] += 1
            else:
                funnel["PREBREAK_DISTANCE_FAIL"] += 1

            # 6. Breakout Path (if candle confirmed)
            if pressure.is_confirmed:
                funnel["BREAKOUT_PRESSURE_CONFIRMED"] += 1

                # Confluence
                confluence = evaluate_breakout_confluence(
                    consolidation=cons,
                    pressure=pressure,
                    ctx_1h=ctx_1h,
                    ctx_30m=ctx_30m,
                    market_ctx=market_ctx,
                    config=config
                )

                c_5m = float(df_5m_closed["Close"].iloc[-1])
                buffer_atr = config.get("BREAKOUT_BUFFER_ATR_MULT", 0.10) * (atr_5m if atr_5m > 0 else 1.0)
                res_line = cons.box_high

                if c_5m < (res_line + buffer_atr):
                    funnel["BREAKOUT_CLOSE_FAIL"] += 1
                    continue

                min_rvol = config.get("MIN_VOLUME_EXPANSION_CONFIRM", 1.25)
                if pressure.volume_ratio < min_rvol and pressure.trigger_model != "MODEL_B_RETEST":
                    funnel["BREAKOUT_RVOL_FAIL"] += 1
                    continue

                if pressure.is_overextended:
                    funnel["BREAKOUT_EXHAUSTION"] += 1
                    continue

                if not confluence.is_approved:
                    funnel["LOW_CONFLUENCE"] += 1
                    continue

                funnel["CONFLUENCE_APPROVED"] += 1

                # R:R Gate
                sl_target = compute_sl_and_target(
                    entry_price=c_5m,
                    atr=atr_5m,
                    ticker=df_1h_cut,
                    mode="MULTI_TF_V2",
                    box_low=cons.box_low
                )
                rr_actual = float(sl_target.get("rr_ratio", 0.0))
                if sl_target.get("is_rejected") or rr_actual < config.get("MIN_RR_RATIO", 1.5):
                    funnel["RR_T1_FAIL"] += 1
                    continue

                funnel["RR_T1_VALID"] += 1

                # Breakout Strength
                brkout_strength = compute_breakout_strength(
                    pressure_result=pressure,
                    consolidation_result=cons,
                    df_5m_closed=df_5m_closed,
                    nifty_5m=None,
                    ist_now=sim_ts,
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
                    is_late_session=False
                )

                if not is_eligible:
                    funnel[reject_reason] += 1
                    continue

                funnel["ALERT_TRIGGERED"] += 1
                alert_candidates.append({
                    "symbol": sym,
                    "severity": severity,
                    "base_score": cons.setup_score,
                    "breakout_score": brkout_strength.breakout_score,
                    "rvol": round(pressure.volume_ratio, 2),
                    "entry": round(c_5m, 2),
                    "stop_loss": sl_target.get("stop_loss"),
                    "t0": sl_target.get("target_0"),
                    "t1": sl_target.get("target_1"),
                    "t1_source": sl_target.get("t1_source"),
                    "rr_ratio": rr_actual
                })

        except Exception as ex:
            funnel["PROCESSING_EXCEPTION"] += 1
            continue

    # Print Funnel Results
    print("\n📊 END-TO-END FUNNEL TELEMETRY BREAKDOWN:")
    print("─" * 60)
    funnel_order = [
        ("UNIVERSE_TOTAL", "1. Fundamental / Available Universe"),
        ("DATA_VALID", "2. Data Valid & Indicators Sufficient"),
        ("15M_QUALITY_BASE", "3. 15M High-Quality Base Detected (>=70/75)"),
        ("NEAR_RESISTANCE", "4. Proximity near Resistance (<=0.40 ATR)"),
        ("IGNITION_READY", "5. Pre-Breakout Ignition Score >= 75"),
        ("ARMED_PRE_BREAKOUT_ACTIVE", "6. ARMED_PRE_BREAKOUT (Projected T1 >= 1.5R)"),
        ("BREAKOUT_PRESSURE_CONFIRMED", "7. 5M Breakout Expansion Triggered"),
        ("CONFLUENCE_APPROVED", "8. Confluence Approved"),
        ("RR_T1_VALID", "9. T0 / T1 Validated (T1 >= 1.5R)"),
        ("ALERT_TRIGGERED", "10. EARLY_BREAKOUT Alerts (Trade Eligible)")
    ]

    for key, desc in funnel_order:
        val = funnel.get(key, 0)
        print(f"  {desc:<50}: {val}")

    print("\n🔍 REJECTION BREAKDOWN:")
    print("─" * 60)
    rejection_keys = [k for k in funnel.keys() if k not in [p[0] for p in funnel_order]]
    for k in sorted(rejection_keys):
        print(f"  • {k:<45}: {funnel[k]}")

    if armed_candidates:
        print(f"\n📦 QUALIFIED ARMED_PRE_BREAKOUT CANDIDATES ({len(armed_candidates)}):")
        print("─" * 90)
        print(f"{'Symbol':<12} | {'Base':<5} | {'Ignition':<8} | {'Dist ATR':<8} | {'Planned Entry':<13} | {'Proj T1':<8} | {'T1 Src':<12} | {'Proj RR':<7}")
        print("─" * 90)
        for cand in armed_candidates:
            print(f"{cand['symbol']:<12} | {cand['base_score']:<5} | {cand['ignition_score']:<8} | {cand['dist_atr']:<8} | ₹{cand['planned_entry']:<12.2f} | ₹{cand['proj_t1']:<7.1f} | {cand['t1_source']:<12} | {cand['proj_rr']:<7.2f}")

    if alert_candidates:
        print(f"\n🌟 PROMOTED EARLY_BREAKOUT ALERTS ({len(alert_candidates)}):")
        print("─" * 90)
        for alt in alert_candidates:
            print(f"  • {alt['symbol']} [{alt['severity']}]: Entry ₹{alt['entry']:.2f}, SL ₹{alt['stop_loss']:.2f}, "
                  f"T0 ₹{alt['t0']:.1f}, T1 ₹{alt['t1']:.1f} ({alt['t1_source']}), RR {alt['rr_ratio']:.2f}, RVOL {alt['rvol']:.2f}x")

    print("=" * 80)


if __name__ == "__main__":
    run_funnel_simulation()
