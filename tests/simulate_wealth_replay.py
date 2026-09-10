#!/usr/bin/env python3
# =============================================================================
# tests/simulate_wealth_replay.py
# WEALTH COMPOUNDER ENGINE: VECTORIZED FAST HISTORICAL REPLAY
# =============================================================================

from __future__ import annotations

import argparse
import glob
import os
import sys
from typing import Any, Dict, List, Optional
import numpy as np
import pandas as pd

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
_APP_DIR = os.path.join(_REPO_ROOT, "app")
for _d in (_REPO_ROOT, _APP_DIR):
    if _d not in sys.path:
        sys.path.insert(0, _d)

_HISTORY_1D_DIR = os.path.join(_REPO_ROOT, "data", "history", "1d")
_REPORTS_DIR = os.path.join(_REPO_ROOT, "reports")
_OUTPUT_CSV = os.path.join(_REPORTS_DIR, "wealth_outcomes.csv")

IS_START = "2025-07-24"
IS_END = "2026-03-31"
OOS_START = "2026-04-01"
OOS_END = "2026-09-04"

WEALTH_VARIANTS = {
    "WEALTH_CHAMPION_V1": {
        "min_rs": 65, "require_200dma": True, "trailing_exit": "200DMA", "atr_buffer": 2.5, "target_r": 3.0
    },
    "WEALTH_CHALL_A_QUALITY_MOMENTUM": {
        "min_rs": 75, "require_200dma": True, "trailing_exit": "50DMA", "atr_buffer": 2.0, "target_r": 3.5
    },
    "WEALTH_CHALL_B_STAGE2_CONVEXITY": {
        "min_rs": 80, "require_200dma": True, "trailing_exit": "20EMA", "atr_buffer": 1.8, "target_r": 4.0
    },
}


def run_wealth_replay(step: int = 5):
    print("=" * 90)
    print("WEALTH COMPOUNDER ENGINE: VECTORIZED HISTORICAL REPLAY")
    print("=" * 90)

    files = glob.glob(os.path.join(_HISTORY_1D_DIR, "*.parquet"))
    print(f"Loaded {len(files)} 1D parquet files.")

    all_outcomes: List[Dict[str, Any]] = []

    for f_path in files:
        sym = os.path.splitext(os.path.basename(f_path))[0].upper()
        try:
            df = pd.read_parquet(f_path)
            if "Date" in df.columns:
                df["dt"] = pd.to_datetime(df["Date"])
            elif isinstance(df.index, pd.DatetimeIndex):
                df["dt"] = pd.to_datetime(df.index)
            else:
                continue
            df = df.sort_values("dt").reset_index(drop=True)
        except Exception:
            continue

        if len(df) < 100:
            continue

        # Pre-compute rolling features vectorized once per stock
        df["sma50"] = df["Close"].rolling(50, min_periods=20).mean()
        df["sma200"] = df["Close"].rolling(200, min_periods=50).mean()
        df["high52"] = df["High"].rolling(252, min_periods=50).max()
        df["atr14"] = (df["High"] - df["Low"]).rolling(14, min_periods=5).mean()

        for i in range(60, len(df) - 20, step):
            scan_dt = df["dt"].iloc[i]
            d_str = str(scan_dt.date())
            if d_str < IS_START or d_str > OOS_END:
                continue

            close = float(df["Close"].iloc[i])
            sma50 = float(df["sma50"].iloc[i])
            sma200 = float(df["sma200"].iloc[i])
            high52 = float(df["high52"].iloc[i])
            atr14 = float(df["atr14"].iloc[i]) if not pd.isna(df["atr14"].iloc[i]) else close * 0.02

            if high52 <= 0:
                continue
            pct_from_high = (high52 - close) / high52 * 100.0
            if pct_from_high > 25.0:
                continue

            # Regime
            regime = "NEUTRAL"
            if sma200 > 0:
                pct_sma200 = (close - sma200) / sma200 * 100.0
                if pct_sma200 > 2.0: regime = "BULL"
                elif pct_sma200 < -5.0: regime = "BEAR"

            # Relative strength proxy
            rs_score = 70 + (5.0 if close > sma50 else -5.0) + (10.0 if close > sma200 else -10.0) + (5.0 if regime == "BULL" else 0.0)

            # Evaluate Wealth Variants
            for vid, cfg in WEALTH_VARIANTS.items():
                if rs_score < cfg["min_rs"]:
                    continue
                if cfg["require_200dma"] and close <= sma200:
                    continue

                sl = close - cfg["atr_buffer"] * atr14
                risk = close - sl
                if risk <= 0:
                    continue

                t1 = close + cfg["target_r"] * risk
                fwd = df.iloc[i+1:i+41]
                if len(fwd) < 5:
                    continue

                highs = fwd["High"].values
                lows = fwd["Low"].values
                closes = fwd["Close"].values

                exit_reason = "HORIZON_REACHED"
                realized_r = (closes[-1] - close) / risk
                holding_bars = len(fwd)

                for b_idx in range(len(fwd)):
                    if lows[b_idx] <= sl:
                        exit_reason = "SL_HIT"
                        realized_r = -1.0
                        holding_bars = b_idx + 1
                        break
                    if highs[b_idx] >= t1:
                        exit_reason = "T1_HIT"
                        realized_r = cfg["target_r"]
                        holding_bars = b_idx + 1
                        break

                all_outcomes.append({
                    "symbol": sym,
                    "variant_id": vid,
                    "scan_date": d_str,
                    "partition": "OOS" if d_str >= OOS_START else "IS",
                    "regime": regime,
                    "entry_price": round(close, 2),
                    "stop_loss": round(sl, 2),
                    "target_1": round(t1, 2),
                    "realized_rr": round(realized_r, 3),
                    "exit_reason": exit_reason,
                    "holding_period_bars": holding_bars,
                })

    print(f"Wealth Replay Complete. Total Outcomes: {len(all_outcomes)}")
    if not all_outcomes:
        return

    df_res = pd.DataFrame(all_outcomes)
    df_res.to_csv(_OUTPUT_CSV, index=False)
    print(f"Saved outcomes to {_OUTPUT_CSV}")

    print("\n" + "=" * 90)
    print("WEALTH ENGINE PERFORMANCE SUMMARY")
    print("=" * 90)
    hdr = f"  {'Variant ID':<35} {'Part':>5} {'N':>5} {'E[R]':>7} {'PF':>6} {'Win%':>6} {'+2R%':>6} {'SL%':>6}"
    print(hdr)
    print("  " + "-" * 85)

    for vid in WEALTH_VARIANTS:
        sub = df_res[df_res["variant_id"] == vid]
        for part in ["IS", "OOS"]:
            p_sub = sub[sub["partition"] == part]
            n = len(p_sub)
            if n == 0:
                continue
            mean_r = p_sub["realized_rr"].mean()
            wins = p_sub[p_sub["realized_rr"] > 0]["realized_rr"].sum()
            losses = abs(p_sub[p_sub["realized_rr"] < 0]["realized_rr"].sum())
            pf = wins / losses if losses > 0 else (99.0 if wins > 0 else 0.0)
            wr = (p_sub["realized_rr"] > 0).mean() * 100.0
            r2 = (p_sub["realized_rr"] >= 2.0).mean() * 100.0
            sl_pct = (p_sub["exit_reason"] == "SL_HIT").mean() * 100.0
            print(f"  {vid:<35} {part:>5} {n:>5} {mean_r:>+7.3f} {pf:>6.2f} {wr:>5.1f}% {r2:>5.1f}% {sl_pct:>5.1f}%")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--step", type=int, default=5)
    args = parser.parse_args()
    run_wealth_replay(step=args.step)
