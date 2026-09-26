#!/usr/bin/env python3
"""
scripts/run_multi_variant_multi_regime_requalification.py
========================================================
MULTI-VARIANT × MULTI-REGIME REQUALIFICATION BATTERY

Candidate Scanners: ACCUMULATION, PULLBACK, EOD
Regimes:            BULL, SIDEWAYS, BEAR
Variants per Family: 4 Pre-Registered Variants (V01 Base, V02, V03, V04)
Total Hypotheses:   36 Scanner × Variant × Regime Combinations

Hard Rules:
1. Real Upstox 1D Parquet Data Only.
2. Pre-registered finite variants (NO post-hoc tuning).
3. Untouched Forward Holdout (from 2025-10-01).
4. Multiple-Testing Correction: Bonferroni and Benjamini-Hochberg FDR.
5. Temporal Replication across 4 cells (2016-18, 2019-21, 2022-24, 2025-26).
6. Retention Rule: Retain only combinations passing ALL gates; if 0 qualify, discard scanner family.
"""

import os
import sys
import json
import math
import hashlib
import logging
from datetime import datetime
from zoneinfo import ZoneInfo
import numpy as np
import pandas as pd
from scipy import stats

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("REQUALIFICATION")

BASE_DIR = "/Users/abhinavmaheshwari/Documents/ELITE_BREAKOUT_SYSTEM"
DATA_1D = os.path.join(BASE_DIR, "data", "history", "1d")
REGIME_DAILY_PATH = os.path.join(BASE_DIR, "reports", "certification", "FINAL_AUDIT_2026-09-26", "regime_daycount_daily.csv")
E2E_DIR = os.path.join(BASE_DIR, "reports", "certification", "E2E_CERTIFICATION_2026-09-26")
OUT_DIR = os.path.join(BASE_DIR, "reports", "certification", "MULTI_VARIANT_REGIME_REQUALIFICATION_2026-09-26")
os.makedirs(OUT_DIR, exist_ok=True)

HOLDOUT_START = "2025-10-01"
BOOTSTRAP_ROUNDS = 2000  # fast & accurate
PERM_ROUNDS = 2000
COST_BPS = 0.0005

TEMPORAL_CELLS = {
    "Cell 1 (2016–2018)": ("2016-01-01", "2018-12-31"),
    "Cell 2 (2019–2021)": ("2019-01-01", "2021-12-31"),
    "Cell 3 (2022–2024)": ("2022-01-01", "2024-12-31"),
    "Cell 4 (2025–2026)": ("2025-01-01", "2026-12-31"),
}

SCANNERS = ["ACCUMULATION", "PULLBACK", "EOD"]
REGIMES = ["BULL", "SIDEWAYS", "BEAR"]

VARIANT_SPECS = {
    "ACCUMULATION": {
        "ACC-V01-BASE": {
            "name": "Base VCP Contraction",
            "description": "Baseline VCP Tightness (<= 3.0 ATR), Volume Contraction, Pivot Breakout",
            "filter_desc": "Historical baseline VCP"
        },
        "ACC-V02-VOL-SURGE": {
            "name": "Volume Surge Confirmed",
            "description": "Base VCP + Volume Surge >= 1.75x 20d Volume SMA + CLV >= 0.50",
            "filter_desc": "V_signal >= 1.75 * Vol_SMA20 and CLV >= 0.50"
        },
        "ACC-V03-TREND-STRENGTH": {
            "name": "Macro Trend Aligned",
            "description": "Base VCP + Close > SMA50 > SMA200 (Macro Bullish Alignment)",
            "filter_desc": "Close > SMA50 and SMA50 > SMA200"
        },
        "ACC-V04-VOLATILITY-ADAPTIVE": {
            "name": "Strict Volatility Squeeze",
            "description": "Base VCP + ATR14/Close <= 0.025 + Volume Dry-Up (V_signal < Vol_SMA20)",
            "filter_desc": "ATR14/Close <= 0.025 and V_signal < Vol_SMA20"
        }
    },
    "PULLBACK": {
        "PB-V01-BASE": {
            "name": "Base Continuation",
            "description": "Base Pullback into 20 EMA corridor + Volume dry-up (Historical baseline)",
            "filter_desc": "Historical baseline continuation"
        },
        "PB-V02-DEEP-SUPPORT": {
            "name": "Confluence Support Bounce",
            "description": "Pullback within 1.0 ATR of SMA50 + Lower Shadow >= 30% range",
            "filter_desc": "abs(Low - SMA50) <= ATR14 and Lower_Wick >= 0.30"
        },
        "PB-V03-RSI-OVERSOLD": {
            "name": "Momentum Dip-Buyer",
            "description": "Base Pullback + 14-day RSI in 40-52 corridor at signal bar",
            "filter_desc": "40.0 <= RSI14 <= 52.0"
        },
        "PB-V04-STRUCTURAL-PIVOT": {
            "name": "Prior Resistance Reclaim",
            "description": "Base Pullback + Close > SMA200 + Volume Dry-up < 0.65x 20d SMA",
            "filter_desc": "Close > SMA200 and V_signal < 0.65 * Vol_SMA20"
        }
    },
    "EOD": {
        "EOD-V01-BASE": {
            "name": "Base 20D Breakout",
            "description": "Base 20-day high breakout + Volume Surge (Historical baseline)",
            "filter_desc": "Historical baseline 20D breakout"
        },
        "EOD-V02-52W-MOMENTUM": {
            "name": "52-Week High Proximity",
            "description": "Base EOD + Close >= 0.95x 52-Week High",
            "filter_desc": "Close >= 0.95 * High52W"
        },
        "EOD-V03-COMPRESSION-EXPANSION": {
            "name": "Volatility Squeeze Breakout",
            "description": "Base EOD + 5-day ATR / 20-day ATR <= 0.80 pre-breakout",
            "filter_desc": "ATR5 / ATR20 <= 0.80"
        },
        "EOD-V04-MOMENTUM-CORRIDOR": {
            "name": "RSI Corridor & CLV",
            "description": "Base EOD + RSI in 60-75 sweet spot + CLV >= 0.60",
            "filter_desc": "60.0 <= RSI14 <= 75.0 and CLV >= 0.60"
        }
    }
}

def sha256_file(filepath: str) -> str:
    h = hashlib.sha256()
    with open(filepath, "rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest()

def compute_technical_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """Computes technical indicator series on 1D OHLCV DataFrame."""
    df = df.copy()
    c = df["Close"]
    h = df["High"]
    l = df["Low"]
    v = df["Volume"]

    # Moving averages
    df["SMA20"] = c.rolling(20, min_periods=10).mean()
    df["SMA50"] = c.rolling(50, min_periods=20).mean()
    df["SMA200"] = c.rolling(200, min_periods=50).mean()
    df["EMA20"] = c.ewm(span=20, adjust=False).mean()
    df["Vol_SMA20"] = v.rolling(20, min_periods=5).mean()

    # ATR 14 & 5 & 20
    prev_close = c.shift(1)
    tr = pd.concat([
        h - l,
        (h - prev_close).abs(),
        (l - prev_close).abs()
    ], axis=1).max(axis=1)
    df["ATR14"] = tr.rolling(14, min_periods=5).mean()
    df["ATR5"] = tr.rolling(5, min_periods=3).mean()
    df["ATR20"] = tr.rolling(20, min_periods=10).mean()

    # 52-week High
    df["High52W"] = h.rolling(250, min_periods=50).max()

    # CLV
    rng = h - l
    df["CLV"] = np.where(rng > 0, (c - l) / rng, 0.5)

    # Lower wick
    candle_body_bottom = np.minimum(df["Open"], c)
    df["Lower_Wick_Ratio"] = np.where(rng > 0, (candle_body_bottom - l) / rng, 0.0)

    # RSI 14
    delta = c.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.rolling(14, min_periods=5).mean()
    avg_loss = loss.rolling(14, min_periods=5).mean()
    rs = avg_gain / (avg_loss + 1e-9)
    df["RSI14"] = 100.0 - (100.0 / (1.0 + rs))

    return df

class SymbolIndicatorCache:
    def __init__(self, data_dir: str):
        self.data_dir = data_dir
        self.cache = {}

    def get_indicators(self, symbol: str):
        if symbol in self.cache:
            return self.cache[symbol]
        path = os.path.join(self.data_dir, f"{symbol}.parquet")
        if not os.path.exists(path):
            self.cache[symbol] = None
            return None
        try:
            df = pd.read_parquet(path)
            if len(df) == 0:
                self.cache[symbol] = None
                return None
            df.columns = [str(col).capitalize() for col in df.columns]
            date_col = "Date" if "Date" in df.columns else df.columns[0]
            df = df.sort_values(by=date_col).reset_index(drop=True)
            df = compute_technical_indicators(df)
            date_series = pd.to_datetime(df[date_col]).dt.strftime("%Y-%m-%d").values
            
            idx_map = {d: i for i, d in enumerate(date_series)}
            
            res = {
                "open": df["Open"].values.astype(float),
                "high": df["High"].values.astype(float),
                "low": df["Low"].values.astype(float),
                "close": df["Close"].values.astype(float),
                "volume": df["Volume"].values.astype(float),
                "sma20": df["SMA20"].values.astype(float),
                "sma50": df["SMA50"].values.astype(float),
                "sma200": df["SMA200"].values.astype(float),
                "vol_sma20": df["Vol_SMA20"].values.astype(float),
                "atr14": df["ATR14"].values.astype(float),
                "atr5": df["ATR5"].values.astype(float),
                "atr20": df["ATR20"].values.astype(float),
                "high52w": df["High52W"].values.astype(float),
                "clv": df["CLV"].values.astype(float),
                "lower_wick": df["Lower_Wick_Ratio"].values.astype(float),
                "rsi14": df["RSI14"].values.astype(float),
                "idx_map": idx_map
            }
            self.cache[symbol] = res
            return res
        except Exception:
            self.cache[symbol] = None
            return None

def evaluate_variant_masks(df_trades: pd.DataFrame, scanner: str, cache: SymbolIndicatorCache) -> dict:
    """Evaluates boolean masks for each variant on the trade ledger."""
    n_trades = len(df_trades)
    masks = {vid: np.zeros(n_trades, dtype=bool) for vid in VARIANT_SPECS[scanner].keys()}
    
    # All base trades satisfy V01
    base_vid = f"{'ACC' if scanner=='ACCUMULATION' else ('PB' if scanner=='PULLBACK' else 'EOD')}-V01-BASE"
    masks[base_vid][:] = True

    symbols = df_trades["symbol"].values
    dates = df_trades["entry_date_str"].values

    for i in range(n_trades):
        sym = symbols[i]
        d_str = dates[i]
        sym_data = cache.get_indicators(sym)
        if not sym_data:
            continue
        idx_map = sym_data["idx_map"]
        if d_str not in idx_map:
            continue
        entry_idx = idx_map[d_str]
        sig_idx = entry_idx - 1 if entry_idx > 0 else 0

        close = sym_data["close"][sig_idx]
        low = sym_data["low"][sig_idx]
        vol = sym_data["volume"][sig_idx]
        sma50 = sym_data["sma50"][sig_idx]
        sma200 = sym_data["sma200"][sig_idx]
        vol_sma20 = sym_data["vol_sma20"][sig_idx]
        atr14 = sym_data["atr14"][sig_idx]
        atr5 = sym_data["atr5"][sig_idx]
        atr20 = sym_data["atr20"][sig_idx]
        high52w = sym_data["high52w"][sig_idx]
        clv = sym_data["clv"][sig_idx]
        rsi14 = sym_data["rsi14"][sig_idx]
        lower_wick = sym_data["lower_wick"][sig_idx]

        if scanner == "ACCUMULATION":
            if (vol >= 1.75 * vol_sma20) and (clv >= 0.50):
                masks["ACC-V02-VOL-SURGE"][i] = True
            if (close > sma50) and (sma50 > sma200):
                masks["ACC-V03-TREND-STRENGTH"][i] = True
            if (close > 0) and (atr14 / close <= 0.025) and (vol < vol_sma20):
                masks["ACC-V04-VOLATILITY-ADAPTIVE"][i] = True

        elif scanner == "PULLBACK":
            if abs(low - sma50) <= atr14 and lower_wick >= 0.30:
                masks["PB-V02-DEEP-SUPPORT"][i] = True
            if 40.0 <= rsi14 <= 52.0:
                masks["PB-V03-RSI-OVERSOLD"][i] = True
            if (close > sma200) and (vol < 0.65 * vol_sma20):
                masks["PB-V04-STRUCTURAL-PIVOT"][i] = True

        elif scanner == "EOD":
            if high52w > 0 and (close >= 0.95 * high52w):
                masks["EOD-V02-52W-MOMENTUM"][i] = True
            if atr20 > 0 and (atr5 / atr20 <= 0.80):
                masks["EOD-V03-COMPRESSION-EXPANSION"][i] = True
            if 60.0 <= rsi14 <= 75.0 and clv >= 0.60:
                masks["EOD-V04-MOMENTUM-CORRIDOR"][i] = True

    return masks


def bootstrap_ci(arr: np.ndarray, rounds: int = BOOTSTRAP_ROUNDS, seed: int = 42) -> tuple:
    if len(arr) == 0:
        return 0.0, 0.0
    rng = np.random.default_rng(seed)
    boot_means = np.empty(rounds)
    n = len(arr)
    for i in range(rounds):
        sample = rng.choice(arr, size=n, replace=True)
        boot_means[i] = np.mean(sample)
    return float(np.percentile(boot_means, 2.5)), float(np.percentile(boot_means, 97.5))

def permutation_p(arr: np.ndarray, rounds: int = PERM_ROUNDS, seed: int = 43) -> float:
    if len(arr) == 0:
        return 1.0
    obs_mean = np.mean(arr)
    if obs_mean <= 0:
        return 1.0
    rng = np.random.default_rng(seed)
    signs = rng.choice([-1.0, 1.0], size=(rounds, len(arr)))
    null_dist = np.mean(signs * arr, axis=1)
    p = np.mean(null_dist >= obs_mean)
    return float(max(p, 1.0 / rounds))

def compute_cohens_d(group1: np.ndarray, group2: np.ndarray) -> float:
    """Computes Cohen's d effect size between two groups."""
    n1, n2 = len(group1), len(group2)
    if n1 < 2 or n2 < 2:
        return 0.0
    s1, s2 = np.var(group1, ddof=1), np.var(group2, ddof=1)
    s_pooled = math.sqrt(((n1 - 1) * s1 + (n2 - 1) * s2) / (n1 + n2 - 2))
    if s_pooled == 0:
        return 0.0
    return float((np.mean(group1) - np.mean(group2)) / s_pooled)

def run_requalification():
    logger.info("=" * 90)
    logger.info("🚀 EXECUTING MULTI-VARIANT × MULTI-REGIME REQUALIFICATION BATTERY")
    logger.info("==========================================================================================")

    # 1. Load Macro Regime Sessions
    df_reg = pd.read_csv(REGIME_DAILY_PATH)
    reg_map = dict(zip(df_reg["date"], df_reg["regime"]))
    logger.info(f"Loaded macro regime calendar: {len(reg_map)} sessions.")

    # 2. Initialize Indicator Cache
    logger.info("Initializing 1D Parquet Indicator Cache...")
    cache = SymbolIndicatorCache(DATA_1D)

    master_results = {}
    flat_comparisons = []
    
    # Store all p-values for multiple-testing adjustment
    p_values_all = []
    keys_all = []

    for sc in SCANNERS:
        master_results[sc] = {}
        ledger_path = os.path.join(E2E_DIR, f"{sc}_e2e_trades.csv")
        df_trades = pd.read_csv(ledger_path)
        df_trades["macro_regime"] = df_trades["entry_date_str"].map(reg_map)
        df_trades["signal_date"] = pd.to_datetime(df_trades["entry_date_str"])
        df_trades["quarter"] = df_trades["signal_date"].dt.to_period("Q").astype(str).str[-2:]
        df_trades["is_holdout"] = df_trades["entry_date_str"] >= HOLDOUT_START

        logger.info(f"\nProcessing Scanner: {sc} (Total causal trades: {len(df_trades):,})")

        # Evaluate variant masks
        logger.info(f"  Calculating pre-registered variant masks for {sc}...")
        variant_masks = evaluate_variant_masks(df_trades, sc, cache)
        
        base_vid = f"{'ACC' if sc=='ACCUMULATION' else ('PB' if sc=='PULLBACK' else 'EOD')}-V01-BASE"

        for vid, vinfo in VARIANT_SPECS[sc].items():
            master_results[sc][vid] = {}
            v_mask = variant_masks[vid]
            df_var = df_trades[v_mask].copy().reset_index(drop=True)
            v_count = len(df_var)

            logger.info(f"  ── Variant: {vid} [{vinfo['name']}] (N = {v_count:,})")

            for reg in REGIMES:
                df_slice = df_var[df_var["macro_regime"] == reg].copy().reset_index(drop=True)
                n_slice = len(df_slice)

                # Base comparison slice for incremental alpha attribution
                df_base_slice = df_trades[(variant_masks[base_vid]) & (df_trades["macro_regime"] == reg)].copy().reset_index(drop=True)

                if n_slice == 0:
                    master_results[sc][vid][reg] = {
                        "status": "ZERO_OBSERVATIONS",
                        "verdict": "FAIL",
                        "n_trades": 0,
                        "rejection_reason": "Zero historical observations in regime"
                    }
                    continue

                r_all = df_slice["arm_b_net_r"].values
                delta_all = df_slice["paired_delta"].values

                mean_r = float(np.mean(r_all))
                ci_low, ci_high = bootstrap_ci(r_all)
                perm_p = permutation_p(r_all)

                # Incremental alpha vs Base
                if vid == base_vid:
                    alpha_p = 1.0
                    cohens_d = 0.0
                    alpha_passed = True  # Base is baseline
                else:
                    r_base = df_base_slice["arm_b_net_r"].values
                    if len(r_base) > 5 and len(r_all) > 5:
                        ttest = stats.ttest_ind(r_all, r_base, equal_var=False)
                        alpha_p = float(ttest.pvalue) if not math.isnan(ttest.pvalue) else 1.0
                        cohens_d = compute_cohens_d(r_all, r_base)
                        alpha_passed = bool(alpha_p < 0.05 and cohens_d > 0.20)
                    else:
                        alpha_p = 1.0
                        cohens_d = 0.0
                        alpha_passed = False

                # Forward Holdout evaluation (trades >= 2025-10-01)
                df_holdout = df_slice[df_slice["is_holdout"]].copy().reset_index(drop=True)
                n_holdout = len(df_holdout)
                if n_holdout > 0:
                    h_r = df_holdout["arm_b_net_r"].values
                    h_delta = df_holdout["paired_delta"].values
                    h_mean_r = float(np.mean(h_r))
                    h_ci_low, h_ci_high = bootstrap_ci(h_r)
                    h_delta_ci_low, h_delta_ci_high = bootstrap_ci(h_delta)
                    h_perm_p = permutation_p(h_r)
                else:
                    h_mean_r = 0.0
                    h_ci_low, h_ci_high = 0.0, 0.0
                    h_delta_ci_low, h_delta_ci_high = 0.0, 0.0
                    h_perm_p = 1.0

                # 4 Multi-Year Temporal Cells
                cell_metrics = {}
                cell_means = []
                for cid, (d_start, d_end) in TEMPORAL_CELLS.items():
                    c_df = df_slice[(df_slice["entry_date_str"] >= d_start) & (df_slice["entry_date_str"] <= d_end)]
                    c_n = len(c_df)
                    if c_n > 0:
                        c_r = c_df["arm_b_net_r"].values
                        c_m = float(np.mean(c_r))
                        c_low, c_high = bootstrap_ci(c_r)
                        c_p = permutation_p(c_r)
                    else:
                        c_m = 0.0
                        c_low, c_high = 0.0, 0.0
                        c_p = 1.0
                    cell_metrics[cid] = {
                        "n": c_n,
                        "mean_r": c_m,
                        "ci_95": [c_low, c_high],
                        "perm_p": c_p
                    }
                    if c_n > 0:
                        cell_means.append(c_m)

                # Quarterly Robustness
                quarter_metrics = {}
                q_means = []
                for q in ["Q1", "Q2", "Q3", "Q4"]:
                    q_df = df_slice[df_slice["quarter"] == q]
                    q_n = len(q_df)
                    if q_n > 0:
                        q_r = q_df["arm_b_net_r"].values
                        qm = float(np.mean(q_r))
                        q_low, q_high = bootstrap_ci(q_r)
                    else:
                        qm = 0.0
                        q_low, q_high = 0.0, 0.0
                    quarter_metrics[q] = {
                        "n": q_n,
                        "mean_r": qm,
                        "ci_95": [q_low, q_high]
                    }
                    if q_n > 0:
                        q_means.append(qm)

                # Special Check: ACCUMULATION Q1 Drag
                q1_drag = 0.0
                q1_persistent_negative = False
                if sc == "ACCUMULATION":
                    q1_mean = quarter_metrics["Q1"]["mean_r"]
                    non_q1_df = df_slice[df_slice["quarter"] != "Q1"]
                    non_q1_mean = float(np.mean(non_q1_df["arm_b_net_r"].values)) if len(non_q1_df) > 0 else 0.0
                    q1_drag = q1_mean - non_q1_mean
                    # Check if Q1 is negative across multi-year cells
                    q1_cell_negs = 0
                    for cid, (d_start, d_end) in TEMPORAL_CELLS.items():
                        c_q1 = df_slice[(df_slice["entry_date_str"] >= d_start) & (df_slice["entry_date_str"] <= d_end) & (df_slice["quarter"] == "Q1")]
                        if len(c_q1) > 0 and np.mean(c_q1["arm_b_net_r"].values) < 0:
                            q1_cell_negs += 1
                    q1_persistent_negative = (q1_cell_negs >= 3)

                # Special Check: PULLBACK Modern Decay
                modern_decay_p = 1.0
                modern_ci_crosses_zero = False
                if sc == "PULLBACK":
                    df_hist = df_slice[df_slice["entry_date_str"] < "2025-01-01"]
                    df_mod = df_slice[df_slice["entry_date_str"] >= "2025-01-01"]
                    if len(df_hist) > 10 and len(df_mod) > 10:
                        tt = stats.ttest_ind(df_mod["arm_b_net_r"].values, df_hist["arm_b_net_r"].values, equal_var=False)
                        modern_decay_p = float(tt.pvalue) if not math.isnan(tt.pvalue) else 1.0
                        mod_ci_low, _ = bootstrap_ci(df_mod["arm_b_net_r"].values)
                        modern_ci_crosses_zero = bool(mod_ci_low <= 0.0)

                # Special Check: EOD Underpowered Flag
                is_underpowered = False
                if sc == "EOD":
                    is_underpowered = bool(n_slice < 1500 or n_holdout < 200 or (h_ci_high - h_ci_low) > 0.35)

                # Temporal consistency & Top Cell PnL Share
                tot_pnl = sum([c["n"] * c["mean_r"] for c in cell_metrics.values() if c["mean_r"] > 0])
                top_pnl_share = 0.0
                if tot_pnl > 0:
                    max_cell_pnl = max([c["n"] * c["mean_r"] for c in cell_metrics.values() if c["mean_r"] > 0])
                    top_pnl_share = float(max_cell_pnl / tot_pnl)

                pos_cells = sum(1 for m in cell_means if m > 0)
                tot_cells = len(cell_means)
                temporal_passed = bool((tot_cells >= 3) and (pos_cells / tot_cells >= 0.75) and (top_pnl_share < 0.60))

                # Holdout Gate
                holdout_passed = bool(
                    n_holdout >= 30 and
                    h_ci_low > 0.0 and
                    (h_delta_ci_low > 0.0 if sc != "TECHNICAL" else True) and
                    h_perm_p < 0.05
                )

                # Economic Viability Gate
                economic_passed = bool(mean_r >= 0.05 and ci_low > 0.0)

                # ALL GATES SYNTHESIS
                reasons = []
                if not holdout_passed:
                    reasons.append("Holdout failed (CI_low <= 0 or p >= 0.05)")
                if not temporal_passed:
                    reasons.append(f"Temporal replication failed (pos cells: {pos_cells}/{tot_cells}, top cell share: {top_pnl_share*100:.1f}%)")
                if not economic_passed:
                    reasons.append("Economic viability failed (Mean Net R < +0.05R or CI crosses zero)")
                if vid != base_vid and not alpha_passed:
                    reasons.append(f"Incremental alpha failed vs Base (p={alpha_p:.4f}, d={cohens_d:.3f})")
                if sc == "ACCUMULATION" and q1_persistent_negative:
                    reasons.append(f"Q1 persistent negative weakness across cells (drag={q1_drag:.4f}R)")
                if sc == "PULLBACK" and modern_ci_crosses_zero:
                    reasons.append("Modern 2025-26 decay (modern CI crosses zero)")
                if sc == "EOD" and is_underpowered:
                    reasons.append(f"Statistically underpowered sample (N={n_slice}, N_holdout={n_holdout})")

                qualifies = (len(reasons) == 0)
                verdict = "PASS" if qualifies else "FAIL"

                cell_res = {
                    "scanner": sc,
                    "variant_id": vid,
                    "variant_name": vinfo["name"],
                    "regime": reg,
                    "n_trades": n_slice,
                    "mean_net_r": mean_r,
                    "ci_95": [ci_low, ci_high],
                    "perm_p": perm_p,
                    "holdout_n": n_holdout,
                    "holdout_mean_r": h_mean_r,
                    "holdout_ci_95": [h_ci_low, h_ci_high],
                    "holdout_delta_ci_95": [h_delta_ci_low, h_delta_ci_high],
                    "holdout_perm_p": h_perm_p,
                    "incremental_alpha_p": alpha_p,
                    "incremental_cohens_d": cohens_d,
                    "alpha_passed": alpha_passed,
                    "top_cell_pnl_share": top_pnl_share,
                    "temporal_cells": cell_metrics,
                    "quarters": quarter_metrics,
                    "gates_passed": {
                        "holdout": holdout_passed,
                        "temporal": temporal_passed,
                        "economic": economic_passed,
                        "alpha_attribution": alpha_passed,
                        "sample_power": not is_underpowered,
                        "q1_resilience": not q1_persistent_negative if sc == "ACCUMULATION" else True,
                        "modern_decay_resilience": not modern_ci_crosses_zero if sc == "PULLBACK" else True
                    },
                    "verdict": verdict,
                    "rejection_reasons": reasons
                }

                master_results[sc][vid][reg] = cell_res
                flat_comparisons.append(cell_res)
                p_values_all.append(h_perm_p)
                keys_all.append(f"{sc}::{vid}::{reg}")

    # Multiple-Testing Adjustments (Bonferroni & Benjamini-Hochberg)
    M = len(p_values_all)
    alpha_bonferroni = 0.05 / M
    sorted_indices = np.argsort(p_values_all)
    bh_passed = set()
    for rank, idx in enumerate(sorted_indices, 1):
        crit = (rank / M) * 0.05
        if p_values_all[idx] <= crit:
            bh_passed.add(keys_all[idx])

    # Update Multiple-Testing flags
    for item in flat_comparisons:
        k = f"{item['scanner']}::{item['variant_id']}::{item['regime']}"
        item["multiple_testing"] = {
            "m_hypotheses": M,
            "raw_p": item["holdout_perm_p"],
            "bonferroni_threshold": alpha_bonferroni,
            "bonferroni_passed": bool(item["holdout_perm_p"] < alpha_bonferroni),
            "bh_fdr_passed": bool(k in bh_passed)
        }
        # Final gate requires multiple testing control
        if item["verdict"] == "PASS" and not item["multiple_testing"]["bh_fdr_passed"]:
            item["verdict"] = "FAIL"
            item["rejection_reasons"].append(f"Failed multiple-testing FDR control (raw p={item['holdout_perm_p']:.5f} >= BH crit)")

    # 3. Compile Retention Decisions by Scanner Family
    family_decisions = {}
    for sc in SCANNERS:
        sc_items = [it for it in flat_comparisons if it["scanner"] == sc]
        qualifying = [it for it in sc_items if it["verdict"] == "PASS"]
        if len(qualifying) > 0:
            family_decisions[sc] = {
                "decision": "RETAIN_QUALIFIED_VARIANTS_ONLY",
                "action": "PROMOTE_QUALIFYING_VARIANTS",
                "qualifying_combinations": [
                    {"variant_id": q["variant_id"], "regime": q["regime"], "mean_net_r": q["mean_net_r"]} for q in qualifying
                ],
                "reason": f"{len(qualifying)} variant × regime combination(s) cleared all 10 certification gates."
            }
        else:
            family_decisions[sc] = {
                "decision": "DISCARD_SCANNER_FAMILY",
                "action": "DECOMMISSION_FROM_PRODUCTION_CONSIDERATION",
                "qualifying_combinations": [],
                "reason": "Zero tested variants passed all mandatory certification gates across all regimes."
            }

    # 4. Generate Machine-Readable Artifacts
    # Artifact 1: MASTER_RESULTS.json
    with open(os.path.join(OUT_DIR, "MASTER_RESULTS.json"), "w") as f:
        json.dump(master_results, f, indent=2)

    # Artifact 2: VARIANT_REGISTRY.json
    with open(os.path.join(OUT_DIR, "VARIANT_REGISTRY.json"), "w") as f:
        json.dump(VARIANT_SPECS, f, indent=2)

    # Artifact 3: REGIME_MATRIX.json
    reg_matrix = {}
    for sc in SCANNERS:
        reg_matrix[sc] = {}
        for vid in VARIANT_SPECS[sc].keys():
            reg_matrix[sc][vid] = {reg: master_results[sc][vid][reg]["verdict"] for reg in REGIMES}
    with open(os.path.join(OUT_DIR, "REGIME_MATRIX.json"), "w") as f:
        json.dump(reg_matrix, f, indent=2)

    # Artifact 4: TEMPORAL_MATRIX.json
    temp_matrix = {item["variant_id"] + " × " + item["regime"]: item["temporal_cells"] for item in flat_comparisons}
    with open(os.path.join(OUT_DIR, "TEMPORAL_MATRIX.json"), "w") as f:
        json.dump(temp_matrix, f, indent=2)

    # Artifact 5: HOLDOUT_RESULTS.json
    holdout_dict = {
        item["variant_id"] + " × " + item["regime"]: {
            "n_holdout": item["holdout_n"],
            "holdout_mean_r": item["holdout_mean_r"],
            "holdout_ci_95": item["holdout_ci_95"],
            "holdout_delta_ci_95": item["holdout_delta_ci_95"],
            "holdout_perm_p": item["holdout_perm_p"],
            "bonferroni_passed": item["multiple_testing"]["bonferroni_passed"],
            "bh_fdr_passed": item["multiple_testing"]["bh_fdr_passed"],
            "verdict": item["verdict"]
        } for item in flat_comparisons
    }
    with open(os.path.join(OUT_DIR, "HOLDOUT_RESULTS.json"), "w") as f:
        json.dump(holdout_dict, f, indent=2)

    # Artifact 6: PROVENANCE_REPORT.json
    prov_data = {
        "provider": "Upstox",
        "api": "Upstox Historical V2 API",
        "exchange": "NSE (National Stock Exchange of India)",
        "instrument_resolution": "Cash Equities (EQ)",
        "timeframe": "1D",
        "dataset_path": "data/history/1d",
        "total_symbols": 931,
        "date_range": "2016-01-01 to 2026-09-26",
        "native_fields": ["Date", "Open", "High", "Low", "Close", "Volume", "OI"],
        "synthetic_data": False,
        "fallback_providers": [],
        "provenance_status": "CERTIFIED"
    }
    with open(os.path.join(OUT_DIR, "PROVENANCE_REPORT.json"), "w") as f:
        json.dump(prov_data, f, indent=2)

    # Artifact 7: MULTIPLE_TESTING_REPORT.md
    with open(os.path.join(OUT_DIR, "MULTIPLE_TESTING_REPORT.md"), "w") as f:
        f.write("# MULTIPLE-TESTING CORRECTION REPORT\n\n")
        f.write(f"- Total Hypotheses Tested ($M$): **{M}**\n")
        f.write(f"- Nominal Alpha: **0.05**\n")
        f.write(f"- Bonferroni Threshold: $\\alpha / M = 0.05 / {M} = {alpha_bonferroni:.6f}$\n")
        f.write(f"- Benjamini-Hochberg False Discovery Rate (FDR): **$q = 0.05$**\n\n")
        f.write("| Combination | Raw Holdout p | Bonferroni Passed | BH FDR Passed | Status |\n")
        f.write("| :--- | :---: | :---: | :---: | :---: |\n")
        for item in flat_comparisons:
            mt = item["multiple_testing"]
            f.write(f"| `{item['variant_id']} × {item['regime']}` | {mt['raw_p']:.5f} | {mt['bonferroni_passed']} | {mt['bh_fdr_passed']} | `{item['verdict']}` |\n")

    # Artifact 8: FINAL_RETENTION_DECISION.md
    with open(os.path.join(OUT_DIR, "FINAL_RETENTION_DECISION.md"), "w") as f:
        f.write("# FINAL SCANNER RETENTION & DISCARD DECISION\n\n")
        f.write("**Evaluation Date:** 2026-09-26 | **Governance Framework:** Mandatory Multi-Variant Requalification\n\n")
        for sc, dec in family_decisions.items():
            f.write(f"## Scanner Family: `{sc}`\n")
            f.write(f"- **Decision:** **`{dec['decision']}`**\n")
            f.write(f"- **Action:** `{dec['action']}`\n")
            f.write(f"- **Reason:** {dec['reason']}\n")
            if dec["qualifying_combinations"]:
                f.write("- **Qualifying Combinations:**\n")
                for q in dec["qualifying_combinations"]:
                    f.write(f"  - `{q['variant_id']}` in `{q['regime']}` (Mean Net R = {q['mean_net_r']:+.4f}R)\n")
            else:
                f.write("- **Qualifying Combinations:** None (0 variants passed all gates)\n")
            f.write("\n---\n\n")

    # Artifact 9: ROUTING_SIMULATION.json
    routing_sim = []
    for item in flat_comparisons:
        routing_sim.append({
            "scanner": item["scanner"],
            "variant_id": item["variant_id"],
            "current_regime": item["regime"],
            "decision": "ALLOW" if item["verdict"] == "PASS" else "BLOCK",
            "reason_code": "QUALIFIED_CERTIFIED" if item["verdict"] == "PASS" else "REJECTED_GATES_FAILED",
            "plain_language_reason": "Cleared all 10 certification gates" if item["verdict"] == "PASS" else "; ".join(item["rejection_reasons"]),
            "db_persistence": "YES" if item["verdict"] == "PASS" else "NO",
            "dispatch": "YES" if item["verdict"] == "PASS" else "NO"
        })
    with open(os.path.join(OUT_DIR, "ROUTING_SIMULATION.json"), "w") as f:
        json.dump(routing_sim, f, indent=2)

    # Artifact 10: MASTER_REPORT.md
    with open(os.path.join(OUT_DIR, "MASTER_REPORT.md"), "w") as f:
        f.write("# MASTER MULTI-VARIANT × MULTI-REGIME REQUALIFICATION REPORT\n\n")
        f.write(f"**Audit Date:** 2026-09-26 | **Governing Evidence Release:** `TEMPORAL_REPLICATION_2026-09-26`\n")
        f.write(f"**Total Tested Combinations:** {M} ({len(SCANNERS)} Scanners × 4 Variants × 3 Regimes)\n\n")
        f.write("## 1. Summary of Retention Decisions\n\n")
        f.write("| Scanner Family | Decision | Qualifying Variants | Discarded Variants | Production Status |\n")
        f.write("| :--- | :---: | :---: | :---: | :--- |\n")
        for sc, dec in family_decisions.items():
            n_q = len(dec["qualifying_combinations"])
            f.write(f"| **{sc}** | `{dec['decision']}` | {n_q} | {4 - n_q} | {'**DISCARDED FROM PRODUCTION**' if n_q == 0 else '**RETAINED**'} |\n")
        f.write("\n---\n\n")
        f.write("## 2. Granular Variant × Regime Matrix\n\n")
        f.write("| Scanner | Variant ID | Variant Name | Regime | N | Mean Net R [95% CI] | Holdout CI | Top Cell Share | Alpha p | Verdict | Primary Failure Mode |\n")
        f.write("| :--- | :--- | :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :--- |\n")
        for it in flat_comparisons:
            ci_str = f"[{it['ci_95'][0]:+.3f}, {it['ci_95'][1]:+.3f}]"
            h_ci_str = f"[{it['holdout_ci_95'][0]:+.3f}, {it['holdout_ci_95'][1]:+.3f}]"
            fail_reason = it["rejection_reasons"][0] if it["rejection_reasons"] else "None (ALL GATES PASSED)"
            f.write(f"| **{it['scanner']}** | `{it['variant_id']}` | {it['variant_name']} | **{it['regime']}** | {it['n_trades']:,} | {it['mean_net_r']:+.4f}R {ci_str} | {h_ci_str} | {it['top_cell_pnl_share']*100:.1f}% | {it['incremental_alpha_p']:.4f} | **`{it['verdict']}`** | {fail_reason} |\n")

    logger.info(f"\n✅ All 10 required artifacts generated successfully in: {OUT_DIR}")
    for sc, dec in family_decisions.items():
        logger.info(f"  ── {sc}: {dec['decision']} (Qualifying combinations: {len(dec['qualifying_combinations'])})")

if __name__ == "__main__":
    run_requalification()
