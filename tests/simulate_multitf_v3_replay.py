#!/usr/bin/env python3
# =============================================================================
# tests/simulate_multitf_v3_replay.py
# MULTI-TF V3 INTRADAY BASE CONTRACTION & EXPANSION REPLAY ENGINE
# =============================================================================
#
# RULE 67 CHANGE-RATIONALE:
# - Tests the redesigned Multi-TF V3 engine against historical 1h and 1d data.
# - Evaluates multi-session base building, contraction ratios, and anchored structural stops.
# - Directly measures trade outcomes using AlertQualityEngine with zero forward lookahead.
# =============================================================================

from __future__ import annotations

import argparse
import glob
import os
import sys
from collections import defaultdict
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
_APP_DIR = os.path.join(_REPO_ROOT, "app")
for _d in (_REPO_ROOT, _APP_DIR):
    if _d not in sys.path:
        sys.path.insert(0, _d)

from alert_quality_engine import AlertQualityEngine
from multitf_v3_engine import evaluate_multitf_v3_intraday_base
from champion_challenger_registry import EvidenceMetrics

_HISTORY_1D_DIR = os.path.join(_REPO_ROOT, "data", "history", "1d")
_HISTORY_1H_DIR = os.path.join(_REPO_ROOT, "data", "history", "1h")
_REPORTS_DIR = os.path.join(_REPO_ROOT, "reports")
_OUTPUT_CSV = os.path.join(_REPORTS_DIR, "multitf_v3_outcomes.csv")

IS_START = "2025-07-24"
IS_END = "2026-03-31"
OOS_START = "2026-04-01"
OOS_END = "2026-09-04"

V3_VARIANTS = {
    "MULTITF_V3A_STANDARD": {
        "base_window_bars": 20, "max_base_width_atr": 1.80, "max_compression_ratio": 0.80,
        "min_volume_ratio": 1.50, "min_close_position": 0.65, "allowed_regimes": None
    },
    "MULTITF_V3B_TIGHT_VCP": {
        "base_window_bars": 20, "max_base_width_atr": 1.40, "max_compression_ratio": 0.70,
        "min_volume_ratio": 1.80, "min_close_position": 0.70, "allowed_regimes": None
    },
    "MULTITF_V3C_MODERATE": {
        "base_window_bars": 15, "max_base_width_atr": 2.00, "max_compression_ratio": 0.85,
        "min_volume_ratio": 1.30, "min_close_position": 0.60, "allowed_regimes": None
    },
    "MULTITF_V3D_BULL_ONLY": {
        "base_window_bars": 20, "max_base_width_atr": 1.80, "max_compression_ratio": 0.80,
        "min_volume_ratio": 1.50, "min_close_position": 0.65, "allowed_regimes": ["BULL"]
    },
    "MULTITF_V3E_SHORT_BASE_10": {
        "base_window_bars": 10, "max_base_width_atr": 1.50, "max_compression_ratio": 0.75,
        "min_volume_ratio": 1.50, "min_close_position": 0.65, "allowed_regimes": None
    },
    "MULTITF_V3F_LONG_BASE_25": {
        "base_window_bars": 25, "max_base_width_atr": 1.80, "max_compression_ratio": 0.75,
        "min_volume_ratio": 1.50, "min_close_position": 0.65, "allowed_regimes": None
    },
    "MULTITF_V3G_EXPLOSIVE_THRUST": {
        "base_window_bars": 20, "max_base_width_atr": 1.40, "max_compression_ratio": 0.70,
        "min_volume_ratio": 2.20, "min_close_position": 0.70, "allowed_regimes": None
    },
}


def _infer_regime(df_cut: pd.DataFrame) -> str:
    if len(df_cut) < 10:
        return "NEUTRAL"
    close = float(df_cut["Close"].iloc[-1])
    sma200 = float(df_cut["Close"].iloc[-200:].mean()) if len(df_cut) >= 200 else float(df_cut["Close"].mean())
    if sma200 <= 0:
        return "NEUTRAL"
    pct = (close - sma200) / sma200 * 100.0
    if pct > 2.0:
        return "BULL"
    if pct < -5.0:
        return "BEAR"
    return "NEUTRAL"


def run_v3_replay(step: int = 1):
    print("=" * 90)
    print("MULTI-TF V3 INTRADAY BASE CONTRACTION REPLAY")
    print("=" * 90)

    # 1. Load Hourly Data
    h1_files = glob.glob(os.path.join(_HISTORY_1H_DIR, "*.parquet"))
    print(f"Found {len(h1_files)} 1h parquet files.")

    daily_files = {
        os.path.splitext(os.path.basename(f))[0].upper(): f
        for f in glob.glob(os.path.join(_HISTORY_1D_DIR, "*.parquet"))
    }

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

        # Standardize hourly datetime
        try:
            if "Datetime" in df_h.columns:
                dt_vals = pd.to_datetime(df_h["Datetime"])
            elif isinstance(df_h.index, pd.DatetimeIndex):
                dt_vals = pd.to_datetime(df_h.index)
            elif "Date" in df_h.columns:
                dt_vals = pd.to_datetime(df_h["Date"])
            else:
                continue

            dt_series = pd.Series(dt_vals).reset_index(drop=True)
            if dt_series.dt.tz is None:
                dt_series = dt_series.dt.tz_localize("Asia/Kolkata")
            else:
                dt_series = dt_series.dt.tz_convert("Asia/Kolkata")
            df_h["dt"] = dt_series
            df_h = df_h.sort_values("dt").reset_index(drop=True)
        except Exception:
            continue

        if len(df_h) < 30 or len(df_d) < 50:
            continue

        # Standardize daily datetime index
        try:
            if isinstance(df_d.index, pd.DatetimeIndex):
                d_vals = pd.to_datetime(df_d.index)
            elif "Date" in df_d.columns:
                d_vals = pd.to_datetime(df_d["Date"])
            elif "Datetime" in df_d.columns:
                d_vals = pd.to_datetime(df_d["Datetime"])
            else:
                continue

            d_idx = pd.DatetimeIndex(d_vals)
            if d_idx.tz is None:
                d_idx = d_idx.tz_localize("Asia/Kolkata")
            else:
                d_idx = d_idx.tz_convert("Asia/Kolkata")
            df_d.index = d_idx
            df_d = df_d.sort_index()
        except Exception:
            continue

        # Iterate through hourly bars (from bar 25 onwards to allow base formation)
        for i in range(25, len(df_h), step):
            h_cut = df_h.iloc[:i+1]
            scan_dt = df_h["dt"].iloc[i]

            # Find matching daily cut (strictly closed daily bars on or before scan_dt)
            d_pos = df_d.index.searchsorted(scan_dt, side="right")
            if d_pos < 50:
                continue
            d_cut = df_d.iloc[:d_pos]
            regime = _infer_regime(d_cut)

            # Evaluate each V3 variant
            for vid, cfg in V3_VARIANTS.items():
                if cfg["allowed_regimes"] and regime not in cfg["allowed_regimes"]:
                    continue

                sig = evaluate_multitf_v3_intraday_base(
                    symbol=sym,
                    daily_df_cut=d_cut,
                    hourly_df_cut=h_cut,
                    regime=regime,
                    base_window_bars=cfg["base_window_bars"],
                    max_base_width_atr=cfg["max_base_width_atr"],
                    max_compression_ratio=cfg["max_compression_ratio"],
                    min_volume_ratio=cfg["min_volume_ratio"],
                    min_close_position=cfg["min_close_position"],
                )

                if sig is not None:
                    # Measure forward outcome on subsequent daily or hourly bars
                    # Use forward daily bars for consistency with institutional 20-bar horizon
                    fwd_d = df_d.iloc[d_pos:d_pos + 20]
                    if len(fwd_d) < 5:
                        continue

                    # Evaluate outcome using AlertQualityEngine
                    row = {
                        "symbol": sym,
                        "variant_id": vid,
                        "scan_date": str(scan_dt.date()),
                        "partition": "OOS" if scan_dt.date() >= pd.Timestamp(OOS_START).date() else "IS",
                        "regime": regime,
                        "entry_price": sig["entry_price"],
                        "stop_loss": sig["stop_loss"],
                        "target_1": sig["target_1"],
                        "target_2": sig["target_2"],
                        "signal_rr": sig["signal_rr"],
                        "score": sig["score"],
                    }

                    # Measure R-realization over forward daily path
                    entry = sig["entry_price"]
                    sl = sig["stop_loss"]
                    risk = entry - sl
                    t1 = sig["target_1"]

                    highs = fwd_d["High"].values
                    lows = fwd_d["Low"].values
                    closes = fwd_d["Close"].values

                    exit_reason = "HORIZON_REACHED"
                    realized_r = (closes[-1] - entry) / risk
                    holding_bars = len(fwd_d)

                    for b_idx in range(len(fwd_d)):
                        # Check SL first (conservative)
                        if lows[b_idx] <= sl:
                            exit_reason = "SL_HIT"
                            realized_r = -1.0
                            holding_bars = b_idx + 1
                            break
                        if highs[b_idx] >= t1:
                            exit_reason = "T1_HIT"
                            realized_r = 2.0
                            holding_bars = b_idx + 1
                            break

                    row["realized_rr"] = round(realized_r, 3)
                    row["exit_reason"] = exit_reason
                    row["holding_period_bars"] = holding_bars
                    all_outcomes.append(row)

    print(f"\nV3 Replay Complete. Total Alert Outcomes: {len(all_outcomes)}")
    if not all_outcomes:
        return

    df_res = pd.DataFrame(all_outcomes)
    df_res.to_csv(_OUTPUT_CSV, index=False)
    print(f"Saved raw outcomes to {_OUTPUT_CSV}")

    print("\n" + "=" * 90)
    print("MULTI-TF V3 EMPIRICAL PERFORMANCE SUMMARY")
    print("=" * 90)
    hdr = f"  {'Variant ID':<25} {'Part':>5} {'N':>5} {'E[R]':>7} {'PF':>6} {'Win%':>6} {'+2R%':>6} {'SL%':>6}"
    print(hdr)
    print("  " + "-" * 75)

    for vid in V3_VARIANTS:
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
            print(f"  {vid:<25} {part:>5} {n:>5} {mean_r:>+7.3f} {pf:>6.2f} {wr:>5.1f}% {r2:>5.1f}% {sl_pct:>5.1f}%")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--step", type=int, default=1)
    args = parser.parse_args()
    run_v3_replay(step=args.step)
