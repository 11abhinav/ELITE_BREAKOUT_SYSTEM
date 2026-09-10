#!/usr/bin/env python3
# =============================================================================
# tests/simulate_multitf_replay.py
# MULTI-TF: CHAMPION V1 vs CHALLENGERS A/B/C HISTORICAL REPLAY ENGINE
# =============================================================================
#
# RULE 67 CHANGE-RATIONALE:
# Empirically evaluates Multi-Timeframe Breakout Scanner variants:
#   1. MULTITF_CHAMPION_V1        : Baseline Weekly Thesis + Daily Setup + Hourly Trigger
#   2. MULTITF_CHALL_A_DAILY      : Strict Daily Alignment (Close > EMA20 > SMA50 > SMA200)
#   3. MULTITF_CHALL_B_CONSOLIDATION: Tight Base Filter (BB Width Pctile <= 0.60 vs 0.80)
#   4. MULTITF_CHALL_C_REGIME     : Macro Regime Filter (BULL & NEUTRAL only; BEAR hard reject)
#
# METHODOLOGY:
# - Weekly DataFrame resampled deterministically from 1D data
# - Daily DataFrame from data/history/1d/
# - Hourly DataFrame from data/history/1h/ (available symbols)
# - Forward outcome evaluation via AlertQualityEngine (10-bar horizon for intraday/swing)
# - Registers into ChampionChallengerRegistry under ScannerFamily.MULTI_TF
# - Exports outcomes to reports/multitf_outcomes.csv
# =============================================================================

from __future__ import annotations

import argparse
import csv
import glob
import logging
import os
import sys
from collections import defaultdict
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
_APP_DIR   = os.path.join(_REPO_ROOT, "app")
for _d in (_REPO_ROOT, _APP_DIR):
    if _d not in sys.path:
        sys.path.insert(0, _d)

from multi_tf_engine import (
    evaluate_multi_tf_v2_symbol,
    compute_bb_width_percentile,
    check_weekly_thesis,
    check_daily_setup,
)
from alert_quality_engine import AlertQualityEngine
from champion_challenger_registry import (
    ChampionChallengerRegistry, ScannerFamily, EvidenceMetrics,
)

logging.basicConfig(
    level=logging.WARNING,
    format="%(asctime)s | %(name)s | %(levelname)s | %(message)s",
)
logger = logging.getLogger("MultiTFReplay")

# =============================================================================
# Constants
# =============================================================================

_HISTORY_1D_DIR = os.path.join(_REPO_ROOT, "data", "history", "1d")
_HISTORY_1H_DIR = os.path.join(_REPO_ROOT, "data", "history", "1h")
_REPORTS_DIR    = os.path.join(_REPO_ROOT, "reports")
_OUTPUT_CSV     = os.path.join(_REPORTS_DIR, "multitf_outcomes.csv")

IS_START  = "2025-07-24"
IS_END    = "2026-03-31"
OOS_START = "2026-04-01"
OOS_END   = "2026-09-04"

WEEKLY_STEP_BARS    = 5
OUTCOME_HORIZON     = 10
MIN_BARS_FOR_SIGNAL = 60
MIN_PRICE_FLOOR     = 100.0

MULTITF_CHAMP_ID    = "MULTITF_CHAMPION_V1"
MULTITF_CHALL_A_ID  = "MULTITF_CHALL_A_DAILY"
MULTITF_CHALL_B_ID  = "MULTITF_CHALL_B_CONSOLIDATION"
MULTITF_CHALL_C_ID  = "MULTITF_CHALL_C_REGIME"
MULTITF_CHALL_D_ID  = "MULTITF_CHALL_D_EXT_22"
MULTITF_CHALL_E_ID  = "MULTITF_CHALL_E_EXT_25"
MULTITF_CHALL_F_ID  = "MULTITF_CHALL_F_EXT_30"
MULTITF_CHALL_G_ID  = "MULTITF_CHALL_G_COMPOSITE"
MULTITF_CHALL_H_ID  = "MULTITF_CHALL_H_TIGHT_BASE"
MULTITF_CHALL_I_ID  = "MULTITF_CHALL_I_EARLY_IGNITION"

ALL_MULTITF_VARIANTS = [
    MULTITF_CHAMP_ID,
    MULTITF_CHALL_A_ID,
    MULTITF_CHALL_B_ID,
    MULTITF_CHALL_C_ID,
    MULTITF_CHALL_D_ID,
    MULTITF_CHALL_E_ID,
    MULTITF_CHALL_F_ID,
    MULTITF_CHALL_G_ID,
    MULTITF_CHALL_H_ID,
    MULTITF_CHALL_I_ID,
]

# =============================================================================
# Data Loading & Preparation
# =============================================================================

def _load_data() -> Tuple[Dict[str, pd.DataFrame], Dict[str, pd.DataFrame]]:
    daily_dfs: Dict[str, pd.DataFrame] = {}
    hourly_dfs: Dict[str, pd.DataFrame] = {}

    d_files = sorted(glob.glob(os.path.join(_HISTORY_1D_DIR, "*.parquet")))
    print(f"   Loading {len(d_files)} 1D parquet files ...", flush=True)
    for fpath in d_files:
        sym = os.path.basename(fpath).replace(".parquet", "")
        try:
            df = pd.read_parquet(fpath)
            if df is None or len(df) < 10:
                continue
            if not isinstance(df.index, pd.DatetimeIndex):
                df.index = pd.to_datetime(df.index)
            if df.index.tz is None:
                df.index = df.index.tz_localize("Asia/Kolkata")
            else:
                df.index = df.index.tz_convert("Asia/Kolkata")
            daily_dfs[sym] = df.sort_index()
        except Exception as exc:
            logger.debug("Skipping 1D %s: %s", sym, exc)

    h_files = sorted(glob.glob(os.path.join(_HISTORY_1H_DIR, "*.parquet")))
    print(f"   Loading {len(h_files)} 1H parquet files ...", flush=True)
    for fpath in h_files:
        sym = os.path.basename(fpath).replace(".parquet", "")
        try:
            df = pd.read_parquet(fpath)
            if df is None or len(df) < 10:
                continue
            if not isinstance(df.index, pd.DatetimeIndex):
                df.index = pd.to_datetime(df.index)
            if df.index.tz is None:
                df.index = df.index.tz_localize("Asia/Kolkata")
            else:
                df.index = df.index.tz_convert("Asia/Kolkata")
            hourly_dfs[sym] = df.sort_index()
        except Exception as exc:
            logger.debug("Skipping 1H %s: %s", sym, exc)

    print(f"   Loaded {len(daily_dfs)} daily symbols, {len(hourly_dfs)} hourly symbols.", flush=True)
    print("   Pre-computing weekly resampled DataFrames for all symbols ...", flush=True)
    weekly_dfs: Dict[str, pd.DataFrame] = {}
    for sym, d_df in daily_dfs.items():
        try:
            weekly_dfs[sym] = _resample_weekly(d_df)
        except Exception:
            pass
    print(f"   Pre-computed {len(weekly_dfs)} weekly DataFrames.", flush=True)
    return daily_dfs, hourly_dfs, weekly_dfs


def _resample_weekly(daily_df: pd.DataFrame) -> pd.DataFrame:
    agg_dict = {"Open": "first", "High": "max", "Low": "min", "Close": "last"}
    if "Volume" in daily_df.columns:
        agg_dict["Volume"] = "sum"
    w_df = daily_df.resample("W-FRI").agg(agg_dict).dropna()
    return w_df


def _build_scan_dates(all_dfs: Dict[str, pd.DataFrame], partition: str, step: int = WEEKLY_STEP_BARS) -> List[pd.Timestamp]:
    from collections import Counter
    date_count: Counter = Counter()
    n_syms = len(all_dfs)

    for df in all_dfs.values():
        normed = df.index.normalize()
        for d in normed.unique():
            date_count[d] += 1

    threshold = max(1, int(n_syms * 0.50))
    market_days = sorted([d for d, cnt in date_count.items() if cnt >= threshold])

    market_ts = pd.DatetimeIndex(market_days)
    if market_ts.tz is None:
        market_ts = market_ts.tz_localize("Asia/Kolkata")
    else:
        market_ts = market_ts.tz_convert("Asia/Kolkata")

    if partition == "IS":
        start_ts = pd.Timestamp(IS_START, tz="Asia/Kolkata")
        end_ts   = pd.Timestamp(IS_END,   tz="Asia/Kolkata")
    elif partition == "OOS":
        start_ts = pd.Timestamp(OOS_START, tz="Asia/Kolkata")
        end_ts   = pd.Timestamp(OOS_END,   tz="Asia/Kolkata")
    else:
        start_ts = pd.Timestamp(IS_START, tz="Asia/Kolkata")
        end_ts   = pd.Timestamp(OOS_END,  tz="Asia/Kolkata")

    window = market_ts[(market_ts >= start_ts) & (market_ts <= end_ts)]
    scan_dates = [window[i] for i in range(0, len(window), step)]
    print(f"   Market days in partition: {len(window)} -> scan dates (step={step}): {len(scan_dates)}", flush=True)
    return scan_dates


def _partition_label(scan_ts: pd.Timestamp) -> str:
    return "OOS" if scan_ts >= pd.Timestamp(OOS_START, tz="Asia/Kolkata") else "IS"


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

# =============================================================================
# Multi-TF Variant Router
# =============================================================================

def _eval_multitf_variants(
    symbol: str,
    weekly_df_cut: pd.DataFrame,
    daily_df_cut: pd.DataFrame,
    hourly_df_cut: Optional[pd.DataFrame],
    regime: str,
) -> Dict[str, Optional[Dict[str, Any]]]:
    results: Dict[str, Optional[Dict[str, Any]]] = {vid: None for vid in ALL_MULTITF_VARIANTS}

    if len(daily_df_cut) < 50 or weekly_df_cut is None or len(weekly_df_cut) < 20:
        return results

    # 1. Weekly Thesis
    w_res = check_weekly_thesis(weekly_df_cut)
    if not w_res.get("passed"):
        return results

    # 2. Daily Setup
    d_res = check_daily_setup(daily_df_cut)
    if not d_res.get("passed"):
        return results

    prior_20d_high = float(d_res["prior_20d_high"])

    # 3. Hourly Data
    if hourly_df_cut is None or len(hourly_df_cut) < 20:
        return results

    latest_h = hourly_df_cut.iloc[-1]
    h_close = float(latest_h["Close"])
    h_open = float(latest_h["Open"])
    h_high = float(latest_h["High"])
    h_low = float(latest_h["Low"])
    h_vol = float(latest_h["Volume"]) if "Volume" in hourly_df_cut.columns else 100000.0

    avg_h_vol = float(hourly_df_cut["Volume"].iloc[-21:-1].mean()) if len(hourly_df_cut) >= 21 and "Volume" in hourly_df_cut.columns else float(hourly_df_cut["Volume"].mean())
    vol_ratio = (h_vol / avg_h_vol) if avg_h_vol > 0 else 1.0

    # Basic Breakout Trigger
    if (h_close <= prior_20d_high) or (vol_ratio < 1.5):
        return results

    candle_range = max(0.01, h_high - h_low)
    atr20 = float((daily_df_cut["High"] - daily_df_cut["Low"]).iloc[-20:].mean()) if len(daily_df_cut) >= 20 else max(0.01, h_close * 0.025)
    breakout_extension_atr = (h_close - prior_20d_high) / atr20 if atr20 > 0 else 0.0
    prev_close = float(hourly_df_cut["Close"].iloc[-2]) if len(hourly_df_cut) >= 2 else h_close
    opening_gap_pct = ((h_open - prev_close) / prev_close * 100.0) if prev_close > 0 else 0.0

    body_ratio = abs(h_close - h_open) / candle_range
    close_pos = (h_close - h_low) / candle_range
    upper_wick_ratio = (h_high - max(h_open, h_close)) / candle_range

    # Daily trend and base metrics
    close = float(daily_df_cut["Close"].iloc[-1])
    bb_pctile = compute_bb_width_percentile(daily_df_cut)
    ema20 = float(daily_df_cut["Close"].ewm(span=20, adjust=False).mean().iloc[-1])
    sma50 = float(daily_df_cut["Close"].rolling(50, min_periods=20).mean().iloc[-1])
    sma200 = float(daily_df_cut["Close"].rolling(200, min_periods=50).mean().iloc[-1]) if len(daily_df_cut) >= 200 else sma50
    daily_trend_ok = (close > ema20) and (ema20 > sma50) and (close > sma200)

    # Base Score calculation
    score = 70.0
    if d_res.get("base_age", 0) >= 15:
        score += 10.0
    if d_res.get("resistance_tests", 0) >= 2:
        score += 10.0
    if vol_ratio >= 2.5:
        score += 10.0

    entry_price = h_close
    stop_loss = round(entry_price - 1.5 * atr20, 2)
    risk_dist = max(0.01, entry_price - stop_loss)
    target_1 = round(entry_price + 2.0 * risk_dist, 2)
    rr_ratio = round((target_1 - entry_price) / risk_dist, 2)

    # Variant specifications: (variant_id, max_ext, max_wick, min_close, req_daily, req_bb, allowed_regs)
    var_specs = [
        (MULTITF_CHAMP_ID,   1.8, 0.25, 0.70, False, False, None),
        (MULTITF_CHALL_A_ID, 1.8, 0.25, 0.70, True,  False, None),
        (MULTITF_CHALL_B_ID, 1.8, 0.25, 0.70, False, True,  None),
        (MULTITF_CHALL_C_ID, 1.8, 0.25, 0.70, False, False, ["BULL", "NEUTRAL"]),
        (MULTITF_CHALL_D_ID, 2.2, 0.25, 0.70, False, False, None),
        (MULTITF_CHALL_E_ID, 2.5, 0.25, 0.70, False, False, None),
        (MULTITF_CHALL_F_ID, 3.0, 0.25, 0.70, False, False, None),
        (MULTITF_CHALL_G_ID, 2.5, 0.35, 0.65, False, False, None),
    ]

    for vid, max_ext, max_wick, min_cpos, req_daily, req_bb, allowed_regs in var_specs:
        if req_daily and not daily_trend_ok:
            continue
        if req_bb and bb_pctile > 0.60:
            continue
        if allowed_regs and regime not in allowed_regs:
            continue

        # Check hourly anti-false-breakout gates
        if breakout_extension_atr > max_ext:
            continue
        if opening_gap_pct > 4.0:
            continue
        if body_ratio < 0.40:
            continue
        if close_pos < min_cpos:
            continue
        if upper_wick_ratio > max_wick:
            continue

        results[vid] = {
            "entry_price": entry_price,
            "stop_loss": stop_loss,
            "target_1": target_1,
            "rr_ratio": rr_ratio,
            "score": score,
            "bb_pctile": round(bb_pctile, 2),
            "regime": regime,
            "stage": "CONFIRMED_MULTITF",
            "engine_path": "MULTITF_V2",
            "variant_id": vid,
        }

    # Challenger H: Pre-Breakout Tight Base Contraction + Early Ignition + Structural Stop
    if len(hourly_df_cut) >= 6:
        prior_5h_high = float(hourly_df_cut["High"].iloc[-6:-1].max())
        prior_5h_low = float(hourly_df_cut["Low"].iloc[-6:-1].min())
        h_cons_range = prior_5h_high - prior_5h_low
        is_tight_cons = (h_cons_range <= 1.2 * atr20)
        is_near_pivot = (prior_5h_high >= prior_20d_high * 0.985)
        is_early_ignite = (h_close >= prior_20d_high * 0.998) and (breakout_extension_atr <= 0.80) and (vol_ratio >= 1.3)
        if is_tight_cons and is_near_pivot and is_early_ignite:
            sl_struct = round(min(prior_20d_high - 0.2 * atr20, prior_5h_low - 0.1 * atr20), 2)
            risk_struct = max(0.01, entry_price - sl_struct)
            t1_struct = round(entry_price + 2.0 * risk_struct, 2)
            rr_struct = round((t1_struct - entry_price) / risk_struct, 2)
            results[MULTITF_CHALL_H_ID] = {
                "entry_price": entry_price,
                "stop_loss": sl_struct,
                "target_1": t1_struct,
                "rr_ratio": rr_struct,
                "score": score + 10.0,
                "bb_pctile": round(bb_pctile, 2),
                "regime": regime,
                "stage": "PRE_BREAKOUT_IGNITION",
                "engine_path": "MULTITF_V3_CONTRACTION",
                "variant_id": MULTITF_CHALL_H_ID,
            }
            if regime in ("BULL", "NEUTRAL"):
                results[MULTITF_CHALL_I_ID] = {
                    **results[MULTITF_CHALL_H_ID],
                    "variant_id": MULTITF_CHALL_I_ID,
                }

    return results

# =============================================================================
# Forward Outcome Measurement
# =============================================================================

def _measure_outcome(
    symbol: str,
    variant_id: str,
    signal: Dict[str, Any],
    df_full: pd.DataFrame,
    scan_ts: pd.Timestamp,
    regime: str,
) -> Optional[Dict[str, Any]]:
    fwd_df = df_full[df_full.index > scan_ts].iloc[:OUTCOME_HORIZON]
    if fwd_df.empty or len(fwd_df) < 2:
        return None

    entry  = float(signal["entry_price"])
    sl     = float(signal["stop_loss"])
    t1     = float(signal["target_1"])
    rr_sig = float(signal.get("rr_ratio", 0.0))

    try:
        outcome = AlertQualityEngine.evaluate_trade_outcome(
            entry_price=entry, stop_loss=sl, target_1=t1,
            target_2=None, price_df=fwd_df, scanner="MULTI_TF",
        )
    except Exception as exc:
        logger.debug("Outcome eval error %s/%s: %s", symbol, variant_id, exc)
        return None

    return {
        "symbol":      symbol,
        "variant_id":  variant_id,
        "scan_date":   str(scan_ts.date()),
        "partition":   _partition_label(scan_ts),
        "regime":      regime,
        "entry_price": round(entry, 2),
        "stop_loss":   round(sl, 2),
        "target_1":    round(t1, 2),
        "signal_rr":   round(rr_sig, 2),
        "score":       round(signal.get("score", 0.0), 1),
        "bb_pctile":   signal.get("bb_pctile", 0.0),
        "realized_rr":               outcome.get("realized_rr", 0.0),
        "exit_reason":               outcome.get("exit_reason", ""),
        "exit_date":                 outcome.get("exit_date", ""),
        "holding_period_bars":       outcome.get("holding_period_bars", 0),
        "max_favorable_excursion_r": outcome.get("max_favorable_excursion_r", 0.0),
        "max_adverse_excursion_r":   outcome.get("max_adverse_excursion_r", 0.0),
        "r1_hit_before_sl":          int(outcome.get("r1_hit_before_sl",   False)),
        "r1_5_hit_before_sl":        int(outcome.get("r1_5_hit_before_sl", False)),
        "r2_hit_before_sl":          int(outcome.get("r2_hit_before_sl",   False)),
        "post_sl_recovered_entry":   int(outcome.get("post_sl_recovered_entry", False)),
        "post_sl_recovered_t1":      int(outcome.get("post_sl_recovered_t1",    False)),
        "post_sl_max_recovery_r":    outcome.get("post_sl_max_recovery_r", 0.0),
    }

# =============================================================================
# Main Replay Loop
# =============================================================================

def run_multitf_replay(
    partition: str = "ALL",
    dry_run: bool = False,
    max_symbols: Optional[int] = None,
    verbose: bool = False,
    step: int = WEEKLY_STEP_BARS,
) -> Tuple[List[Dict[str, Any]], Dict[str, int], Dict[str, int], Dict[str, Dict[str, int]]]:
    print("\n" + "=" * 90)
    print("MULTI-TF: CHAMPION V1 vs CHALLENGERS HISTORICAL REPLAY ENGINE")
    print("=" * 90)
    print(f"   Partition     : {partition}  |  Dry-run: {dry_run}")
    print(f"   IS  window    : {IS_START} -> {IS_END}")
    print(f"   OOS window    : {OOS_START} -> {OOS_END}")
    print(f"   Scan step     : every {step} bars  |  Outcome horizon: {OUTCOME_HORIZON} bars")
    print("=" * 90, flush=True)

    daily_dfs, hourly_dfs, weekly_dfs = _load_data()
    if max_symbols:
        keys = sorted(daily_dfs.keys())[:max_symbols]
        daily_dfs = {k: daily_dfs[k] for k in keys}
        weekly_dfs = {k: weekly_dfs[k] for k in keys if k in weekly_dfs}
        print(f"   [DEV] Capped to {len(daily_dfs)} symbols.", flush=True)

    scan_dates = _build_scan_dates(daily_dfs, partition, step=step)
    print(f"   Scan dates    : {len(scan_dates)} ({scan_dates[0].date()} -> {scan_dates[-1].date()})\n", flush=True)

    all_outcomes:    List[Dict[str, Any]] = []
    signal_counts    = defaultdict(int)
    no_outcome_count = defaultdict(int)
    funnel_per_variant: Dict[str, Dict[str, int]] = {vid: defaultdict(int) for vid in ALL_MULTITF_VARIANTS}

    total = len(daily_dfs) * len(scan_dates)
    print(f"   Running {total:,} evaluations ({len(daily_dfs)} symbols x {len(scan_dates)} dates) ...", flush=True)

    dot_ctr = 0
    for scan_ts in scan_dates:
        for sym, df_full in daily_dfs.items():
            pos = df_full.index.searchsorted(scan_ts, side="right")
            if pos < MIN_BARS_FOR_SIGNAL:
                continue
            df_cut = df_full.iloc[:pos]
            if float(df_cut["Close"].iloc[-1]) < MIN_PRICE_FLOOR:
                continue

            w_df = weekly_dfs.get(sym)
            weekly_df_cut = None
            if w_df is not None:
                w_pos = w_df.index.searchsorted(scan_ts, side="right")
                if w_pos >= 20:
                    weekly_df_cut = w_df.iloc[:w_pos]

            # Hourly cut if symbol exists in hourly dataset
            h_full = hourly_dfs.get(sym)
            h_cut = None
            if h_full is not None:
                h_pos = h_full.index.searchsorted(scan_ts, side="right")
                if h_pos >= 20:
                    h_cut = h_full.iloc[:h_pos]

            regime = _infer_regime(df_cut)
            sigs = _eval_multitf_variants(sym, weekly_df_cut, df_cut, h_cut, regime)

            for vid in ALL_MULTITF_VARIANTS:
                funnel_per_variant[vid]["EVALUATED"] += 1
                sig = sigs.get(vid)
                if sig is not None:
                    funnel_per_variant[vid]["ALERTED"] += 1
                    signal_counts[vid] += 1
                    if not dry_run:
                        row = _measure_outcome(sym, vid, sig, df_full, scan_ts, regime)
                        if row:
                            all_outcomes.append(row)
                        else:
                            no_outcome_count[vid] += 1
                    if verbose:
                        print(f"  [{vid}] {sym} @ {scan_ts.date()} entry={sig['entry_price']:.2f}")

            dot_ctr += 1
            if dot_ctr % 500 == 0:
                print(".", end="", flush=True)

    print(f"\n\n   Replay complete. Outcome rows: {len(all_outcomes):,}", flush=True)
    return all_outcomes, dict(signal_counts), dict(no_outcome_count), funnel_per_variant


def _compute_and_register(
    all_outcomes: List[Dict[str, Any]],
    partition: str,
) -> Tuple[ChampionChallengerRegistry, Dict[str, EvidenceMetrics]]:
    registry = ChampionChallengerRegistry()
    metrics_map: Dict[str, EvidenceMetrics] = {}

    if not all_outcomes:
        return registry, metrics_map

    df_all = pd.DataFrame(all_outcomes)
    parts  = ["IS", "OOS"] if partition == "ALL" else [partition]
    REGISTRY_PARTITION = {"IS": "IN_SAMPLE", "OOS": "OOS"}

    for vid in ALL_MULTITF_VARIANTS:
        for part in parts:
            df_v = df_all[(df_all["variant_id"] == vid) & (df_all["partition"] == part)]
            if df_v.empty:
                continue
            period = f"{IS_START}->{IS_END}" if part == "IS" else f"{OOS_START}->{OOS_END}"
            registry_part = REGISTRY_PARTITION[part]
            m = EvidenceMetrics.from_outcomes_df(
                df=df_v, variant_id=vid,
                partition_type=registry_part, evaluation_period=period,
            )
            metrics_map[f"{vid}_{part}"] = m
            try:
                registry.update_variant_metrics(vid, m, registry_part)
            except KeyError as exc:
                logger.warning("Variant not in registry: %s", exc)

    return registry, metrics_map


def _print_report(
    all_outcomes, signal_counts, no_outcome_count,
    funnel_per_variant, registry, metrics_map, partition, dry_run,
) -> None:
    print("\n" + "=" * 105)
    print("MULTI-TF: CHAMPION V1 vs CHALLENGERS COMPARISON REPORT")
    print("=" * 105)

    print("\nALERT VOLUME PER VARIANT:")
    print(f"  {'Variant':<32} {'Total Alerts':>14}  {'No-Fwd-Data':>13}  {'Flag'}")
    print("  " + "-" * 80)
    for vid in ALL_MULTITF_VARIANTS:
        n  = signal_counts.get(vid, 0)
        nf = no_outcome_count.get(vid, 0)
        flag = "  <<< INSUFFICIENT_SAMPLE (N<15)" if n < 15 else ""
        print(f"  {vid:<32} {n:>14}  {nf:>13}{flag}")

    if dry_run or not all_outcomes:
        print("\n  [DRY-RUN or NO OUTCOMES] Outcome measurement skipped.")
        return

    parts = ["IS", "OOS"] if partition == "ALL" else [partition]
    print("\nEVIDENCE METRICS BY VARIANT x PARTITION:")
    hdr = (f"  {'Variant':<32} {'Part':>5} {'N':>5} {'E[R]':>7} {'PF':>6} {'Win%':>6} "
           f"{'CI_lo':>7} {'p-val':>7} {'+1R%':>6} {'+2R%':>6} {'Tier':<18} {'Status'}")
    print(hdr)
    print("  " + "-" * 120)
    for vid in ALL_MULTITF_VARIANTS:
        variant = registry.get_variant(vid)
        st_str  = variant.status.value if variant else "N/A"
        for part in parts:
            key = f"{vid}_{part}"
            m   = metrics_map.get(key)
            if m is None or m.sample_size == 0:
                print(f"  {vid:<32} {part:>5} {'---':>5}")
                continue
            tier_str = variant.allocation_tier.value if variant else "N/A"
            p_str = f"{m.approx_p_value:.4f}" if m.approx_p_value is not None else "N/A "
            flag  = " <<INSUFF" if m.sample_size < 15 else ""
            print(
                f"  {vid:<32} {part:>5} {m.sample_size:>5} "
                f"{m.expectancy_r:>+7.3f} {m.profit_factor:>6.2f} {m.win_rate_pct:>6.1f} "
                f"{m.ci_95_lower_r:>+7.3f} {p_str:>7} "
                f"{m.r1_hit_rate_pct:>6.1f} {m.r2_hit_rate_pct:>6.1f} "
                f"{tier_str:<18} {st_str}{flag}"
            )

    print("\nCHAMPION / CHALLENGER PROMOTION VERDICT:")
    print("  Criteria: higher E[R] (OOS preferred), higher PF, no >10% R2 regression, N>=15, CI-delta > -0.10R")
    print("  " + "-" * 80)
    winner = registry.compare_and_promote(ScannerFamily.MULTI_TF)
    if winner is not None:
        bm = winner.oos_metrics or winner.in_sample_metrics
        if bm:
            print(f"  Winning Champion: {winner.variant_id} | Status: {winner.status.value} | Tier: {winner.allocation_tier.value}")
            print(f"  E[R]: {bm.expectancy_r:+.3f}R | PF: {bm.profit_factor:.2f} | N: {bm.sample_size}")
        else:
            print(f"  Winning Champion: {winner.variant_id}")
    else:
        print("  WARNING: No champion determined.")
    print("=" * 105)


def _save_csv(all_outcomes: List[Dict[str, Any]], output_path: str) -> None:
    if not all_outcomes:
        return
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    fieldnames = list(all_outcomes[0].keys())
    with open(output_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(all_outcomes)
    print(f"\n  Raw outcomes saved: {output_path} ({len(all_outcomes):,} rows)")


def main() -> None:
    parser = argparse.ArgumentParser(description="Multi-TF Historical Replay Engine")
    parser.add_argument("--partition", default="ALL", choices=["IS", "OOS", "ALL"])
    parser.add_argument("--dry-run",     action="store_true")
    parser.add_argument("--max-symbols", type=int, default=None)
    parser.add_argument("--verbose",     action="store_true")
    parser.add_argument("--no-csv",      action="store_true")
    parser.add_argument("--step",        type=int, default=WEEKLY_STEP_BARS, help="Scan cadence step in market days")
    args = parser.parse_args()

    all_outcomes, signal_counts, no_outcome_count, funnel_per_variant = run_multitf_replay(
        partition=args.partition,
        dry_run=args.dry_run,
        max_symbols=args.max_symbols,
        verbose=args.verbose,
        step=args.step,
    )

    registry, metrics_map = _compute_and_register(all_outcomes, args.partition)

    _print_report(
        all_outcomes=all_outcomes,
        signal_counts=signal_counts,
        no_outcome_count=no_outcome_count,
        funnel_per_variant=funnel_per_variant,
        registry=registry,
        metrics_map=metrics_map,
        partition=args.partition,
        dry_run=args.dry_run,
    )

    if not args.dry_run and not args.no_csv and all_outcomes:
        _save_csv(all_outcomes, _OUTPUT_CSV)


if __name__ == "__main__":
    main()
