#!/usr/bin/env python3
# =============================================================================
# tests/simulate_vcp_replay.py
# ACCUMULATION / VCP: CHAMPION V1 vs CHALLENGERS A/B/C HISTORICAL REPLAY ENGINE
# =============================================================================
#
# RULE 67 CHANGE-RATIONALE:
# VCP is the system's strongest baseline alpha engine (baseline E[R]=+0.57R, PF=2.35).
# The master directive explicitly mandates:
#   "Improve the strong edge without filtering away the exceptional winners.
#    For asymmetric scanners such as Reversal and VCP, explicitly preserve and
#    evaluate the right tail (+3R, +5R, +10R). Do not destroy large winners
#    merely to improve win rate."
#
# CANDIDATE CHALLENGERS TESTED:
#   1. VCP_CHAMPION_V1        : Baseline Accumulation V2 Engine (CONFIRMED breakout)
#   2. VCP_CHALL_A_VOL_DRYUP  : Volume dry-up gate: requires pre-breakout contraction
#                               volume <= 0.75x SMA20 (institutional quiet absorption)
#   3. VCP_CHALL_B_TIGHTNESS  : Tight base gate: T3 contraction depth <= 6.0%
#                               (stricter volatility contraction)
#   4. VCP_CHALL_C_COMBINED   : Combined: dry-up <= 0.75x + T3 <= 6.0% + score >= 80
#                               + breakout thrust vol_ratio >= 1.5x
#
# RIGHT-TAIL MEASUREMENT CONTRACT:
# Explicitly tracks +1R, +1.5R, +2R, +3R, +5R, and +10R conversion rates.
# Registers into ChampionChallengerRegistry under ScannerFamily.ACCUMULATION_VCP.
# Exports outcomes to reports/vcp_outcomes.csv.
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

from accumulation_engine import evaluate_accumulation_v2_symbol
from alert_quality_engine import AlertQualityEngine
from champion_challenger_registry import (
    ChampionChallengerRegistry, ScannerFamily, EvidenceMetrics, VariantStatus, ScannerVariant,
)

logging.basicConfig(
    level=logging.WARNING,
    format="%(asctime)s | %(name)s | %(levelname)s | %(message)s",
)
logger = logging.getLogger("VCPReplay")

# =============================================================================
# Constants
# =============================================================================

_HISTORY_1D_DIR = os.path.join(_REPO_ROOT, "data", "history", "1d")
_REPORTS_DIR    = os.path.join(_REPO_ROOT, "reports")
_OUTPUT_CSV     = os.path.join(_REPORTS_DIR, "vcp_outcomes.csv")

IS_START  = "2025-07-24"
IS_END    = "2026-03-31"
OOS_START = "2026-04-01"
OOS_END   = "2026-09-04"

WEEKLY_STEP_BARS    = 5
OUTCOME_HORIZON     = 20
MIN_BARS_FOR_SIGNAL = 60
MIN_PRICE_FLOOR     = 100.0

VCP_CHAMP_ID    = "VCP_CHAMPION_V1"
VCP_CHALL_A_ID  = "VCP_CHALL_A_VOL_DRYUP"
VCP_CHALL_B_ID  = "VCP_CHALL_B_TIGHTNESS"
VCP_CHALL_C_ID  = "VCP_CHALL_C_COMBINED"
VCP_CHALL_D_ID  = "VCP_CHALL_D_T3_MODERATE"
VCP_CHALL_E_ID  = "VCP_CHALL_E_EXPANSION_THRUST"
VCP_CHALL_F_ID  = "VCP_CHALL_F_REGIME_ALIGNED"
VCP_CHALL_G_ID  = "VCP_CHALL_G_RUNNER_EXPANSION"
VCP_CHALL_H_ID  = "VCP_CHALL_H_TIGHT_THRUST"
VCP_CHALL_I_ID  = "VCP_CHALL_I_RUNNER_SCALE_OUT"
VCP_CHALL_J_ID  = "VCP_CHALL_J_CHANDELIER_TRAIL"

ALL_VCP_VARIANTS = [
    VCP_CHAMP_ID,
    VCP_CHALL_A_ID,
    VCP_CHALL_B_ID,
    VCP_CHALL_C_ID,
    VCP_CHALL_D_ID,
    VCP_CHALL_E_ID,
    VCP_CHALL_F_ID,
    VCP_CHALL_G_ID,
    VCP_CHALL_H_ID,
    VCP_CHALL_I_ID,
    VCP_CHALL_J_ID,
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
# Accumulation / VCP Variant Router
# =============================================================================

def _eval_vcp_variants(
    symbol: str,
    df_cut: pd.DataFrame,
    macro_regime: str = "NEUTRAL",
) -> Dict[str, Optional[Dict[str, Any]]]:
    """
    Evaluates Accumulation / VCP Champion and Challengers.
    Returns {variant_id: signal_dict | None}.
    """
    results: Dict[str, Optional[Dict[str, Any]]] = {vid: None for vid in ALL_VCP_VARIANTS}

    try:
        res = evaluate_accumulation_v2_symbol(symbol=symbol, df=df_cut)
    except Exception as exc:
        logger.debug("VCP eval error %s: %s", symbol, exc)
        return results

    if res.get("state") != "CONFIRMED":
        return results

    close = float(df_cut["Close"].iloc[-1])
    resistance_level = float(res.get("breakout_level", close))
    atr20 = float(res.get("atr_20", close * 0.025))
    score = float(res.get("score", 70.0))
    vol_ratio = float(res.get("vol_ratio", 1.0))
    acc_class = str(res.get("accumulation_class", "UNKNOWN"))

    # Contraction depths (T1, T2, T3)
    t1_depth = float((df_cut["High"].iloc[-30:-20].max() - df_cut["Low"].iloc[-30:-20].min()) / df_cut["High"].iloc[-30:-20].max() * 100.0) if len(df_cut) >= 30 else 15.0
    t2_depth = float((df_cut["High"].iloc[-20:-10].max() - df_cut["Low"].iloc[-20:-10].min()) / df_cut["High"].iloc[-20:-10].max() * 100.0) if len(df_cut) >= 20 else 10.0
    t3_depth = float((df_cut["High"].iloc[-10:-1].max() - df_cut["Low"].iloc[-10:-1].min()) / df_cut["High"].iloc[-10:-1].max() * 100.0) if len(df_cut) >= 10 else 5.0

    # Volume dry-up ratio during prior 5 bars before breakout
    avg_vol_20 = float(df_cut["Volume"].iloc[-21:-1].mean()) if len(df_cut) >= 21 else float(df_cut["Volume"].mean())
    prior_5d_vol_avg = float(df_cut["Volume"].iloc[-6:-1].mean()) if len(df_cut) >= 6 else avg_vol_20
    pre_breakout_vol_dryup_ratio = (prior_5d_vol_avg / avg_vol_20) if avg_vol_20 > 0 else 1.0

    # Stop Loss & Target Geometry
    support_level = float(df_cut["Low"].iloc[-15:-1].min()) if len(df_cut) >= 15 else float(df_cut["Low"].min())
    stop_loss = round(max(0.01, support_level - 0.5 * atr20), 2)
    entry_price = round(close, 2)
    risk_dist = max(0.01, entry_price - stop_loss)
    target_1 = round(entry_price + (2.5 * risk_dist), 2)
    rr_ratio = round((target_1 - entry_price) / risk_dist, 2)

    base_sig = {
        "entry_price": entry_price,
        "stop_loss": stop_loss,
        "target_1": target_1,
        "rr_ratio": rr_ratio,
        "score": score,
        "vol_ratio": vol_ratio,
        "t3_depth": round(t3_depth, 2),
        "pre_breakout_vol_dryup_ratio": round(pre_breakout_vol_dryup_ratio, 2),
        "accumulation_class": acc_class,
        "stage": "CONFIRMED_ACCUMULATION",
        "engine_path": "ACCUMULATION_V2",
    }

    # 1. Champion V1: Baseline confirmed
    results[VCP_CHAMP_ID] = {**base_sig, "variant_id": VCP_CHAMP_ID}

    # 2. Challenger A: Volume Dry-Up Gate (pre-breakout volume <= 0.80x SMA20)
    if pre_breakout_vol_dryup_ratio <= 0.80:
        results[VCP_CHALL_A_ID] = {**base_sig, "variant_id": VCP_CHALL_A_ID}

    # 3. Challenger B: Base Tightness Gate (T3 depth <= 6.0%)
    if t3_depth <= 6.0:
        results[VCP_CHALL_B_ID] = {**base_sig, "variant_id": VCP_CHALL_B_ID}

    # 4. Challenger C: Combined (Dry-Up <= 0.80x + Tightness <= 6.0% + Score >= 80 + Thrust Vol >= 1.5x)
    if (pre_breakout_vol_dryup_ratio <= 0.80 and t3_depth <= 6.0
            and score >= 80.0 and vol_ratio >= 1.5):
        results[VCP_CHALL_C_ID] = {**base_sig, "variant_id": VCP_CHALL_C_ID}

    # 5. Challenger D: Moderate Contraction Tightness (T3 <= 8.0%)
    if t3_depth <= 8.0:
        results[VCP_CHALL_D_ID] = {**base_sig, "variant_id": VCP_CHALL_D_ID}

    # 6. Challenger E: Moderate Tightness (T3 <= 8.0%) + Breakout Thrust Volume >= 1.5x
    if t3_depth <= 8.0 and vol_ratio >= 1.5:
        results[VCP_CHALL_E_ID] = {**base_sig, "variant_id": VCP_CHALL_E_ID}

    # 7. Challenger F: Macro Regime Filter (BULL and NEUTRAL only)
    if macro_regime in ("BULL", "NEUTRAL"):
        results[VCP_CHALL_F_ID] = {**base_sig, "variant_id": VCP_CHALL_F_ID}

    # 8. Challenger G: Runner Expansion (Moderate T3 <= 8.0% + Volume Thrust >= 1.8x + Bull/Neutral Regime)
    if t3_depth <= 8.0 and vol_ratio >= 1.8 and macro_regime in ("BULL", "NEUTRAL"):
        results[VCP_CHALL_G_ID] = {**base_sig, "variant_id": VCP_CHALL_G_ID}

    # 9. Challenger H: Tight Thrust (T3 <= 7.0% + Thrust >= 1.5x + Score >= 75)
    if t3_depth <= 7.0 and vol_ratio >= 1.5 and score >= 75.0:
        results[VCP_CHALL_H_ID] = {**base_sig, "variant_id": VCP_CHALL_H_ID}

    # 10. Challenger I: Runner Scale-Out (Moderate T3 <= 8.0% + Volume Thrust >= 1.8x + Bull/Neutral Regime)
    if t3_depth <= 8.0 and vol_ratio >= 1.8 and macro_regime in ("BULL", "NEUTRAL"):
        results[VCP_CHALL_I_ID] = {**base_sig, "variant_id": VCP_CHALL_I_ID}

    # 11. Challenger J: Chandelier Trail (Moderate T3 <= 8.0% + Volume Thrust >= 1.8x + Bull/Neutral Regime)
    if t3_depth <= 8.0 and vol_ratio >= 1.8 and macro_regime in ("BULL", "NEUTRAL"):
        results[VCP_CHALL_J_ID] = {**base_sig, "variant_id": VCP_CHALL_J_ID}

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
            target_2=None, price_df=fwd_df, scanner="ACCUMULATION_VCP",
        )
    except Exception as exc:
        logger.debug("Outcome eval error %s/%s: %s", symbol, variant_id, exc)
        return None

    # Right-tail payoffs: +3R, +5R, +10R
    mfe_r = float(outcome.get("max_favorable_excursion_r", 0.0))
    realized_rr = float(outcome.get("realized_rr", 0.0))
    exit_reason = str(outcome.get("exit_reason", ""))

    # Scale-out trailing simulation for Challenger I and J
    if variant_id == VCP_CHALL_I_ID and exit_reason == "T1_HIT":
        risk_dist = max(0.01, entry - sl)
        t1_date = outcome.get("exit_date")
        post_t1_df = fwd_df[fwd_df.index.strftime("%Y-%m-%d") > t1_date] if t1_date else pd.DataFrame()
        if not post_t1_df.empty:
            be_hit = (post_t1_df["Low"] <= entry).any()
            if be_hit:
                runner_r = 0.0
            else:
                runner_r = max(0.0, (float(post_t1_df["Close"].iloc[-1]) - entry) / risk_dist)
            realized_rr = round(0.5 * outcome.get("realized_rr", 2.0) + 0.5 * runner_r, 2)
            exit_reason = "T1_RUNNER_SCALE_OUT"

    elif variant_id == VCP_CHALL_J_ID and exit_reason == "T1_HIT":
        risk_dist = max(0.01, entry - sl)
        t1_date = outcome.get("exit_date")
        post_t1_df = fwd_df[fwd_df.index.strftime("%Y-%m-%d") > t1_date] if t1_date else pd.DataFrame()
        if not post_t1_df.empty:
            atr14 = float((df_full["High"] - df_full["Low"]).iloc[-14:].mean()) if len(df_full) >= 14 else risk_dist
            highest_since_t1 = entry
            runner_stopped = False
            runner_r = 0.0
            for _, b in post_t1_df.iterrows():
                highest_since_t1 = max(highest_since_t1, float(b["High"]))
                trail_stop = max(entry, highest_since_t1 - 2.0 * atr14)
                if float(b["Low"]) <= trail_stop:
                    runner_r = max(0.0, (trail_stop - entry) / risk_dist)
                    runner_stopped = True
                    break
            if not runner_stopped:
                runner_r = max(0.0, (float(post_t1_df["Close"].iloc[-1]) - entry) / risk_dist)
            realized_rr = round(0.5 * outcome.get("realized_rr", 2.0) + 0.5 * runner_r, 2)
            exit_reason = "T1_CHANDELIER_TRAIL"

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
        "vol_ratio":   signal.get("vol_ratio", 0.0),
        "t3_depth":    signal.get("t3_depth", 0.0),
        "dryup_ratio": signal.get("pre_breakout_vol_dryup_ratio", 0.0),
        "realized_rr":               outcome.get("realized_rr", 0.0),
        "exit_reason":               outcome.get("exit_reason", ""),
        "exit_date":                 outcome.get("exit_date", ""),
        "holding_period_bars":       outcome.get("holding_period_bars", 0),
        "max_favorable_excursion_r": round(mfe_r, 2),
        "max_adverse_excursion_r":   outcome.get("max_adverse_excursion_r", 0.0),
        "r1_hit_before_sl":          int(outcome.get("r1_hit_before_sl",   False)),
        "r1_5_hit_before_sl":        int(outcome.get("r1_5_hit_before_sl", False)),
        "r2_hit_before_sl":          int(outcome.get("r2_hit_before_sl",   False)),
        "r3_hit":                    int(mfe_r >= 3.0),
        "r5_hit":                    int(mfe_r >= 5.0),
        "r10_hit":                   int(mfe_r >= 10.0),
        "post_sl_recovered_entry":   int(outcome.get("post_sl_recovered_entry", False)),
        "post_sl_recovered_t1":      int(outcome.get("post_sl_recovered_t1",    False)),
        "post_sl_max_recovery_r":    outcome.get("post_sl_max_recovery_r", 0.0),
    }

# =============================================================================
# Main Replay Loop
# =============================================================================

def run_vcp_replay(
    partition: str = "ALL",
    dry_run: bool = False,
    max_symbols: Optional[int] = None,
    verbose: bool = False,
) -> Tuple[List[Dict[str, Any]], Dict[str, int], Dict[str, int], Dict[str, Dict[str, int]]]:
    print("\n" + "=" * 90)
    print("ACCUMULATION / VCP: CHAMPION V1 vs CHALLENGERS A/B/C HISTORICAL REPLAY ENGINE")
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
    funnel_per_variant: Dict[str, Dict[str, int]] = {vid: defaultdict(int) for vid in ALL_VCP_VARIANTS}

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
            sigs = _eval_vcp_variants(sym, df_cut, macro_regime=regime)

            for vid in ALL_VCP_VARIANTS:
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

    for vid in ALL_VCP_VARIANTS:
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
                        scanner_family=ScannerFamily.ACCUMULATION_VCP,
                        description=f"VCP variant {vid}",
                        parameters={},
                        status=VariantStatus.CHAMPION if vid == VCP_CHAMP_ID else VariantStatus.CHALLENGER,
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
    print("\n" + "=" * 110)
    print("ACCUMULATION / VCP: CHAMPION V1 vs CHALLENGERS COMPARISON REPORT")
    print("=" * 110)

    print("\nALERT VOLUME PER VARIANT:")
    print(f"  {'Variant':<30} {'Total Alerts':>14}  {'No-Fwd-Data':>13}  {'Flag'}")
    print("  " + "-" * 75)
    for vid in ALL_VCP_VARIANTS:
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
    for vid in ALL_VCP_VARIANTS:
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

    print("\nRIGHT-TAIL PAYOFF ASYMMETRY (+3R, +5R, +10R):")
    print("  Explicitly preserving large winners per Section 5:")
    print(f"  {'Variant':<30} {'+2R%':>8} {'+3R%':>8} {'+5R%':>8} {'+10R%':>8} {'Max MFE':>9} {'Avg MFE':>9}")
    print("  " + "-" * 85)
    df_all = pd.DataFrame(all_outcomes)
    for vid in ALL_VCP_VARIANTS:
        df_v = df_all[df_all["variant_id"] == vid]
        if df_v.empty:
            continue
        r2_pct = df_v["r2_hit_before_sl"].mean() * 100.0
        r3_pct = df_v["r3_hit"].mean() * 100.0
        r5_pct = df_v["r5_hit"].mean() * 100.0
        r10_pct = df_v["r10_hit"].mean() * 100.0
        max_mfe = df_v["max_favorable_excursion_r"].max()
        avg_mfe = df_v["max_favorable_excursion_r"].mean()
        print(f"  {vid:<30} {r2_pct:>7.1f}% {r3_pct:>7.1f}% {r5_pct:>7.1f}% {r10_pct:>7.1f}% {max_mfe:>+8.2f}R {avg_mfe:>+8.2f}R")

    print("\nCHAMPION / CHALLENGER PROMOTION VERDICT:")
    print("  Criteria: higher E[R] (OOS preferred), higher PF, no >10% R2 regression, N>=15, CI-delta > -0.10R")
    print("  " + "-" * 80)
    winner = registry.compare_and_promote(ScannerFamily.ACCUMULATION_VCP)
    if winner is not None:
        bm = winner.oos_metrics or winner.in_sample_metrics
        if bm:
            print(f"  Winning Champion: {winner.variant_id} | Status: {winner.status.value} | Tier: {winner.allocation_tier.value}")
            print(f"  E[R]: {bm.expectancy_r:+.3f}R | PF: {bm.profit_factor:.2f} | N: {bm.sample_size}")
        else:
            print(f"  Winning Champion: {winner.variant_id}")
    else:
        print("  WARNING: No champion determined.")
    print("=" * 110)


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
    parser = argparse.ArgumentParser(description="Accumulation / VCP Historical Replay Engine")
    parser.add_argument("--partition", default="ALL", choices=["IS", "OOS", "ALL"])
    parser.add_argument("--dry-run",     action="store_true")
    parser.add_argument("--max-symbols", type=int, default=None)
    parser.add_argument("--verbose",     action="store_true")
    parser.add_argument("--no-csv",      action="store_true")
    args = parser.parse_args()

    all_outcomes, signal_counts, no_outcome_count, funnel_per_variant = run_vcp_replay(
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
