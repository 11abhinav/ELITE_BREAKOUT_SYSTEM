#!/usr/bin/env python3
# =============================================================================
# scripts/run_v59_rigorous_frontier_optimization.py
# V5.9 RIGOROUS MULTI-FRONTIER WIN-RATE & NET-ALPHA OPTIMIZATION ENGINE
# =============================================================================
# Research Mandate:
# "Find the highest sustainable Net Win Rate that can be achieved for each scanner,
# including testing 60%, 70%, 80% and 90% WR frontiers, while preserving meaningful
# expectancy, PF, sample depth, regime robustness and drawdown."
#
# Scanner-Specific Objective Functions:
# 1. Strong Alpha Scanners (VCP, EOD, Reversal, Pullback, MultiTF 5M):
#    Maximize Net WR s.t. Net ER >= 80% of Control ER, Net PF >= 1.30-1.40, N >= Control N
# 2. Weak Scanners (Technical Ahat, Daily Builder, MultiTF 1H, Wealth):
#    Maximize Net WR & recover economic edge (Net ER > +0.10R, Net PF >= 1.25-1.30)
# 3. Short Covering:
#    Maximize Bear/Neutral Net WR, Bear Net ER, and Bear PF
# 4. Multibagger:
#    Maximize 5R+ frequency and Net ER right-tail convexity (NO WR constraint)
# =============================================================================

import glob
import json
import os
import sys
import time
from typing import Any, Dict, List
import numpy as np
import pandas as pd

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
_HISTORY_1D_DIR = os.path.join(_REPO_ROOT, "data", "history", "1d")
_HISTORY_1H_DIR = os.path.join(_REPO_ROOT, "data", "history", "1h")
_REPORTS_DIR = os.path.join(_REPO_ROOT, "reports")
os.makedirs(_REPORTS_DIR, exist_ok=True)

from v58_prelive_certification_engine import (
    FRICTION_PARAMETERS,
    SECTOR_MAP,
    get_sector,
    apply_realistic_friction,
    calc_performance_summary
)

DEV_START = "2025-07-24"
VAL_START = "2026-01-01"
HLD_START = "2026-06-01"
HLD_END   = "2026-09-04"

def get_regime(sma50: float, sma200: float, c: float) -> str:
    if c >= sma50 and sma50 >= sma200:
        return "BULL"
    elif c < sma50 and sma50 < sma200:
        return "BEAR"
    else:
        return "NEUTRAL"

# ── DEFINITION OF MULTI-FRONTIER CANDIDATE LEVERS (UP TO 90% WR) ─────────────
FRONTIER_SPECS = {
    "ACCUMULATION_VCP": [
        {"id": "VCP_V58_CONTROL", "frontier": "CONTROL", "lb": 15, "bb_pct": 0.45, "dry_vol": 0.80, "vol_thrust": 1.5, "cpos": 0.65, "target_r": 3.0, "horizon": 20, "holding_type": "SWING_BREAKOUT"},
        {"id": "VCP_V59_F90_PINCH_14R", "frontier": "FRONTIER_90", "lb": 15, "bb_pct": 0.25, "dry_vol": 0.60, "vol_thrust": 2.0, "cpos": 0.85, "target_r": 1.4, "horizon": 10, "holding_type": "SWING_BREAKOUT"},
        {"id": "VCP_V59_F80_PRECISION_18R", "frontier": "FRONTIER_80", "lb": 15, "bb_pct": 0.30, "dry_vol": 0.65, "vol_thrust": 1.8, "cpos": 0.80, "target_r": 1.8, "horizon": 12, "holding_type": "SWING_BREAKOUT"},
        {"id": "VCP_V59_F70_BALANCED_22R", "frontier": "FRONTIER_70", "lb": 15, "bb_pct": 0.35, "dry_vol": 0.70, "vol_thrust": 1.6, "cpos": 0.75, "target_r": 2.2, "horizon": 15, "holding_type": "SWING_BREAKOUT"},
        {"id": "VCP_V59_F60_CAPACITY_25R", "frontier": "FRONTIER_60", "lb": 15, "bb_pct": 0.40, "dry_vol": 0.75, "vol_thrust": 1.5, "cpos": 0.70, "target_r": 2.5, "horizon": 20, "holding_type": "SWING_BREAKOUT"},
    ],
    "EOD_BREAKOUT": [
        {"id": "EOD_V58_CONTROL", "frontier": "CONTROL", "lb": 15, "max_span_atr": 2.5, "cpos": 0.65, "vol_surge": 1.0, "target_r": 2.5, "horizon": 15, "holding_type": "SWING_BREAKOUT"},
        {"id": "EOD_V59_F90_TIGHT_14R", "frontier": "FRONTIER_90", "lb": 12, "max_span_atr": 1.3, "cpos": 0.88, "vol_surge": 2.0, "target_r": 1.4, "horizon": 8, "holding_type": "SWING_BREAKOUT"},
        {"id": "EOD_V59_F80_PRECISION_18R", "frontier": "FRONTIER_80", "lb": 15, "max_span_atr": 1.6, "cpos": 0.82, "vol_surge": 1.8, "target_r": 1.8, "horizon": 10, "holding_type": "SWING_BREAKOUT"},
        {"id": "EOD_V59_F70_BALANCED_22R", "frontier": "FRONTIER_70", "lb": 15, "max_span_atr": 1.8, "cpos": 0.75, "vol_surge": 1.5, "target_r": 2.2, "horizon": 12, "holding_type": "SWING_BREAKOUT"},
        {"id": "EOD_V59_F60_CAPACITY_24R", "frontier": "FRONTIER_60", "lb": 15, "max_span_atr": 2.0, "cpos": 0.70, "vol_surge": 1.3, "target_r": 2.4, "horizon": 15, "holding_type": "SWING_BREAKOUT"},
    ],
    "REVERSAL": [
        {"id": "REV_V58_CONTROL", "frontier": "CONTROL", "lb": 15, "min_clv": 0.60, "vol_mult": 1.2, "target_r": 3.0, "horizon": 15, "holding_type": "SWING_COUNTER_TREND"},
        {"id": "REV_V59_F90_EXHAUST_15R", "frontier": "FRONTIER_90", "lb": 15, "min_clv": 0.90, "vol_mult": 2.0, "target_r": 1.5, "horizon": 8, "holding_type": "SWING_COUNTER_TREND"},
        {"id": "REV_V59_F80_PRECISION_18R", "frontier": "FRONTIER_80", "lb": 15, "min_clv": 0.85, "vol_mult": 1.8, "target_r": 1.8, "horizon": 10, "holding_type": "SWING_COUNTER_TREND"},
        {"id": "REV_V59_F70_BALANCED_22R", "frontier": "FRONTIER_70", "lb": 15, "min_clv": 0.78, "vol_mult": 1.5, "target_r": 2.2, "horizon": 12, "holding_type": "SWING_COUNTER_TREND"},
        {"id": "REV_V59_F60_ALPHA_25R", "frontier": "FRONTIER_60", "lb": 15, "min_clv": 0.72, "vol_mult": 1.4, "target_r": 2.5, "horizon": 15, "holding_type": "SWING_COUNTER_TREND"},
    ],
    "PULLBACK_V2": [
        {"id": "PULL_V58_CONTROL", "frontier": "CONTROL", "pullback_bars": 5, "cpos": 0.60, "vol_dry": 0.85, "target_r": 2.5, "horizon": 15, "holding_type": "SWING_TREND"},
        {"id": "PULL_V59_F90_ENGULF_14R", "frontier": "FRONTIER_90", "pullback_bars": 4, "cpos": 0.88, "vol_dry": 0.60, "target_r": 1.4, "horizon": 8, "holding_type": "SWING_TREND"},
        {"id": "PULL_V59_F80_PRECISION_18R", "frontier": "FRONTIER_80", "pullback_bars": 4, "cpos": 0.82, "vol_dry": 0.65, "target_r": 1.8, "horizon": 10, "holding_type": "SWING_TREND"},
        {"id": "PULL_V59_F70_BALANCED_20R", "frontier": "FRONTIER_70", "pullback_bars": 5, "cpos": 0.75, "vol_dry": 0.70, "target_r": 2.0, "horizon": 12, "holding_type": "SWING_TREND"},
        {"id": "PULL_V59_F60_CAPACITY_22R", "frontier": "FRONTIER_60", "pullback_bars": 5, "cpos": 0.70, "vol_dry": 0.75, "target_r": 2.2, "horizon": 15, "holding_type": "SWING_TREND"},
    ],
    "MULTITF_5M": [
        {"id": "M5M_V58_CONTROL", "frontier": "CONTROL", "cpos": 0.65, "vol_surge": 1.5, "target_r": 2.5, "horizon": 10, "holding_type": "INTRADAY_MOMENTUM"},
        {"id": "M5M_V59_F80_OPENING_SURGE_18R", "frontier": "FRONTIER_80", "cpos": 0.85, "vol_surge": 2.2, "target_r": 1.8, "horizon": 8, "holding_type": "INTRADAY_MOMENTUM"},
        {"id": "M5M_V59_F70_BALANCED_20R", "frontier": "FRONTIER_70", "cpos": 0.80, "vol_surge": 1.8, "target_r": 2.0, "horizon": 10, "holding_type": "INTRADAY_MOMENTUM"},
        {"id": "M5M_V59_F60_CAPACITY_22R", "frontier": "FRONTIER_60", "cpos": 0.72, "vol_surge": 1.5, "target_r": 2.2, "horizon": 12, "holding_type": "INTRADAY_MOMENTUM"},
    ],
    "TECHNICAL_AHAT": [
        {"id": "AHAT_V58_CONTROL", "frontier": "CONTROL", "cpos": 0.60, "rsi_min": 48, "target_r": 2.5, "horizon": 15, "holding_type": "SWING_CONFLUENCE"},
        {"id": "AHAT_V59_F80_TRIPLE_CONFL_16R", "frontier": "FRONTIER_80", "cpos": 0.85, "rsi_min": 60, "target_r": 1.6, "horizon": 10, "holding_type": "SWING_CONFLUENCE"},
        {"id": "AHAT_V59_F70_PRECISION_19R", "frontier": "FRONTIER_70", "cpos": 0.80, "rsi_min": 56, "target_r": 1.9, "horizon": 12, "holding_type": "SWING_CONFLUENCE"},
        {"id": "AHAT_V59_F60_BALANCED_22R", "frontier": "FRONTIER_60", "cpos": 0.75, "rsi_min": 52, "target_r": 2.2, "horizon": 15, "holding_type": "SWING_CONFLUENCE"},
    ],
    "DAILY_BUILDER": [
        {"id": "BUILD_V58_CONTROL", "frontier": "CONTROL", "lb": 20, "cpos": 0.60, "vol_surge": 1.2, "target_r": 2.5, "horizon": 15, "holding_type": "SWING_BREADTH"},
        {"id": "BUILD_V59_F80_SURGE_PINCH_16R", "frontier": "FRONTIER_80", "lb": 12, "cpos": 0.85, "vol_surge": 2.0, "target_r": 1.6, "horizon": 10, "holding_type": "SWING_BREADTH"},
        {"id": "BUILD_V59_F70_PRECISION_19R", "frontier": "FRONTIER_70", "lb": 15, "cpos": 0.80, "vol_surge": 1.7, "target_r": 1.9, "horizon": 12, "holding_type": "SWING_BREADTH"},
        {"id": "BUILD_V59_F60_BALANCED_22R", "frontier": "FRONTIER_60", "lb": 20, "cpos": 0.75, "vol_surge": 1.5, "target_r": 2.2, "horizon": 15, "holding_type": "SWING_BREADTH"},
    ],
    "WEALTH": [
        {"id": "WEALTH_V58_CONTROL", "frontier": "CONTROL", "lb": 40, "cpos": 0.60, "vol_surge": 1.2, "target_r": 3.0, "horizon": 30, "holding_type": "POSITIONAL_COMPOUND"},
        {"id": "WEALTH_V59_F70_STAGE1_TIGHT_22R", "frontier": "FRONTIER_70", "lb": 40, "cpos": 0.80, "vol_surge": 1.8, "target_r": 2.2, "horizon": 25, "holding_type": "POSITIONAL_COMPOUND"},
        {"id": "WEALTH_V59_F60_QUALITY_BASE_25R", "frontier": "FRONTIER_60", "lb": 40, "cpos": 0.75, "vol_surge": 1.5, "target_r": 2.5, "horizon": 30, "holding_type": "POSITIONAL_COMPOUND"},
    ],
    "SHORT_COVERING_EOD": [
        {"id": "SC_V58_CONTROL", "frontier": "CONTROL", "lb": 15, "vol_surge": 1.6, "cpos": 0.65, "target_r": 2.5, "horizon": 12, "holding_type": "SWING_SQUEEZE"},
        {"id": "SC_V59_F80_SQUEEZE_SURGE_16R", "frontier": "FRONTIER_80", "lb": 12, "vol_surge": 2.2, "cpos": 0.85, "target_r": 1.6, "horizon": 8, "holding_type": "SWING_SQUEEZE"},
        {"id": "SC_V59_F70_PRECISION_19R", "frontier": "FRONTIER_70", "lb": 15, "vol_surge": 1.8, "cpos": 0.80, "target_r": 1.9, "horizon": 10, "holding_type": "SWING_SQUEEZE"},
        {"id": "SC_V59_F60_BALANCED_22R", "frontier": "FRONTIER_60", "lb": 15, "vol_surge": 1.5, "cpos": 0.72, "target_r": 2.2, "horizon": 12, "holding_type": "SWING_SQUEEZE"},
    ],
    "MULTIBAGGER": [
        {"id": "MBAG_V58_CONTROL", "frontier": "CONTROL", "lb": 60, "vol_surge": 1.65, "target_r": 5.0, "horizon": 40, "holding_type": "POSITIONAL_CONVEXITY"},
        {"id": "MBAG_V59_CONVEX_A_75D_180V_5R", "frontier": "CONVEXITY_75D", "lb": 75, "vol_surge": 1.80, "target_r": 5.0, "horizon": 45, "holding_type": "POSITIONAL_CONVEXITY"},
        {"id": "MBAG_V59_CONVEX_B_90D_200V_55R", "frontier": "CONVEXITY_90D", "lb": 90, "vol_surge": 2.00, "target_r": 5.5, "horizon": 50, "holding_type": "POSITIONAL_CONVEXITY"},
    ]
}

def simulate_trade_numpy(highs, lows, closes, entry, sl, tgt_p, target_r, max_fwd):
    risk = entry - sl
    if risk <= 0 or np.isnan(risk):
        return 0.0
    
    for f_idx in range(1, max_fwd + 1):
        if lows[f_idx] <= sl:
            return -1.0
        if highs[f_idx] >= tgt_p:
            return target_r
    
    exit_p = closes[max_fwd]
    if np.isnan(exit_p):
        return 0.0
    return (exit_p - entry) / risk

def run_multi_frontier_optimizer():
    print("=" * 135)
    print("V5.9 MULTI-FRONTIER WIN-RATE & NET-ALPHA OPTIMIZATION ENGINE")
    print("Evaluating 60%, 70%, 80%, 90% Win Rate Frontiers & Multibagger Convexity across 11 Scanners")
    print("=" * 135)
    t0 = time.time()

    daily_files = sorted(glob.glob(os.path.join(_HISTORY_1D_DIR, "*.parquet")))
    print(f"Loaded {len(daily_files)} daily equity parquets.")

    all_outcomes: List[Dict[str, Any]] = []

    for fpath in daily_files:
        sym = os.path.basename(fpath).replace(".parquet", "").upper()
        try:
            df = pd.read_parquet(fpath)
            if df is None or len(df) < 110:
                continue
            if not isinstance(df.index, pd.DatetimeIndex):
                df.index = pd.to_datetime(df.index)
            if df.index.tz is None:
                df.index = df.index.tz_localize("Asia/Kolkata")
            else:
                df.index = df.index.tz_convert("Asia/Kolkata")
            df = df.sort_index()
            # Enforce Weekend Ban
            df = df[df.index.dayofweek < 5]
            df = df.dropna(subset=["Open", "High", "Low", "Close"])
        except Exception:
            continue

        n_bars = len(df)
        if n_bars < 110:
            continue

        c = df["Close"].values.astype(np.float64)
        o = df["Open"].values.astype(np.float64)
        h = df["High"].values.astype(np.float64)
        l = df["Low"].values.astype(np.float64)
        v = df["Volume"].values.astype(np.float64) if "Volume" in df.columns else np.ones(n_bars, dtype=np.float64) * 10000.0
        dates = df.index.strftime("%Y-%m-%d").values

        sma20 = pd.Series(c).rolling(20, min_periods=5).mean().values
        sma50 = pd.Series(c).rolling(50, min_periods=20).mean().values
        sma200 = pd.Series(c).rolling(200, min_periods=50).mean().values if n_bars >= 200 else sma50
        atr14 = pd.Series(h - l).rolling(14, min_periods=5).mean().values
        vol20 = pd.Series(v).rolling(20, min_periods=5).mean().values

        # Bollinger Band Width Percentile for VCP
        std20 = pd.Series(c).rolling(20, min_periods=5).std().values
        bb_width = (4.0 * std20) / np.maximum(sma20, 1.0)
        bb_width_pct = pd.Series(bb_width).rolling(60, min_periods=20).rank(pct=True).values

        # RSI 14
        delta = pd.Series(c).diff()
        gain = delta.clip(lower=0)
        loss = -delta.clip(upper=0)
        avg_gain = gain.rolling(14, min_periods=5).mean()
        avg_loss = loss.rolling(14, min_periods=5).mean()
        rs = avg_gain / (avg_loss + 1e-9)
        rsi14 = (100.0 - (100.0 / (1.0 + rs))).values

        for i in range(100, n_bars - 1):
            dt = dates[i]
            if dt < DEV_START or dt > HLD_END:
                continue

            part = "CALIBRATION_DEV" if dt < VAL_START else ("TUNING_VAL" if dt < HLD_START else "LOCKED_REPRODUCTION")
            reg = get_regime(sma50[i], sma200[i], c[i])

            curr_c = c[i]
            curr_h = h[i]
            curr_l = l[i]
            curr_v = v[i]
            curr_atr = atr14[i] if atr14[i] > 0 and not np.isnan(atr14[i]) else curr_c * 0.02
            cpos = (curr_c - curr_l) / (curr_h - curr_l + 1e-9)

            # ── 1. ACCUMULATION_VCP ──────────────────────────────────────────
            for spec in FRONTIER_SPECS["ACCUMULATION_VCP"]:
                lb = spec["lb"]
                if i - lb < 0 or i - 5 < 0:
                    continue
                high_box = np.max(h[i-lb:i])
                low_box = np.min(l[i-lb:i])
                curr_bb = bb_width_pct[i] if not np.isnan(bb_width_pct[i]) else 0.5
                if curr_c > high_box and curr_bb <= spec["bb_pct"] and cpos >= spec["cpos"]:
                    prior_dry = np.mean(v[i-5:i]) / (vol20[i] + 1e-9)
                    if prior_dry <= spec["dry_vol"] and (curr_v / (vol20[i] + 1e-9)) >= spec["vol_thrust"]:
                        entry_p = curr_c
                        sl_p = low_box
                        risk = max(entry_p - sl_p, curr_atr * 0.4)
                        tgt_p = entry_p + (risk * spec["target_r"])
                        max_fwd = min(spec["horizon"], n_bars - 1 - i)
                        if max_fwd > 0:
                            exit_r = simulate_trade_numpy(h[i:], l[i:], c[i:], entry_p, sl_p, tgt_p, spec["target_r"], max_fwd)
                            if not np.isnan(exit_r) and not np.isinf(exit_r):
                                all_outcomes.append({
                                    "scanner": "ACCUMULATION_VCP", "variant_id": spec["id"], "frontier": spec["frontier"],
                                    "symbol": sym, "date": dt, "partition": part, "regime": reg,
                                    "gross_r": exit_r, "holding_type": spec["holding_type"]
                                })

            # ── 2. EOD_BREAKOUT ──────────────────────────────────────────────
            for spec in FRONTIER_SPECS["EOD_BREAKOUT"]:
                lb = spec["lb"]
                if i - lb < 0:
                    continue
                shelf_high = np.max(h[i-lb:i])
                shelf_low = np.min(l[i-lb:i])
                span_atr = (shelf_high - shelf_low) / curr_atr

                if curr_c > shelf_high and cpos >= spec["cpos"] and span_atr <= spec["max_span_atr"]:
                    if (curr_v / (vol20[i] + 1e-9)) >= spec["vol_surge"]:
                        entry_p = curr_c
                        sl_p = shelf_low
                        risk = max(entry_p - sl_p, curr_atr * 0.4)
                        tgt_p = entry_p + (risk * spec["target_r"])
                        max_fwd = min(spec["horizon"], n_bars - 1 - i)
                        if max_fwd > 0:
                            exit_r = simulate_trade_numpy(h[i:], l[i:], c[i:], entry_p, sl_p, tgt_p, spec["target_r"], max_fwd)
                            if not np.isnan(exit_r) and not np.isinf(exit_r):
                                all_outcomes.append({
                                    "scanner": "EOD_BREAKOUT", "variant_id": spec["id"], "frontier": spec["frontier"],
                                    "symbol": sym, "date": dt, "partition": part, "regime": reg,
                                    "gross_r": exit_r, "holding_type": spec["holding_type"]
                                })

            # ── 3. REVERSAL ──────────────────────────────────────────────────
            for spec in FRONTIER_SPECS["REVERSAL"]:
                lb = spec["lb"]
                if i - lb < 0:
                    continue
                prior_low = np.min(l[i-lb:i])
                if curr_l < prior_low and curr_c > prior_low and cpos >= spec["min_clv"]:
                    if (curr_v / (vol20[i] + 1e-9)) >= spec["vol_mult"]:
                        entry_p = curr_c
                        sl_p = curr_l - (curr_atr * 0.15)
                        risk = max(entry_p - sl_p, curr_atr * 0.4)
                        tgt_p = entry_p + (risk * spec["target_r"])
                        max_fwd = min(spec["horizon"], n_bars - 1 - i)
                        if max_fwd > 0:
                            exit_r = simulate_trade_numpy(h[i:], l[i:], c[i:], entry_p, sl_p, tgt_p, spec["target_r"], max_fwd)
                            if not np.isnan(exit_r) and not np.isinf(exit_r):
                                all_outcomes.append({
                                    "scanner": "REVERSAL", "variant_id": spec["id"], "frontier": spec["frontier"],
                                    "symbol": sym, "date": dt, "partition": part, "regime": reg,
                                    "gross_r": exit_r, "holding_type": spec["holding_type"]
                                })

            # ── 4. PULLBACK_V2 ───────────────────────────────────────────────
            for spec in FRONTIER_SPECS["PULLBACK_V2"]:
                pb_bars = spec["pullback_bars"]
                if i - pb_bars < 0:
                    continue
                if not np.isnan(sma50[i]) and not np.isnan(sma20[i]) and curr_c > sma50[i] and curr_l <= sma20[i] * 1.01 and curr_c >= sma20[i] * 0.99:
                    if cpos >= spec["cpos"]:
                        recent_vol = np.mean(v[i-pb_bars:i]) / (vol20[i] + 1e-9)
                        if recent_vol <= spec["vol_dry"]:
                            entry_p = curr_c
                            sl_p = np.min(l[i-pb_bars:i+1]) - (curr_atr * 0.10)
                            risk = max(entry_p - sl_p, curr_atr * 0.4)
                            tgt_p = entry_p + (risk * spec["target_r"])
                            max_fwd = min(spec["horizon"], n_bars - 1 - i)
                            if max_fwd > 0:
                                exit_r = simulate_trade_numpy(h[i:], l[i:], c[i:], entry_p, sl_p, tgt_p, spec["target_r"], max_fwd)
                                if not np.isnan(exit_r) and not np.isinf(exit_r):
                                    all_outcomes.append({
                                        "scanner": "PULLBACK_V2", "variant_id": spec["id"], "frontier": spec["frontier"],
                                        "symbol": sym, "date": dt, "partition": part, "regime": reg,
                                        "gross_r": exit_r, "holding_type": spec["holding_type"]
                                    })

            # ── 5. MULTITF_5M (Simulated from opening momentum) ───────────────
            for spec in FRONTIER_SPECS["MULTITF_5M"]:
                if curr_c > o[i] and cpos >= spec["cpos"] and (curr_v / (vol20[i] + 1e-9)) >= spec["vol_surge"]:
                    entry_p = curr_c
                    sl_p = o[i] - (curr_atr * 0.15)
                    risk = max(entry_p - sl_p, curr_atr * 0.4)
                    tgt_p = entry_p + (risk * spec["target_r"])
                    max_fwd = min(spec["horizon"], n_bars - 1 - i)
                    if max_fwd > 0:
                        exit_r = simulate_trade_numpy(h[i:], l[i:], c[i:], entry_p, sl_p, tgt_p, spec["target_r"], max_fwd)
                        if not np.isnan(exit_r) and not np.isinf(exit_r):
                            all_outcomes.append({
                                "scanner": "MULTITF_5M", "variant_id": spec["id"], "frontier": spec["frontier"],
                                "symbol": sym, "date": dt, "partition": part, "regime": reg,
                                "gross_r": exit_r, "holding_type": spec["holding_type"]
                            })

            # ── 6. TECHNICAL_AHAT ────────────────────────────────────────────
            for spec in FRONTIER_SPECS["TECHNICAL_AHAT"]:
                if not np.isnan(sma20[i]) and not np.isnan(sma50[i]) and not np.isnan(rsi14[i]):
                    if curr_c > sma20[i] and curr_c > sma50[i] and rsi14[i] >= spec["rsi_min"] and cpos >= spec["cpos"]:
                        entry_p = curr_c
                        sl_p = min(sma20[i], curr_l) - (curr_atr * 0.10)
                        risk = max(entry_p - sl_p, curr_atr * 0.4)
                        tgt_p = entry_p + (risk * spec["target_r"])
                        max_fwd = min(spec["horizon"], n_bars - 1 - i)
                        if max_fwd > 0:
                            exit_r = simulate_trade_numpy(h[i:], l[i:], c[i:], entry_p, sl_p, tgt_p, spec["target_r"], max_fwd)
                            if not np.isnan(exit_r) and not np.isinf(exit_r):
                                all_outcomes.append({
                                    "scanner": "TECHNICAL_AHAT", "variant_id": spec["id"], "frontier": spec["frontier"],
                                    "symbol": sym, "date": dt, "partition": part, "regime": reg,
                                    "gross_r": exit_r, "holding_type": spec["holding_type"]
                                })

            # ── 7. DAILY_BUILDER ─────────────────────────────────────────────
            for spec in FRONTIER_SPECS["DAILY_BUILDER"]:
                lb = spec["lb"]
                if i - lb < 0 or i - 5 < 0:
                    continue
                hh = np.max(h[i-lb:i])
                if curr_c > hh and cpos >= spec["cpos"] and (curr_v / (vol20[i] + 1e-9)) >= spec["vol_surge"]:
                    entry_p = curr_c
                    sl_p = np.min(l[i-5:i+1])
                    risk = max(entry_p - sl_p, curr_atr * 0.4)
                    tgt_p = entry_p + (risk * spec["target_r"])
                    max_fwd = min(spec["horizon"], n_bars - 1 - i)
                    if max_fwd > 0:
                        exit_r = simulate_trade_numpy(h[i:], l[i:], c[i:], entry_p, sl_p, tgt_p, spec["target_r"], max_fwd)
                        if not np.isnan(exit_r) and not np.isinf(exit_r):
                            all_outcomes.append({
                                "scanner": "DAILY_BUILDER", "variant_id": spec["id"], "frontier": spec["frontier"],
                                "symbol": sym, "date": dt, "partition": part, "regime": reg,
                                "gross_r": exit_r, "holding_type": spec["holding_type"]
                            })

            # ── 8. WEALTH ────────────────────────────────────────────────────
            for spec in FRONTIER_SPECS["WEALTH"]:
                lb = spec["lb"]
                if i - lb < 0 or i - 15 < 0:
                    continue
                hh_wealth = np.max(h[i-lb:i])
                if curr_c > hh_wealth and cpos >= spec["cpos"] and (curr_v / (vol20[i] + 1e-9)) >= spec["vol_surge"]:
                    entry_p = curr_c
                    sl_p = np.min(l[i-15:i+1])
                    risk = max(entry_p - sl_p, curr_atr * 0.5)
                    tgt_p = entry_p + (risk * spec["target_r"])
                    max_fwd = min(spec["horizon"], n_bars - 1 - i)
                    if max_fwd > 0:
                        exit_r = simulate_trade_numpy(h[i:], l[i:], c[i:], entry_p, sl_p, tgt_p, spec["target_r"], max_fwd)
                        if not np.isnan(exit_r) and not np.isinf(exit_r):
                            all_outcomes.append({
                                "scanner": "WEALTH", "variant_id": spec["id"], "frontier": spec["frontier"],
                                "symbol": sym, "date": dt, "partition": part, "regime": reg,
                                "gross_r": exit_r, "holding_type": spec["holding_type"]
                            })

            # ── 9. SHORT_COVERING_EOD ────────────────────────────────────────
            for spec in FRONTIER_SPECS["SHORT_COVERING_EOD"]:
                if reg in ["BEAR", "NEUTRAL"]:
                    lb = spec["lb"]
                    if i - lb < 0:
                        continue
                    prior_low = np.min(l[i-lb:i])
                    if curr_l < prior_low and curr_c > prior_low and (curr_v / (vol20[i] + 1e-9)) >= spec["vol_surge"] and cpos >= spec["cpos"]:
                        entry_p = curr_c
                        sl_p = curr_l - (curr_atr * 0.10)
                        risk = max(entry_p - sl_p, curr_atr * 0.4)
                        tgt_p = entry_p + (risk * spec["target_r"])
                        max_fwd = min(spec["horizon"], n_bars - 1 - i)
                        if max_fwd > 0:
                            exit_r = simulate_trade_numpy(h[i:], l[i:], c[i:], entry_p, sl_p, tgt_p, spec["target_r"], max_fwd)
                            if not np.isnan(exit_r) and not np.isinf(exit_r):
                                all_outcomes.append({
                                    "scanner": "SHORT_COVERING_EOD", "variant_id": spec["id"], "frontier": spec["frontier"],
                                    "symbol": sym, "date": dt, "partition": part, "regime": reg,
                                    "gross_r": exit_r, "holding_type": spec["holding_type"]
                                })

            # ── 10. MULTIBAGGER ──────────────────────────────────────────────
            for spec in FRONTIER_SPECS["MULTIBAGGER"]:
                lb = spec["lb"]
                if i - lb < 0 or i - 15 < 0:
                    continue
                base_high = np.max(h[i-lb:i])
                if curr_c > base_high and (curr_v / (vol20[i] + 1e-9)) >= spec["vol_surge"]:
                    entry_p = curr_c
                    sl_p = np.min(l[i-15:i+1])
                    risk = max(entry_p - sl_p, curr_atr * 0.8)
                    tgt_p = entry_p + (risk * spec["target_r"])
                    max_fwd = min(spec["horizon"], n_bars - 1 - i)
                    if max_fwd > 0:
                        exit_r = simulate_trade_numpy(h[i:], l[i:], c[i:], entry_p, sl_p, tgt_p, spec["target_r"], max_fwd)
                        if not np.isnan(exit_r) and not np.isinf(exit_r):
                            all_outcomes.append({
                                "scanner": "MULTIBAGGER", "variant_id": spec["id"], "frontier": spec["frontier"],
                                "symbol": sym, "date": dt, "partition": part, "regime": reg,
                                "gross_r": exit_r, "holding_type": spec["holding_type"]
                            })

    # ── 11. MULTITF_1H (Processing Hourly Parquets) ──────────────────────────
    h1_files = sorted(glob.glob(os.path.join(_HISTORY_1H_DIR, "*.parquet")))
    daily_map = {os.path.basename(f).replace(".parquet", "").upper(): f for f in daily_files}
    print(f"Loaded {len(h1_files)} hourly equity parquets for MultiTF 1H sweep.")

    h1_specs = [
        {"id": "M1H_V58_CONTROL", "frontier": "CONTROL", "base_bars": 20, "comp": 0.70, "vol": 1.70, "target_r": 2.6, "holding_type": "INTRADAY_SWING_HOURLY"},
        {"id": "M1H_V59_F80_PINCH_18R", "frontier": "FRONTIER_80", "base_bars": 22, "comp": 0.60, "vol": 2.00, "target_r": 1.8, "holding_type": "INTRADAY_SWING_HOURLY"},
        {"id": "M1H_V59_F70_BALANCED_22R", "frontier": "FRONTIER_70", "base_bars": 20, "comp": 0.65, "vol": 1.80, "target_r": 2.2, "holding_type": "INTRADAY_SWING_HOURLY"},
        {"id": "M1H_V59_F60_CAPACITY_25R", "frontier": "FRONTIER_60", "base_bars": 18, "comp": 0.70, "vol": 1.65, "target_r": 2.5, "holding_type": "INTRADAY_SWING_HOURLY"},
    ]

    for hpath in h1_files:
        sym = os.path.basename(hpath).replace(".parquet", "").upper()
        if sym not in daily_map:
            continue
        try:
            df_h = pd.read_parquet(hpath)
            if df_h is None or len(df_h) < 40:
                continue
            if not isinstance(df_h.index, pd.DatetimeIndex):
                if "Datetime" in df_h.columns:
                    df_h.index = pd.to_datetime(df_h["Datetime"])
                elif "Date" in df_h.columns:
                    df_h.index = pd.to_datetime(df_h["Date"])
            if df_h.index.tz is None:
                df_h.index = df_h.index.tz_localize("Asia/Kolkata")
            else:
                df_h.index = df_h.index.tz_convert("Asia/Kolkata")
            df_h = df_h.sort_index()
            df_h = df_h[df_h.index.dayofweek < 5]
            df_h = df_h.dropna(subset=["Open", "High", "Low", "Close"])
        except Exception:
            continue

        n_hbars = len(df_h)
        if n_hbars < 40:
            continue

        hc = df_h["Close"].values.astype(np.float64)
        hh = df_h["High"].values.astype(np.float64)
        hl = df_h["Low"].values.astype(np.float64)
        hv = df_h["Volume"].values.astype(np.float64) if "Volume" in df_h.columns else np.ones(n_hbars, dtype=np.float64) * 5000.0
        h_dates = df_h.index.strftime("%Y-%m-%d").values

        h_vol20 = pd.Series(hv).rolling(20, min_periods=5).mean().values
        h_atr14 = pd.Series(hh - hl).rolling(14, min_periods=5).mean().values

        for hi in range(25, n_hbars - 1):
            h_dt = h_dates[hi]
            if h_dt < DEV_START or h_dt > HLD_END:
                continue

            h_part = "CALIBRATION_DEV" if h_dt < VAL_START else ("TUNING_VAL" if h_dt < HLD_START else "LOCKED_REPRODUCTION")
            curr_hc = hc[hi]
            curr_hh = hh[hi]
            curr_hl = hl[hi]
            curr_hatr = h_atr14[hi] if h_atr14[hi] > 0 and not np.isnan(h_atr14[hi]) else curr_hc * 0.015
            h_cpos = (curr_hc - curr_hl) / (curr_hh - curr_hl + 1e-9)

            for spec in h1_specs:
                bbars = spec["base_bars"]
                if hi - bbars < 0:
                    continue
                base_h = np.max(hh[hi-bbars:hi])
                base_l = np.min(hl[hi-bbars:hi])
                base_range = base_h - base_l
                comp_ratio = base_range / (curr_hatr * bbars * 0.25 + 1e-9)

                if curr_hc > base_h and h_cpos >= 0.70 and comp_ratio <= spec["comp"]:
                    if (hv[hi] / (h_vol20[hi] + 1e-9)) >= spec["vol"]:
                        entry_p = curr_hc
                        sl_p = base_l
                        risk = max(entry_p - sl_p, curr_hatr * 0.5)
                        tgt_p = entry_p + (risk * spec["target_r"])
                        max_fwd = min(30, n_hbars - 1 - hi)
                        if max_fwd > 0:
                            exit_r = simulate_trade_numpy(hh[hi:], hl[hi:], hc[hi:], entry_p, sl_p, tgt_p, spec["target_r"], max_fwd)
                            if not np.isnan(exit_r) and not np.isinf(exit_r):
                                all_outcomes.append({
                                    "scanner": "MULTITF_1H", "variant_id": spec["id"], "frontier": spec["frontier"],
                                    "symbol": sym, "date": h_dt, "partition": h_part, "regime": "NEUTRAL",
                                    "gross_r": exit_r, "holding_type": spec["holding_type"]
                                })

    out_df = pd.DataFrame(all_outcomes)
    print(f"\nSweep completed in {time.time()-t0:.1f}s. Total simulated trade outcomes across 11 scanners: {len(out_df)}")

    # Apply realistic friction
    out_df["r_multiple"] = out_df["gross_r"]
    out_df["net_r"] = out_df.apply(lambda r: apply_realistic_friction(r, r["holding_type"]), axis=1)

    # ── AGGREGATE RESULTS ACROSS MULTI-TIERED WIN-RATE FRONTIERS ─────────────
    summary_rows = []
    print("\n" + "=" * 145)
    print("V5.9 MASTER MULTI-FRONTIER WIN-RATE CERTIFICATION MATRIX (CONTROL VS 60%, 70%, 80%, 90% FRONTIERS)")
    print("=" * 145)
    print(f"{'SCANNER':<19} | {'VARIANT ID':<34} | {'FRONTIER':<14} | {'N':>4} | {'GROSS WR':>8} | {'NET WR':>7} | {'GROSS ER':>9} | {'NET ER':>8} | {'NET PF':>7} | {'NET 95% CI':>16} | {'NET DD':>7}")
    print("-" * 145)

    for scn, grp in out_df.groupby("scanner"):
        h_grp = grp[grp["partition"] == "LOCKED_REPRODUCTION"]
        for v_id, v_sub in h_grp.groupby("variant_id"):
            f_tier = v_sub["frontier"].iloc[0]
            g_perf = calc_performance_summary(v_sub["gross_r"].values)
            n_perf = calc_performance_summary(v_sub["net_r"].values)

            # Multibagger 5R+ frequency
            five_r_rate = round(float(np.mean(v_sub["gross_r"].values >= 4.9) * 100), 1) if scn == "MULTIBAGGER" else 0.0

            summary_rows.append({
                "scanner": scn,
                "variant_id": v_id,
                "frontier": f_tier,
                "n": n_perf["n"],
                "gross_wr": g_perf["win_pct"],
                "net_wr": n_perf["win_pct"],
                "gross_er": g_perf["er"],
                "net_er": n_perf["er"],
                "gross_pf": g_perf["pf"],
                "net_pf": n_perf["pf"],
                "net_ci_low": n_perf["ci_low"],
                "net_ci_high": n_perf["ci_high"],
                "net_max_dd": n_perf["max_dd_r"],
                "5r_rate": five_r_rate
            })

            badge = "👑 " if n_perf["win_pct"] >= 60.0 or ("MBAG" in v_id and five_r_rate >= 8.0) else ("⭐ " if n_perf["win_pct"] >= 50.0 else "   ")
            print(f"{badge}{scn:<16} | {v_id:<34} | {f_tier:<14} | {n_perf['n']:>4} | {g_perf['win_pct']:>7.1f}% | {n_perf['win_pct']:>6.1f}% | {g_perf['er']:>+8.3f}R | {n_perf['er']:>+7.3f}R | {n_perf['pf']:>7.2f} | [{n_perf['ci_low']:>+5.2f}, {n_perf['ci_high']:>+5.2f}] | {n_perf['max_dd_r']:>6.1f}R")
        print("-" * 145)

    res_df = pd.DataFrame(summary_rows)
    out_csv = os.path.join(_REPORTS_DIR, "v59_frontier_optimization_matrix.csv")
    res_df.to_csv(out_csv, index=False)
    print(f"\nSaved multi-frontier optimization matrix to: {out_csv}")

    out_json = os.path.join(_REPORTS_DIR, "v59_frontier_optimization_results.json")
    with open(out_json, "w") as f:
        json.dump(summary_rows, f, indent=2)
    print(f"Saved JSON results to: {out_json}")

if __name__ == "__main__":
    run_multi_frontier_optimizer()
