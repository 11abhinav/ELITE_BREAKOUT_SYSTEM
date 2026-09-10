#!/usr/bin/env python3
# =============================================================================
# tests/simulate_multitf_5m_replay.py
# MULTI-TF 5M MONITOR: VECTORIZED FAST HISTORICAL REPLAY
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

_HISTORY_1H_DIR = os.path.join(_REPO_ROOT, "data", "history", "1h")
_HISTORY_1D_DIR = os.path.join(_REPO_ROOT, "data", "history", "1d")
_REPORTS_DIR = os.path.join(_REPO_ROOT, "reports")
_OUTPUT_CSV = os.path.join(_REPORTS_DIR, "multitf_5m_outcomes.csv")

IS_START = "2025-07-24"
IS_END = "2026-03-31"
OOS_START = "2026-04-01"
OOS_END = "2026-09-04"

M5_VARIANTS = {
    "MULTITF_5M_CHAMPION_V1": {
        "rvol_min": 2.0, "consolidation_bars": 12, "max_span_atr": 1.5, "target_r": 2.0
    },
    "MULTITF_5M_CHALL_A_TIGHT_COIL_IGNITION": {
        "rvol_min": 2.5, "consolidation_bars": 15, "max_span_atr": 1.2, "target_r": 2.5
    },
}


def run_m5_replay(step: int = 5):
    print("=" * 90)
    print("MULTI-TF 5M MONITOR: VECTORIZED FAST REPLAY")
    print("=" * 90)

    h1_files = glob.glob(os.path.join(_HISTORY_1H_DIR, "*.parquet"))
    daily_files = {
        os.path.splitext(os.path.basename(f))[0].upper(): f
        for f in glob.glob(os.path.join(_HISTORY_1D_DIR, "*.parquet"))
    }
    print(f"Loaded {len(h1_files)} 1H parquet files.")

    all_outcomes: List[Dict[str, Any]] = []

    for h_path in h1_files:
        sym = os.path.splitext(os.path.basename(h_path))[0].upper()
        d_path = daily_files.get(sym)
        if not d_path:
            continue

        try:
            df_h = pd.read_parquet(h_path)
            df_d = pd.read_parquet(d_path)
        except Exception:
            continue

        if "Datetime" in df_h.columns:
            df_h["dt"] = pd.to_datetime(df_h["Datetime"])
        elif isinstance(df_h.index, pd.DatetimeIndex):
            df_h["dt"] = pd.to_datetime(df_h.index)
        elif "Date" in df_h.columns:
            df_h["dt"] = pd.to_datetime(df_h["Date"])
        else:
            continue
        df_h = df_h.sort_values("dt").reset_index(drop=True)

        if "Date" in df_d.columns:
            df_d["dt"] = pd.to_datetime(df_d["Date"])
        elif isinstance(df_d.index, pd.DatetimeIndex):
            df_d["dt"] = pd.to_datetime(df_d.index)
        else:
            continue
        df_d = df_d.sort_values("dt").reset_index(drop=True)

        if len(df_h) < 30 or len(df_d) < 50:
            continue

        # Precalculate rolling indicators
        df_h["vol_sma"] = df_h["Volume"].rolling(15, min_periods=5).mean()
        df_h["base_high12"] = df_h["High"].shift(1).rolling(12, min_periods=5).max()
        df_h["base_low12"] = df_h["Low"].shift(1).rolling(12, min_periods=5).min()
        df_h["base_high15"] = df_h["High"].shift(1).rolling(15, min_periods=5).max()
        df_h["base_low15"] = df_h["Low"].shift(1).rolling(15, min_periods=5).min()

        df_d["sma200"] = df_d["Close"].rolling(200, min_periods=50).mean()
        df_d["atr14"] = (df_d["High"] - df_d["Low"]).rolling(14, min_periods=5).mean()

        for i in range(20, len(df_h) - 10, step):
            scan_dt = df_h["dt"].iloc[i]
            d_str = str(scan_dt.date())
            if d_str < IS_START or d_str > OOS_END:
                continue

            close = float(df_h["Close"].iloc[i])
            vol = float(df_h["Volume"].iloc[i])
            vol_sma = float(df_h["vol_sma"].iloc[i]) if not pd.isna(df_h["vol_sma"].iloc[i]) else vol
            rvol = (vol / vol_sma) if vol_sma > 0 else 1.0

            d_sub = df_d[df_d["dt"] <= scan_dt]
            if len(d_sub) < 10:
                continue
            d_atr = float(d_sub["atr14"].iloc[-1]) if not pd.isna(d_sub["atr14"].iloc[-1]) else close * 0.02
            sma200 = float(d_sub["sma200"].iloc[-1]) if not pd.isna(d_sub["sma200"].iloc[-1]) else close

            regime = "NEUTRAL"
            if sma200 > 0:
                pct = (close - sma200) / sma200 * 100.0
                if pct > 2.0: regime = "BULL"
                elif pct < -5.0: regime = "BEAR"

            for vid, cfg in M5_VARIANTS.items():
                if rvol < cfg["rvol_min"]:
                    continue

                cb = cfg["consolidation_bars"]
                base_high = float(df_h[f"base_high{cb}"].iloc[i]) if f"base_high{cb}" in df_h.columns else close
                base_low = float(df_h[f"base_low{cb}"].iloc[i]) if f"base_low{cb}" in df_h.columns else close
                base_span = base_high - base_low

                if base_span > cfg["max_span_atr"] * d_atr:
                    continue
                if close <= base_high:
                    continue

                sl = base_low - 0.10 * d_atr
                risk = close - sl
                if risk <= 0:
                    continue

                t1 = close + cfg["target_r"] * risk
                fwd_d = df_d[df_d["dt"] > scan_dt].iloc[:10]
                if len(fwd_d) < 2:
                    continue

                highs = fwd_d["High"].values
                lows = fwd_d["Low"].values
                closes = fwd_d["Close"].values

                exit_reason = "HORIZON_REACHED"
                realized_r = (closes[-1] - close) / risk
                holding_bars = len(fwd_d)

                for b_idx in range(len(fwd_d)):
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

    print(f"5M Replay Complete. Total Outcomes: {len(all_outcomes)}")
    if not all_outcomes:
        return

    df_res = pd.DataFrame(all_outcomes)
    df_res.to_csv(_OUTPUT_CSV, index=False)
    print(f"Saved outcomes to {_OUTPUT_CSV}")

    print("\n" + "=" * 90)
    print("MULTI-TF 5M MONITOR PERFORMANCE SUMMARY")
    print("=" * 90)
    hdr = f"  {'Variant ID':<40} {'Part':>5} {'N':>5} {'E[R]':>7} {'PF':>6} {'Win%':>6} {'+2R%':>6} {'SL%':>6}"
    print(hdr)
    print("  " + "-" * 90)

    for vid in M5_VARIANTS:
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
    run_m5_replay(step=args.step)
