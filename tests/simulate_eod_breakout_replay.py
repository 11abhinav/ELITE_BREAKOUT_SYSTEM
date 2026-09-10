#!/usr/bin/env python3
# =============================================================================
# tests/simulate_eod_breakout_replay.py
# EOD BREAKOUT: CHAMPION V1 vs CHALLENGERS A/B/C/D/E HISTORICAL REPLAY ENGINE
# =============================================================================
#
# RULE 67 CHANGE-RATIONALE:
# This engine empirically evaluates EOD Breakout variants under the institutional
# Champion/Challenger doctrine. It investigates:
#   1. Breakout thrust (does requiring > 0.50x or > 0.30x ATR eliminate false breaks?)
#   2. Score gate (does raising the minimum score threshold to 80 improve expectancy?)
#   3. Pivot overextension (does capping extension at <= 2.5% prevent chasing late moves?)
#   4. Composite filter (balanced thrust + extension + score threshold)
#
# REPLAY METHODOLOGY
# ------------------
# 1. Load all 1D parquet files from data/history/1d/ (882 symbols)
# 2. Build majority-vote weekly scan grid (56 dates, 2025-07-24 -> 2026-08-31)
# 3. For each date x symbol:
#    a. Slice df to bars <= scan_date (zero-lookahead)
#    b. Evaluate eod_v2_engine.evaluate_eod_v2_symbol()
#    c. Route alerts to Champion V1 and Challengers A/B/C/D/E
# 4. Measure forward outcomes via AlertQualityEngine (20-bar horizon, data > scan_date)
# 5. Compute EvidenceMetrics, register into ChampionChallengerRegistry
# 6. Run compare_and_promote(ScannerFamily.EOD_BREAKOUT)
# 7. Print comparison report and save CSV to reports/eod_breakout_outcomes.csv
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

from eod_v2_engine import evaluate_eod_v2_symbol
from alert_quality_engine import AlertQualityEngine
from champion_challenger_registry import (
    ChampionChallengerRegistry, ScannerFamily, EvidenceMetrics,
)

logging.basicConfig(
    level=logging.WARNING,
    format="%(asctime)s | %(name)s | %(levelname)s | %(message)s",
)
logger = logging.getLogger("EODReplay")

# =============================================================================
# Constants
# =============================================================================

_HISTORY_1D_DIR = os.path.join(_REPO_ROOT, "data", "history", "1d")
_REPORTS_DIR    = os.path.join(_REPO_ROOT, "reports")
_OUTPUT_CSV     = os.path.join(_REPORTS_DIR, "eod_breakout_outcomes.csv")

IS_START  = "2025-07-24"
IS_END    = "2026-03-31"
OOS_START = "2026-04-01"
OOS_END   = "2026-09-04"

WEEKLY_STEP_BARS    = 5
OUTCOME_HORIZON     = 20
MIN_BARS_FOR_SIGNAL = 60
MIN_PRICE_FLOOR     = 100.0

EOD_CHAMP_ID    = "EOD_CHAMPION_V1"
EOD_CHALL_A_ID  = "EOD_CHALL_A_THRUST"
EOD_CHALL_B_ID  = "EOD_CHALL_B_SCORE80"
EOD_CHALL_C_ID  = "EOD_CHALL_C_EXTENSION"
EOD_CHALL_D_ID  = "EOD_CHALL_D_THRUST_030"
EOD_CHALL_E_ID  = "EOD_CHALL_E_COMBINED"
EOD_CHALL_F_ID  = "EOD_CHALL_F_CALIBRATED_SCORE"
EOD_CHALL_G_ID  = "EOD_CHALL_G_SWEET_SPOT"
EOD_CHALL_H_ID  = "EOD_CHALL_H_THRUST_CALIBRATED"
EOD_CHALL_I_ID  = "EOD_CHALL_I_MONOTONIC_RUBRIC"

ALL_EOD_VARIANTS = [
    EOD_CHAMP_ID,
    EOD_CHALL_A_ID,
    EOD_CHALL_B_ID,
    EOD_CHALL_C_ID,
    EOD_CHALL_D_ID,
    EOD_CHALL_E_ID,
    EOD_CHALL_F_ID,
    EOD_CHALL_G_ID,
    EOD_CHALL_H_ID,
    EOD_CHALL_I_ID,
]

# =============================================================================
# Data Loading & Calendar
# =============================================================================

def _load_all_symbols(base_dir: str) -> Dict[str, pd.DataFrame]:
    symbol_dfs: Dict[str, pd.DataFrame] = {}
    parquet_files = sorted(glob.glob(os.path.join(base_dir, "*.parquet")))
    print(f"   Loading {len(parquet_files)} parquet files ...", flush=True)

    for fpath in parquet_files:
        sym = os.path.basename(fpath).replace(".parquet", "")
        try:
            df = pd.read_parquet(fpath)
            if df is None or df.empty or len(df) < 10:
                continue
            if not isinstance(df.index, pd.DatetimeIndex):
                df.index = pd.to_datetime(df.index)
            if df.index.tz is None:
                df.index = df.index.tz_localize("Asia/Kolkata")
            else:
                df.index = df.index.tz_convert("Asia/Kolkata")
            df = df.sort_index()
            for col in ("Open", "High", "Low", "Close", "Volume"):
                if col not in df.columns:
                    raise ValueError(f"Missing column {col}")
            symbol_dfs[sym] = df
        except Exception as exc:
            logger.debug("Skipping %s: %s", sym, exc)

    print(f"   Loaded {len(symbol_dfs)} valid symbols.", flush=True)
    return symbol_dfs


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

    if not market_days:
        raise RuntimeError("No market trading days found from symbol data.")

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
# EOD Variant Router
# =============================================================================

def _eval_eod_variants(
    symbol: str,
    df_cut: pd.DataFrame,
    macro_regime: str = "NEUTRAL",
) -> Dict[str, Optional[Dict[str, Any]]]:
    """
    Evaluates EOD Breakout Champion and Challengers.
    Returns {variant_id: signal_dict | None}.
    """
    results: Dict[str, Optional[Dict[str, Any]]] = {vid: None for vid in ALL_EOD_VARIANTS}

    try:
        res = evaluate_eod_v2_symbol(symbol=symbol, df=df_cut)
    except Exception as exc:
        logger.debug("EOD eval error %s: %s", symbol, exc)
        return results

    if res.get("state") != "CONFIRMED":
        return results

    close = float(df_cut["Close"].iloc[-1])
    prior_20d_high = float(res.get("prior_20d_high", 0.0))
    if prior_20d_high <= 0:
        return results

    # ATR20
    true_ranges = (df_cut["High"] - df_cut["Low"]).abs()
    atr20 = float(true_ranges.iloc[-20:].mean()) if len(true_ranges) >= 20 else float(true_ranges.mean())
    atr20 = max(0.01, atr20)

    # Metrics
    breakout_thrust_atr = (close - prior_20d_high) / atr20
    pivot_extension_pct = (close - prior_20d_high) / prior_20d_high * 100.0
    quality_score = float(res.get("quality_score", 0.0))

    # Stop Loss and Target
    prior = df_cut.iloc[:-1]
    base_low = float(prior["Low"].iloc[-21:].min()) if len(prior) >= 21 else float(prior["Low"].min())
    stop_loss = round(max(prior_20d_high * 0.96, base_low * 0.995, close - 2.0 * atr20), 2)
    entry_price = round(close, 2)
    risk_dist = max(0.01, entry_price - stop_loss)
    target_1 = round(entry_price + 2.0 * risk_dist, 2)
    rr_ratio = round((target_1 - entry_price) / risk_dist, 2)

    base_sig = {
        "entry_price": entry_price,
        "stop_loss": stop_loss,
        "target_1": target_1,
        "rr_ratio": rr_ratio,
        "score": quality_score,
        "breakout_thrust_atr": round(breakout_thrust_atr, 2),
        "pivot_extension_pct": round(pivot_extension_pct, 2),
        "stage": "CONFIRMED_BREAKOUT",
        "engine_path": "EOD_V2",
    }

    # Champion V1: Standard confirmed breakout
    results[EOD_CHAMP_ID] = {**base_sig, "variant_id": EOD_CHAMP_ID}

    # Challenger A: Thrust > 0.50 x ATR
    if breakout_thrust_atr >= 0.50:
        results[EOD_CHALL_A_ID] = {**base_sig, "variant_id": EOD_CHALL_A_ID}

    # Challenger B: Score >= 80
    if quality_score >= 80.0:
        results[EOD_CHALL_B_ID] = {**base_sig, "variant_id": EOD_CHALL_B_ID}

    # Challenger C: Max Extension <= 2.5%
    if pivot_extension_pct <= 2.5:
        results[EOD_CHALL_C_ID] = {**base_sig, "variant_id": EOD_CHALL_C_ID}

    # Challenger D: Moderate Thrust > 0.30 x ATR
    if breakout_thrust_atr >= 0.30:
        results[EOD_CHALL_D_ID] = {**base_sig, "variant_id": EOD_CHALL_D_ID}

    # Challenger E: Combined (Thrust >= 0.30 ATR, Extension <= 2.5%, Score >= 75)
    if breakout_thrust_atr >= 0.30 and pivot_extension_pct <= 2.5 and quality_score >= 75.0:
        results[EOD_CHALL_E_ID] = {**base_sig, "variant_id": EOD_CHALL_E_ID}

    # Challenger F: Calibrated score with late-extension penalty (>2.5% past pivot = -15 pts)
    score_cal = (quality_score - 15.0) if pivot_extension_pct > 2.5 else quality_score
    if score_cal >= 70.0:
        results[EOD_CHALL_F_ID] = {**base_sig, "score": score_cal, "variant_id": EOD_CHALL_F_ID}

    # Challenger G: Sweet-spot gate (Score 70-79 or 90-100; or 80-89 only if extension <= 2.0%)
    sweet_spot = (70.0 <= quality_score < 80.0) or (quality_score >= 90.0) or (80.0 <= quality_score < 90.0 and pivot_extension_pct <= 2.0)
    if sweet_spot:
        results[EOD_CHALL_G_ID] = {**base_sig, "variant_id": EOD_CHALL_G_ID}

    # Challenger H: Thrust >= 0.40 ATR + Extension <= 2.5%
    if breakout_thrust_atr >= 0.40 and pivot_extension_pct <= 2.5:
        results[EOD_CHALL_H_ID] = {**base_sig, "variant_id": EOD_CHALL_H_ID}

    # Challenger I: Monotonic Rubric
    # Direct formula-level recalibration penalizing pivot extension and rewarding tight thrust
    score_mono = quality_score
    if pivot_extension_pct <= 1.0:
        score_mono += 5.0
    elif pivot_extension_pct > 3.0:
        score_mono -= 25.0
    elif pivot_extension_pct > 2.0:
        score_mono -= 15.0

    if breakout_thrust_atr >= 0.40:
        score_mono += 5.0
    
    score_mono = round(max(0.0, min(100.0, score_mono)), 1)
    if score_mono >= 70.0:
        results[EOD_CHALL_I_ID] = {**base_sig, "score": score_mono, "variant_id": EOD_CHALL_I_ID}

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
            target_2=None, price_df=fwd_df, scanner="EOD",
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
        "thrust_atr":  signal.get("breakout_thrust_atr", 0.0),
        "ext_pct":     signal.get("pivot_extension_pct", 0.0),
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
# Replay Loop
# =============================================================================

def run_eod_replay(
    partition: str = "ALL",
    dry_run: bool = False,
    max_symbols: Optional[int] = None,
    verbose: bool = False,
) -> Tuple[List[Dict[str, Any]], Dict[str, int], Dict[str, int], Dict[str, Dict[str, int]]]:
    print("\n" + "=" * 90)
    print("EOD BREAKOUT: CHAMPION V1 vs CHALLENGERS A/B/C/D/E HISTORICAL REPLAY ENGINE")
    print("=" * 90)
    print(f"   Partition     : {partition}  |  Dry-run: {dry_run}")
    print(f"   IS  window    : {IS_START} -> {IS_END}")
    print(f"   OOS window    : {OOS_START} -> {OOS_END}")
    print(f"   Scan step     : every {WEEKLY_STEP_BARS} bars  |  Outcome horizon: {OUTCOME_HORIZON} bars")
    print("=" * 90, flush=True)

    all_dfs = _load_all_symbols(_HISTORY_1D_DIR)
    if max_symbols:
        keys = sorted(all_dfs.keys())[:max_symbols]
        all_dfs = {k: all_dfs[k] for k in keys}
        print(f"   [DEV] Capped to {len(all_dfs)} symbols.", flush=True)

    scan_dates = _build_scan_dates(all_dfs, partition)
    print(f"   Scan dates    : {len(scan_dates)} ({scan_dates[0].date()} -> {scan_dates[-1].date()})\n", flush=True)

    all_outcomes:    List[Dict[str, Any]] = []
    signal_counts    = defaultdict(int)
    no_outcome_count = defaultdict(int)
    funnel_per_variant: Dict[str, Dict[str, int]] = {vid: defaultdict(int) for vid in ALL_EOD_VARIANTS}

    total = len(all_dfs) * len(scan_dates)
    print(f"   Running {total:,} evaluations ({len(all_dfs)} symbols x {len(scan_dates)} dates) ...", flush=True)

    dot_ctr = 0
    for scan_ts in scan_dates:
        for sym, df_full in all_dfs.items():
            pos = df_full.index.searchsorted(scan_ts, side="right")
            if pos < MIN_BARS_FOR_SIGNAL:
                continue
            df_cut = df_full.iloc[:pos]
            if float(df_cut["Close"].iloc[-1]) < MIN_PRICE_FLOOR:
                continue

            regime = _infer_regime(df_cut)
            sigs = _eval_eod_variants(sym, df_cut, macro_regime=regime)

            for vid in ALL_EOD_VARIANTS:
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

    for vid in ALL_EOD_VARIANTS:
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
    print("\n" + "=" * 100)
    print("EOD BREAKOUT: CHAMPION V1 vs CHALLENGERS COMPARISON REPORT")
    print("=" * 100)

    print("\nALERT VOLUME PER VARIANT:")
    print(f"  {'Variant':<30} {'Total Alerts':>14}  {'No-Fwd-Data':>13}  {'Flag'}")
    print("  " + "-" * 75)
    for vid in ALL_EOD_VARIANTS:
        n  = signal_counts.get(vid, 0)
        nf = no_outcome_count.get(vid, 0)
        flag = "  <<< INSUFFICIENT_SAMPLE (N<15)" if n < 15 else ""
        print(f"  {vid:<30} {n:>14}  {nf:>13}{flag}")

    if dry_run or not all_outcomes:
        print("\n  [DRY-RUN or NO OUTCOMES] Outcome measurement skipped.")
        return

    parts = ["IS", "OOS"] if partition == "ALL" else [partition]
    print("\nEVIDENCE METRICS BY VARIANT x PARTITION:")
    hdr = (f"  {'Variant':<30} {'Part':>5} {'N':>5} {'E[R]':>7} {'PF':>6} {'Win%':>6} "
           f"{'CI_lo':>7} {'p-val':>7} {'+1R%':>6} {'+2R%':>6} {'Tier':<18} {'Status'}")
    print(hdr)
    print("  " + "-" * 115)
    for vid in ALL_EOD_VARIANTS:
        variant = registry.get_variant(vid)
        st_str  = variant.status.value if variant else "N/A"
        for part in parts:
            key = f"{vid}_{part}"
            m   = metrics_map.get(key)
            if m is None or m.sample_size == 0:
                print(f"  {vid:<30} {part:>5} {'---':>5}")
                continue
            tier_str = variant.allocation_tier.value if variant else "N/A"
            p_str = f"{m.approx_p_value:.4f}" if m.approx_p_value is not None else "N/A "
            flag  = " <<INSUFF" if m.sample_size < 15 else ""
            print(
                f"  {vid:<30} {part:>5} {m.sample_size:>5} "
                f"{m.expectancy_r:>+7.3f} {m.profit_factor:>6.2f} {m.win_rate_pct:>6.1f} "
                f"{m.ci_95_lower_r:>+7.3f} {p_str:>7} "
                f"{m.r1_hit_rate_pct:>6.1f} {m.r2_hit_rate_pct:>6.1f} "
                f"{tier_str:<18} {st_str}{flag}"
            )

    print("\nCHAMPION / CHALLENGER PROMOTION VERDICT:")
    print("  Criteria: higher E[R] (OOS preferred), higher PF, no >10% R2 regression, N>=15, CI-delta > -0.10R")
    print("  " + "-" * 75)
    winner = registry.compare_and_promote(ScannerFamily.EOD_BREAKOUT)
    if winner is not None:
        bm = winner.oos_metrics or winner.in_sample_metrics
        if bm:
            print(f"  Winning Champion: {winner.variant_id} | Status: {winner.status.value} | Tier: {winner.allocation_tier.value}")
            print(f"  E[R]: {bm.expectancy_r:+.3f}R | PF: {bm.profit_factor:.2f} | N: {bm.sample_size}")
        else:
            print(f"  Winning Champion: {winner.variant_id}")
    else:
        print("  WARNING: No champion determined.")

    print("\nPOST-SL RECOVERY (% stopped trades recovering to entry / T1):")
    print(f"  {'Variant':<30} {'->Entry%':>10}  {'->T1%':>8}  {'N stopped':>10}")
    print("  " + "-" * 65)
    df_all = pd.DataFrame(all_outcomes)
    for vid in ALL_EOD_VARIANTS:
        df_v = df_all[df_all["variant_id"] == vid]
        if df_v.empty:
            continue
        stopped = df_v[df_v["exit_reason"].isin(["SL_HIT", "SAME_BAR_CONFLICT_SL"])]
        if stopped.empty:
            print(f"  {vid:<30}  {0.0:>9.1f}%  {0.0:>7.1f}%  {0:>10}")
            continue
        re = stopped["post_sl_recovered_entry"].mean() * 100.0
        rt = stopped["post_sl_recovered_t1"].mean() * 100.0
        print(f"  {vid:<30}  {re:>9.1f}%  {rt:>7.1f}%  {len(stopped):>10}")
    print("=" * 100)


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
    parser = argparse.ArgumentParser(description="EOD Breakout Historical Replay Engine")
    parser.add_argument("--partition", default="ALL", choices=["IS", "OOS", "ALL"])
    parser.add_argument("--dry-run",     action="store_true")
    parser.add_argument("--max-symbols", type=int, default=None)
    parser.add_argument("--verbose",     action="store_true")
    parser.add_argument("--no-csv",      action="store_true")
    args = parser.parse_args()

    all_outcomes, signal_counts, no_outcome_count, funnel_per_variant = run_eod_replay(
        partition=args.partition,
        dry_run=args.dry_run,
        max_symbols=args.max_symbols,
        verbose=args.verbose,
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
