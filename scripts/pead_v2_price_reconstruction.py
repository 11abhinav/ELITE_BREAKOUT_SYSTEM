#!/usr/bin/env python3
"""
scripts/pead_v2_price_reconstruction.py
========================================
EARNINGS_SURPRISE_QUALITY_V2 — Upstox Price Reconstruction

For every qualified event (N=371), fetches Upstox 1D OHLCV from the
historical candle API and computes:
  - T+1 trading date (next actual NSE session — not calendar +1)
  - T+1 Open = execution price
  - Forward returns: +1D, +3D, +5D, +10D, +20D (close-to-close from T+1)
  - MFE: max favorable excursion within 60 calendar days from T+1
  - MAE: max adverse excursion within 60 calendar days from T+1
  - Hold return: close at T+60 (or last available if held through end)

Design:
  - One Upstox API call per symbol (not per event) — fetches full
    2+ year window, then slices per event. Efficient and rate-limit-friendly.
  - Checkpointing per symbol to disk (JSON) — safe to re-run after failure.
  - Strict T+1 resolution: skips weekends + NSE public holidays.
  - No calendar-day +1 shortcut. No same-day open/close used.
  - All provenance recorded per event row.

Governance:
  - Price source: Upstox Historical Candle API V2 (1D interval)
  - Instrument key: NSE_EQ|<ISIN> (via certified mapper)
  - Friction: 5 bps round-trip applied to hold-period return
  - No future information in T+1 resolution
  - Synthetic data: NONE
"""

from __future__ import annotations

import os
import sys
import json
import time
import hashlib
import logging
from datetime import datetime, date, timedelta
from typing import Dict, List, Optional, Tuple, Any
from urllib.parse import quote

import numpy as np
import pandas as pd
import requests

# ── paths ────────────────────────────────────────────────────────────────────
_BASE = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(_BASE, "app"))

MANIFEST_PATH = os.path.join(
    _BASE, "reports", "earnings_surprise_quality_v2",
    "EARNINGS_SURPRISE_QUALITY_V2_event_manifest.json"
)
REPORTS_DIR   = os.path.join(_BASE, "reports", "earnings_surprise_quality_v2")
CKPT_DIR      = os.path.join(_BASE, "data", "pead_v2_price_cache")
RESULTS_CSV   = os.path.join(REPORTS_DIR, "EARNINGS_SURPRISE_QUALITY_V2_price_results.csv")
os.makedirs(CKPT_DIR, exist_ok=True)
os.makedirs(REPORTS_DIR, exist_ok=True)

# ── Upstox ───────────────────────────────────────────────────────────────────
try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(_BASE, ".env"))
except Exception:
    pass

UPSTOX_TOKEN = os.getenv("UPSTOX_ACCESS_TOKEN", "")
if not UPSTOX_TOKEN:
    raise EnvironmentError(
        "UPSTOX_ACCESS_TOKEN not set. Export it or add to .env"
    )

# ── instrument mapper ─────────────────────────────────────────────────────────
try:
    from market_data.providers.upstox_instrument_mapper import get_upstox_instrument_key
except Exception:
    def get_upstox_instrument_key(sym: str) -> Optional[str]:
        return None

# ── constants ─────────────────────────────────────────────────────────────────
STRATEGY_ID        = "EARNINGS_SURPRISE_QUALITY_V2"
HOLD_CALENDAR_DAYS = 60
FRICTION_BPS       = 5.0
HOLD_DAYS          = [1, 3, 5, 10, 20]
API_DELAY_S        = 0.5          # between Upstox calls
FETCH_FROM_DATE    = "2019-01-01" # wide enough to cover all events
FETCH_TO_DATE      = datetime.now().strftime("%Y-%m-%d")

# NSE public holidays (approximate — add new dates as needed)
# Source: NSE official holiday calendar
NSE_HOLIDAYS: set = {
    # 2020
    date(2020,  2, 21), date(2020,  3, 10), date(2020,  4,  2),
    date(2020,  4, 6),  date(2020,  4, 10), date(2020,  4, 14),
    date(2020,  5,  1), date(2020, 10,  2), date(2020, 11, 16),
    date(2020, 11, 30), date(2020, 12, 25),
    # 2021
    date(2021,  1, 26), date(2021,  3, 11), date(2021,  3, 29),
    date(2021,  4,  2), date(2021,  4, 14), date(2021,  4, 21),
    date(2021,  5, 13), date(2021,  7, 21), date(2021,  8, 19),
    date(2021,  9, 10), date(2021, 10, 2),  date(2021, 10, 15),
    date(2021, 11,  4), date(2021, 11,  5), date(2021, 12, 24),
    # 2022
    date(2022,  1, 26), date(2022,  3,  1), date(2022,  3, 18),
    date(2022,  4, 14), date(2022,  4, 15), date(2022,  5,  3),
    date(2022,  8, 9),  date(2022,  8, 15), date(2022,  8, 31),
    date(2022, 10,  2), date(2022, 10,  5), date(2022, 10, 24),
    date(2022, 10, 26), date(2022,  11, 8), date(2022, 12, 26),
    # 2023
    date(2023,  1, 26), date(2023,  2, 18), date(2023,  3,  7),
    date(2023,  3, 22), date(2023,  3, 30), date(2023,  4,  4),
    date(2023,  4, 7),  date(2023,  4, 14), date(2023,  5,  1),
    date(2023,  6, 28), date(2023,  8, 15), date(2023,  9, 19),
    date(2023, 10,  2), date(2023, 10, 24), date(2023, 11, 13),
    date(2023, 11, 14), date(2023, 11, 27), date(2023, 12, 25),
    # 2024
    date(2024,  1, 22), date(2024,  1, 26), date(2024,  3, 8),
    date(2024,  3, 25), date(2024,  3, 29), date(2024,  4, 11),
    date(2024,  4, 14), date(2024,  4, 17), date(2024,  4, 21),
    date(2024,  5, 23), date(2024,  6, 17), date(2024,  7, 17),
    date(2024,  8, 15), date(2024, 10,  2), date(2024, 10,  2),
    date(2024, 11,  1), date(2024, 11, 15), date(2024, 12, 25),
    # 2025
    date(2025,  1, 26), date(2025,  2, 26), date(2025,  3, 14),
    date(2025,  3, 31), date(2025,  4, 10), date(2025,  4, 14),
    date(2025,  4, 18), date(2025,  5,  1), date(2025,  8, 15),
    date(2025,  8, 27), date(2025, 10,  2), date(2025, 10,  2),
    date(2025, 10, 21), date(2025, 10, 22), date(2025, 11,  5),
    date(2025, 12, 25),
    # 2026
    date(2026,  1, 26), date(2026,  3, 20), date(2026,  4,  2),
    date(2026,  4,  3), date(2026,  4, 14), date(2026,  5,  1),
    date(2026,  8, 15),
}

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# NSE TRADING CALENDAR
# ─────────────────────────────────────────────────────────────────────────────

def is_nse_trading_day(d: date) -> bool:
    """Return True if d is a valid NSE trading session."""
    return d.weekday() < 5 and d not in NSE_HOLIDAYS


def next_trading_day(d: date, n: int = 1) -> date:
    """
    Return the nth trading day strictly AFTER d.
    n=1 → next trading session (T+1)
    n=3 → 3 trading days after d (T+3)
    """
    current = d + timedelta(days=1)
    count = 0
    while count < n:
        if is_nse_trading_day(current):
            count += 1
            if count < n:
                current += timedelta(days=1)
        else:
            current += timedelta(days=1)
    return current


def trading_days_between(start: date, candle_dates: List[date]) -> List[date]:
    """
    Given a sorted list of available candle dates, return those that are
    NSE trading days between start (exclusive) and 60 calendar days later.
    """
    end_calendar = start + timedelta(days=HOLD_CALENDAR_DAYS)
    return [d for d in candle_dates if start < d <= end_calendar
            and is_nse_trading_day(d)]


# ─────────────────────────────────────────────────────────────────────────────
# UPSTOX PRICE FETCH
# ─────────────────────────────────────────────────────────────────────────────

def _upstox_headers() -> dict:
    return {
        "Accept":        "application/json",
        "Authorization": f"Bearer {UPSTOX_TOKEN}",
    }


def fetch_daily_candles(sym: str) -> Optional[pd.DataFrame]:
    """
    Fetch full Upstox 1D OHLCV history for sym.
    Returns DataFrame with columns [Date, Open, High, Low, Close, Volume]
    sorted ascending by Date, or None on failure.

    Checkpoints to disk so repeated runs skip already-fetched symbols.
    """
    ckpt = os.path.join(CKPT_DIR, f"{sym}.parquet")
    if os.path.exists(ckpt):
        try:
            df = pd.read_parquet(ckpt)
            log.debug(f"  [{sym}] loaded from checkpoint ({len(df)} rows)")
            return df
        except Exception:
            pass

    ikey = get_upstox_instrument_key(sym)
    if not ikey:
        log.warning(f"  [{sym}] instrument key not found — SKIP")
        return None

    enc_key = quote(ikey, safe="")
    url = (f"https://api.upstox.com/v2/historical-candle/"
           f"{enc_key}/day/{FETCH_TO_DATE}/{FETCH_FROM_DATE}")

    for attempt in range(1, 4):
        try:
            r = requests.get(url, headers=_upstox_headers(), timeout=20)
            if r.status_code == 429:
                wait = attempt * 15
                log.warning(f"  [{sym}] Upstox 429 — wait {wait}s")
                time.sleep(wait)
                continue
            if r.status_code in (401, 403):
                log.error(f"  [{sym}] Upstox auth error {r.status_code} — check token")
                return None
            if r.status_code != 200:
                log.debug(f"  [{sym}] HTTP {r.status_code}")
                return None
            candles = r.json().get("data", {}).get("candles", [])
            if not candles:
                log.debug(f"  [{sym}] no candles returned")
                return None

            df = pd.DataFrame(
                candles, columns=["Date", "Open", "High", "Low", "Close", "Volume", "OI"]
            )
            df["Date"] = pd.to_datetime(df["Date"]).dt.date
            df = df[["Date", "Open", "High", "Low", "Close", "Volume"]].copy()
            df = df[df["Date"].apply(is_nse_trading_day)]  # drop any holiday candles
            df = df.sort_values("Date").reset_index(drop=True)

            # Persist checkpoint
            df.to_parquet(ckpt, index=False)
            log.debug(f"  [{sym}] fetched {len(df)} candles → cached")
            return df

        except Exception as e:
            log.debug(f"  [{sym}] attempt {attempt}: {e}")
            time.sleep(attempt * 3)

    return None


# ─────────────────────────────────────────────────────────────────────────────
# PER-EVENT PRICE EXTRACTION
# ─────────────────────────────────────────────────────────────────────────────

def extract_event_prices(
    event: Dict,
    sym_candles: pd.DataFrame,
) -> Dict[str, Any]:
    """
    For a single event, resolve T+1 open and forward returns.

    Parameters
    ----------
    event       : event dict from manifest (has signal_ts, symbol, category, yoy_sue)
    sym_candles : DataFrame with Date (date), Open, High, Low, Close, Volume

    Returns
    -------
    result dict — always has 'price_fetch_status' key.
    """
    signal_ts  = pd.Timestamp(event["signal_ts"])
    signal_date = signal_ts.date()
    sym         = event["symbol"]

    candle_dates = sorted(sym_candles["Date"].tolist())
    candle_map   = sym_candles.set_index("Date").to_dict("index")

    # ── T+1 resolution: next actual NSE trading session ──────────────────────
    t1_date = next_trading_day(signal_date, n=1)

    # Advance T+1 forward if candles unavailable (e.g., suspended stock)
    attempts = 0
    while t1_date not in candle_map and attempts < 10:
        t1_date = next_trading_day(t1_date, n=1)
        attempts += 1

    if t1_date not in candle_map:
        return {
            "symbol":             sym,
            "period_end_date":    event["period_end_date"],
            "signal_ts":          event["signal_ts"],
            "category":           event["category"],
            "yoy_sue":            event["yoy_sue"],
            "price_fetch_status": "NO_CANDLE_AT_T1",
            "t1_date":            str(t1_date),
            "data_provenance":    "UPSTOX_V2_1D",
        }

    t1_candle = candle_map[t1_date]
    t1_open   = float(t1_candle["Open"])
    t1_close  = float(t1_candle["Close"])

    if t1_open <= 0:
        return {
            "symbol":             sym,
            "period_end_date":    event["period_end_date"],
            "signal_ts":          event["signal_ts"],
            "category":           event["category"],
            "yoy_sue":            event["yoy_sue"],
            "price_fetch_status": "T1_OPEN_ZERO_OR_NEGATIVE",
            "t1_date":            str(t1_date),
            "data_provenance":    "UPSTOX_V2_1D",
        }

    # ── Forward returns (T+N close relative to T+1 open) ─────────────────────
    fwd_returns = {}
    for n in HOLD_DAYS:
        tn_date = next_trading_day(t1_date, n=n)
        if tn_date in candle_map:
            tn_close = float(candle_map[tn_date]["Close"])
            fwd_returns[f"ret_{n}d_bps"] = round(
                (tn_close / t1_open - 1) * 10000, 2  # in bps
            )
            fwd_returns[f"ret_{n}d_pct"] = round(
                (tn_close / t1_open - 1) * 100, 4
            )
        else:
            fwd_returns[f"ret_{n}d_bps"] = None
            fwd_returns[f"ret_{n}d_pct"] = None

    # ── MFE / MAE over 60 calendar days from T+1 ─────────────────────────────
    hold_dates = trading_days_between(t1_date, candle_dates)
    mfe_pct    = None
    mae_pct    = None
    hold_ret_pct = None
    hold_days_actual = 0

    if hold_dates:
        highs  = [float(candle_map[d]["High"])  for d in hold_dates if d in candle_map]
        lows   = [float(candle_map[d]["Low"])   for d in hold_dates if d in candle_map]
        closes = [float(candle_map[d]["Close"]) for d in hold_dates if d in candle_map]

        if highs and lows:
            mfe_pct = round((max(highs) / t1_open - 1) * 100, 4)
            mae_pct = round((min(lows)  / t1_open - 1) * 100, 4)

        if closes:
            hold_ret_pct     = round((closes[-1] / t1_open - 1) * 100, 4)
            hold_days_actual = len(hold_dates)

    # Apply friction to hold-period return (5 bps round-trip)
    friction_pct = FRICTION_BPS / 100
    hold_ret_net_pct = (round(hold_ret_pct - friction_pct, 4)
                        if hold_ret_pct is not None else None)

    return {
        "symbol":             sym,
        "period_end_date":    event["period_end_date"],
        "signal_ts":          event["signal_ts"],
        "signal_date":        str(signal_date),
        "t1_date":            str(t1_date),
        "t1_open":            t1_open,
        "t1_close":           t1_close,
        "category":           event["category"],
        "yoy_sue":            event["yoy_sue"],
        "surprise_t":         event.get("surprise_t"),
        "sigma":              event.get("sigma"),
        "quality_roce_5y":    event.get("quality_roce_5y"),
        "quality_sales_cagr_5y": event.get("quality_sales_cagr_5y"),
        **fwd_returns,
        "mfe_pct":            mfe_pct,
        "mae_pct":            mae_pct,
        "hold_ret_gross_pct": hold_ret_pct,
        "hold_ret_net_pct":   hold_ret_net_pct,
        "hold_days_actual":   hold_days_actual,
        "data_provenance":    "UPSTOX_V2_1D",
        "instrument_key":     get_upstox_instrument_key(sym) or "UNKNOWN",
        "price_fetch_status": "OK",
    }


# ─────────────────────────────────────────────────────────────────────────────
# MAIN RECONSTRUCTION
# ─────────────────────────────────────────────────────────────────────────────

def run_price_reconstruction(events: List[Dict]) -> pd.DataFrame:
    """
    Fetch Upstox prices for all events.
    One API call per unique symbol (batched), then slice per event.
    """
    unique_symbols = sorted(set(e["symbol"] for e in events))
    log.info(f"Fetching Upstox 1D candles for {len(unique_symbols)} unique symbols "
             f"({len(events)} events)...")

    sym_candles: Dict[str, Optional[pd.DataFrame]] = {}
    fetch_ok = fetch_fail = fetch_ckpt = 0

    for i, sym in enumerate(unique_symbols):
        ckpt = os.path.join(CKPT_DIR, f"{sym}.parquet")
        from_ckpt = os.path.exists(ckpt)

        df = fetch_daily_candles(sym)
        if df is not None and not df.empty:
            sym_candles[sym] = df
            if from_ckpt:
                fetch_ckpt += 1
            else:
                fetch_ok += 1
                time.sleep(API_DELAY_S)   # rate limit only for live fetches
        else:
            sym_candles[sym] = None
            fetch_fail += 1

        if (i + 1) % 25 == 0:
            log.info(f"  Progress: {i+1}/{len(unique_symbols)} symbols | "
                     f"ok={fetch_ok} ckpt={fetch_ckpt} fail={fetch_fail}")

    log.info(f"Fetch complete: ok={fetch_ok} ckpt={fetch_ckpt} fail={fetch_fail}")

    # ── Per-event extraction ──────────────────────────────────────────────────
    results = []
    no_candle = price_ok = 0

    for event in events:
        sym = event["symbol"]
        candles = sym_candles.get(sym)

        if candles is None or candles.empty:
            results.append({
                "symbol":             sym,
                "period_end_date":    event["period_end_date"],
                "signal_ts":          event["signal_ts"],
                "category":           event["category"],
                "yoy_sue":            event["yoy_sue"],
                "price_fetch_status": "CANDLES_UNAVAILABLE",
                "data_provenance":    "UPSTOX_V2_1D",
            })
            no_candle += 1
            continue

        result = extract_event_prices(event, candles)
        results.append(result)
        if result["price_fetch_status"] == "OK":
            price_ok += 1

    log.info(f"Event extraction: price_ok={price_ok} no_candle={no_candle} "
             f"other_fail={len(events)-price_ok-no_candle}")

    return pd.DataFrame(results)


# ─────────────────────────────────────────────────────────────────────────────
# COMPARATIVE ANALYSIS
# ─────────────────────────────────────────────────────────────────────────────

def print_comparative_analysis(df: pd.DataFrame):
    """
    Print STRONG_BEAT vs WEAK_BEAT vs NEUTRAL vs MISS comparison.
    This is the primary research question: does large YoY-SUE add value?
    """
    ok = df[df["price_fetch_status"] == "OK"].copy()
    if ok.empty:
        log.warning("No successfully priced events for analysis.")
        return

    categories = ["STRONG_BEAT", "WEAK_BEAT", "NEUTRAL", "MISS"]
    metrics = ["ret_1d_pct", "ret_3d_pct", "ret_5d_pct",
               "ret_10d_pct", "ret_20d_pct",
               "hold_ret_net_pct", "mfe_pct", "mae_pct"]

    log.info("\n" + "="*75)
    log.info("  COMPARATIVE ANALYSIS: STRONG_BEAT vs WEAK_BEAT vs NEUTRAL vs MISS")
    log.info("  (All events within same quality-gate-passed population)")
    log.info("="*75)

    # Per-category summary table
    rows = []
    for cat in categories:
        sub = ok[ok["category"] == cat]
        if sub.empty:
            continue
        row = {"category": cat, "N": len(sub)}
        for m in metrics:
            vals = sub[m].dropna()
            row[f"{m}_mean"] = round(vals.mean(), 4) if len(vals) else None
            row[f"{m}_med"]  = round(vals.median(), 4) if len(vals) else None
            row[f"{m}_std"]  = round(vals.std(), 4) if len(vals) else None
            row[f"{m}_pos%"] = round((vals > 0).mean() * 100, 1) if len(vals) else None
        rows.append(row)

    summary_df = pd.DataFrame(rows)

    # Print compact table
    def _fmt(val, decimals=2):
        if val is None or (isinstance(val, float) and np.isnan(val)):
            return "  N/A"
        return f"{float(val):.{decimals}f}%"

    log.info(f"\n{'Category':<14} {'N':>5} | "
             f"{'ret1d':>8} {'ret5d':>8} {'ret20d':>8} {'hold_net':>9} | "
             f"{'MFE':>8} {'MAE':>8} | {'pos20d%':>8}")
    log.info("-" * 80)
    for _, r in summary_df.iterrows():
        log.info(
            f"{r['category']:<14} {int(r['N']):>5} | "
            f"{_fmt(r.get('ret_1d_pct_mean')):>8} "
            f"{_fmt(r.get('ret_5d_pct_mean')):>8} "
            f"{_fmt(r.get('ret_20d_pct_mean')):>8} "
            f"{_fmt(r.get('hold_ret_net_pct_mean')):>9} | "
            f"{_fmt(r.get('mfe_pct_mean')):>8} "
            f"{_fmt(r.get('mae_pct_mean')):>8} | "
            f"{_fmt(r.get('ret_20d_pct_pos%'), decimals=1):>8}"
        )

    log.info("\n[NOTE] Block-bootstrap by event-date cluster required before "
             "attributing any observed difference to signal quality.")
    log.info("       Raw event count does NOT imply statistical power.\n")

    return summary_df


# ─────────────────────────────────────────────────────────────────────────────
# GOVERNANCE REPORT
# ─────────────────────────────────────────────────────────────────────────────

def write_price_report(df: pd.DataFrame):
    ok = df[df["price_fetch_status"] == "OK"]
    run_ts = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")

    cats = ok.groupby("category").agg(
        N=("yoy_sue", "count"),
        mean_hold_net=("hold_ret_net_pct", "mean"),
        median_hold_net=("hold_ret_net_pct", "median"),
        mean_ret_20d=("ret_20d_pct", "mean"),
        pos_ret_20d_pct=("ret_20d_pct", lambda x: (x > 0).mean() * 100),
        mean_mfe=("mfe_pct", "mean"),
        mean_mae=("mae_pct", "mean"),
    ).reset_index()

    report = f"""# EARNINGS_SURPRISE_QUALITY_V2 — PRICE RECONSTRUCTION REPORT
Generated: {run_ts} IST

### DATA PROVENANCE
Provider: Upstox Historical Candle API V2
Endpoint: /v2/historical-candle/{{instrument_key}}/day/{{to}}/{{from}}
Exchange: NSE (NSE_EQ|ISIN instrument keys)
Instrument resolution: certified mapper (market_data.providers.upstox_instrument_mapper)
Interval: 1D (daily OHLCV)
Friction: {FRICTION_BPS} bps round-trip applied to hold-period returns
T+1 basis: next actual NSE trading session (weekends + official holidays skipped)
Synthetic data: NONE
Fallback providers: NONE
PROVENANCE_STATUS = CERTIFIED_UPSTOX_V2_1D

### PRICE FETCH SUMMARY
Total events: {len(df)}
Successfully priced (T+1 OK): {len(ok)}
Failed / no candle: {len(df) - len(ok)}

### RETURN COMPARISON BY CATEGORY
| Category | N | Hold_Net_Mean | Hold_Net_Med | Ret20d_Mean | Win20d% | MFE | MAE |
|----------|---|---------------|--------------|-------------|---------|-----|-----|
"""
    for _, r in cats.iterrows():
        report += (
            f"| {r['category']} | {int(r['N'])} | "
            f"{r['mean_hold_net']:.2f}% | {r['median_hold_net']:.2f}% | "
            f"{r['mean_ret_20d']:.2f}% | {r['pos_ret_20d_pct']:.1f}% | "
            f"{r['mean_mfe']:.2f}% | {r['mean_mae']:.2f}% |\n"
        )

    report += f"""
### GOVERNANCE NOTES
- These raw means are DESCRIPTIVE only.
- Statistical significance requires block-bootstrap by event-date cluster.
- PEAD events cluster around earnings seasons (Q1: Feb/Mar, Q2: May,
  Q3: Aug, Q4: Nov). Adjacent events share macro regime → N_eff << raw N.
- Do NOT promote based on this table alone.

### NEXT STEP
Run block-bootstrap CI + permutation p-value before any promotion decision.

BACKTEST_STATUS = IN_CERTIFICATION (price reconstruction complete; 
                  statistical battery pending)
"""
    rpt_path = os.path.join(REPORTS_DIR,
                            "EARNINGS_SURPRISE_QUALITY_V2_price_report.md")
    with open(rpt_path, "w") as f:
        f.write(report)

    # Event-window audit (per user's correction request)
    audit_rows = []
    for sym in df["symbol"].unique():
        sym_events = df[df["symbol"] == sym].sort_values("signal_ts")
        first = sym_events.iloc[0]
        audit_rows.append({
            "symbol":         sym,
            "first_signal_ts": first["signal_ts"],
            "category":       first["category"],
            "yoy_sue":        first["yoy_sue"],
            "price_ok":       (sym_events["price_fetch_status"] == "OK").sum(),
        })
    audit_df = pd.DataFrame(audit_rows).sort_values("first_signal_ts")
    audit_path = os.path.join(REPORTS_DIR, "event_window_per_symbol_audit.csv")
    audit_df.to_csv(audit_path, index=False)

    log.info(f"Price report: {rpt_path}")
    log.info(f"Per-symbol event audit: {audit_path}")
    return rpt_path


# ─────────────────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────────────────

def main():
    log.info("=" * 65)
    log.info(f"  {STRATEGY_ID} — UPSTOX PRICE RECONSTRUCTION")
    log.info("=" * 65)

    # Load events
    with open(MANIFEST_PATH) as f:
        manifest = json.load(f)
    events = manifest["events"]
    log.info(f"Loaded {len(events)} events from manifest")
    log.info(f"  DB SHA256: {manifest['db_sha256']}")

    # Run price fetch
    results_df = run_price_reconstruction(events)

    # Save raw results
    results_df.to_csv(RESULTS_CSV, index=False)
    log.info(f"Results saved: {RESULTS_CSV} ({len(results_df)} rows)")

    # Comparative analysis
    print_comparative_analysis(results_df)

    # Governance report
    write_price_report(results_df)

    ok_count   = (results_df["price_fetch_status"] == "OK").sum()
    sb_count   = len(results_df[(results_df["price_fetch_status"] == "OK") &
                                (results_df["category"] == "STRONG_BEAT")])
    log.info(f"\nFINAL: {ok_count}/{len(results_df)} events priced | "
             f"{sb_count} STRONG_BEAT with prices")
    log.info("BACKTEST_STATUS = IN_CERTIFICATION (statistical battery pending)")


if __name__ == "__main__":
    main()
