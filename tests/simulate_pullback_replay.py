#!/usr/bin/env python3
# =============================================================================
# tests/simulate_pullback_replay.py
# PULLBACK: CHAMPION V1 vs CHALLENGERS A/B/C/D HISTORICAL REPLAY ENGINE
# =============================================================================
#
# RULE 67 CHANGE-RATIONALE:
# This engine empirically investigates the known Pullback weakness:
#   45.8% of stopped trades subsequently recover to entry or T1.
# The research objective is to resolve:
#   BAD SIGNAL vs GOOD SIGNAL + BAD STOP vs GOOD SIGNAL + BAD ENTRY
#
# CANDIDATE STOP ARCHITECTURES TESTED:
#   1. PULLBACK_CHAMPION_V1   : Baseline (Adaptive ATR 1.8x clamped [4.5%, 7.5%])
#   2. PULLBACK_CHALL_A_1_5ATR: Tighter Stop = 1.5 x ATR14 clamped [3.5%, 6.0%]
#   3. PULLBACK_CHALL_B_1_8ATR: Moderate Stop = 1.8 x ATR14 clamped [4.5%, 7.5%]
#   4. PULLBACK_CHALL_C_2ATR_SHELF: Structural Swing Shelf Stop = min(support * 0.99, entry - 2.0*ATR) clamped [4.5%, 9.0%]
#   5. PULLBACK_CHALL_D_ADAPTIVE  : Volatility-Regime Adaptive Stop:
#                                   - Low vol (ATR < 2.5%): 1.5x ATR [3.5%, 5.5%]
#                                   - Normal vol (2.5% - 4.0%): 1.8x ATR [4.5%, 7.5%]
#                                   - High vol (> 4.0%): 2.2x ATR [6.0%, 9.5%]
#
# OUTPUT CONTRACT:
# Measures: Stop width -> stop-out rate -> post-SL recovery -> realized expectancy
#           -> average MAE -> average MFE -> +2R conversion -> capital efficiency
# Registers into ChampionChallengerRegistry under ScannerFamily.PULLBACK.
# Exports outcomes to reports/pullback_outcomes.csv.
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

from pullback_engine import evaluate_pullback_v2_symbol
from alert_quality_engine import AlertQualityEngine
from champion_challenger_registry import (
    ChampionChallengerRegistry, ScannerFamily, EvidenceMetrics, VariantStatus, ScannerVariant,
)

logging.basicConfig(
    level=logging.WARNING,
    format="%(asctime)s | %(name)s | %(levelname)s | %(message)s",
)
logger = logging.getLogger("PullbackReplay")

# =============================================================================
# Constants
# =============================================================================

_HISTORY_1D_DIR = os.path.join(_REPO_ROOT, "data", "history", "1d")
_REPORTS_DIR    = os.path.join(_REPO_ROOT, "reports")
_OUTPUT_CSV     = os.path.join(_REPORTS_DIR, "pullback_outcomes.csv")

IS_START  = "2025-07-24"
IS_END    = "2026-03-31"
OOS_START = "2026-04-01"
OOS_END   = "2026-09-04"

WEEKLY_STEP_BARS    = 5
OUTCOME_HORIZON     = 20
MIN_BARS_FOR_SIGNAL = 60
MIN_PRICE_FLOOR     = 100.0

CHAMP_ID    = "PULLBACK_CHAMPION_V1"
CHALL_A_ID  = "PULLBACK_CHALL_A_1_5ATR"
CHALL_B_ID  = "PULLBACK_CHALL_B_1_8ATR"
CHALL_C_ID  = "PULLBACK_CHALL_C_2ATR_SHELF"
CHALL_D_ID  = "PULLBACK_CHALL_D_ADAPTIVE"
CHALL_E_ID  = "PULLBACK_CHALL_E_CONFIRMED_TURN"
CHALL_F_ID  = "PULLBACK_CHALL_F_SHELF_CONFIRMED"
CHALL_G_ID  = "PULLBACK_CHALL_G_DEEP_SHELF_2_2ATR"
CHALL_H_ID  = "PULLBACK_CHALL_H_TIGHT_ENTRY_1_8ATR_CONFIRMED"
CHALL_I_ID  = "PULLBACK_CHALL_I_REGIME_SENSITIVE"

ALL_PULLBACK_VARIANTS = [
    CHAMP_ID,
    CHALL_A_ID,
    CHALL_B_ID,
    CHALL_C_ID,
    CHALL_D_ID,
    CHALL_E_ID,
    CHALL_F_ID,
    CHALL_G_ID,
    CHALL_H_ID,
    CHALL_I_ID,
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
# Pullback Variant Stop Router
# =============================================================================

def _compute_pullback_stops(
    entry: float,
    atr_14: float,
    support_level: float,
    variant_id: str,
) -> Tuple[float, float, float]:
    """
    Computes (stop_loss, target_1, rr_ratio) for a given stop architecture variant.
    Enforces natural 2.5R target geometry across all variants.
    """
    atr_pct = (atr_14 / entry) if entry > 0 else 0.028

    if variant_id in (CHAMP_ID, CHALL_B_ID):
        # Baseline / Challenger B: 1.8x ATR clamped [4.5%, 7.5%]
        raw_stop_pct = (atr_14 * 1.8) / entry
        stop_pct = max(min(raw_stop_pct, 0.075), 0.045)
        sl = round(entry * (1.0 - stop_pct), 2)

    elif variant_id == CHALL_A_ID:
        # Challenger A: 1.5x ATR tighter clamped [3.5%, 6.0%]
        raw_stop_pct = (atr_14 * 1.5) / entry
        stop_pct = max(min(raw_stop_pct, 0.060), 0.035)
        sl = round(entry * (1.0 - stop_pct), 2)

    elif variant_id in (CHALL_C_ID, CHALL_F_ID, CHALL_I_ID):
        # Challenger C, F & I: 2.0x ATR + anchored below swing shelf
        shelf_sl = min(support_level * 0.99, entry - 2.0 * atr_14)
        raw_stop_pct = (entry - shelf_sl) / entry
        stop_pct = max(min(raw_stop_pct, 0.090), 0.045)
        sl = round(entry * (1.0 - stop_pct), 2)

    elif variant_id == CHALL_G_ID:
        # Challenger G: 2.2x ATR + deep shelf breathing room
        shelf_sl = min(support_level * 0.985, entry - 2.2 * atr_14)
        raw_stop_pct = (entry - shelf_sl) / entry
        stop_pct = max(min(raw_stop_pct, 0.100), 0.050)
        sl = round(entry * (1.0 - stop_pct), 2)

    elif variant_id == CHALL_H_ID:
        # Challenger H: 1.8x ATR + tight shelf anchor
        shelf_sl = min(support_level * 0.99, entry - 1.8 * atr_14)
        raw_stop_pct = (entry - shelf_sl) / entry
        stop_pct = max(min(raw_stop_pct, 0.080), 0.040)
        sl = round(entry * (1.0 - stop_pct), 2)

    elif variant_id in (CHALL_D_ID, CHALL_E_ID):
        # Challenger D & E: Volatility-regime adaptive stop
        if atr_pct < 0.025:
            # Low volatility regime -> tighter stop
            raw_stop_pct = (atr_14 * 1.5) / entry
            stop_pct = max(min(raw_stop_pct, 0.055), 0.035)
        elif atr_pct <= 0.040:
            # Normal volatility regime -> standard stop
            raw_stop_pct = (atr_14 * 1.8) / entry
            stop_pct = max(min(raw_stop_pct, 0.075), 0.045)
        else:
            # High volatility regime -> wider breathing room
            raw_stop_pct = (atr_14 * 2.2) / entry
            stop_pct = max(min(raw_stop_pct, 0.095), 0.060)
        sl = round(entry * (1.0 - stop_pct), 2)

    else:
        # Fallback to 5% fixed stop
        sl = round(entry * 0.95, 2)

    risk = max(0.01, entry - sl)
    t1 = round(entry + (2.5 * risk), 2)
    rr = round((t1 - entry) / risk, 2)
    return sl, t1, rr


def _eval_pullback_variants(
    symbol: str,
    df_cut: pd.DataFrame,
    macro_regime: str = "BULL",
) -> Dict[str, Optional[Dict[str, Any]]]:
    """
    Evaluates Pullback V2 core setup and routes to all 10 stop-loss architecture and entry variants.
    """
    results: Dict[str, Optional[Dict[str, Any]]] = {vid: None for vid in ALL_PULLBACK_VARIANTS}

    try:
        res = evaluate_pullback_v2_symbol(symbol=symbol, df=df_cut)
    except Exception as exc:
        logger.debug("Pullback eval error %s: %s", symbol, exc)
        return results

    if res.get("state") != "CONFIRMED":
        return results

    entry = float(res.get("entry_price", df_cut["Close"].iloc[-1]))
    atr_20 = float(res.get("atr_20", entry * 0.028))
    support_level = float(res.get("support_level", entry * 0.96))
    score = float(res.get("score", 70.0))
    support_type = res.get("support_type", "STRUCTURAL_SWING_LOW")

    # Check candle turn-up confirmation for Challengers E, F, G, H, I
    curr_close = float(df_cut["Close"].iloc[-1])
    curr_open = float(df_cut["Open"].iloc[-1])
    prior_low = float(df_cut["Low"].iloc[-2]) if len(df_cut) >= 2 else curr_close
    is_turn_up = (curr_close >= curr_open) and (curr_close > prior_low)

    for vid in ALL_PULLBACK_VARIANTS:
        if vid in (CHALL_E_ID, CHALL_F_ID, CHALL_G_ID, CHALL_H_ID, CHALL_I_ID) and not is_turn_up:
            continue  # Reversal not confirmed; avoid buying a falling knife candle

        if vid == CHALL_I_ID and macro_regime == "BEAR":
            continue  # Reject in bear regime

        sl, t1, rr = _compute_pullback_stops(
            entry=entry,
            atr_14=atr_20,
            support_level=support_level,
            variant_id=vid,
        )
        results[vid] = {
            "variant_id":    vid,
            "entry_price":   entry,
            "stop_loss":     sl,
            "target_1":      t1,
            "rr_ratio":      rr,
            "score":         score,
            "support_type":  support_type,
            "support_level": support_level,
            "stop_width_pct": round((entry - sl) / entry * 100.0, 2),
            "stage":         "CONFIRMED_PULLBACK",
            "engine_path":   "PULLBACK_V2",
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
            target_2=None, price_df=fwd_df, scanner="PULLBACK",
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
        "support_type": signal.get("support_type", ""),
        "stop_width_pct": signal.get("stop_width_pct", 0.0),
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

def run_pullback_replay(
    partition: str = "ALL",
    dry_run: bool = False,
    max_symbols: Optional[int] = None,
    verbose: bool = False,
    step: int = WEEKLY_STEP_BARS,
) -> Tuple[List[Dict[str, Any]], Dict[str, int], Dict[str, int], Dict[str, Dict[str, int]]]:
    print("\n" + "=" * 90)
    print("PULLBACK: CHAMPION V1 vs CHALLENGERS A/B/C/D HISTORICAL REPLAY ENGINE")
    print("=" * 90)
    print(f"   Partition     : {partition}  |  Dry-run: {dry_run}")
    print(f"   IS  window    : {IS_START} -> {IS_END}")
    print(f"   OOS window    : {OOS_START} -> {OOS_END}")
    print(f"   Scan step     : every {step} bars  |  Outcome horizon: {OUTCOME_HORIZON} bars")
    print("=" * 90, flush=True)

    all_dfs = _load_all_symbols(_HISTORY_1D_DIR)
    if max_symbols:
        keys = sorted(all_dfs.keys())[:max_symbols]
        all_dfs = {k: all_dfs[k] for k in keys}
        print(f"   [DEV] Capped to {len(all_dfs)} symbols.", flush=True)

    scan_dates = _build_scan_dates(all_dfs, partition, step=step)
    print(f"   Scan dates    : {len(scan_dates)} ({scan_dates[0].date()} -> {scan_dates[-1].date()})\n", flush=True)

    all_outcomes:    List[Dict[str, Any]] = []
    signal_counts    = defaultdict(int)
    no_outcome_count = defaultdict(int)
    funnel_per_variant: Dict[str, Dict[str, int]] = {vid: defaultdict(int) for vid in ALL_PULLBACK_VARIANTS}

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
            sigs = _eval_pullback_variants(sym, df_cut, macro_regime=regime)

            for vid in ALL_PULLBACK_VARIANTS:
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
                        print(f"  [{vid}] {sym} @ {scan_ts.date()} entry={sig['entry_price']:.2f} SL={sig['stop_loss']:.2f}")

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

    for vid in ALL_PULLBACK_VARIANTS:
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
            if registry.get_variant(vid) is None:
                registry.register_variant(
                    ScannerVariant(
                        variant_id=vid,
                        scanner_family=ScannerFamily.PULLBACK,
                        description=f"Pullback variant {vid}",
                        parameters={},
                        status=VariantStatus.CHAMPION if vid == CHAMP_ID else VariantStatus.CHALLENGER,
                    )
                )
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
    print("PULLBACK: CHAMPION V1 vs CHALLENGERS COMPARISON REPORT")
    print("=" * 105)

    print("\nALERT VOLUME PER VARIANT:")
    print(f"  {'Variant':<32} {'Total Alerts':>14}  {'No-Fwd-Data':>13}  {'Flag'}")
    print("  " + "-" * 80)
    for vid in ALL_PULLBACK_VARIANTS:
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
    for vid in ALL_PULLBACK_VARIANTS:
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
    winner = registry.compare_and_promote(ScannerFamily.PULLBACK)
    if winner is not None:
        bm = winner.oos_metrics or winner.in_sample_metrics
        if bm:
            print(f"  Winning Champion: {winner.variant_id} | Status: {winner.status.value} | Tier: {winner.allocation_tier.value}")
            print(f"  E[R]: {bm.expectancy_r:+.3f}R | PF: {bm.profit_factor:.2f} | N: {bm.sample_size}")
        else:
            print(f"  Winning Champion: {winner.variant_id}")
    else:
        print("  WARNING: No champion determined.")

    print("\nSTOP-LOSS ARCHITECTURE DIAGNOSTICS:")
    print("  Evaluating Stop Width vs Stop-Out Rate vs Post-SL Recovery vs Realized Expectancy:")
    print(f"  {'Variant':<32} {'Avg Stop%':>10} {'SL-Hit%':>9} {'->Entry%':>10} {'->T1%':>8} {'Avg MAE':>9} {'Avg MFE':>9}")
    print("  " + "-" * 95)
    df_all = pd.DataFrame(all_outcomes)
    for vid in ALL_PULLBACK_VARIANTS:
        df_v = df_all[df_all["variant_id"] == vid]
        if df_v.empty:
            continue
        avg_stop = df_v["stop_width_pct"].mean()
        sl_hits = df_v["exit_reason"].isin(["SL_HIT", "SAME_BAR_CONFLICT_SL"])
        sl_pct = sl_hits.mean() * 100.0
        stopped = df_v[sl_hits]
        re = stopped["post_sl_recovered_entry"].mean() * 100.0 if not stopped.empty else 0.0
        rt = stopped["post_sl_recovered_t1"].mean() * 100.0 if not stopped.empty else 0.0
        mae = df_v["max_adverse_excursion_r"].mean()
        mfe = df_v["max_favorable_excursion_r"].mean()
        print(f"  {vid:<32} {avg_stop:>9.2f}% {sl_pct:>8.1f}% {re:>9.1f}% {rt:>7.1f}% {mae:>+8.2f}R {mfe:>+8.2f}R")
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
    parser = argparse.ArgumentParser(description="Pullback Stop-Loss Historical Replay Engine")
    parser.add_argument("--partition", default="ALL", choices=["IS", "OOS", "ALL"])
    parser.add_argument("--dry-run",     action="store_true")
    parser.add_argument("--max-symbols", type=int, default=None)
    parser.add_argument("--verbose",     action="store_true")
    parser.add_argument("--no-csv",      action="store_true")
    parser.add_argument("--step",        type=int, default=WEEKLY_STEP_BARS, help="Cadence step in bars")
    args = parser.parse_args()

    all_outcomes, signal_counts, no_outcome_count, funnel_per_variant = run_pullback_replay(
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
