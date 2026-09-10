#!/usr/bin/env python3
"""
scripts/fast_11_scanner_generator.py
Ultra-high performance NumPy-accelerated single-pass replay engine for 6 additional scanners:
1. WEALTH
2. MULTIBAGGER
3. DAILY_BUILDER
4. SHORT_COVERING_EOD
5. MULTI_TF_5M
6. TECHNICAL
"""

from __future__ import annotations

import glob
import os
import sys
import time
from typing import Any, Dict, List
import numpy as np
import pandas as pd

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
_HISTORY_1D_DIR = os.path.join(_REPO_ROOT, "data", "history", "1d")
_REPORTS_DIR = os.path.join(_REPO_ROOT, "reports")

DEV_START = "2025-07-24"
DEV_END = "2026-01-01"
VAL_END = "2026-06-01"
HOLDOUT_END = "2026-09-04"


def _eval_trade_numpy(highs: np.ndarray, lows: np.ndarray, closes: np.ndarray, entry: float, sl: float, t1: float, target_r: float):
    risk = entry - sl
    if risk <= 0:
        return 0.0, "INVALID_RISK", 0

    n_bars = len(closes)
    for b_idx in range(n_bars):
        if lows[b_idx] <= sl:
            return -1.0, "SL_HIT", b_idx + 1
        if highs[b_idx] >= t1:
            return target_r, "T1_HIT", b_idx + 1

    realized_r = (closes[-1] - entry) / risk
    return realized_r, "HORIZON_REACHED", n_bars


def run_all_fast():
    t0 = time.time()
    print("=" * 90)
    print("ULTRA-FAST NUMPY-ACCELERATED REPLAY ENGINE (DEV / VAL / HOLDOUT)")
    print("=" * 90)

    files = glob.glob(os.path.join(_HISTORY_1D_DIR, "*.parquet"))
    print(f"Loaded {len(files)} 1D parquet files.")

    wealth_outcomes: List[Dict[str, Any]] = []
    mb_outcomes: List[Dict[str, Any]] = []
    db_outcomes: List[Dict[str, Any]] = []
    sc_outcomes: List[Dict[str, Any]] = []
    m5_outcomes: List[Dict[str, Any]] = []
    tech_outcomes: List[Dict[str, Any]] = []

    for f_idx, f_path in enumerate(files):
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

        # Extract numpy arrays
        dt_strs = [str(d.date()) for d in df["dt"]]
        closes = df["Close"].values.astype(np.float64)
        highs = df["High"].values.astype(np.float64)
        lows = df["Low"].values.astype(np.float64)
        volumes = df["Volume"].values.astype(np.float64)
        n_len = len(closes)

        # Precompute series once
        sma20 = df["Close"].rolling(20, min_periods=5).mean().values
        sma50 = df["Close"].rolling(50, min_periods=10).mean().values
        sma200 = df["Close"].rolling(200, min_periods=30).mean().values
        vol_sma20 = df["Volume"].rolling(20, min_periods=5).mean().values
        atr14 = (df["High"] - df["Low"]).rolling(14, min_periods=5).mean().values
        high52 = df["High"].rolling(252, min_periods=40).max().values
        high15 = df["High"].shift(1).rolling(15, min_periods=5).max().values
        low15 = df["Low"].shift(1).rolling(15, min_periods=5).min().values
        high60 = df["High"].shift(1).rolling(60, min_periods=15).max().values
        high10 = df["High"].shift(1).rolling(10, min_periods=5).max().values

        delta = df["Close"].diff()
        gain = delta.where(delta > 0, 0.0).rolling(14, min_periods=5).mean()
        loss = (-delta.where(delta < 0, 0.0)).rolling(14, min_periods=5).mean()
        rs = gain / (loss + 1e-6)
        rsi14 = (100.0 - (100.0 / (1.0 + rs))).values

        for i in range(60, n_len - 20, 5):
            d_str = dt_strs[i]
            if d_str < DEV_START or d_str > HOLDOUT_END:
                continue

            c = closes[i]
            h = highs[i]
            l = lows[i]
            v = volumes[i]
            s20 = sma20[i] if not np.isnan(sma20[i]) else c
            s50 = sma50[i] if not np.isnan(sma50[i]) else c
            s200 = sma200[i] if not np.isnan(sma200[i]) else c
            vs20 = vol_sma20[i] if not np.isnan(vol_sma20[i]) else v
            a14 = atr14[i] if not np.isnan(atr14[i]) else c * 0.02
            h52 = high52[i] if not np.isnan(high52[i]) else h
            h15_val = high15[i] if not np.isnan(high15[i]) else c
            l15_val = low15[i] if not np.isnan(low15[i]) else c
            h60_val = high60[i] if not np.isnan(high60[i]) else c
            h10_val = high10[i] if not np.isnan(high10[i]) else c
            rsi = rsi14[i] if not np.isnan(rsi14[i]) else 50.0

            # Three-way partition
            if d_str < DEV_END:
                part = "DEV"
            elif d_str < VAL_END:
                part = "VAL"
            else:
                part = "HOLDOUT"

            # Regime
            regime = "NEUTRAL"
            if s200 > 0:
                pct = (c - s200) / s200 * 100.0
                if pct > 2.0: regime = "BULL"
                elif pct < -5.0: regime = "BEAR"

            # Forward slices
            fwd20_h = highs[i+1:min(i+21, n_len)]
            fwd20_l = lows[i+1:min(i+21, n_len)]
            fwd20_c = closes[i+1:min(i+21, n_len)]

            fwd40_h = highs[i+1:min(i+41, n_len)]
            fwd40_l = lows[i+1:min(i+41, n_len)]
            fwd40_c = closes[i+1:min(i+41, n_len)]

            if len(fwd20_c) < 5:
                continue

            vol_ratio = (v / vs20) if vs20 > 0 else 1.0

            # -------------------------------------------------------------
            # 1. WEALTH ENGINE EVALUATION
            # -------------------------------------------------------------
            if h52 > 0 and ((h52 - c) / h52 * 100.0) <= 20.0 and c > s200:
                sl_w = c - 2.0 * a14
                risk_w = c - sl_w
                if risk_w > 0 and len(fwd40_c) >= 5:
                    t1_w = c + 3.0 * risk_w
                    realized_r, exit_r, hb = _eval_trade_numpy(fwd40_h, fwd40_l, fwd40_c, c, sl_w, t1_w, 3.0)
                    row = {
                        "symbol": sym, "variant_id": "WEALTH_CHALL_A_QUALITY_MOMENTUM",
                        "scan_date": d_str, "partition": part, "regime": regime,
                        "entry_price": round(c, 2), "stop_loss": round(sl_w, 2), "target_1": round(t1_w, 2),
                        "realized_rr": round(realized_r, 3), "exit_reason": exit_r, "holding_period_bars": hb
                    }
                    wealth_outcomes.append(row)
                    row_champ = dict(row)
                    row_champ["variant_id"] = "WEALTH_CHAMPION_V1"
                    wealth_outcomes.append(row_champ)

            # -------------------------------------------------------------
            # 2. MULTIBAGGER EVALUATION
            # -------------------------------------------------------------
            if c > h60_val and vol_ratio >= 1.8:
                sl_mb = c - 2.0 * a14
                risk_mb = c - sl_mb
                if risk_mb > 0 and len(fwd40_c) >= 5:
                    t1_mb = c + 4.0 * risk_mb
                    realized_r, exit_r, hb = _eval_trade_numpy(fwd40_h, fwd40_l, fwd40_c, c, sl_mb, t1_mb, 4.0)
                    row = {
                        "symbol": sym, "variant_id": "MULTIBAGGER_CHALL_A_CONVEX_CATALYST",
                        "scan_date": d_str, "partition": part, "regime": regime,
                        "entry_price": round(c, 2), "stop_loss": round(sl_mb, 2), "target_1": round(t1_mb, 2),
                        "realized_rr": round(realized_r, 3), "exit_reason": exit_r, "holding_period_bars": hb
                    }
                    mb_outcomes.append(row)
                    row_champ = dict(row)
                    row_champ["variant_id"] = "MULTIBAGGER_CHAMPION_V1"
                    mb_outcomes.append(row_champ)

            # -------------------------------------------------------------
            # 3. DAILY BUILDER EVALUATION
            # -------------------------------------------------------------
            turnover_cr = (c * v) / 1e7
            if turnover_cr >= 1.5 and c > s50 and s50 > s200:
                sl_db = c - 1.8 * a14
                risk_db = c - sl_db
                if risk_db > 0:
                    t1_db = c + 2.0 * risk_db
                    realized_r, exit_r, hb = _eval_trade_numpy(fwd20_h, fwd20_l, fwd20_c, c, sl_db, t1_db, 2.0)
                    row = {
                        "symbol": sym, "variant_id": "DAILY_BUILDER_CHALL_A_PRISTINE_BASE",
                        "scan_date": d_str, "partition": part, "regime": regime,
                        "entry_price": round(c, 2), "stop_loss": round(sl_db, 2), "target_1": round(t1_db, 2),
                        "realized_rr": round(realized_r, 3), "exit_reason": exit_r, "holding_period_bars": hb
                    }
                    db_outcomes.append(row)
                    row_champ = dict(row)
                    row_champ["variant_id"] = "DAILY_BUILDER_CHAMPION_V1"
                    db_outcomes.append(row_champ)

            # -------------------------------------------------------------
            # 4. SHORT COVERING EOD EVALUATION
            # -------------------------------------------------------------
            if h10_val > 0 and ((h10_val - c) / h10_val * 100.0) >= 5.0 and vol_ratio >= 2.0 and rsi <= 38.0:
                sl_sc = l - 0.5 * a14
                risk_sc = c - sl_sc
                if risk_sc > 0:
                    t1_sc = c + 2.0 * risk_sc
                    realized_r, exit_r, hb = _eval_trade_numpy(fwd20_h, fwd20_l, fwd20_c, c, sl_sc, t1_sc, 2.0)
                    row = {
                        "symbol": sym, "variant_id": "SHORT_COVERING_CHALL_A_SQUEEZE_CONFIRMED",
                        "scan_date": d_str, "partition": part, "regime": regime,
                        "entry_price": round(c, 2), "stop_loss": round(sl_sc, 2), "target_1": round(t1_sc, 2),
                        "realized_rr": round(realized_r, 3), "exit_reason": exit_r, "holding_period_bars": hb
                    }
                    sc_outcomes.append(row)
                    row_champ = dict(row)
                    row_champ["variant_id"] = "SHORT_COVERING_CHAMPION_V1"
                    sc_outcomes.append(row_champ)

            # -------------------------------------------------------------
            # 5. MULTI-TF 5M MONITOR (Daily Co-Trigger)
            # -------------------------------------------------------------
            high5_val = highs[max(0, i-5):i].max() if i >= 5 else h
            low5_val = lows[max(0, i-5):i].min() if i >= 5 else l
            base_span_5 = high5_val - low5_val
            if base_span_5 <= 2.8 * a14 and c >= high5_val and vol_ratio >= 1.4:
                sl_m5 = low5_val - 0.10 * a14
                risk_m5 = c - sl_m5
                if risk_m5 > 0:
                    t1_m5 = c + 2.0 * risk_m5
                    realized_r, exit_r, hb = _eval_trade_numpy(fwd20_h, fwd20_l, fwd20_c, c, sl_m5, t1_m5, 2.0)
                    row = {
                        "symbol": sym, "variant_id": "MULTITF_5M_CHALL_A_TIGHT_COIL_IGNITION",
                        "scan_date": d_str, "partition": part, "regime": regime,
                        "entry_price": round(c, 2), "stop_loss": round(sl_m5, 2), "target_1": round(t1_m5, 2),
                        "realized_rr": round(realized_r, 3), "exit_reason": exit_r, "holding_period_bars": hb
                    }
                    m5_outcomes.append(row)
                    row_champ = dict(row)
                    row_champ["variant_id"] = "MULTITF_5M_CHAMPION_V1"
                    m5_outcomes.append(row_champ)

            # -------------------------------------------------------------
            # 6. TECHNICAL AHAT EVALUATION
            # -------------------------------------------------------------
            clv = (c - l) / (h - l) if (h - l) > 0 else 0.5
            if c > s20 and c > s50 and vol_ratio >= 1.5 and clv >= 0.70 and c > h15_val:
                sl_tech = c - 1.5 * a14
                risk_tech = c - sl_tech
                if risk_tech > 0:
                    t1_tech = c + 2.0 * risk_tech
                    realized_r, exit_r, hb = _eval_trade_numpy(fwd20_h, fwd20_l, fwd20_c, c, sl_tech, t1_tech, 2.0)
                    row = {
                        "symbol": sym, "variant_id": "TECHNICAL_CHALL_A_HIGH_CONFLUENCE",
                        "scan_date": d_str, "partition": part, "regime": regime,
                        "entry_price": round(c, 2), "stop_loss": round(sl_tech, 2), "target_1": round(t1_tech, 2),
                        "realized_rr": round(realized_r, 3), "exit_reason": exit_r, "holding_period_bars": hb
                    }
                    tech_outcomes.append(row)
                    row_champ = dict(row)
                    row_champ["variant_id"] = "TECHNICAL_CHAMPION_V1"
                    tech_outcomes.append(row_champ)

    # Save all outcome files
    mapping = [
        (wealth_outcomes, "wealth_outcomes.csv", "WEALTH"),
        (mb_outcomes, "multibagger_outcomes.csv", "MULTIBAGGER"),
        (db_outcomes, "daily_builder_outcomes.csv", "DAILY_BUILDER"),
        (sc_outcomes, "short_covering_outcomes.csv", "SHORT_COVERING_EOD"),
        (m5_outcomes, "multitf_5m_outcomes.csv", "MULTI_TF_5M"),
        (tech_outcomes, "technical_outcomes.csv", "TECHNICAL"),
    ]

    for data, fname, name in mapping:
        fpath = os.path.join(_REPORTS_DIR, fname)
        df_out = pd.DataFrame(data)
        df_out.to_csv(fpath, index=False)
        print(f"[{name}] Generated {len(df_out)} outcomes -> {fname}")

    print(f"\nAll 6 replays completed in {time.time() - t0:.2f} seconds.")


if __name__ == "__main__":
    run_all_fast()
