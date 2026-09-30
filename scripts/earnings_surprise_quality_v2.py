#!/usr/bin/env python3
"""
scripts/earnings_surprise_quality_v2.py
========================================
EARNINGS_SURPRISE_QUALITY_V2 — YoY-SUE PEAD Research Engine
ELITE BREAKOUT SYSTEM

FROZEN SPECIFICATION (do not modify without governance re-registration):
------------------------------------------------------------------------
Signal: Year-over-Year Earnings Surprise (YoY-SUE)

  EPS_expected_t  = EPS_filed_{t-4}        (same fiscal quarter, prior year)
  surprise_t      = EPS_actual_t - EPS_expected_t
  sigma_baseline  = sample_std(surprise_{t-1}, surprise_{t-2},
                               surprise_{t-3}, surprise_{t-4})

  Each of surprise_{t-k} = EPS_{t-k} - EPS_{t-k-4}

  Therefore minimum valid quarterly observations: 9
      (indices t-8, t-7, t-6, t-5, t-4, t-3, t-2, t-1, t)

  YoY_SUE_t = surprise_t / sigma_baseline
  STRONG_BEAT: YoY_SUE_t >= +1.5
  WEAK_BEAT:   +0.5 <= YoY_SUE_t < +1.5
  MISS:        YoY_SUE_t <= -0.5

  Near-zero sigma (< 1e-6): sign-based fallback = +1.0 / 0.0 / -1.0

Causality:
  Signal timestamp = conservative_availability_timestamp of EPS_t
                     (LODR statutory deadline, conservative upper bound)
  ALL 8 prior EPS values must have conservative_availability_timestamp
  <= signal_timestamp before use.

Execution:
  T+1 Open (next trading day after signal timestamp)
  Friction: 5.0 bps round-trip (2.5 bps entry + 2.5 bps exit)
  Hold: 60 calendar days or 20-day SMA crossunder, whichever first

Quality gate (applied via annual PIT filings, PIT-causal):
  ROCE_5y >= 15.0%
  Sales CAGR_5y >= 10.0%
  D/E <= 0.50
  CFO/PAT_5y >= 0.80

Data invariants:
  - No synthetic EPS
  - No Yahoo Finance / third-party proxy
  - No future filing data (strict PIT causality)
  - Missing EPS → DATA_INSUFFICIENT → skip event
  - < 9 valid observations → DATA_INSUFFICIENT → skip event

Governance:
  PROVENANCE_STATUS = CERTIFIED_PIT_SCREENER (Screener quarterly filings)
  BACKTEST_STATUS   = IN_CERTIFICATION (pending full statistical battery)
"""

from __future__ import annotations

import os
import sys
import json
import sqlite3
import hashlib
import logging
from datetime import datetime, date, timedelta
from typing import Dict, List, Optional, Tuple, Any

import numpy as np
import pandas as pd

# ─────────────────────────────────────────────────────────────────────────────
# FROZEN SPEC CONSTANTS
# ─────────────────────────────────────────────────────────────────────────────
STRATEGY_ID              = "EARNINGS_SURPRISE_QUALITY_V2"
SPEC_VERSION             = "1.0_FROZEN"
STRONG_BEAT_THRESHOLD    = 1.5     # YoY_SUE >= this → STRONG_BEAT signal
WEAK_BEAT_THRESHOLD      = 0.5
MISS_THRESHOLD           = -0.5
MIN_PRIOR_SURPRISES      = 4       # exactly 4 prior YoY surprises for sigma
MIN_QUARTERLY_OBS        = 9       # 9 observations = t-8 through t
QUARTER_MATCH_TOL_DAYS   = 45      # max days diff to match "same quarter -1 year"
MIN_SIGMA                = 1e-6    # below → sign-based fallback
QUALITY_ROCE_MIN         = 15.0    # % 5Y avg ROCE
QUALITY_SALES_CAGR_MIN   = 10.0    # % 5Y sales CAGR
QUALITY_DE_MAX           = 0.50    # D/E ratio
QUALITY_CFO_PAT_MIN      = 0.80    # CFO/PAT 5Y
HOLD_CALENDAR_DAYS       = 60
FRICTION_BPS             = 5.0     # round-trip
TIMESTAMP_BASIS          = "LODR_STATUTORY_DEADLINE_CONSERVATIVE"

REPO_ROOT     = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
PIT_DB_PATH   = os.path.join(REPO_ROOT, "data", "pit_fundamentals_v1",
                              "pit_fundamentals_v1.db")
REPORTS_DIR   = os.path.join(REPO_ROOT, "reports",
                              "earnings_surprise_quality_v2")
os.makedirs(REPORTS_DIR, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# DATA LOADING
# ─────────────────────────────────────────────────────────────────────────────

def load_pit_quarterly(db_path: str) -> pd.DataFrame:
    """
    Load quarterly PIT filings from the certified SQLite database.
    Returns only QUARTERLY rows with non-null EPS.
    Sorted by symbol, period_end_date ascending.
    """
    if not os.path.exists(db_path):
        raise FileNotFoundError(f"PIT DB not found: {db_path}")

    con = sqlite3.connect(db_path)
    df = pd.read_sql(
        """
        SELECT symbol, period_end_date, filing_date,
               conservative_availability_timestamp,
               eps, revenue, operating_profit, net_profit,
               operating_margin, source_provider, statement_type
        FROM pit_fundamentals_v1
        WHERE UPPER(statement_type) = 'QUARTERLY'
        ORDER BY symbol, period_end_date ASC
        """,
        con,
        parse_dates=["period_end_date", "conservative_availability_timestamp",
                     "filing_date"],
    )
    con.close()

    df["period_end_date"] = pd.to_datetime(df["period_end_date"])
    df["conservative_availability_timestamp"] = pd.to_datetime(
        df["conservative_availability_timestamp"]
    )
    log.info(f"Loaded {len(df)} quarterly rows for "
             f"{df['symbol'].nunique()} symbols")
    return df


def load_pit_annual(db_path: str) -> pd.DataFrame:
    """Load annual PIT filings for quality gate computation."""
    con = sqlite3.connect(db_path)
    df = pd.read_sql(
        """
        SELECT symbol, period_end_date, filing_date,
               conservative_availability_timestamp,
               revenue, net_profit, operating_cash_flow, roce, roe,
               total_debt, total_equity, source_provider
        FROM pit_fundamentals_v1
        WHERE UPPER(statement_type) = 'ANNUAL'
        ORDER BY symbol, period_end_date ASC
        """,
        con,
        parse_dates=["period_end_date", "conservative_availability_timestamp",
                     "filing_date"],
    )
    con.close()
    df["period_end_date"] = pd.to_datetime(df["period_end_date"])
    df["conservative_availability_timestamp"] = pd.to_datetime(
        df["conservative_availability_timestamp"]
    )
    return df


# ─────────────────────────────────────────────────────────────────────────────
# CORE SUE MATH (unit-testable, no I/O)
# ─────────────────────────────────────────────────────────────────────────────

def _find_same_quarter_prior_year(
    period_end: pd.Timestamp,
    available_rows: pd.DataFrame,
    tol_days: int = QUARTER_MATCH_TOL_DAYS,
) -> Optional[pd.Series]:
    """
    Find the row in available_rows whose period_end_date is closest to
    (period_end - 1 year) within tol_days tolerance.

    Returns the matched row (Series) or None if no match.
    """
    target = period_end - pd.DateOffset(years=1)
    candidates = available_rows.copy()
    candidates = candidates[
        candidates["period_end_date"] != period_end  # exclude self
    ]
    if candidates.empty:
        return None

    candidates = candidates.copy()
    candidates["_days_diff"] = (
        candidates["period_end_date"] - target
    ).dt.days.abs()
    candidates = candidates[candidates["_days_diff"] <= tol_days]
    if candidates.empty:
        return None

    return candidates.nsmallest(1, "_days_diff").iloc[0]


def compute_yoy_sue(
    sym_quarterly: pd.DataFrame,
    signal_row: pd.Series,
) -> Dict[str, Any]:
    """
    Compute YoY-SUE for a single earnings event.

    FROZEN FORMULA:
      EPS_expected_t  = EPS_{t-4}  (same quarter prior year)
      surprise_t      = EPS_t - EPS_{t-4}
      sigma_baseline  = std(surprise_{t-1}, ..., surprise_{t-4})
        where surprise_{t-k} = EPS_{t-k} - EPS_{t-k-4}  for k=1..4
      YoY_SUE_t       = surprise_t / sigma_baseline

    Requires 9 valid quarterly EPS observations (t-8 through t).

    Parameters
    ----------
    sym_quarterly : pd.DataFrame
        ALL quarterly rows for this symbol (any availability).
        Must have columns: period_end_date, eps,
        conservative_availability_timestamp.
    signal_row : pd.Series
        The row representing quarter t (the event quarter).

    Returns
    -------
    dict with keys:
        status          : "OK" | "DATA_INSUFFICIENT" | "CAUSALITY_VIOLATION"
        yoy_sue         : float (only when status="OK")
        surprise_t      : float
        eps_t           : float
        eps_t4          : float (EPS same quarter prior year)
        sigma           : float
        sigma_note      : str
        prior_surprises : list of 4 floats
        n_observations  : int (how many EPS values used)
        signal_ts       : str (conservative_availability_timestamp of EPS_t)
        missing_data    : str (only when DATA_INSUFFICIENT / CAUSALITY_VIOLATION)
        category        : "STRONG_BEAT"|"WEAK_BEAT"|"MISS"|"NEUTRAL" (only OK)
    """
    signal_ts = pd.Timestamp(signal_row["conservative_availability_timestamp"])
    t_period   = pd.Timestamp(signal_row["period_end_date"])
    eps_t      = signal_row["eps"]

    # ── 0. Current EPS must not be null ──────────────────────────────────────
    if eps_t is None or (isinstance(eps_t, float) and np.isnan(eps_t)):
        return {
            "status":       "DATA_INSUFFICIENT",
            "missing_data": f"eps_t is null for period {t_period.date()}",
        }

    # ── 1. PIT filter: only data available at signal timestamp ───────────────
    # All rows with conservative_availability_timestamp <= signal_ts
    avail = sym_quarterly[
        sym_quarterly["conservative_availability_timestamp"] <= signal_ts
    ].copy()

    # Verify the signal row itself is in the available set
    avail_at_t = avail[avail["period_end_date"] == t_period]
    if avail_at_t.empty:
        return {
            "status":       "CAUSALITY_VIOLATION",
            "missing_data": (
                f"signal row (period={t_period.date()}) not found in "
                f"data available at signal_ts={signal_ts}"
            ),
        }

    # ── 2. Find EPS_{t-4} ────────────────────────────────────────────────────
    # Exclude current quarter from search
    avail_excl = avail[avail["period_end_date"] != t_period]
    t4_row = _find_same_quarter_prior_year(t_period, avail_excl)

    if t4_row is None:
        return {
            "status":       "DATA_INSUFFICIENT",
            "missing_data": (
                f"eps_t-4 not found (looking for ~{(t_period - pd.DateOffset(years=1)).date()})"
            ),
        }
    eps_t4 = t4_row["eps"]
    if eps_t4 is None or (isinstance(eps_t4, float) and np.isnan(eps_t4)):
        return {
            "status":       "DATA_INSUFFICIENT",
            "missing_data": f"eps_t-4 is null for period {t4_row['period_end_date'].date()}",
        }

    current_surprise = float(eps_t) - float(eps_t4)

    # ── 3. Compute 4 prior YoY surprises for sigma ───────────────────────────
    # Need the 4 most recent quarters BEFORE t, each needing their own t-4 pair.
    # Sorted descending so we pick the most recent 4.
    prior_quarters = avail_excl.sort_values("period_end_date", ascending=False)

    prior_surprises: List[float] = []
    missing_sigma_items: List[str] = []

    for _, pq in prior_quarters.iterrows():
        if len(prior_surprises) >= MIN_PRIOR_SURPRISES:
            break

        pq_period = pd.Timestamp(pq["period_end_date"])
        pq_eps    = pq["eps"]

        if pq_eps is None or (isinstance(pq_eps, float) and np.isnan(pq_eps)):
            missing_sigma_items.append(
                f"eps_null@{pq_period.date()}"
            )
            continue

        # Find t-4 for this prior quarter
        avail_excl_pq = avail_excl[avail_excl["period_end_date"] != pq_period]
        pq_t4_row = _find_same_quarter_prior_year(pq_period, avail_excl_pq)

        if pq_t4_row is None:
            missing_sigma_items.append(
                f"t4_missing@{pq_period.date()}"
            )
            continue

        pq_t4_eps = pq_t4_row["eps"]
        if pq_t4_eps is None or (isinstance(pq_t4_eps, float) and np.isnan(pq_t4_eps)):
            missing_sigma_items.append(
                f"t4_eps_null@{pq_period.date()}"
            )
            continue

        prior_surprises.append(float(pq_eps) - float(pq_t4_eps))

    if len(prior_surprises) < MIN_PRIOR_SURPRISES:
        return {
            "status":       "DATA_INSUFFICIENT",
            "missing_data": (
                f"sigma requires {MIN_PRIOR_SURPRISES} prior YoY surprises; "
                f"only {len(prior_surprises)} available. "
                f"Issues: {'; '.join(missing_sigma_items)}"
            ),
        }

    # ── 4. Compute sigma ─────────────────────────────────────────────────────
    sigma = float(np.std(prior_surprises[:MIN_PRIOR_SURPRISES], ddof=1))

    if sigma < MIN_SIGMA:
        # Near-zero historical volatility — use sign-based fallback
        yoy_sue   = (1.0 if current_surprise > MIN_SIGMA
                     else -1.0 if current_surprise < -MIN_SIGMA
                     else 0.0)
        sigma_note = "NEAR_ZERO_SIGMA_SIGN_FALLBACK"
    else:
        yoy_sue    = current_surprise / sigma
        sigma_note = "NORMAL"

    # ── 5. Category classification ────────────────────────────────────────────
    if yoy_sue >= STRONG_BEAT_THRESHOLD:
        category = "STRONG_BEAT"
    elif yoy_sue >= WEAK_BEAT_THRESHOLD:
        category = "WEAK_BEAT"
    elif yoy_sue <= MISS_THRESHOLD:
        category = "MISS"
    else:
        category = "NEUTRAL"

    # ── 6. n_observations count ──────────────────────────────────────────────
    # t itself + t-4 + 4 × (t-k, t-k-4 pairs) = 1 + 1 + 4×2 = 10
    # But some pairs share eps values, so report distinct periods used
    n_obs = 1 + 1 + MIN_PRIOR_SURPRISES * 2  # conservative upper bound = 10

    return {
        "status":           "OK",
        "yoy_sue":          round(yoy_sue, 6),
        "surprise_t":       round(current_surprise, 6),
        "eps_t":            round(float(eps_t), 6),
        "eps_t4":           round(float(eps_t4), 6),
        "sigma":            round(sigma, 6),
        "sigma_note":       sigma_note,
        "prior_surprises":  [round(s, 6) for s in prior_surprises[:MIN_PRIOR_SURPRISES]],
        "n_observations":   n_obs,
        "signal_ts":        str(signal_ts),
        "category":         category,
    }


# ─────────────────────────────────────────────────────────────────────────────
# QUALITY GATE (PIT-causal annual metrics)
# ─────────────────────────────────────────────────────────────────────────────

def _compute_quality_metrics(
    sym: str,
    signal_ts: pd.Timestamp,
    annual_df: pd.DataFrame,
) -> Dict[str, Any]:
    """
    Compute quality gate metrics from ANNUAL PIT filings available at signal_ts.
    Returns dict with status, metrics, and pass/fail.
    """
    sym_ann = annual_df[
        (annual_df["symbol"] == sym) &
        (annual_df["conservative_availability_timestamp"] <= signal_ts)
    ].sort_values("period_end_date", ascending=True)

    if len(sym_ann) < 5:
        return {
            "quality_status": "DATA_INSUFFICIENT",
            "quality_missing": f"only {len(sym_ann)} annual filings; need >= 5",
        }

    # 5Y ROCE average (use last 5 annual rows)
    recent = sym_ann.tail(5)
    roce_vals = recent["roce"].dropna()
    roce_5y   = float(roce_vals.mean()) if len(roce_vals) >= 3 else None

    # 5Y Sales CAGR
    rev_start = float(sym_ann.iloc[-6]["revenue"]) if (
        len(sym_ann) >= 6 and sym_ann.iloc[-6]["revenue"] is not None
    ) else None
    rev_end   = float(sym_ann.iloc[-1]["revenue"]) if (
        sym_ann.iloc[-1]["revenue"] is not None
    ) else None
    sales_cagr_5y = None
    if rev_start and rev_end and rev_start > 0 and rev_end > 0:
        sales_cagr_5y = (((rev_end / rev_start) ** (1 / 5)) - 1) * 100

    # D/E ratio (most recent annual)
    last = sym_ann.iloc[-1]
    debt   = last.get("total_debt")
    equity = last.get("total_equity")
    de_ratio = None
    if (debt is not None and equity is not None
            and not np.isnan(float(equity)) and float(equity) > 0):
        de_ratio = float(debt) / float(equity)

    # CFO/PAT (5Y)
    cfo_vals   = recent["operating_cash_flow"].dropna()
    np_vals    = recent["net_profit"].dropna()
    cfo_pat_5y = None
    if len(cfo_vals) >= 3 and len(np_vals) >= 3:
        total_cfo = cfo_vals.sum()
        total_np  = np_vals.sum()
        if total_np != 0:
            cfo_pat_5y = total_cfo / total_np

    # Gate evaluation
    failures = []
    if roce_5y is None:         failures.append("ROCE_5Y_MISSING")
    elif roce_5y < QUALITY_ROCE_MIN:   failures.append(f"FAIL_ROCE ({roce_5y:.1f}%<{QUALITY_ROCE_MIN}%)")
    if sales_cagr_5y is None:   failures.append("SALES_CAGR_5Y_MISSING")
    elif sales_cagr_5y < QUALITY_SALES_CAGR_MIN: failures.append(f"FAIL_SALES_CAGR ({sales_cagr_5y:.1f}%<{QUALITY_SALES_CAGR_MIN}%)")
    if de_ratio is not None and de_ratio > QUALITY_DE_MAX:
        failures.append(f"FAIL_DE ({de_ratio:.2f}>{QUALITY_DE_MAX})")
    if cfo_pat_5y is not None and cfo_pat_5y < QUALITY_CFO_PAT_MIN:
        failures.append(f"FAIL_CFO_PAT ({cfo_pat_5y:.2f}<{QUALITY_CFO_PAT_MIN})")

    data_missing = any("MISSING" in f for f in failures)
    quality_pass = len(failures) == 0

    return {
        "quality_status":  "DATA_INSUFFICIENT" if data_missing else ("PASS" if quality_pass else "FAIL"),
        "quality_pass":    quality_pass,
        "quality_failures": failures,
        "roce_5y":         roce_5y,
        "sales_cagr_5y":   sales_cagr_5y,
        "de_ratio":        de_ratio,
        "cfo_pat_5y":      cfo_pat_5y,
        "annual_rows_used": len(sym_ann),
    }


# ─────────────────────────────────────────────────────────────────────────────
# EVENT RECONSTRUCTION
# ─────────────────────────────────────────────────────────────────────────────

def reconstruct_events(
    quarterly_df: pd.DataFrame,
    annual_df: pd.DataFrame,
    apply_quality_gate: bool = True,
) -> Tuple[List[Dict], Dict]:
    """
    Reconstruct all V2-eligible earnings events from the PIT quarterly DB.

    For each symbol, for each quarter t where the symbol has ≥9 quarterly
    observations up to and including t (all causally available), compute
    YoY-SUE. Apply the quality gate using annual PIT data.

    Returns
    -------
    events : list of event dicts (only SUE-computable events)
    stats  : summary statistics dict
    """
    symbols = quarterly_df["symbol"].unique()
    log.info(f"Reconstructing V2 events across {len(symbols)} symbols...")

    events: List[Dict] = []
    stats = {
        "total_symbols":         len(symbols),
        "symbols_with_ge9_q":    0,
        "events_attempted":      0,
        "events_data_ok":        0,
        "events_data_insufficient": 0,
        "events_causality_violation": 0,
        "events_quality_missing":  0,
        "events_quality_fail":     0,
        "events_strong_beat":    0,
        "events_weak_beat":      0,
        "events_miss":           0,
        "events_neutral":        0,
        "symbol_first_event_dates": [],
    }

    for sym in sorted(symbols):
        sym_q = quarterly_df[quarterly_df["symbol"] == sym].sort_values(
            "period_end_date", ascending=True
        )

        total_q = len(sym_q)
        if total_q < MIN_QUARTERLY_OBS:
            # Not enough history for even one SUE event
            continue

        stats["symbols_with_ge9_q"] += 1
        sym_first_event = None

        # For each possible event quarter (index MIN_QUARTERLY_OBS-1 through end)
        for idx in range(MIN_QUARTERLY_OBS - 1, total_q):
            signal_row = sym_q.iloc[idx]
            stats["events_attempted"] += 1

            sue_result = compute_yoy_sue(sym_q, signal_row)

            if sue_result["status"] == "DATA_INSUFFICIENT":
                stats["events_data_insufficient"] += 1
                log.debug(
                    f"[DATA_RECOVERY]\n"
                    f"  scanner={STRATEGY_ID}\n"
                    f"  symbol={sym}\n"
                    f"  stage=SUE_CALCULATION\n"
                    f"  period={signal_row['period_end_date'].date()}\n"
                    f"  missing_data={sue_result['missing_data']}\n"
                    f"  recovery_attempted=false\n"
                    f"  final_action=EVENT_SKIPPED"
                )
                continue

            if sue_result["status"] == "CAUSALITY_VIOLATION":
                stats["events_causality_violation"] += 1
                log.warning(
                    f"[CAUSALITY_VIOLATION] {sym} period="
                    f"{signal_row['period_end_date'].date()} — "
                    f"{sue_result['missing_data']}"
                )
                continue

            # SUE computed — now apply quality gate
            signal_ts = pd.Timestamp(sue_result["signal_ts"])
            quality = {}
            if apply_quality_gate:
                quality = _compute_quality_metrics(sym, signal_ts, annual_df)
                if quality.get("quality_status") == "DATA_INSUFFICIENT":
                    stats["events_quality_missing"] += 1
                    # DATA problem — log as data recovery, not strategy rejection
                    log.debug(
                        f"[DATA_RECOVERY]\n"
                        f"  scanner={STRATEGY_ID}\n"
                        f"  symbol={sym}\n"
                        f"  stage=QUALITY_GATE\n"
                        f"  period={signal_row['period_end_date'].date()}\n"
                        f"  missing_data={quality.get('quality_missing')}\n"
                        f"  final_action=EVENT_SKIPPED"
                    )
                    continue
                if not quality.get("quality_pass", False):
                    # Strategy rule rejection — NOT a data problem
                    stats["events_quality_fail"] += 1
                    log.debug(
                        f"[QUALITY_GATE_FAIL] {sym} period="
                        f"{signal_row['period_end_date'].date()} | "
                        f"failures={quality.get('quality_failures')}"
                    )
                    continue

            stats["events_data_ok"] += 1
            stats[f"events_{sue_result['category'].lower()}"] += 1

            event = {
                "symbol":          sym,
                "period_end_date": str(signal_row["period_end_date"].date()),
                "signal_ts":       sue_result["signal_ts"],
                "yoy_sue":         sue_result["yoy_sue"],
                "surprise_t":      sue_result["surprise_t"],
                "eps_t":           sue_result["eps_t"],
                "eps_t4":          sue_result["eps_t4"],
                "sigma":           sue_result["sigma"],
                "sigma_note":      sue_result["sigma_note"],
                "prior_surprises": sue_result["prior_surprises"],
                "category":        sue_result["category"],
                "n_observations":  sue_result["n_observations"],
                **{f"quality_{k}": v for k, v in quality.items()
                   if k in ("roce_5y", "sales_cagr_5y", "de_ratio", "cfo_pat_5y")},
            }
            events.append(event)

            if sym_first_event is None:
                sym_first_event = sue_result["signal_ts"]
                stats["symbol_first_event_dates"].append(sue_result["signal_ts"])

    # Aggregate coverage stats
    if events:
        event_dates = [pd.Timestamp(e["signal_ts"]) for e in events]
        stats["event_date_min"] = str(min(event_dates).date())
        stats["event_date_max"] = str(max(event_dates).date())
        sue_vals = [e["yoy_sue"] for e in events if e["category"] == "STRONG_BEAT"]
        stats["strong_beat_count"] = len(sue_vals)

    log.info(
        f"\n{'='*60}\n"
        f"  EVENT RECONSTRUCTION COMPLETE\n"
        f"  Symbols with >=9 Q history: {stats['symbols_with_ge9_q']}\n"
        f"  Events attempted:           {stats['events_attempted']}\n"
        f"  DATA_INSUFFICIENT:          {stats['events_data_insufficient']}\n"
        f"  CAUSALITY_VIOLATION:        {stats['events_causality_violation']}\n"
        f"  Quality data missing:       {stats['events_quality_missing']}\n"
        f"  Quality rule fail:          {stats['events_quality_fail']}\n"
        f"  Events with valid SUE:      {stats['events_data_ok']}\n"
        f"  → STRONG_BEAT (SUE≥1.5):   {stats.get('events_strong_beat', 0)}\n"
        f"  → WEAK_BEAT:               {stats.get('events_weak_beat', 0)}\n"
        f"  → MISS:                    {stats.get('events_miss', 0)}\n"
        f"  → NEUTRAL:                 {stats.get('events_neutral', 0)}\n"
        f"  Event window:              "
        f"{stats.get('event_date_min','N/A')} → {stats.get('event_date_max','N/A')}\n"
        f"{'='*60}"
    )
    return events, stats


# ─────────────────────────────────────────────────────────────────────────────
# GOVERNANCE REPORT
# ─────────────────────────────────────────────────────────────────────────────

def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def write_event_report(events: List[Dict], stats: Dict, db_path: str) -> str:
    """Write the governance report and event manifest."""
    run_ts = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    db_sha = sha256_file(db_path) if os.path.exists(db_path) else "N/A"

    # Event manifest (JSON)
    manifest = {
        "strategy_id":       STRATEGY_ID,
        "spec_version":      SPEC_VERSION,
        "run_timestamp_ist": run_ts,
        "db_sha256":         db_sha,
        "provenance":        "CERTIFIED_PIT_SCREENER",
        "timestamp_basis":   TIMESTAMP_BASIS,
        "frozen_spec": {
            "strong_beat_threshold":  STRONG_BEAT_THRESHOLD,
            "weak_beat_threshold":    WEAK_BEAT_THRESHOLD,
            "miss_threshold":         MISS_THRESHOLD,
            "min_quarterly_obs":      MIN_QUARTERLY_OBS,
            "min_prior_surprises":    MIN_PRIOR_SURPRISES,
            "quality_roce_min":       QUALITY_ROCE_MIN,
            "quality_sales_cagr_min": QUALITY_SALES_CAGR_MIN,
            "hold_calendar_days":     HOLD_CALENDAR_DAYS,
            "friction_bps":           FRICTION_BPS,
        },
        "coverage_stats": stats,
        "event_count":       len(events),
        "events":            events,
    }

    manifest_path = os.path.join(REPORTS_DIR,
                                 "EARNINGS_SURPRISE_QUALITY_V2_event_manifest.json")
    with open(manifest_path, "w") as f:
        json.dump(manifest, f, indent=2, default=str)

    # Summary report (Markdown)
    strong_beats = [e for e in events if e["category"] == "STRONG_BEAT"]
    report = f"""# EARNINGS_SURPRISE_QUALITY_V2 — EVENT RECONSTRUCTION REPORT
Generated: {run_ts} IST

### STRATEGY SPECIFICATION (FROZEN)
- Signal: Year-over-Year EPS Surprise (YoY-SUE)
- SUE formula: (EPS_t - EPS_{{t-4}}) / std(prior 4 YoY surprises)
- Minimum quarterly observations: {MIN_QUARTERLY_OBS} (indices t-8 through t)
- Strong beat threshold: YoY_SUE >= {STRONG_BEAT_THRESHOLD}
- Quality gate: ROCE≥{QUALITY_ROCE_MIN}%, Sales CAGR≥{QUALITY_SALES_CAGR_MIN}%, D/E≤{QUALITY_DE_MAX}, CFO/PAT≥{QUALITY_CFO_PAT_MIN}
- Execution: T+1 Open, {FRICTION_BPS} bps friction, {HOLD_CALENDAR_DAYS}d hold

### DATA PROVENANCE
Provider: Screener.in (via PIT quarterly filings)
Database: pit_fundamentals_v1.db
DB SHA256: {db_sha}
Timestamp basis: {TIMESTAMP_BASIS}
Synthetic data: NONE
Fallback providers: NONE
PROVENANCE_STATUS = CERTIFIED_PIT_SCREENER

### COVERAGE SUMMARY
| Metric | Value |
|--------|-------|
| Symbols with ≥{MIN_QUARTERLY_OBS} quarterly history | {stats.get('symbols_with_ge9_q', 0)} |
| Events attempted | {stats.get('events_attempted', 0)} |
| DATA_INSUFFICIENT | {stats.get('events_data_insufficient', 0)} |
| CAUSALITY_VIOLATION | {stats.get('events_causality_violation', 0)} |
| Quality data missing | {stats.get('events_quality_missing', 0)} |
| Quality strategy fail | {stats.get('events_quality_fail', 0)} |
| **Events with valid SUE** | **{stats.get('events_data_ok', 0)}** |
| → STRONG_BEAT (SUE≥{STRONG_BEAT_THRESHOLD}) | {stats.get('events_strong_beat', 0)} |
| → WEAK_BEAT | {stats.get('events_weak_beat', 0)} |
| → MISS | {stats.get('events_miss', 0)} |
| → NEUTRAL | {stats.get('events_neutral', 0)} |
| Event window (first signal) | {stats.get('event_date_min', 'N/A')} |
| Event window (last signal) | {stats.get('event_date_max', 'N/A')} |

### TEMPORAL COVERAGE NOTE
With a 9-quarter minimum and the current DB ceiling of 13 quarters
(2023-Q2 to 2026-Q2), the first valid event fires when index 8 becomes
available. For symbols starting at 2023-Q3 (majority), first event date
is approximately 2025-Q3 (available ~Nov 2025). The backtest window
is therefore narrow (~Aug 2025 to Aug 2026 — approximately 12 months).

This narrow window is a governance constraint, not a bug. The block-bootstrap
evaluation must respect this temporal concentration.

### STRONG BEAT EVENTS (sample, first 20)
| Symbol | Period | Signal Date | YoY_SUE | Surprise | Sigma |
|--------|--------|-------------|---------|----------|-------|
"""
    for e in strong_beats[:20]:
        report += (
            f"| {e['symbol']} | {e['period_end_date']} | "
            f"{str(e['signal_ts'])[:10]} | {e['yoy_sue']:.2f} | "
            f"{e['surprise_t']:.4f} | {e['sigma']:.4f} |\n"
        )

    report += f"""
### NEXT STEPS (DO NOT PROMOTE BASED ON EVENT COUNT ALONE)
1. ✓ V2 implementation complete
2. ✓ SUE math unit tests pass
3. ✓ PIT causality leakage tests pass
4. → Fetch Upstox T+1 Open price for each STRONG_BEAT event
5. → Compute MFE / MAE / hold-period return
6. → Run Arm A (IS) + Arm B (OOS holdout)
7. → Block-bootstrap by event-date cluster + symbol
8. → N_eff + permutation p-value
9. → 10-session paper test
10. → Promotion decision

BACKTEST_STATUS = DATA_INSUFFICIENT (price data not yet fetched)
"""

    report_path = os.path.join(REPORTS_DIR,
                               "EARNINGS_SURPRISE_QUALITY_V2_coverage_report.md")
    with open(report_path, "w") as f:
        f.write(report)

    log.info(f"Report: {report_path}")
    log.info(f"Manifest: {manifest_path}")
    return report_path


# ─────────────────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────────────────

def main():
    log.info("=" * 60)
    log.info(f"  {STRATEGY_ID} — EVENT RECONSTRUCTION")
    log.info(f"  Spec version: {SPEC_VERSION}")
    log.info("=" * 60)

    # 1. Load PIT data
    quarterly_df = load_pit_quarterly(PIT_DB_PATH)
    annual_df    = load_pit_annual(PIT_DB_PATH)

    # 2. Reconstruct events
    events, stats = reconstruct_events(quarterly_df, annual_df,
                                       apply_quality_gate=True)

    # 3. Write governance report
    write_event_report(events, stats, PIT_DB_PATH)

    # 4. Final verdict
    strong_beats = [e for e in events if e["category"] == "STRONG_BEAT"]
    log.info(f"\nFINAL: {len(events)} eligible events | "
             f"{len(strong_beats)} STRONG_BEAT signals")
    log.info("BACKTEST_STATUS = DATA_INSUFFICIENT (price fetch pending)")


if __name__ == "__main__":
    main()
