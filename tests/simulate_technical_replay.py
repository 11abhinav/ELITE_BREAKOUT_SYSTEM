#!/usr/bin/env python3
# =============================================================================
# tests/simulate_technical_replay.py
# TECHNICAL AHAT SCANNER: VECTORIZED FAST HISTORICAL REPLAY
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
_OUTPUT_CSV = os.path.join(_REPORTS_DIR, "technical_outcomes.csv")

IS_START = "2025-07-24"
IS_END = "2026-03-31"
OOS_START = "2026-04-01"
OOS_END = "2026-09-04"

TECH_VARIANTS = {
    "TECHNICAL_CHAMPION_V1": {
        "min_confluence_score": 65, "min_rvol": 1.5, "atr_buffer": 1.8, "target_r": 2.0
    },
    "TECHNICAL_CHALL_A_HIGH_CONFLUENCE": {
        "min_confluence_score": 80, "min_rvol": 2.0, "atr_buffer": 1.5, "target_r": 2.5
    },
}


def run_tech_replay(step: int = 5):
    print("=" * 90)
    print("TECHNICAL AHAT SCANNER: VECTORIZED HISTORICAL REPLAY")
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

        if len(df) < 80:
            continue

        # Precompute rolling features
        df["sma20"] = df["Close"].rolling(20, min_periods=5).mean()
        df["sma50"] = df["Close"].rolling(50, min_periods=10).mean()
        df["sma200"] = df["Close"].rolling(200, min_periods=50).mean()
        df["vol_sma20"] = df["Volume"].rolling(20, min_periods=5).mean()
        df["atr14"] = (df["High"] - df["Low"]).rolling(14, min_periods=5).mean()
        df["pivot15"] = df["High"].shift(1).rolling(15, min_periods=5).max()

        for i in range(50, len(df) - 20, step):
            scan_dt = df["dt"].iloc[i]
            d_str = str(scan_dt.date())
            if d_str < IS_START or d_str > OOS_END:
                continue

            close = float(df["Close"].iloc[i])
            high = float(df["High"].iloc[i])
            low = float(df["Low"].iloc[i])
            vol = float(df["Volume"].iloc[i])
            vol_sma20 = float(df["vol_sma20"].iloc[i]) if not pd.isna(df["vol_sma20"].iloc[i]) else vol
            rvol = (vol / vol_sma20) if vol_sma20 > 0 else 1.0
            atr14 = float(df["atr14"].iloc[i]) if not pd.isna(df["atr14"].iloc[i]) else close * 0.02
            sma20 = float(df["sma20"].iloc[i]) if not pd.isna(df["sma20"].iloc[i]) else close
            sma50 = float(df["sma50"].iloc[i]) if not pd.isna(df["sma50"].iloc[i]) else close
            sma200 = float(df["sma200"].iloc[i]) if not pd.isna(df["sma200"].iloc[i]) else close
            pivot15 = float(df["pivot15"].iloc[i]) if not pd.isna(df["pivot15"].iloc[i]) else close

            regime = "NEUTRAL"
            if sma200 > 0:
                pct = (close - sma200) / sma200 * 100.0
                if pct > 2.0: regime = "BULL"
                elif pct < -5.0: regime = "BEAR"

            # Confluence score
            score = 50.0
            if close > sma20: score += 10.0
            if close > sma50: score += 10.0
            if rvol >= 1.5: score += 15.0
            if regime == "BULL": score += 10.0
            clv = (close - low) / (high - low) if (high - low) > 0 else 0.5
            if clv >= 0.70: score += 10.0

            if close <= pivot15:
                continue

            for vid, cfg in TECH_VARIANTS.items():
                if score < cfg["min_confluence_score"]:
                    continue
                if rvol < cfg["min_rvol"]:
                    continue

                sl = close - cfg["atr_buffer"] * atr14
                risk = close - sl
                if risk <= 0:
                    continue

                t1 = close + cfg["target_r"] * risk
                fwd = df.iloc[i+1:i+21]
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

    print(f"Technical Replay Complete. Total Outcomes: {len(all_outcomes)}")
    if not all_outcomes:
        return

    df_res = pd.DataFrame(all_outcomes)
    df_res.to_csv(_OUTPUT_CSV, index=False)
    print(f"Saved outcomes to {_OUTPUT_CSV}")

    print("\n" + "=" * 90)
    print("TECHNICAL AHAT PERFORMANCE SUMMARY")
    print("=" * 90)
    hdr = f"  {'Variant ID':<40} {'Part':>5} {'N':>5} {'E[R]':>7} {'PF':>6} {'Win%':>6} {'+2R%':>6} {'SL%':>6}"
    print(hdr)
    print("  " + "-" * 90)

    for vid in TECH_VARIANTS:
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
    run_tech_replay(step=args.step)
