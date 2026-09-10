#!/usr/bin/env python3
# =============================================================================
# tests/simulate_short_covering_replay.py
# SHORT COVERING EOD: VECTORIZED FAST HISTORICAL REPLAY
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
_OUTPUT_CSV = os.path.join(_REPORTS_DIR, "short_covering_outcomes.csv")

IS_START = "2025-07-24"
IS_END = "2026-03-31"
OOS_START = "2026-04-01"
OOS_END = "2026-09-04"

SHORT_COVERING_VARIANTS = {
    "SHORT_COVERING_CHAMPION_V1": {
        "min_vol_surge": 2.0, "rsi_oversold": 35.0, "atr_buffer": 1.8, "target_r": 2.0
    },
    "SHORT_COVERING_CHALL_A_SQUEEZE_CONFIRMED": {
        "min_vol_surge": 2.5, "rsi_oversold": 40.0, "atr_buffer": 1.5, "target_r": 2.5
    },
}


def run_short_covering_replay(step: int = 5):
    print("=" * 90)
    print("SHORT COVERING EOD ENGINE: VECTORIZED HISTORICAL REPLAY")
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

        if len(df) < 60:
            continue

        # Precompute rolling features
        df["vol_sma20"] = df["Volume"].rolling(20, min_periods=5).mean()
        df["sma200"] = df["Close"].rolling(200, min_periods=50).mean()
        df["atr14"] = (df["High"] - df["Low"]).rolling(14, min_periods=5).mean()
        df["high10"] = df["High"].shift(1).rolling(10, min_periods=5).max()

        # Vectorized RSI 14
        delta = df["Close"].diff()
        gain = delta.where(delta > 0, 0.0).rolling(14, min_periods=5).mean()
        loss = (-delta.where(delta < 0, 0.0)).rolling(14, min_periods=5).mean()
        rs = gain / (loss + 1e-6)
        df["rsi14"] = 100.0 - (100.0 / (1.0 + rs))

        for i in range(50, len(df) - 15, step):
            scan_dt = df["dt"].iloc[i]
            d_str = str(scan_dt.date())
            if d_str < IS_START or d_str > OOS_END:
                continue

            close = float(df["Close"].iloc[i])
            low = float(df["Low"].iloc[i])
            vol = float(df["Volume"].iloc[i])
            vol_sma20 = float(df["vol_sma20"].iloc[i]) if not pd.isna(df["vol_sma20"].iloc[i]) else vol
            vol_surge = (vol / vol_sma20) if vol_sma20 > 0 else 1.0
            atr14 = float(df["atr14"].iloc[i]) if not pd.isna(df["atr14"].iloc[i]) else close * 0.02
            sma200 = float(df["sma200"].iloc[i]) if not pd.isna(df["sma200"].iloc[i]) else close
            rsi = float(df["rsi14"].iloc[i]) if not pd.isna(df["rsi14"].iloc[i]) else 50.0
            prior_10_high = float(df["high10"].iloc[i]) if not pd.isna(df["high10"].iloc[i]) else close

            regime = "NEUTRAL"
            if sma200 > 0:
                pct = (close - sma200) / sma200 * 100.0
                if pct > 2.0: regime = "BULL"
                elif pct < -5.0: regime = "BEAR"

            if prior_10_high <= 0:
                continue
            pct_down = (prior_10_high - close) / prior_10_high * 100.0
            if pct_down < 4.0:
                continue

            for vid, cfg in SHORT_COVERING_VARIANTS.items():
                if vol_surge < cfg["min_vol_surge"]:
                    continue
                if rsi > cfg["rsi_oversold"]:
                    continue

                sl = low - cfg["atr_buffer"] * atr14 * 0.5
                risk = close - sl
                if risk <= 0:
                    continue

                t1 = close + cfg["target_r"] * risk
                fwd = df.iloc[i+1:i+16]
                if len(fwd) < 3:
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

    print(f"Short Covering Replay Complete. Total Outcomes: {len(all_outcomes)}")
    if not all_outcomes:
        return

    df_res = pd.DataFrame(all_outcomes)
    df_res.to_csv(_OUTPUT_CSV, index=False)
    print(f"Saved outcomes to {_OUTPUT_CSV}")

    print("\n" + "=" * 90)
    print("SHORT COVERING EOD PERFORMANCE SUMMARY")
    print("=" * 90)
    hdr = f"  {'Variant ID':<40} {'Part':>5} {'N':>5} {'E[R]':>7} {'PF':>6} {'Win%':>6} {'+2R%':>6} {'SL%':>6}"
    print(hdr)
    print("  " + "-" * 90)

    for vid in SHORT_COVERING_VARIANTS:
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
            print(f"  {vid:<40} {part:>5} {n:>5} {mean_r:>+7.3f} {pf:>6.2f} {wr:>5.1f}% {r2:>5.1f}% {sl_pct:>5.1f}%")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--step", type=int, default=5)
    args = parser.parse_args()
    run_short_covering_replay(step=args.step)
