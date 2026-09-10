#!/usr/bin/env python3
# =============================================================================
# tests/simulate_reversal_v1_vs_v2_replay.py
# REVERSAL V1 vs V2-A/B/C/D HISTORICAL REPLAY ENGINE
# =============================================================================
#
# RULE 67 CHANGE-RATIONALE:
# This is the PRIMARY empirical proof mechanism for Release 4.
# It answers: "Does the V2 structural selection logic produce better alerts
# than the V1 champion?"
#
# REPLAY METHODOLOGY
# ------------------
# 1. Load all 1D parquet files from data/history/1d/ (879 symbols, ~279 bars)
# 2. Build a WEEKLY scan grid (5-bar step) across IS and OOS partitions:
#    IS  : 2025-07-24 -> 2026-03-31
#    OOS : 2026-04-01 -> 2026-09-04
# 3. For each scan date x symbol:
#    a. Slice df to bars <= scan_date (zero-lookahead contract)
#    b. Evaluate V1 via reversal_engine.evaluate_reversal_v2_symbol()
#    c. Evaluate V2 via reversal_v2_state_machine.evaluate_reversal_v2()
#    d. Apply per-variant stage-gate logic to decide alert yes/no
# 4. For each alert, measure forward outcomes via AlertQualityEngine
#    (forward window = bars strictly AFTER scan_date, up to 20 bars)
# 5. Accumulate outcome rows per variant x partition
# 6. Compute EvidenceMetrics, register into ChampionChallengerRegistry
# 7. Run compare_and_promote(ScannerFamily.REVERSAL)
# 8. Print full comparison report + save CSV to reports/
#
# LOOKAHEAD CONTRACT
# ------------------
# Signal : df sliced to index <= scan_date (no future bars in evaluation)
# Outcome: forward_df = df[df.index > scan_date].iloc[:HORIZON]
#          -- strictly NO overlap with signal window
#
# SINGLE-BAR STAGE 2+3 INTERPRETATION
# -------------------------------------
# evaluate_reversal_v2() evaluates all 5 stages on ONE bar. Stages 2+3 require
# a bar whose low undercuts the 25-bar range (sweep) AND whose close recovers
# above that level (reclaim) -- i.e., a hammer or bullish engulfing candle.
# This is the strictest interpretation. If V2-A/B alert counts are very low,
# multi-bar state tracking will be added in a V2.1 iteration.
#
# MINIMUM SAMPLE GUARD
# ---------------------
# Variants with N < 15 total alerts are labeled INSUFFICIENT_SAMPLE.
# No tier upgrade is possible for INSUFFICIENT_SAMPLE variants.
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

# ── sys.path setup ────────────────────────────────────────────────────────────
# RULE 67: Explicit insertion required for direct python execution.
# The root conftest.py handles this for pytest runs.
_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
_APP_DIR   = os.path.join(_REPO_ROOT, "app")
for _d in (_REPO_ROOT, _APP_DIR):
    if _d not in sys.path:
        sys.path.insert(0, _d)

# ── Application imports ───────────────────────────────────────────────────────
from reversal_engine import evaluate_reversal_v2_symbol           # V1 champion
from reversal_v2_state_machine import (                            # V2 & V2.1 challengers
    evaluate_reversal_v2, evaluate_reversal_v2_1, ReversalV2Config, ReversalStage,
)
from alert_quality_engine import AlertQualityEngine                # Outcome engine
from champion_challenger_registry import (
    ChampionChallengerRegistry, ScannerFamily, EvidenceMetrics,
)

logging.basicConfig(
    level=logging.WARNING,
    format="%(asctime)s | %(name)s | %(levelname)s | %(message)s",
)
logger = logging.getLogger("ReversalReplay")

# =============================================================================
# Constants
# =============================================================================

_HISTORY_1D_DIR = os.path.join(_REPO_ROOT, "data", "history", "1d")
_REPORTS_DIR    = os.path.join(_REPO_ROOT, "reports")
_OUTPUT_CSV     = os.path.join(_REPORTS_DIR, "reversal_v1_vs_v2_outcomes.csv")

IS_START  = "2025-07-24"
IS_END    = "2026-03-31"
OOS_START = "2026-04-01"
OOS_END   = "2026-09-04"

WEEKLY_STEP_BARS    = 5
OUTCOME_HORIZON     = 20
MIN_BARS_FOR_SIGNAL = 60
MIN_PRICE_FLOOR     = 100.0

V1_ID   = "REVERSAL_CHAMPION_V1"
V2A_ID  = "REVERSAL_V2A_SWEEP_RECLAIM"
V2B_ID  = "REVERSAL_V2B_DISPLACEMENT"
V2C_ID  = "REVERSAL_V2C_HIGHER_LOW"
V2D_ID  = "REVERSAL_V2D_FULL_FUNNEL"
V21A_ID = "REVERSAL_V21A_SWEEP_RECLAIM"
V21B_ID = "REVERSAL_V21B_DISPLACEMENT"
V21C_ID = "REVERSAL_V21C_HIGHER_LOW"
V21D_ID = "REVERSAL_V21D_FULL_FUNNEL"
V22A_ID = "REVERSAL_V22A_NO_BEAR"
V22B_ID = "REVERSAL_V22B_CAPITULATION"
V22C_ID = "REVERSAL_V22C_WIDER_SL"
V22D_ID = "REVERSAL_V22D_COMPOSITE"

ALL_VARIANT_IDS = [
    V1_ID,
    V2A_ID, V2B_ID, V2C_ID, V2D_ID,
    V21A_ID, V21B_ID, V21C_ID, V21D_ID,
    V22A_ID, V22B_ID, V22C_ID, V22D_ID,
]

V2_CFG = ReversalV2Config()


# =============================================================================
# Data Loading
# =============================================================================

def _load_all_symbols(base_dir: str) -> Dict[str, pd.DataFrame]:
    """
    Loads all 1D parquet files. Returns {symbol: df} with IST DatetimeIndex.
    RULE 72: Symbols that fail to load are silently skipped.
    """
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


def _build_scan_dates(
    all_dfs: Dict[str, pd.DataFrame],
    partition: str,
    step: int = WEEKLY_STEP_BARS,
) -> List[pd.Timestamp]:
    """
    Builds the weekly scan grid from REAL MARKET TRADING DAYS.

    RULE 65: Only dates actually present in the data are used.

    IMPLEMENTATION DETAIL:
    Using the union of 882 symbol calendars produces thousands of dates because
    different symbols have slightly different timestamp precision (some 00:00:00,
    some 05:30:00, etc.). We instead use a MAJORITY-VOTE approach:
    - Normalize all timestamps to midnight IST (date-only granularity)
    - Count how many symbols have data for each normalized date
    - Keep only dates present in >= 50% of symbols (real market days)
    - Step every N dates for the weekly scan cadence

    This produces ~55-60 scan dates for the full IS+OOS window, which is the
    correct weekly cadence for ~14 months of data.
    """
    from collections import Counter

    # RULE 67: Normalize to midnight IST to unify different timestamp precisions
    # across symbols (avoids union explosion from e.g. 00:00:00 vs 05:30:00).
    date_count: Counter = Counter()
    n_syms = len(all_dfs)

    for df in all_dfs.values():
        # Normalize to date-only timestamp (midnight IST)
        normed = df.index.normalize()
        for d in normed.unique():
            date_count[d] += 1

    # Majority-vote threshold: date must be present in >=50% of symbols
    threshold = max(1, int(n_syms * 0.50))
    market_days = sorted(
        [d for d, cnt in date_count.items() if cnt >= threshold]
    )

    if not market_days:
        raise RuntimeError(
            "Could not determine market trading days from symbol data. "
            "Ensure data/history/1d/ has sufficient parquet files."
        )

    # Convert to tz-aware Timestamps for comparison
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
    else:  # ALL
        start_ts = pd.Timestamp(IS_START, tz="Asia/Kolkata")
        end_ts   = pd.Timestamp(OOS_END,  tz="Asia/Kolkata")

    window = market_ts[(market_ts >= start_ts) & (market_ts <= end_ts)]
    if len(window) == 0:
        raise RuntimeError(
            f"No trading dates found in partition '{partition}' "
            f"({start_ts.date()} -> {end_ts.date()}). "
            f"Data date range detected: {market_ts[0].date()} -> {market_ts[-1].date()}."
        )

    scan_dates = [window[i] for i in range(0, len(window), step)]
    print(f"   Market days in partition: {len(window)}  ->  scan dates (step={step}): {len(scan_dates)}", flush=True)
    return scan_dates


def _partition_label(scan_ts: pd.Timestamp) -> str:
    return "OOS" if scan_ts >= pd.Timestamp(OOS_START, tz="Asia/Kolkata") else "IS"


# =============================================================================
# Macro Regime Inference (zero-lookahead)
# =============================================================================

def _infer_regime(df_cut: pd.DataFrame) -> str:
    """
    Proxies macro regime from price vs SMA200. Uses only df_cut (past bars).
    Returns BULL / NEUTRAL / BEAR.
    """
    if len(df_cut) < 10:
        return "NEUTRAL"
    close = float(df_cut["Close"].iloc[-1])
    if "SMA200" in df_cut.columns:
        sma200 = float(df_cut["SMA200"].iloc[-1])
    else:
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
# V1 Signal Evaluation
# =============================================================================

def _eval_v1(symbol: str, df_cut: pd.DataFrame) -> Optional[Dict[str, Any]]:
    """
    Evaluates V1 champion logic. Returns signal dict if state=="CONFIRMED", else None.
    Entry = close, SL = trough_low * 0.995, T1 = breakout_level.
    """
    try:
        result = evaluate_reversal_v2_symbol(symbol=symbol, df=df_cut)
    except Exception as exc:
        logger.debug("V1 eval error %s: %s", symbol, exc)
        return None

    if result.get("state") != "CONFIRMED":
        return None

    close = float(df_cut["Close"].iloc[-1])
    breakout_level = result.get("breakout_level") or 0.0

    prior = df_cut.iloc[:-1]
    trough_low = float((prior["Low"].iloc[-25:] if len(prior) >= 25 else prior["Low"]).min())
    stop_loss  = round(trough_low * 0.995, 2)
    entry = round(close, 2)
    t1    = round(breakout_level, 2) if breakout_level > entry else round(entry * 1.06, 2)
    risk  = max(0.01, entry - stop_loss)
    rr    = round((t1 - entry) / risk, 2)

    if rr < 1.0:
        return None

    return {
        "variant_id":  V1_ID,
        "entry_price": entry,
        "stop_loss":   stop_loss,
        "target_1":    t1,
        "rr_ratio":    rr,
        "score":       result.get("score", 70.0),
        "stage":       "V1_CONFIRMED",
        "engine_path": result.get("engine_path", "V1"),
    }


# =============================================================================
# V2 Signal Evaluation + Variant Gate
# =============================================================================

def _eval_v2_variants_from_probe(
    v2r: Any,
) -> Dict[str, Optional[Dict[str, Any]]]:
    results: Dict[str, Optional[Dict[str, Any]]] = {vid: None for vid in (V2A_ID, V2B_ID, V2C_ID, V2D_ID)}

    if v2r is None:
        return results

    stage = v2r.stage
    entry = v2r.entry_price
    sl    = v2r.stop_loss
    t1    = v2r.target_1
    rr    = v2r.rr_ratio or 0.0

    if entry is None or sl is None or t1 is None:
        return results

    base = {
        "entry_price": entry, "stop_loss": sl, "target_1": t1,
        "rr_ratio": rr, "score": v2r.score,
        "stage": stage.value, "engine_path": v2r.engine_path,
    }

    # V2-A: Stage 3 or higher
    if stage in (ReversalStage.RECLAIM_CONFIRMED, ReversalStage.DISPLACEMENT_BURST,
                 ReversalStage.HIGH_CONFIDENCE_ALERT) and rr >= 1.5:
        results[V2A_ID] = {**base, "variant_id": V2A_ID}

    # V2-B: Stage 4 or higher
    if stage in (ReversalStage.DISPLACEMENT_BURST, ReversalStage.HIGH_CONFIDENCE_ALERT) and rr >= 1.5:
        results[V2B_ID] = {**base, "variant_id": V2B_ID}

    # V2-C: Stage 4+ AND Higher Low
    higher_low_ok = (v2r.stage5_higher_low is True or stage == ReversalStage.HIGH_CONFIDENCE_ALERT)
    if stage in (ReversalStage.DISPLACEMENT_BURST, ReversalStage.HIGH_CONFIDENCE_ALERT) and higher_low_ok and rr >= 1.5:
        results[V2C_ID] = {**base, "variant_id": V2C_ID}

    # V2-D: Stage 5 only
    if stage == ReversalStage.HIGH_CONFIDENCE_ALERT and v2r.live_trade_allowed:
        results[V2D_ID] = {**base, "variant_id": V2D_ID}

    return results


# =============================================================================
# V2.1 & V2.2 Multi-Bar Signal Evaluation + Variant Gate
# =============================================================================

def _eval_v2_1_variants_from_probe(
    v21r: Any,
    df_cut: pd.DataFrame,
    macro_regime: str = "NEUTRAL",
) -> Dict[str, Optional[Dict[str, Any]]]:
    results: Dict[str, Optional[Dict[str, Any]]] = {
        vid: None for vid in (V21A_ID, V21B_ID, V21C_ID, V21D_ID, V22A_ID, V22B_ID, V22C_ID)
    }

    if v21r is None:
        return results

    stage = v21r.stage
    entry = v21r.entry_price
    sl    = v21r.stop_loss
    t1    = v21r.target_1
    rr    = v21r.rr_ratio or 0.0

    if entry is None or sl is None or t1 is None:
        return results

    base = {
        "entry_price": entry, "stop_loss": sl, "target_1": t1,
        "rr_ratio": rr, "score": v21r.score,
        "stage": stage.value, "engine_path": v21r.engine_path,
    }

    # V2.1-A: Stage 3 or higher
    if stage in (ReversalStage.RECLAIM_CONFIRMED, ReversalStage.DISPLACEMENT_BURST,
                 ReversalStage.HIGH_CONFIDENCE_ALERT) and rr >= 1.5:
        results[V21A_ID] = {**base, "variant_id": V21A_ID}

    # V2.1-B: Stage 4 or higher
    if stage in (ReversalStage.DISPLACEMENT_BURST, ReversalStage.HIGH_CONFIDENCE_ALERT) and rr >= 1.5:
        results[V21B_ID] = {**base, "variant_id": V21B_ID}

    # V2.1-C: Stage 4+ AND Higher Low
    higher_low_ok = (v21r.stage5_higher_low is True or stage == ReversalStage.HIGH_CONFIDENCE_ALERT)
    if stage in (ReversalStage.DISPLACEMENT_BURST, ReversalStage.HIGH_CONFIDENCE_ALERT) and higher_low_ok and rr >= 1.5:
        results[V21C_ID] = {**base, "variant_id": V21C_ID}

    # V2.1-D: Stage 5 only
    if stage == ReversalStage.HIGH_CONFIDENCE_ALERT and v21r.live_trade_allowed:
        results[V21D_ID] = {**base, "variant_id": V21D_ID}

    # V2.2-A: V2.1-A with Macro BEAR regime hard reject (Bull/Neutral only)
    if results.get(V21A_ID) is not None and macro_regime in ("BULL", "NEUTRAL"):
        results[V22A_ID] = {**base, "variant_id": V22A_ID}

    # V2.2-B: V2.1-A + Capitulation confirmation (RVOL >= 2.0x or RSI < 30)
    rvol_ok = (getattr(v21r, "stage2_rvol", 0.0) or 0.0) >= 2.0
    rsi_ok = (getattr(v21r, "stage1_rsi", 50.0) or 50.0) < 30.0
    if results.get(V21A_ID) is not None and (rvol_ok or rsi_ok):
        results[V22B_ID] = {**base, "variant_id": V22B_ID}

    # V2.2-C: V2.1-A + Buffered SL (Sequence Low - 0.5x ATR buffer)
    if results.get(V21A_ID) is not None:
        atr14 = float((df_cut["High"] - df_cut["Low"]).iloc[-14:].mean()) if len(df_cut) >= 14 else 1.0
        sl_wide = round(sl - 0.5 * atr14, 2)
        risk_wide = max(0.01, entry - sl_wide)
        t1_wide = round(entry + 2.0 * risk_wide, 2)
        rr_wide = round((t1_wide - entry) / risk_wide, 2)
        results[V22C_ID] = {
            **base,
            "stop_loss": sl_wide,
            "target_1": t1_wide,
            "rr_ratio": rr_wide,
            "variant_id": V22C_ID,
        }

        # V2.2-D: Composite (Buffered SL + No-Bear + Capitulation Confirmation)
        if macro_regime in ("BULL", "NEUTRAL") and (rvol_ok or rsi_ok):
            results[V22D_ID] = {
                **base,
                "stop_loss": sl_wide,
                "target_1": t1_wide,
                "rr_ratio": rr_wide,
                "variant_id": V22D_ID,
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
    """
    Measures forward trade outcome via AlertQualityEngine.
    LOOKAHEAD CONTRACT: fwd_df uses only bars STRICTLY AFTER scan_ts.
    """
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
            target_2=None, price_df=fwd_df, scanner="REVERSAL",
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
        "stage":       signal.get("stage", ""),
        "engine_path": signal.get("engine_path", ""),
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

def run_replay(
    partition: str = "ALL",
    dry_run: bool = False,
    max_symbols: Optional[int] = None,
    verbose: bool = False,
    step: int = WEEKLY_STEP_BARS,
) -> Tuple[List[Dict[str, Any]], Dict[str, int], Dict[str, int], Dict[str, int], Dict]:
    """
    Main replay entry point. Returns
    (all_outcomes, signal_counts, no_outcome_count, stage_counters, funnel_per_variant)
    """
    print("\n" + "=" * 90)
    print("REVERSAL V1 vs V2-A/B/C/D HISTORICAL REPLAY ENGINE")
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
    print(f"   Scan dates    : {len(scan_dates)}  "
          f"({scan_dates[0].date()} -> {scan_dates[-1].date()})\n", flush=True)

    all_outcomes:    List[Dict[str, Any]] = []
    signal_counts    = defaultdict(int)
    no_outcome_count = defaultdict(int)
    stage_counters   = defaultdict(int)
    v21_stage_counters = defaultdict(int)
    funnel_per_variant: Dict[str, Dict[str, int]] = {vid: defaultdict(int) for vid in ALL_VARIANT_IDS}

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

            # V1
            funnel_per_variant[V1_ID]["EVALUATED"] += 1
            v1_sig = _eval_v1(sym, df_cut)
            if v1_sig is not None:
                funnel_per_variant[V1_ID]["ALERTED"] += 1
                signal_counts[V1_ID] += 1
                if not dry_run:
                    row = _measure_outcome(sym, V1_ID, v1_sig, df_full, scan_ts, regime)
                    if row:
                        all_outcomes.append(row)
                    else:
                        no_outcome_count[V1_ID] += 1
                if verbose:
                    print(f"  [V1] {sym} @ {scan_ts.date()} entry={v1_sig['entry_price']:.2f} R:R={v1_sig['rr_ratio']:.1f}")

            # V2 -- evaluate once and reuse probe
            try:
                probe = evaluate_reversal_v2(sym, df_cut, config=V2_CFG, macro_regime=regime)
                stage_counters[probe.stage.value] += 1
                v2_sigs = _eval_v2_variants_from_probe(probe)
                for vid in (V2A_ID, V2B_ID, V2C_ID, V2D_ID):
                    funnel_per_variant[vid]["EVALUATED"] += 1
                    sig = v2_sigs.get(vid)
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
                            print(f"  [{vid}] {sym} @ {scan_ts.date()} "
                                  f"stage={sig['stage']} entry={sig['entry_price']:.2f} R:R={sig['rr_ratio']:.1f}")
            except Exception:
                stage_counters["ERROR"] += 1

            # V2.1 & V2.2 -- evaluate once and reuse probe
            try:
                probe_21 = evaluate_reversal_v2_1(sym, df_cut, config=V2_CFG, macro_regime=regime)
                v21_stage_counters[probe_21.stage.value] += 1
                v21_sigs = _eval_v2_1_variants_from_probe(probe_21, df_cut, regime)
                for vid in (V21A_ID, V21B_ID, V21C_ID, V21D_ID, V22A_ID, V22B_ID, V22C_ID, V22D_ID):
                    funnel_per_variant[vid]["EVALUATED"] += 1
                    sig = v21_sigs.get(vid)
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
                            print(f"  [{vid}] {sym} @ {scan_ts.date()} "
                                  f"stage={sig['stage']} entry={sig['entry_price']:.2f} R:R={sig['rr_ratio']:.1f}")
            except Exception:
                v21_stage_counters["ERROR"] += 1

            dot_ctr += 1
            if dot_ctr % 500 == 0:
                print(".", end="", flush=True)

    print(f"\n\n   Replay complete. Outcome rows: {len(all_outcomes):,}", flush=True)
    return (
        all_outcomes,
        dict(signal_counts),
        dict(no_outcome_count),
        dict(stage_counters),
        dict(v21_stage_counters),
        funnel_per_variant,
    )


# =============================================================================
# Evidence Metrics Computation + Registry
# =============================================================================

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

    # RULE 67: registry.update_variant_metrics() and EvidenceMetrics both use
    # "IN_SAMPLE" (not "IS") as the partition_type string. We use "IS"/"OOS" as
    # short internal labels (dataframe partition column, metrics_map keys).
    # This mapping translates at the point of registry/EvidenceMetrics calls only.
    REGISTRY_PARTITION = {"IS": "IN_SAMPLE", "OOS": "OOS"}

    for vid in ALL_VARIANT_IDS:
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



# =============================================================================
# Report
# =============================================================================

def _print_report(
    all_outcomes, signal_counts, no_outcome_count,
    stage_counters, v21_stage_counters, funnel_per_variant,
    registry, metrics_map, partition, dry_run,
) -> None:

    print("\n" + "=" * 100)
    print("REVERSAL V1 vs V2 vs V2.1 (MULTI-BAR) COMPARISON REPORT")
    print("=" * 100)

    print("\nALERT VOLUME PER VARIANT:")
    print(f"  {'Variant':<35} {'Total Alerts':>14}  {'No-Fwd-Data':>13}  {'Flag'}")
    print("  " + "-" * 80)
    for vid in ALL_VARIANT_IDS:
        n  = signal_counts.get(vid, 0)
        nf = no_outcome_count.get(vid, 0)
        flag = "  <<< INSUFFICIENT_SAMPLE (N<15)" if n < 15 else ""
        print(f"  {vid:<35} {n:>14}  {nf:>13}{flag}")

    print("\nV2 SINGLE-CANDLE STAGE FUNNEL ATTRITION:")
    order = ["NO_SETUP", "EXHAUSTION_CANDIDATE", "SWEEP_SETUP",
             "RECLAIM_CONFIRMED", "DISPLACEMENT_BURST", "HIGH_CONFIDENCE_ALERT", "ERROR"]
    tot = sum(stage_counters.values())
    print(f"  {'Stage':<30} {'Count':>8}  {'Pct':>8}")
    print("  " + "-" * 52)
    for st in order:
        cnt = stage_counters.get(st, 0)
        pct = cnt / tot * 100.0 if tot > 0 else 0.0
        print(f"  {st:<30} {cnt:>8}  {pct:>7.2f}%")
    print(f"  {'TOTAL':<30} {tot:>8}")

    print("\nV2.1 MULTI-BAR (5-BAR WINDOW) STAGE FUNNEL ATTRITION:")
    tot_21 = sum(v21_stage_counters.values())
    print(f"  {'Stage':<30} {'Count':>8}  {'Pct':>8}")
    print("  " + "-" * 52)
    for st in order:
        cnt = v21_stage_counters.get(st, 0)
        pct = cnt / tot_21 * 100.0 if tot_21 > 0 else 0.0
        print(f"  {st:<30} {cnt:>8}  {pct:>7.2f}%")
    print(f"  {'TOTAL':<30} {tot_21:>8}")

    if dry_run:
        print("\n  [DRY-RUN] Outcome measurement skipped. Run without --dry-run for metrics.")
        return

    if not all_outcomes:
        print("\n  WARNING: No outcomes measured (no alerts, or all lacked forward data).")
        return

    parts = ["IS", "OOS"] if partition == "ALL" else [partition]

    print("\nEVIDENCE METRICS BY VARIANT x PARTITION:")
    hdr = (f"  {'Variant':<35} {'Part':>5} {'N':>5} {'E[R]':>7} {'PF':>6} {'Win%':>6} "
           f"{'CI_lo':>7} {'p-val':>7} {'+1R%':>6} {'+2R%':>6} {'Tier':<20} {'Status'}")
    print(hdr)
    print("  " + "-" * 120)
    for vid in ALL_VARIANT_IDS:
        variant = registry.get_variant(vid)
        st_str  = variant.status.value if variant else "N/A"
        for part in parts:
            key = f"{vid}_{part}"
            m   = metrics_map.get(key)
            if m is None or m.sample_size == 0:
                print(f"  {vid:<35} {part:>5} {'---':>5}")
                continue
            tier_str = variant.allocation_tier.value if variant else "N/A"
            p_str = f"{m.approx_p_value:.4f}" if m.approx_p_value is not None else "N/A "
            flag  = " <<INSUFF" if m.sample_size < 15 else ""
            print(
                f"  {vid:<35} {part:>5} {m.sample_size:>5} "
                f"{m.expectancy_r:>+7.3f} {m.profit_factor:>6.2f} {m.win_rate_pct:>6.1f} "
                f"{m.ci_95_lower_r:>+7.3f} {p_str:>7} "
                f"{m.r1_hit_rate_pct:>6.1f} {m.r2_hit_rate_pct:>6.1f} "
                f"{tier_str:<20} {st_str}{flag}"
            )

    print("\nCHAMPION / CHALLENGER PROMOTION VERDICT:")
    print("  Criteria: higher E[R] (OOS preferred), higher PF, no >10% R2 regression, N>=15, CI-delta > -0.10R")
    print("  " + "-" * 70)
    winner = registry.compare_and_promote(ScannerFamily.REVERSAL)
    if winner is not None:
        bm = winner.oos_metrics or winner.in_sample_metrics
        if bm:
            print(f"  Champion: {winner.variant_id}  |  Status: {winner.status.value}  |  Tier: {winner.allocation_tier.value}")
            print(f"  E[R]: {bm.expectancy_r:+.3f}R  |  PF: {bm.profit_factor:.2f}  |  N: {bm.sample_size}")
        else:
            print(f"  Champion: {winner.variant_id} (no metrics available yet)")
    else:
        print("  WARNING: No champion determined.")

    print("\nREGIME BREAKDOWN (E[R]):")
    pref_part = "OOS" if partition in ("ALL", "OOS") else "IS"
    print(f"  Partition: {pref_part}")
    print(f"  {'Variant':<35} {'BULL':>9}  {'NEUTRAL':>9}  {'BEAR':>9}")
    print("  " + "-" * 68)
    for vid in ALL_VARIANT_IDS:
        m = metrics_map.get(f"{vid}_{pref_part}")
        if m is None or not m.regime_breakdown:
            print(f"  {vid:<35} {'N/A':>9}  {'N/A':>9}  {'N/A':>9}")
            continue
        def _f(v): return f"{v:>+9.3f}" if v == v else "      N/A"
        print(f"  {vid:<35} "
              f"{_f(m.regime_breakdown.get('BULL', float('nan')))}  "
              f"{_f(m.regime_breakdown.get('NEUTRAL', float('nan')))}  "
              f"{_f(m.regime_breakdown.get('BEAR', float('nan')))}")

    print("\nPOST-SL RECOVERY (% stopped trades recovering to entry / T1):")
    print(f"  {'Variant':<35} {'->Entry%':>10}  {'->T1%':>8}  {'N stopped':>10}")
    print("  " + "-" * 70)
    df_all = pd.DataFrame(all_outcomes)
    for vid in ALL_VARIANT_IDS:
        df_v = df_all[df_all["variant_id"] == vid]
        if df_v.empty:
            print(f"  {vid:<35}  {'N/A':>10}  {'N/A':>8}  {'N/A':>10}")
            continue
        stopped = df_v[df_v["exit_reason"].isin(["SL_HIT", "SAME_BAR_CONFLICT_SL"])]
        if stopped.empty:
            print(f"  {vid:<35}  {0.0:>9.1f}%  {0.0:>7.1f}%  {0:>10}")
            continue
        re = stopped["post_sl_recovered_entry"].mean() * 100.0
        rt = stopped["post_sl_recovered_t1"].mean() * 100.0
        print(f"  {vid:<35}  {re:>9.1f}%  {rt:>7.1f}%  {len(stopped):>10}")

    print("\nINTERPRETATION NOTES:")
    print("  V1 prior: E[R]=+0.03R, PF=1.06 (registry documentation; this replay measures empirically).")
    print("  V2-A/B: less restrictive -> higher N; V2-D: most restrictive -> smallest N.")
    print("  If N<15 for any variant: INSUFFICIENT_SAMPLE, no tier promotion possible.")
    print("  Stage 2+3 single-bar interpretation: hammer/bullish engulfing required (same candle).")
    print("  If V2-A/B alert counts are very low -> V2.1 multi-bar state tracking iteration.")
    print("=" * 100)


# =============================================================================
# CSV Export
# =============================================================================

def _save_csv(all_outcomes: List[Dict[str, Any]], output_path: str) -> None:
    if not all_outcomes:
        return
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    fieldnames = list(all_outcomes[0].keys())
    with open(output_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(all_outcomes)
    print(f"\n  Raw outcomes saved: {output_path}  ({len(all_outcomes):,} rows)")


# =============================================================================
# Entry Point
# =============================================================================

def main() -> None:
    parser = argparse.ArgumentParser(description="Reversal V1 vs V2-A/B/C/D Historical Replay Engine")
    parser.add_argument("--partition", default="ALL", choices=["IS", "OOS", "ALL"])
    parser.add_argument("--dry-run",     action="store_true")
    parser.add_argument("--max-symbols", type=int, default=None)
    parser.add_argument("--verbose",     action="store_true")
    parser.add_argument("--no-csv",      action="store_true")
    parser.add_argument("--step",        type=int, default=WEEKLY_STEP_BARS, help="Scan cadence step in market days")
    args = parser.parse_args()

    all_outcomes, signal_counts, no_outcome_count, stage_counters, v21_stage_counters, funnel_per_variant = run_replay(
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
        stage_counters=stage_counters,
        v21_stage_counters=v21_stage_counters,
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
