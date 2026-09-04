#!/usr/bin/env python3
"""
tests/validate_multitf_v3_historical.py
MTF V3 Downstream Outcome & Structural Quality Validation

Simulates 15m base detections at historical T0 points across data/history/15m/*.parquet,
then tracks downstream price action over the subsequent 16 bars (~4 hours):
- Did price reach resistance?
- Did breakout confirmation occur (close > resistance)?
- Maximum Favorable Excursion (MFE %)
- Maximum Adverse Excursion (MAE %)
- Breakout Hold Rate (%)
Groups metrics by Base Quality Score buckets:
  50-64 | 65-74 | 75-84 | 85-100
"""

import os
import sys
import glob
from datetime import datetime
from zoneinfo import ZoneInfo
import pandas as pd
import numpy as np

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "app")))

from multitf.consolidation import detect_15m_consolidation
from multitf.data import strip_closed_candles
from config import MULTI_TF_V2_CONFIG

IST = ZoneInfo("Asia/Kolkata")


def _get_atr(df: pd.DataFrame, period: int = 14) -> float:
    if len(df) < period + 1:
        return 0.0
    h = df["High"].astype(float).values
    l = df["Low"].astype(float).values
    c = df["Close"].astype(float).values
    tr = np.maximum(h[1:] - l[1:], np.maximum(np.abs(h[1:] - c[:-1]), np.abs(l[1:] - c[:-1])))
    return float(pd.Series(tr).rolling(period).mean().iloc[-1])


def run_historical_validation(lookback_forward_bars: int = 16, sample_limit: int = 80):
    pattern = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "data", "history", "15m", "*.parquet"))
    files = sorted(glob.glob(pattern))
    if not files:
        print(f"❌ No 15m parquet files found at: {pattern}")
        return

    print(f"📊 Running MTF V3 Downstream Validation across {min(len(files), sample_limit)} stocks...")
    print(f"   Tracking downstream performance over {lookback_forward_bars} bars (~4 hours) post-detection.")

    results = []

    for idx, fpath in enumerate(files[:sample_limit]):
        sym = os.path.basename(fpath).replace(".parquet", "")
        try:
            df = pd.read_parquet(fpath)
            if df.empty or len(df) < 50:
                continue

            # Simulate T0 at length - lookback_forward_bars
            t0_idx = len(df) - lookback_forward_bars
            if t0_idx < 35:
                continue

            df_t0 = df.iloc[:t0_idx].copy()
            df_forward = df.iloc[t0_idx:t0_idx + lookback_forward_bars].copy()

            atr = _get_atr(df_t0)
            if atr <= 0:
                continue

            fake_now = df_t0.index[-1] if isinstance(df_t0.index[-1], datetime) else datetime.now(IST)
            base = detect_15m_consolidation(df_t0, atr, fake_now, MULTI_TF_V2_CONFIG, symbol=sym)

            if not base.is_valid:
                continue

            # Measure downstream outcome over df_forward
            box_high = base.box_high
            box_low = base.box_low
            f_highs = df_forward["High"].astype(float).values
            f_lows = df_forward["Low"].astype(float).values
            f_closes = df_forward["Close"].astype(float).values

            max_forward_high = float(np.max(f_highs))
            min_forward_low = float(np.min(f_lows))

            reached_resistance = bool(max_forward_high >= box_high)
            breakout_confirmed = bool(any(c > box_high for c in f_closes))

            # MFE & MAE relative to breakout level (box_high)
            mfe_pct = max(0.0, (max_forward_high - box_high) / box_high * 100.0)
            mae_pct = max(0.0, (box_high - min_forward_low) / box_high * 100.0)

            # Hold rate: did it break out AND not collapse below support?
            held_breakout = bool(breakout_confirmed and min_forward_low >= box_low)

            results.append({
                "symbol": sym,
                "base_quality": base.base_quality_score,
                "proximity": base.proximity_score,
                "opportunity": base.setup_score,
                "stage": base.lifecycle_stage,
                "bars": base.bars_count,
                "reached_resistance": reached_resistance,
                "breakout_confirmed": breakout_confirmed,
                "held_breakout": held_breakout,
                "mfe_pct": mfe_pct,
                "mae_pct": mae_pct
            })

        except Exception as ex:
            continue

    if not results:
        print("⚠️ No valid bases detected in sample.")
        return

    df_res = pd.DataFrame(results)
    print(f"\n✅ Evaluated {len(df_res)} valid detected setups across {sample_limit} stocks.\n")

    # Define score buckets
    bins = [0, 49, 64, 74, 84, 100]
    labels = ["<50 (Reject)", "50-64 (Watch)", "65-74 (Good)", "75-84 (Strong)", "85-100 (Exceptional)"]
    df_res["bucket"] = pd.cut(df_res["base_quality"], bins=bins, labels=labels)

    # Print Table
    print(f"{'Score Bucket':<22} | {'Setups':<7} | {'Reach Res %':<12} | {'Breakout %':<11} | {'Avg MFE %':<10} | {'Hold Rate %':<11}")
    print("─" * 85)

    for label in labels[1:]:  # skip reject
        sub = df_res[df_res["bucket"] == label]
        n_setups = len(sub)
        if n_setups == 0:
            print(f"{label:<22} | {0:<7} | {'N/A':<12} | {'N/A':<11} | {'N/A':<10} | {'N/A':<11}")
            continue

        reach_pct = (sub["reached_resistance"].sum() / n_setups) * 100.0
        bo_pct = (sub["breakout_confirmed"].sum() / n_setups) * 100.0
        avg_mfe = sub["mfe_pct"].mean()
        hold_pct = (sub["held_breakout"].sum() / n_setups) * 100.0

        print(f"{label:<22} | {n_setups:<7} | {reach_pct:>10.1f}% | {bo_pct:>9.1f}% | {avg_mfe:>8.2f}% | {hold_pct:>9.1f}%")

    print("─" * 85)

    # Lifecycle Stage Distribution
    print("\n📍 Lifecycle Stage Distribution at T0:")
    stage_counts = df_res["stage"].value_counts().to_dict()
    for st, cnt in stage_counts.items():
        print(f"   • {st:<15}: {cnt} setups ({cnt / len(df_res) * 100:.1f}%)")


if __name__ == "__main__":
    run_historical_validation()
