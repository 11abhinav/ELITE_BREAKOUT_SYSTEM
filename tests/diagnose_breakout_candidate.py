#!/usr/bin/env python3
"""
tests/diagnose_breakout_candidate.py
Forensic breakdown of the historical breakout candidate (HEROMOTOCO) at 2026-08-18 13:30 IST.
Answers User Question #2:
  Base
  Breakout Score
  RVOL
  Confluence components
  1H context
  30M context
  RR
  T0
  T1
"""

import os
import sys
import pandas as pd
import numpy as np
from datetime import datetime
from zoneinfo import ZoneInfo

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


def diagnose_candidate(symbol: str = "HEROMOTOCO", target_time: str = "2026-08-18 13:30:00+05:30", market_status: str = "BEAR"):
    base_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "data", "history"))
    sim_ts = pd.to_datetime(target_time)
    config = MULTI_TF_V2_CONFIG.copy()

    df_1d = pd.read_parquet(os.path.join(base_dir, "1d", f"{symbol}.parquet"))
    df_1h = pd.read_parquet(os.path.join(base_dir, "1h", f"{symbol}.parquet"))
    df_30m = pd.read_parquet(os.path.join(base_dir, "30m", f"{symbol}.parquet"))
    df_15m = pd.read_parquet(os.path.join(base_dir, "15m", f"{symbol}.parquet"))
    df_5m = pd.read_parquet(os.path.join(base_dir, "5m", f"{symbol}.parquet"))

    df_15m_cut = df_15m[df_15m.index <= sim_ts]
    df_5m_cut = df_5m[df_5m.index <= sim_ts]
    df_30m_cut = df_30m[df_30m.index <= sim_ts]
    df_1h_cut = df_1h[df_1h.index <= sim_ts]

    atr_15m = _get_atr(df_15m_cut)
    atr_5m = _get_atr(df_5m_cut)
    daily_atr = _get_atr(df_1d)

    cons = detect_15m_consolidation(df_15m_cut, atr_15m, sim_ts, config, symbol=symbol)
    ctx_1h = evaluate_1h_context(df_1h_cut, config)
    ctx_30m = evaluate_30m_context(df_30m_cut, cons.box_high, config)
    market_ctx = {"status": market_status, "regime": market_status}

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

    confluence = evaluate_breakout_confluence(
        consolidation=cons,
        pressure=pressure,
        ctx_1h=ctx_1h,
        ctx_30m=ctx_30m,
        market_ctx=market_ctx,
        config=config
    )

    c_5m = float(df_5m_closed["Close"].iloc[-1])
    sl_target = compute_sl_and_target(
        entry_price=c_5m,
        atr=atr_5m,
        ticker=df_1h_cut,
        mode="MULTI_TF_V2",
        box_low=cons.box_low
    )
    rr_actual = float(sl_target.get("rr_ratio", 0.0))

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

    dist_to_high = cons.box_high - c_5m
    dist_atr = dist_to_high / atr_15m if atr_15m > 0 else 0.0

    ign_res = compute_ignition_score(
        consolidation=cons,
        pressure=pressure,
        distance_to_box_high_atr=dist_atr,
        ctx_1h=ctx_1h,
        config=config
    )

    print("=" * 80)
    print(f"🔬 FORENSIC CANDIDATE DIAGNOSTIC: {symbol} @ {sim_ts} IST")
    print("=" * 80)
    print(f"  • Price & Box Geometry : Close=₹{c_5m:.2f} | Box High=₹{cons.box_high:.2f} | Box Low=₹{cons.box_low:.2f}")
    print(f"  • 15M Base Score       : {cons.setup_score} (Rating: {cons.base_rating_label})")
    print(f"    - Maturity           : {cons.score_maturity}/20")
    print(f"    - Tightness          : {cons.score_tightness}/20")
    print(f"    - Resistance Quality : {cons.score_resistance_quality}/15")
    print(f"    - Repeated Tests     : {cons.score_repeated_tests}/15 (Tests: {cons.resistance_test_count})")
    print(f"    - Compression        : {cons.score_compression}/15 (Ratio: {cons.compression_ratio:.2f})")
    print(f"    - Higher Lows        : {cons.score_higher_lows}/10 (HL: {cons.has_higher_lows})")
    print(f"    - Support Integrity  : {cons.score_support_integrity}/5")
    print("─" * 80)
    print(f"  • Breakout Score       : {brkout_strength.breakout_score} (Rating: {brkout_strength.breakout_rating_label})")
    print(f"    - RVOL Score         : {brkout_strength.to_dict().get('score_breakdown', {}).get('rvol', 'N/A')}/35 (RVOL: {pressure.volume_ratio:.2f}x)")
    print(f"    - Acceleration       : {brkout_strength.to_dict().get('score_breakdown', {}).get('acceleration', 'N/A')}/15 (Acc: {pressure.volume_acceleration:.2f}x)")
    print(f"    - Penetration        : {brkout_strength.to_dict().get('score_breakdown', {}).get('penetration', 'N/A')}/20 (Pen: {brkout_strength.penetration_atr:.2f} ATR)")
    print(f"    - Close Position     : {brkout_strength.to_dict().get('score_breakdown', {}).get('close_position', 'N/A')}/20 (Pos: {brkout_strength.close_position:.2f})")
    print(f"    - Energy / Velocity  : {brkout_strength.breakout_energy} ({brkout_strength.breakout_energy_label}) | Velocity: {brkout_strength.velocity_label}")
    print("─" * 80)
    print(f"  • 5M Pressure State    : is_confirmed={pressure.is_confirmed} (Model: {pressure.trigger_model}), is_attempt={pressure.is_attempt}")
    print(f"  • Ignition Score       : {ign_res['ignition_score']} (Ready: {ign_res['is_ignition_ready']})")
    print(f"    - Breakdown          : {ign_res['score_breakdown']}")
    print("─" * 80)
    print(f"  • Confluence Scoring   : Total={confluence.total_score} | Approved={confluence.is_approved}")
    print(f"    - Structure          : {confluence.score_structure}/40")
    print(f"    - Momentum           : {confluence.score_momentum}/25")
    print(f"    - Volume             : {confluence.score_volume}/15")
    print(f"    - Context            : {confluence.score_context}/20")
    print(f"  • 1H Context           : {ctx_1h}")
    print(f"  • 30M Context          : {ctx_30m}")
    print(f"  • Market Regime        : {market_status}")
    print("─" * 80)
    print(f"  • R:R & Target Map     :")
    print(f"    - Entry Price        : ₹{float(sl_target.get('entry_price') or 0):.2f}")
    print(f"    - Stop Loss          : ₹{float(sl_target.get('stop_loss') or 0):.2f}")
    print(f"    - T0 (Obstacle)      : ₹{float(sl_target.get('target_0') or 0):.2f}")
    print(f"    - T1 (Trade Target)  : ₹{float(sl_target.get('target_1') or 0):.2f} [{sl_target.get('t1_source')}]")
    print(f"    - Actual R:R Ratio   : {rr_actual:.2f} (Required: >= {config.get('MIN_RR_RATIO', 1.5):.2f})")
    print("─" * 80)
    print(f"  • Trade Eligibility    : Eligible={is_eligible} | Reject Reason={reject_reason or 'NONE'}")
    print(f"  • Alert Severity       : {severity}")
    print("=" * 80)

if __name__ == "__main__":
    diagnose_candidate()
