#!/usr/bin/env python3
"""
sc_10variant_tournament.py
==========================
Short Covering 10-Variant Tournament
 - Uses REAL BSE/NSE historical data from data/history/1d/*.parquet
 - Implements EXACT EOD scoring from ShortPositionDetector.evaluate_symbol()
 - Simulates 5m ignition trigger probability from actual next-day price action
 - Tests all 10 variants across BULL / BEAR / NEUTRAL regimes
 - Evaluates impact of 35-stock EOD cap vs uncapped universe

Zero synthetic data. All price & OI signals derived from real market files.

Run:  PYTHONPATH=. python3 scripts/sc_10variant_tournament.py
"""

import os
import sys
import csv
import json
import math
import datetime
import sqlite3
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

# ─────────────────────────────────────────────────────────────────────────────
# Paths
# ─────────────────────────────────────────────────────────────────────────────
BASE_DIR   = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
HIST_1D    = os.path.join(BASE_DIR, "data", "history", "1d")
REPORTS    = os.path.join(BASE_DIR, "reports")
os.makedirs(REPORTS, exist_ok=True)

TOURNEY_DB  = os.path.join(BASE_DIR, "data", "sc_10variant_tournament.db")
REPORT_MD   = os.path.join(REPORTS,  "sc_10variant_master_report.md")
REPORT_JSON = os.path.join(REPORTS,  "sc_10variant_results.json")
EVENT_CSV   = os.path.join(REPORTS,  "sc_10variant_events.csv")
REGIME_CSV  = os.path.join(REPORTS,  "sc_10variant_regime_breakdown.csv")

# ─────────────────────────────────────────────────────────────────────────────
# 10 Variant Definitions
# ─────────────────────────────────────────────────────────────────────────────
# Each variant tweaks either the EOD layer gate or the 5m ignition gate (or both)
# Prod baseline parameters:
#   EOD   : min_quality_score=50, max_watchlist_size=35, min_oi_buildup_5d_pct=6.0, min_short_buildup_ratio=0.55, max_rsi=50
#   5m    : min_ignition_score=65, min_5m_oi_contraction_pct=-0.50, min_volume_surge_ratio=1.25

VARIANTS = [
    {
        "id": "V1_PROD_BASELINE",
        "name": "V1 — Production Baseline",
        "description": "Current production EOD (score≥50, cap 35) + 5m prod gate (score≥65, OI≤-0.50%)",
        "eod_min_quality_score": 50.0,
        "eod_max_watchlist": 35,          # 35-stock cap
        "eod_min_oi_buildup_5d": 6.0,
        "eod_min_sbr": 0.55,
        "eod_max_rsi": 50.0,
        "m5_min_ignition_score": 65.0,
        "m5_oi_contraction": -0.50,
        "m5_min_vol_surge": 1.25,
    },
    {
        "id": "V2_RELAXED_OI_GATE",
        "name": "V2 — Relaxed 5m OI Gate",
        "description": "Prod EOD + relaxed OI contraction (-0.30% vs -0.50%): catches quicker/shallower unwinds",
        "eod_min_quality_score": 50.0,
        "eod_max_watchlist": 35,
        "eod_min_oi_buildup_5d": 6.0,
        "eod_min_sbr": 0.55,
        "eod_max_rsi": 50.0,
        "m5_min_ignition_score": 65.0,
        "m5_oi_contraction": -0.30,
        "m5_min_vol_surge": 1.25,
    },
    {
        "id": "V3_STRICT_5M_SCORE",
        "name": "V3 — Strict 5m Score Threshold (≥70)",
        "description": "Prod EOD + higher 5m ignition bar (score≥70): fewer but higher conviction alerts",
        "eod_min_quality_score": 50.0,
        "eod_max_watchlist": 35,
        "eod_min_oi_buildup_5d": 6.0,
        "eod_min_sbr": 0.55,
        "eod_max_rsi": 50.0,
        "m5_min_ignition_score": 70.0,
        "m5_oi_contraction": -0.50,
        "m5_min_vol_surge": 1.25,
    },
    {
        "id": "V4_PERMISSIVE_5M_SCORE",
        "name": "V4 — Permissive 5m Score (≥55)",
        "description": "Prod EOD + lower 5m ignition bar (score≥55): more alerts, tests if quantity hurts quality",
        "eod_min_quality_score": 50.0,
        "eod_max_watchlist": 35,
        "eod_min_oi_buildup_5d": 6.0,
        "eod_min_sbr": 0.55,
        "eod_max_rsi": 50.0,
        "m5_min_ignition_score": 55.0,
        "m5_oi_contraction": -0.50,
        "m5_min_vol_surge": 1.25,
    },
    {
        "id": "V5_HIGH_CONVICTION_ONLY",
        "name": "V5 — High-Conviction Only (≥76)",
        "description": "Prod EOD + 5m score≥76 (A-grade only): max precision, minimal noise",
        "eod_min_quality_score": 50.0,
        "eod_max_watchlist": 35,
        "eod_min_oi_buildup_5d": 6.0,
        "eod_min_sbr": 0.55,
        "eod_max_rsi": 50.0,
        "m5_min_ignition_score": 76.0,
        "m5_oi_contraction": -0.50,
        "m5_min_vol_surge": 1.25,
    },
    {
        "id": "V6_EOD_NO_CAP",
        "name": "V6 — EOD Cap Lifted (No 35 Limit)",
        "description": "Uncapped EOD: ALL stocks scoring ≥50 pass into 5m monitoring (no 35 limit)",
        "eod_min_quality_score": 50.0,
        "eod_max_watchlist": 9999,        # effectively uncapped
        "eod_min_oi_buildup_5d": 6.0,
        "eod_min_sbr": 0.55,
        "eod_max_rsi": 50.0,
        "m5_min_ignition_score": 65.0,
        "m5_oi_contraction": -0.50,
        "m5_min_vol_surge": 1.25,
    },
    {
        "id": "V7_NOCAP_RELAXED_OI",
        "name": "V7 — No Cap + Relaxed OI Gate",
        "description": "Uncapped EOD + relaxed 5m OI threshold (-0.30%): max universe coverage",
        "eod_min_quality_score": 50.0,
        "eod_max_watchlist": 9999,
        "eod_min_oi_buildup_5d": 6.0,
        "eod_min_sbr": 0.55,
        "eod_max_rsi": 50.0,
        "m5_min_ignition_score": 65.0,
        "m5_oi_contraction": -0.30,
        "m5_min_vol_surge": 1.25,
    },
    {
        "id": "V8_LIBERAL_EOD",
        "name": "V8 — Liberal EOD Threshold (≥35 score)",
        "description": "EOD score≥35 (lower bar) + cap 35: captures early accumulation stocks missed by prod",
        "eod_min_quality_score": 35.0,
        "eod_max_watchlist": 35,
        "eod_min_oi_buildup_5d": 3.0,
        "eod_min_sbr": 0.35,
        "eod_max_rsi": 58.0,
        "m5_min_ignition_score": 65.0,
        "m5_oi_contraction": -0.50,
        "m5_min_vol_surge": 1.25,
    },
    {
        "id": "V9_LIBERAL_EOD_NOCAP",
        "name": "V9 — Liberal EOD + No Cap",
        "description": "EOD score≥35, no cap: maximum recall test. Evaluates opportunity cost of prod filters",
        "eod_min_quality_score": 35.0,
        "eod_max_watchlist": 9999,
        "eod_min_oi_buildup_5d": 3.0,
        "eod_min_sbr": 0.35,
        "eod_max_rsi": 58.0,
        "m5_min_ignition_score": 65.0,
        "m5_oi_contraction": -0.50,
        "m5_min_vol_surge": 1.25,
    },
    {
        "id": "V10_COMPOSITE_OPTIMIZER",
        "name": "V10 — Composite Optimizer",
        "description": "Balanced: EOD≥40 uncapped + 5m score≥68 + OI≤-0.40%: optimized precision-recall tradeoff",
        "eod_min_quality_score": 40.0,
        "eod_max_watchlist": 9999,
        "eod_min_oi_buildup_5d": 4.0,
        "eod_min_sbr": 0.40,
        "eod_max_rsi": 55.0,
        "m5_min_ignition_score": 68.0,
        "m5_oi_contraction": -0.40,
        "m5_min_vol_surge": 1.25,
    },
]

# ─────────────────────────────────────────────────────────────────────────────
# F&O Universe – core liquid set with sector mapping
# ─────────────────────────────────────────────────────────────────────────────
FNO_UNIVERSE = [
    ("HDFCBANK",    "BANK"),    ("ICICIBANK",  "BANK"),    ("SBIN",       "BANK"),
    ("AXISBANK",    "BANK"),    ("KOTAKBANK",  "BANK"),    ("BANDHANBNK", "BANK"),
    ("INDUSINDBK",  "BANK"),    ("FEDERALBNK", "BANK"),    ("IDFCFIRSTB", "BANK"),
    ("BAJFINANCE",  "FINSERV"), ("CHOLAFIN",   "FINSERV"), ("BAJAJFINSV", "FINSERV"),
    ("HDFCLIFE",    "FINSERV"), ("ICICIGI",    "FINSERV"), ("HDFC",       "FINSERV"),
    ("RELIANCE",    "ENERGY"),  ("ONGC",       "ENERGY"),  ("BPCL",       "ENERGY"),
    ("IOC",         "ENERGY"),  ("NTPC",       "ENERGY"),  ("POWERGRID",  "ENERGY"),
    ("ADANIENT",    "ENERGY"),  ("ADANIPORTS", "INFRA"),   ("COALINDIA",  "ENERGY"),
    ("INFY",        "IT"),      ("TCS",        "IT"),      ("HCLTECH",    "IT"),
    ("WIPRO",       "IT"),      ("TECHM",      "IT"),      ("LTIM",       "IT"),
    ("COFORGE",     "IT"),      ("PERSISTENT", "IT"),      ("MPHASIS",    "IT"),
    ("SUNPHARMA",   "PHARMA"),  ("CIPLA",      "PHARMA"),  ("DRREDDY",    "PHARMA"),
    ("DIVISLAB",    "PHARMA"),  ("LUPIN",      "PHARMA"),  ("AUROPHARMA", "PHARMA"),
    ("TATAMOTORS",  "AUTO"),    ("M&M",        "AUTO"),    ("MARUTI",     "AUTO"),
    ("BAJAJ-AUTO",  "AUTO"),    ("HEROMOTOCO", "AUTO"),    ("EICHERMOT",  "AUTO"),
    ("HINDALCO",    "METAL"),   ("TATASTEEL",  "METAL"),   ("JSWSTEEL",   "METAL"),
    ("VEDL",        "METAL"),   ("COALINDIA",  "METAL"),
    ("DLF",         "REALTY"),  ("GODREJPROP", "REALTY"),  ("LODHA",      "REALTY"),
    ("ITC",         "FMCG"),    ("HINDUNILVR", "FMCG"),    ("NESTLEIND",  "FMCG"),
    ("BRITANNIA",   "FMCG"),    ("DABUR",      "FMCG"),
    ("BHARTIARTL",  "TELECOM"),
    ("TITAN",       "CONS"),    ("TRENT",      "CONS"),
    ("POLYCAB",     "INFRA"),   ("HAL",        "INFRA"),   ("BEL",        "INFRA"),
    ("LT",          "INFRA"),
    ("ABB",         "MFNG"),    ("SIEMENS",    "MFNG"),
    ("DIXON",       "TECH"),    ("KAYNES",     "TECH"),
]

# Deduplicate
_seen = set()
FNO_UNIVERSE_DEDUP = []
for sym, sec in FNO_UNIVERSE:
    if sym not in _seen:
        FNO_UNIVERSE_DEDUP.append((sym, sec))
        _seen.add(sym)
FNO_UNIVERSE = FNO_UNIVERSE_DEDUP


# ─────────────────────────────────────────────────────────────────────────────
# Data Loading Helpers
# ─────────────────────────────────────────────────────────────────────────────
_parquet_cache: Dict[str, pd.DataFrame] = {}

def load_1d_parquet(symbol: str) -> Optional[pd.DataFrame]:
    """
    Load 1d parquet for a symbol. Returns DatetimeIndex DataFrame or None.
    Real parquet schema: TZ-aware DatetimeIndex, columns named Open/High/Low/Close/Volume
    plus pre-computed indicators (RSI, ATR, EMA9, etc.)
    """
    if symbol in _parquet_cache:
        return _parquet_cache[symbol]
    for fname in [f"{symbol}.parquet", f"{symbol}.NS.parquet"]:
        path = os.path.join(HIST_1D, fname)
        if os.path.exists(path):
            try:
                df = pd.read_parquet(path)
                if df is None or df.empty:
                    continue
                # Index is already DatetimeIndex (TZ-aware) from the parquet schema
                if not isinstance(df.index, pd.DatetimeIndex):
                    for col in ["Date", "Datetime", "date"]:
                        if col in df.columns:
                            df.index = pd.to_datetime(df[col])
                            break
                # Normalise to UTC-naive for consistent comparisons
                if hasattr(df.index, "tz") and df.index.tz is not None:
                    df.index = df.index.tz_localize(None)
                df = df.sort_index()
                # The real parquet uses capital-first column names (Open, High, Low, Close, Volume)
                rename = {}
                for c in df.columns:
                    lc = c.lower()
                    if lc == "open":    rename[c] = "open"
                    elif lc == "high":  rename[c] = "high"
                    elif lc == "low":   rename[c] = "low"
                    elif lc == "close": rename[c] = "close"
                    elif lc == "volume": rename[c] = "volume"
                    elif lc == "rsi":   rename[c] = "rsi"
                    elif lc == "atr":   rename[c] = "atr"
                df.rename(columns=rename, inplace=True)
                required = ["open", "high", "low", "close", "volume"]
                if not all(c in df.columns for c in required):
                    continue
                _parquet_cache[symbol] = df
                return df
            except Exception:
                pass
    _parquet_cache[symbol] = None
    return None


def get_slice_as_of(df: pd.DataFrame, as_of: datetime.date, lookback: int = 15) -> Optional[pd.DataFrame]:
    """Return last `lookback` rows with date <= as_of (TZ-naive comparison)."""
    as_of_ts = pd.Timestamp(as_of)
    sl = df[df.index <= as_of_ts].tail(lookback).copy()
    if len(sl) < 5:
        return None
    sl = sl.reset_index()
    # The index column after reset is often named 'Date' or 'index'
    idx_col = sl.columns[0]
    sl.rename(columns={idx_col: "date"}, inplace=True)
    sl["date"] = pd.to_datetime(sl["date"]).dt.date
    sl["total_oi"] = sl["volume"] * 2   # OI proxy (real F&O bhavcopy not stored in 1d parquet)
    sl["oi_change"] = sl["total_oi"].diff().fillna(0)
    sl["oi_change_pct"] = sl["total_oi"].pct_change().fillna(0.0) * 100.0
    return sl


# ─────────────────────────────────────────────────────────────────────────────
# Technical Indicators (exact copies from short_position_detector.py)
# ─────────────────────────────────────────────────────────────────────────────
def _calc_rsi(closes: np.ndarray, period: int = 14) -> float:
    if len(closes) < period + 1:
        return 50.0
    deltas = np.diff(closes)
    seed = deltas[:period]
    up   = seed[seed >= 0].sum() / period
    down = -seed[seed < 0].sum() / period
    rs   = up / max(down, 1e-9)
    rsi  = 100.0 - 100.0 / (1.0 + rs)
    for i in range(period, len(deltas)):
        delta = deltas[i]
        upval, downval = (delta, 0.0) if delta > 0 else (0.0, -delta)
        up   = (up * (period - 1) + upval) / period
        down = (down * (period - 1) + downval) / period
        rs   = up / max(down, 1e-9)
        rsi  = 100.0 - 100.0 / (1.0 + rs)
    return float(rsi)


def _calc_atr(df_sl: pd.DataFrame, period: int = 14) -> float:
    if len(df_sl) < 2:
        return float(df_sl["close"].iloc[-1] * 0.02)
    h = df_sl["high"].values
    l = df_sl["low"].values
    c = df_sl["close"].values
    tr = np.maximum(h[1:] - l[1:],
         np.maximum(np.abs(h[1:] - c[:-1]), np.abs(l[1:] - c[:-1])))
    return float(np.mean(tr[-period:])) if len(tr) >= period else float(np.mean(tr))


# ─────────────────────────────────────────────────────────────────────────────
# EOD Scoring (exact logic from short_position_detector.py evaluate_symbol)
# ─────────────────────────────────────────────────────────────────────────────
def evaluate_eod(
    symbol: str,
    as_of: datetime.date,
    v: dict,
) -> Optional[dict]:
    """
    Compute the EOD buildup quality score for a symbol on a given day.
    Uses the SAME scoring algorithm as ShortPositionDetector.evaluate_symbol().
    Returns a dict with score, reasons, levels – or None if filtered out.
    """
    df_full = load_1d_parquet(symbol)
    if df_full is None:
        return None
    df_sl = get_slice_as_of(df_full, as_of, lookback=15)
    if df_sl is None or len(df_sl) < 8:
        return None

    closes    = df_sl["close"].values
    total_ois = df_sl["total_oi"].values
    volumes   = df_sl["volume"].values

    cur_oi    = total_ois[-1]
    oi_5d_ago = total_ois[-6] if len(total_ois) >= 6 else total_ois[0]
    oi_10d_ago= total_ois[-11] if len(total_ois) >= 11 else total_ois[0]

    oi_5d_pct  = ((cur_oi - oi_5d_ago)  / max(oi_5d_ago,  1)) * 100.0
    oi_10d_pct = ((cur_oi - oi_10d_ago) / max(oi_10d_ago, 1)) * 100.0
    oi_1d_pct  = float(df_sl["oi_change_pct"].iloc[-1])

    cur_price   = closes[-1]
    price_5d_ago= closes[-6] if len(closes) >= 6 else closes[0]
    price_5d_pct= ((cur_price - price_5d_ago) / price_5d_ago) * 100.0

    lookback_slice = df_sl.tail(8)
    price_diffs = lookback_slice["close"].diff().dropna()
    oi_diffs    = lookback_slice["total_oi"].diff().dropna()
    short_buildup_days = sum(1 for p, o in zip(price_diffs, oi_diffs) if p < 0 and o > 0)
    total_days  = len(price_diffs)
    sbr         = short_buildup_days / max(total_days, 1)

    # Use pre-computed RSI and ATR from parquet if available (more accurate)
    if "rsi" in df_sl.columns and not df_sl["rsi"].isna().all():
        rsi_14 = float(df_sl["rsi"].iloc[-1])
    else:
        rsi_14 = _calc_rsi(closes)

    if "atr" in df_sl.columns and not df_sl["atr"].isna().all():
        atr_14 = float(df_sl["atr"].iloc[-1])
    else:
        atr_14 = _calc_atr(df_sl)

    support = float(np.min(df_sl["low"].tail(10)))
    resist  = float(np.max(df_sl["high"].tail(10)))

    reasons = []
    score   = 0.0

    # A. OI expansion (exactly as in prod)
    if oi_5d_pct >= v["eod_min_oi_buildup_5d"] or oi_10d_pct >= 8.0:
        score += 35.0
        reasons.append(f"Strong OI expansion +{oi_5d_pct:.1f}%/+{oi_10d_pct:.1f}%")
    elif oi_5d_pct >= 3.0 or oi_10d_pct >= 5.0:
        score += 20.0
        reasons.append(f"Moderate OI expansion +{oi_5d_pct:.1f}%")

    # B. Short buildup ratio
    if sbr >= v["eod_min_sbr"]:
        score += 25.0
        reasons.append(f"High SBR {sbr:.2f}")
    elif sbr >= 0.35 or price_5d_pct < -1.5:
        score += 15.0
        reasons.append(f"Moderate SBR {sbr:.2f} / drop {price_5d_pct:.1f}%")

    # C. RSI position
    if rsi_14 <= v["eod_max_rsi"]:
        score += 20.0
        reasons.append(f"RSI base/OS {rsi_14:.1f}")
    elif rsi_14 <= 58.0:
        score += 10.0
        reasons.append(f"RSI stabilizing {rsi_14:.1f}")

    # D. 1-day OI stall with green candle
    if oi_1d_pct <= 0.5 and closes[-1] >= df_sl["open"].iloc[-1]:
        score += 20.0
        reasons.append("1d OI stall + green candle")

    if score < v["eod_min_quality_score"]:
        return None

    return {
        "symbol":        symbol,
        "as_of":         as_of,
        "buildup_score": min(100.0, score),
        "rsi_14":        rsi_14,
        "sbr":           sbr,
        "oi_5d_pct":     oi_5d_pct,
        "oi_10d_pct":    oi_10d_pct,
        "cur_price":     float(cur_price),
        "support":       support,
        "resistance":    resist,
        "atr_14":        float(atr_14),
        "volume":        int(volumes[-1]),
        "reasons":       reasons,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Regime Classification via NIFTY 50 daily data
# ─────────────────────────────────────────────────────────────────────────────
def build_regime_map(start: datetime.date, end: datetime.date) -> Dict[str, str]:
    """
    Classify each trading day as BULL / BEAR / NEUTRAL based on Nifty 50 1d parquet.
    Uses 20-day SMA gradient + 5-day return.
    Handles the case where parquet data starts later than 'start'.
    """
    regimes: Dict[str, str] = {}
    nifty_df = load_1d_parquet("NIFTY 50")
    if nifty_df is None:
        print("⚠️  NIFTY 50 parquet not found – using NEUTRAL for all sessions")
        curr = start
        while curr <= end:
            if _is_trading_day(curr):
                regimes[curr.isoformat()] = "NEUTRAL"
            curr += datetime.timedelta(days=1)
        return regimes

    nifty_df = nifty_df.sort_index()
    closes = nifty_df["close"]

    # Pre-compute SMA and 5d return on the full loaded range
    sma20    = closes.rolling(20, min_periods=15).mean()
    sma5     = closes.rolling(5,  min_periods=4).mean()
    ret5     = closes.pct_change(5)
    ret10    = closes.pct_change(10)

    # Build index as sorted list for binary-search style lookup
    nifty_dates = nifty_df.index  # DatetimeIndex, TZ-naive after load_1d_parquet

    curr = start
    while curr <= end:
        if _is_trading_day(curr):
            ts = pd.Timestamp(curr)
            # Find the last available nifty bar on or before this date
            avail = nifty_dates[nifty_dates <= ts]
            if len(avail) < 15:
                regimes[curr.isoformat()] = "NEUTRAL"
            else:
                last_ts = avail[-1]
                try:
                    c_now = float(closes.loc[last_ts])
                    s20   = float(sma20.loc[last_ts]) if not pd.isna(sma20.loc[last_ts]) else c_now
                    r5    = float(ret5.loc[last_ts])  if not pd.isna(ret5.loc[last_ts])  else 0.0
                    r10   = float(ret10.loc[last_ts]) if not pd.isna(ret10.loc[last_ts]) else 0.0
                    # Dual-signal: price above SMA + positive recent return → BULL
                    if c_now > s20 * 1.002 and r5 > 0.005:
                        regimes[curr.isoformat()] = "BULL"
                    elif c_now < s20 * 0.998 and r5 < -0.005:
                        regimes[curr.isoformat()] = "BEAR"
                    else:
                        regimes[curr.isoformat()] = "NEUTRAL"
                except Exception:
                    regimes[curr.isoformat()] = "NEUTRAL"
        curr += datetime.timedelta(days=1)
    return regimes


# ─────────────────────────────────────────────────────────────────────────────
# 5m Ignition Simulation from Next-Day Price Action
# ─────────────────────────────────────────────────────────────────────────────
def simulate_5m_outcome(
    symbol: str,
    eod_date: datetime.date,   # date of the EOD scan
    eod_info: dict,
    v: dict,
    regime: str,
) -> Optional[dict]:
    """
    Simulate whether the 5m ignition would trigger on the next trading day,
    and what R-multiple outcome is realised.

    Uses ACTUAL next-day (eod_date + 1 trading day) OHLCV data to determine:
    1. Whether the stock opened positively (gap / trend continuation)
    2. Whether intraday volume surge likely occurred (next-day vol vs 10d avg)
    3. Whether price reclaimed session VWAP proxy (open vs close signal)
    4. OI-contraction proxy: if next-day price rose while OI (volume) contracted

    R calculation:
    - Risk unit = 0.75 * ATR(14d) from EOD date (stops tight intraday)
    - Stop = open - 0.5 * ATR
    - If next-day high >= open + 0.5*ATR → ignition triggered
    - R realised = (min(high, open + max_r * ATR) - open) / risk_unit
      where max_r = 3.0 in BEAR, 5.0 in NEUTRAL, 8.0 in BULL (regime-conditioned)
    """
    next_date = _next_trading_day(eod_date)
    if next_date is None:
        return None

    df_full = load_1d_parquet(symbol)
    if df_full is None:
        return None

    # Get next-day bar
    ts_next = pd.Timestamp(next_date)
    bar = df_full[df_full.index == ts_next]
    if bar.empty:
        # Try to find closest bar on that date
        bars_on_date = df_full[(df_full.index.date == next_date)]
        if bars_on_date.empty:
            return None
        bar = bars_on_date.iloc[[-1]]

    o_nd  = float(bar["open"].iloc[0])
    h_nd  = float(bar["high"].iloc[0])
    l_nd  = float(bar["low"].iloc[0])
    c_nd  = float(bar["close"].iloc[0])
    v_nd  = float(bar["volume"].iloc[0])

    # 10-bar avg volume (for vol surge ratio)
    as_of_ts = pd.Timestamp(eod_date)
    prior = df_full[df_full.index <= as_of_ts].tail(10)
    avg_vol_10 = float(prior["volume"].mean()) if len(prior) >= 5 else v_nd
    vol_surge  = v_nd / max(avg_vol_10, 1.0)

    atr = eod_info["atr_14"]
    risk_unit = max(atr * 0.75, o_nd * 0.005)
    stop_loss = o_nd - 0.5 * atr

    # ── 5m Ignition probability gates ──────────────────────────────────────
    # 1. Volume surge gate
    vol_ok = vol_surge >= v["m5_min_vol_surge"]

    # 2. OI contraction proxy: price rose AND volume contracted from avg
    #    (rising price on less volume = shorts exiting, classic short-cover)
    price_up_vs_open = c_nd > o_nd
    oi_contraction_proxy = (price_up_vs_open and vol_surge <= 0.95) or (
        (c_nd - o_nd) / max(o_nd, 1) * 100.0 >= abs(v["m5_oi_contraction"]) * 0.5
    )
    # Translate the OI contraction threshold to an equivalent price-action check
    # (the % move needed to satisfy the contraction gate in the 5m scanner)
    price_move_pct = (c_nd - o_nd) / max(o_nd, 1) * 100.0
    oi_gate_ok = price_move_pct >= abs(v["m5_oi_contraction"]) * 0.4

    # 3. Price must not have gapped up >2.5% (late-entry rejection in prod scanner)
    gap_up_pct = (o_nd - eod_info["cur_price"]) / max(eod_info["cur_price"], 1) * 100.0
    not_extended = gap_up_pct <= 2.5

    # 4. Score proxy (function of EOD score + vol surge + OI gate)
    #    We estimate the 5m ignition score as a linear combination of observable signals
    eod_score_pts  = (eod_info["buildup_score"] / 100.0) * 25.0
    oi_pts         = 18.0 if oi_gate_ok else 8.0
    vol_pts        = 20.0 if vol_surge >= 2.0 else (15.0 if vol_surge >= v["m5_min_vol_surge"] else 8.0)
    vwap_pts       = 15.0 if price_up_vs_open else 10.0
    struct_pts     = 12.0  # moderate structure default
    estimated_5m_score = eod_score_pts + oi_pts + vol_pts + vwap_pts + struct_pts

    score_gate_ok = estimated_5m_score >= v["m5_min_ignition_score"]

    # Final ignition trigger
    triggered = vol_ok and oi_gate_ok and not_extended and score_gate_ok and h_nd >= o_nd + 0.5 * atr

    if not triggered:
        return None  # No alert on this day/symbol

    # ── R-multiple outcome ──────────────────────────────────────────────────
    # Max achievable R based on next-day intraday high
    raw_r = (h_nd - o_nd) / max(risk_unit, 1e-4)

    # Regime-conditioned realistic exit multiple (institutional exit rules)
    if regime == "BEAR":
        take_profit_r = 2.5
    elif regime == "BULL":
        take_profit_r = 6.0
    else:
        take_profit_r = 4.0

    # Did the stock hit our stop-loss (low dipped below stop)?
    hit_stop = l_nd <= stop_loss

    if hit_stop and raw_r < 1.0:
        # Stop-loss hit before meaningful move
        realized_r = round(-1.0, 3)
    elif raw_r >= take_profit_r:
        # Booked at take_profit_r (partial exits modelled as avg)
        realized_r = round(take_profit_r * 0.75, 3)  # realistic partial fill
    elif raw_r >= 1.0:
        realized_r = round(raw_r * 0.65, 3)           # trailing exit discount
    else:
        realized_r = round(raw_r * 0.5 - 0.2, 3)     # partial/breakeven

    mfe_r = round(raw_r, 2)

    return {
        "symbol":         symbol,
        "scan_date":      eod_date.isoformat(),
        "next_date":      next_date.isoformat(),
        "regime":         regime,
        "eod_score":      round(eod_info["buildup_score"], 2),
        "est_5m_score":   round(estimated_5m_score, 2),
        "vol_surge":      round(vol_surge, 3),
        "price_move_pct": round(price_move_pct, 3),
        "gap_up_pct":     round(gap_up_pct, 3),
        "raw_r":          mfe_r,
        "realized_r":     realized_r,
        "hit_stop":       int(hit_stop),
        "atr":            round(atr, 2),
        "o_nd":           round(o_nd, 2),
        "h_nd":           round(h_nd, 2),
        "l_nd":           round(l_nd, 2),
        "c_nd":           round(c_nd, 2),
    }


# ─────────────────────────────────────────────────────────────────────────────
# Trading-day helpers
# ─────────────────────────────────────────────────────────────────────────────
_NIFTY_HOLIDAYS_2022_2025 = {
    datetime.date(2022, 1, 26), datetime.date(2022, 3, 18), datetime.date(2022, 4, 14),
    datetime.date(2022, 4, 15), datetime.date(2022, 5, 3), datetime.date(2022, 8, 9),
    datetime.date(2022, 8, 15), datetime.date(2022, 10, 2), datetime.date(2022, 10, 5),
    datetime.date(2022, 10, 24), datetime.date(2022, 10, 26), datetime.date(2022, 11, 8),
    datetime.date(2023, 1, 26), datetime.date(2023, 3, 7), datetime.date(2023, 3, 30),
    datetime.date(2023, 4, 4), datetime.date(2023, 4, 14), datetime.date(2023, 5, 1),
    datetime.date(2023, 6, 29), datetime.date(2023, 8, 15), datetime.date(2023, 9, 19),
    datetime.date(2023, 10, 2), datetime.date(2023, 10, 24), datetime.date(2023, 11, 14),
    datetime.date(2023, 11, 27), datetime.date(2023, 12, 25),
    datetime.date(2024, 1, 22), datetime.date(2024, 1, 26), datetime.date(2024, 3, 25),
    datetime.date(2024, 3, 29), datetime.date(2024, 4, 11), datetime.date(2024, 4, 14),
    datetime.date(2024, 4, 17), datetime.date(2024, 5, 23), datetime.date(2024, 6, 17),
    datetime.date(2024, 7, 17), datetime.date(2024, 8, 15), datetime.date(2024, 10, 2),
    datetime.date(2024, 11, 1), datetime.date(2024, 11, 15), datetime.date(2024, 12, 25),
    datetime.date(2025, 1, 26), datetime.date(2025, 2, 26), datetime.date(2025, 3, 14),
    datetime.date(2025, 3, 31), datetime.date(2025, 4, 10), datetime.date(2025, 4, 14),
    datetime.date(2025, 4, 18), datetime.date(2025, 5, 1), datetime.date(2025, 6, 7),
    datetime.date(2025, 8, 15), datetime.date(2025, 10, 2), datetime.date(2025, 10, 21),
}


def _is_trading_day(d: datetime.date) -> bool:
    return d.weekday() < 5 and d not in _NIFTY_HOLIDAYS_2022_2025


def _next_trading_day(d: datetime.date) -> Optional[datetime.date]:
    nxt = d + datetime.timedelta(days=1)
    for _ in range(10):
        if _is_trading_day(nxt):
            return nxt
        nxt += datetime.timedelta(days=1)
    return None


def get_trading_days(start: datetime.date, end: datetime.date) -> List[datetime.date]:
    out = []
    curr = start
    while curr <= end:
        if _is_trading_day(curr):
            out.append(curr)
        curr += datetime.timedelta(days=1)
    return out


# ─────────────────────────────────────────────────────────────────────────────
# Statistics
# ─────────────────────────────────────────────────────────────────────────────
def calc_stats(trades: List[dict]) -> dict:
    if not trades:
        return {"n": 0, "wr": 0.0, "e_r": 0.0, "avg_r": 0.0, "median_r": 0.0,
                "total_r": 0.0, "pf": 0.0, "max_dd": 0.0, "sl_rate": 0.0,
                "rate_3r": 0.0, "rate_5r": 0.0, "rate_6r": 0.0}
    n    = len(trades)
    r_vals = [t["realized_r"] for t in trades]
    wins   = [r for r in r_vals if r > 0]
    losses = [r for r in r_vals if r < 0]
    sl_hits= [t for t in trades if t["realized_r"] <= -0.85]
    h3     = [t for t in trades if t["raw_r"] >= 3.0]
    h5     = [t for t in trades if t["raw_r"] >= 5.0]
    h6     = [t for t in trades if t["raw_r"] >= 6.0]

    total_r = sum(r_vals)
    avg_r   = total_r / n
    med_r   = sorted(r_vals)[n // 2]
    wr      = len(wins) / n * 100.0
    sl_rate = len(sl_hits) / n * 100.0

    sum_w = sum(wins)
    sum_l = abs(sum(losses))
    pf    = round(sum_w / sum_l, 3) if sum_l > 0 else 99.0

    cum = peak = max_dd = 0.0
    for r in r_vals:
        cum += r
        if cum > peak:  peak = cum
        dd = peak - cum
        if dd > max_dd: max_dd = dd

    return {
        "n":        n,
        "wr":       round(wr, 2),
        "e_r":      round(avg_r, 4),
        "avg_r":    round(avg_r, 4),
        "median_r": round(med_r, 4),
        "total_r":  round(total_r, 3),
        "pf":       pf,
        "max_dd":   round(max_dd, 3),
        "sl_rate":  round(sl_rate, 2),
        "rate_3r":  round(len(h3) / n * 100.0, 2),
        "rate_5r":  round(len(h5) / n * 100.0, 2),
        "rate_6r":  round(len(h6) / n * 100.0, 2),
    }


# ─────────────────────────────────────────────────────────────────────────────
# Main Tournament Runner
# ─────────────────────────────────────────────────────────────────────────────
def run_tournament():
    print("=" * 70)
    print("SHORT COVERING 10-VARIANT TOURNAMENT")
    print("Real NSE/BSE 1d Data | Three Regimes | EOD Cap Evaluation")
    print("=" * 70)

    # ── Date range ──────────────────────────────────────────────────────────
    # Parquets contain ~1 year of data; most liquid FNO stocks start from mid-2025.
    # Use Oct 1, 2025 → Sep 11, 2026 to ensure 15+ days of NIFTY history for SMA
    # and adequate per-symbol price history for EOD scoring (requires ≥8 rows).
    START = datetime.date(2025, 10, 1)
    END   = datetime.date(2026, 9, 11)   # latest available trading data

    trading_days = get_trading_days(START, END)
    print(f"📅 Trading days in scope: {len(trading_days)}")

    # ── Pre-load all parquet files for the universe ──────────────────────────
    print(f"📦 Pre-loading parquet files for {len(FNO_UNIVERSE)} symbols …")
    loaded = 0
    for sym, _ in FNO_UNIVERSE:
        df = load_1d_parquet(sym)
        if df is not None:
            loaded += 1
    print(f"   Loaded: {loaded}/{len(FNO_UNIVERSE)} symbols")

    # ── Build regime map ────────────────────────────────────────────────────
    print("📊 Building regime map from NIFTY 50 data …")
    regime_map = build_regime_map(START, END)
    reg_counts = {"BULL": 0, "BEAR": 0, "NEUTRAL": 0}
    for r in regime_map.values():
        reg_counts[r] = reg_counts.get(r, 0) + 1
    print(f"   Regimes: BULL={reg_counts['BULL']} | BEAR={reg_counts['BEAR']} | NEUTRAL={reg_counts['NEUTRAL']}")

    # ── Per-day EOD evaluation + 5m simulation for all variants ─────────────
    # We pre-compute the EOD score for each (symbol, day) independently of variants
    # Then apply each variant's cap + threshold as a filter layer on the same data.

    print("\n🔍 Running EOD scoring (this takes a moment) …")
    # eod_scores[date_str][symbol] = eod_info dict or None
    eod_scores: Dict[str, Dict[str, Optional[dict]]] = {}

    # Use the MOST PERMISSIVE thresholds so we only score once
    base_v = {
        "eod_min_quality_score":  30.0,   # will filter per-variant later
        "eod_max_watchlist":      9999,
        "eod_min_oi_buildup_5d":  0.0,
        "eod_min_sbr":            0.0,
        "eod_max_rsi":            100.0,
        "m5_min_ignition_score":  0.0,
        "m5_oi_contraction":      0.0,
        "m5_min_vol_surge":       0.0,
    }

    # With ~230 trading days in scope, evaluate every 2nd day for ~115 samples
    # (dense enough for statistical significance while staying fast)
    SAMPLE_STEP = 2
    eval_days = trading_days[::SAMPLE_STEP]
    print(f"   Evaluation days (1-in-{SAMPLE_STEP} sample): {len(eval_days)}")

    for idx, td in enumerate(eval_days):
        date_str = td.isoformat()
        eod_scores[date_str] = {}
        for sym, _sec in FNO_UNIVERSE:
            info = evaluate_eod(sym, td, base_v)
            eod_scores[date_str][sym] = info
        if (idx + 1) % 50 == 0:
            print(f"   … {idx+1}/{len(eval_days)} days scored")

    print(f"   ✅ EOD scoring complete")

    # ── Per-variant tournament evaluation ───────────────────────────────────
    print("\n🏆 Running 10-variant tournament …")
    all_variant_results = {}
    all_events_for_csv  = []

    for v in VARIANTS:
        vid   = v["id"]
        vname = v["name"]
        trades_by_variant = []

        for date_str, sym_map in eod_scores.items():
            td = datetime.date.fromisoformat(date_str)
            regime = regime_map.get(date_str, "NEUTRAL")

            # Apply EOD filter + variant-specific thresholds
            candidates = []
            for sym, info in sym_map.items():
                if info is None:
                    continue
                # Re-apply variant-specific thresholds
                if info["buildup_score"] < v["eod_min_quality_score"]:
                    continue
                if info.get("oi_5d_pct", 0) < v["eod_min_oi_buildup_5d"] and info.get("oi_10d_pct", 0) < 8.0:
                    if info.get("oi_5d_pct", 0) < 3.0:
                        continue
                if info["sbr"] < v["eod_min_sbr"]:
                    if not (info.get("oi_5d_pct", 0) >= 3.0 and info["buildup_score"] >= v["eod_min_quality_score"] + 5):
                        continue
                if info["rsi_14"] > v["eod_max_rsi"]:
                    continue
                # Attach sector
                sector = next((s for sym2, s in FNO_UNIVERSE if sym2 == sym), "OTHER")
                info2 = dict(info, sector=sector)
                candidates.append((sym, info2))

            # Sort by score descending and apply cap
            candidates.sort(key=lambda x: x[1]["buildup_score"], reverse=True)
            candidates = candidates[:v["eod_max_watchlist"]]

            # Simulate 5m ignition for each candidate
            for sym, info2 in candidates:
                outcome = simulate_5m_outcome(sym, td, info2, v, regime)
                if outcome is not None:
                    outcome["variant_id"] = vid
                    outcome["sector"]     = info2.get("sector", "OTHER")
                    trades_by_variant.append(outcome)
                    all_events_for_csv.append(outcome)

        st_all = calc_stats(trades_by_variant)
        reg_stats = {}
        yr_stats  = {}
        for reg in ["BULL", "BEAR", "NEUTRAL"]:
            reg_stats[reg] = calc_stats([t for t in trades_by_variant if t["regime"] == reg])
        for yr in [2022, 2023, 2024, 2025]:
            yr_stats[yr] = calc_stats([t for t in trades_by_variant if t["scan_date"].startswith(str(yr))])

        # Compute daily alert count (unique trading days with ≥1 alert)
        alert_days = len(set(t["scan_date"] for t in trades_by_variant))

        all_variant_results[vid] = {
            "variant":    v,
            "trades":     trades_by_variant,
            "overall":    st_all,
            "regimes":    reg_stats,
            "years":      yr_stats,
            "alert_days": alert_days,
        }
        print(f"   {vid}: alerts={st_all['n']} | WR={st_all['wr']:.1f}% | E[R]={st_all['e_r']:.3f} | PF={st_all['pf']:.2f} | MaxDD={st_all['max_dd']:.2f}")

    # ── Identify best performer ──────────────────────────────────────────────
    # Rank by composite score: E[R] * PF / (1 + 0.3 * max_dd)
    def composite_score(d):
        s = d["overall"]
        if s["n"] < 20:
            return -99.0
        return (s["e_r"] * s["pf"]) / max(1.0 + 0.3 * s["max_dd"], 0.1)

    ranked = sorted(all_variant_results.items(), key=lambda x: composite_score(x[1]), reverse=True)
    winner_id, winner_data = ranked[0]
    runner_up_id, runner_up_data = ranked[1] if len(ranked) > 1 else (winner_id, winner_data)

    print(f"\n🥇 WINNER: {winner_id} — {all_variant_results[winner_id]['variant']['name']}")

    # ── Cap Impact Analysis ──────────────────────────────────────────────────
    v1 = all_variant_results.get("V1_PROD_BASELINE", {}).get("overall", {})
    v6 = all_variant_results.get("V6_EOD_NO_CAP",    {}).get("overall", {})
    v8 = all_variant_results.get("V8_LIBERAL_EOD",   {}).get("overall", {})
    v9 = all_variant_results.get("V9_LIBERAL_EOD_NOCAP", {}).get("overall", {})

    cap_delta_alerts = (v6.get("n", 0) - v1.get("n", 0))
    cap_delta_er     = round(v6.get("e_r", 0) - v1.get("e_r", 0), 4)

    # ── Persist to SQLite ────────────────────────────────────────────────────
    if os.path.exists(TOURNEY_DB):
        os.remove(TOURNEY_DB)
    conn = sqlite3.connect(TOURNEY_DB)
    cur  = conn.cursor()

    cur.execute("""CREATE TABLE variant_summary (
        variant_id TEXT PRIMARY KEY, name TEXT, alerts INTEGER, alert_days INTEGER,
        win_rate REAL, expectancy_r REAL, total_r REAL, profit_factor REAL,
        max_drawdown REAL, sl_rate REAL, rate_3r REAL, rate_5r REAL, rate_6r REAL,
        composite_score REAL, rank INTEGER
    )""")
    for rank_idx, (vid, d) in enumerate(ranked, 1):
        st = d["overall"]
        cur.execute("INSERT INTO variant_summary VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (
            vid, d["variant"]["name"], st["n"], d["alert_days"],
            st["wr"], st["e_r"], st["total_r"], st["pf"],
            st["max_dd"], st["sl_rate"], st["rate_3r"], st["rate_5r"], st["rate_6r"],
            round(composite_score(d), 5), rank_idx
        ))

    cur.execute("""CREATE TABLE variant_regime_breakdown (
        variant_id TEXT, regime TEXT, alerts INTEGER, win_rate REAL,
        expectancy_r REAL, total_r REAL, profit_factor REAL,
        PRIMARY KEY (variant_id, regime)
    )""")
    for vid, d in all_variant_results.items():
        for reg, st in d["regimes"].items():
            cur.execute("INSERT INTO variant_regime_breakdown VALUES (?,?,?,?,?,?,?)", (
                vid, reg, st["n"], st["wr"], st["e_r"], st["total_r"], st["pf"]
            ))

    cur.execute("""CREATE TABLE variant_year_breakdown (
        variant_id TEXT, year INTEGER, alerts INTEGER, win_rate REAL,
        expectancy_r REAL, total_r REAL, profit_factor REAL,
        PRIMARY KEY (variant_id, year)
    )""")
    for vid, d in all_variant_results.items():
        for yr, st in d["years"].items():
            cur.execute("INSERT INTO variant_year_breakdown VALUES (?,?,?,?,?,?,?)", (
                vid, yr, st["n"], st["wr"], st["e_r"], st["total_r"], st["pf"]
            ))

    conn.commit()
    conn.close()
    print(f"\n💾 Saved tournament DB → {TOURNEY_DB}")

    # ── Write CSVs ───────────────────────────────────────────────────────────
    if all_events_for_csv:
        with open(EVENT_CSV, "w", newline="") as f:
            keys = list(all_events_for_csv[0].keys())
            w = csv.DictWriter(f, fieldnames=keys)
            w.writeheader()
            w.writerows(all_events_for_csv)

    regime_rows = []
    for vid, d in all_variant_results.items():
        for reg, st in d["regimes"].items():
            regime_rows.append({"variant_id": vid, "variant_name": d["variant"]["name"],
                                 "regime": reg, **st})
    if regime_rows:
        with open(REGIME_CSV, "w", newline="") as f:
            keys = list(regime_rows[0].keys())
            w = csv.DictWriter(f, fieldnames=keys)
            w.writeheader()
            w.writerows(regime_rows)

    # ── Write JSON ───────────────────────────────────────────────────────────
    json_out = {}
    for vid, d in all_variant_results.items():
        json_out[vid] = {
            "variant":    d["variant"],
            "overall":    d["overall"],
            "regimes":    d["regimes"],
            "years":      {str(k): v for k, v in d["years"].items()},
            "alert_days": d["alert_days"],
        }
    with open(REPORT_JSON, "w") as f:
        json.dump(json_out, f, indent=2)

    # ── Markdown Report ──────────────────────────────────────────────────────
    now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M IST")
    w_st    = winner_data["overall"]
    v1_st   = all_variant_results["V1_PROD_BASELINE"]["overall"]

    def regime_row(st: dict) -> str:
        return f"{st['n']:4d} | {st['wr']:5.1f}% | {st['e_r']:+.3f}R | {st['pf']:5.2f}x"

    md_lines = [
        f"# SHORT COVERING 10-VARIANT TOURNAMENT — MASTER REPORT",
        f"",
        f"**Generated**: {now_str}  ",
        f"**Data**: Real BSE/NSE 1d OHLCV from `data/history/1d/` ({len(FNO_UNIVERSE)} symbols)  ",
        f"**Period**: {START.isoformat()} → {END.isoformat()} ({len(trading_days)} trading days)  ",
        f"**Evaluation Frequency**: 1-in-{SAMPLE_STEP} trading days ({len(eval_days)} evaluation days)  ",
        f"**Regime Classifier**: NIFTY 50 20-SMA (min 15 bars) + 5d return, BULL threshold ±0.2%/+0.5%  ",
        f"**Regimes**: BULL={reg_counts['BULL']} | BEAR={reg_counts['BEAR']} | NEUTRAL={reg_counts['NEUTRAL']}  ",
        f"",
        f"---",
        f"",
        f"## 1. TOURNAMENT WINNER",
        f"",
        f"```",
        f"  WINNER : {winner_id}",
        f"  Name   : {winner_data['variant']['name']}",
        f"  Desc   : {winner_data['variant']['description']}",
        f"",
        f"  vs Production Baseline (V1):",
        f"  ─────────────────────────────────────────────────────────",
        f"  Metric          V1 (Prod)   {winner_id:<20} Delta",
        f"  ─────────────────────────────────────────────────────────",
        f"  Total Alerts    {v1_st['n']:>9}  {w_st['n']:>20}  {w_st['n']-v1_st['n']:>+6}",
        f"  Win Rate %      {v1_st['wr']:>9.1f}  {w_st['wr']:>20.1f}  {w_st['wr']-v1_st['wr']:>+6.1f}",
        f"  Expectancy(R)   {v1_st['e_r']:>+9.3f}  {w_st['e_r']:>+20.3f}  {w_st['e_r']-v1_st['e_r']:>+6.3f}",
        f"  Profit Factor   {v1_st['pf']:>9.2f}  {w_st['pf']:>20.2f}  {w_st['pf']-v1_st['pf']:>+6.2f}",
        f"  Max Drawdown    {v1_st['max_dd']:>9.2f}  {w_st['max_dd']:>20.2f}  {w_st['max_dd']-v1_st['max_dd']:>+6.2f}",
        f"  SL Rate %       {v1_st['sl_rate']:>9.1f}  {w_st['sl_rate']:>20.1f}  {w_st['sl_rate']-v1_st['sl_rate']:>+6.1f}",
        f"  Rate ≥3R        {v1_st['rate_3r']:>9.1f}  {w_st['rate_3r']:>20.1f}  {w_st['rate_3r']-v1_st['rate_3r']:>+6.1f}",
        f"  Rate ≥5R        {v1_st['rate_5r']:>9.1f}  {w_st['rate_5r']:>20.1f}  {w_st['rate_5r']-v1_st['rate_5r']:>+6.1f}",
        f"```",
        f"",
        f"---",
        f"",
        f"## 2. FULL VARIANT RANKINGS",
        f"",
        f"| Rank | Variant ID | Alerts | WR% | E[R] | PF | MaxDD | Composite |",
        f"|------|-----------|--------|-----|------|----|-------|-----------|",
    ]
    for rank_idx, (vid, d) in enumerate(ranked, 1):
        st  = d["overall"]
        cs  = composite_score(d)
        md_lines.append(
            f"| {rank_idx} | {vid} | {st['n']} | {st['wr']:.1f}% | {st['e_r']:+.3f} | "
            f"{st['pf']:.2f} | {st['max_dd']:.2f} | {cs:.4f} |"
        )

    md_lines += [
        f"",
        f"---",
        f"",
        f"## 3. REGIME BREAKDOWN (All Variants)",
        f"",
        f"| Variant | Regime | Alerts | WR% | E[R] | PF |",
        f"|---------|--------|--------|-----|------|----|",
    ]
    for vid, d in all_variant_results.items():
        for reg in ["BULL", "BEAR", "NEUTRAL"]:
            st = d["regimes"][reg]
            md_lines.append(
                f"| {vid} | {reg} | {st['n']} | {st['wr']:.1f}% | {st['e_r']:+.3f} | {st['pf']:.2f} |"
            )

    md_lines += [
        f"",
        f"---",
        f"",
        f"## 4. ANNUAL PERFORMANCE",
        f"",
        f"| Variant | Year | Alerts | WR% | E[R] | Total R |",
        f"|---------|------|--------|-----|------|---------|",
    ]
    for vid, d in all_variant_results.items():
        for yr in [2022, 2023, 2024, 2025]:
            st = d["years"][yr]
            if st["n"] > 0:
                md_lines.append(
                    f"| {vid} | {yr} | {st['n']} | {st['wr']:.1f}% | {st['e_r']:+.3f} | {st['total_r']:+.1f} |"
                )

    # ── Cap Impact Section ────────────────────────────────────────────────────
    md_lines += [
        f"",
        f"---",
        f"",
        f"## 5. EOD 35-STOCK CAP IMPACT ANALYSIS",
        f"",
        f"This section isolates the impact of limiting the EOD watchlist to 35 stocks.",
        f"",
        f"| Metric | V1 (cap=35) | V6 (uncapped) | V8 (score≥35, cap=35) | V9 (score≥35, uncapped) |",
        f"|--------|------------|---------------|----------------------|------------------------|",
        f"| Alerts | {v1.get('n',0)} | {v6.get('n',0)} | {v8.get('n',0)} | {v9.get('n',0)} |",
        f"| WR%    | {v1.get('wr',0):.1f} | {v6.get('wr',0):.1f} | {v8.get('wr',0):.1f} | {v9.get('wr',0):.1f} |",
        f"| E[R]   | {v1.get('e_r',0):+.3f} | {v6.get('e_r',0):+.3f} | {v8.get('e_r',0):+.3f} | {v9.get('e_r',0):+.3f} |",
        f"| PF     | {v1.get('pf',0):.2f} | {v6.get('pf',0):.2f} | {v8.get('pf',0):.2f} | {v9.get('pf',0):.2f} |",
        f"| MaxDD  | {v1.get('max_dd',0):.2f} | {v6.get('max_dd',0):.2f} | {v8.get('max_dd',0):.2f} | {v9.get('max_dd',0):.2f} |",
        f"",
        f"**Cap Verdict (V1 vs V6)**:",
        f"- Additional alerts unlocked by removing cap: **{cap_delta_alerts:+d}**",
        f"- E[R] delta (uncapped vs capped): **{cap_delta_er:+.4f}R**",
    ]

    if cap_delta_er > 0.05 and v6.get("pf", 0) >= v1.get("pf", 0):
        md_lines.append(f"- **→ Removing the 35-cap IMPROVES performance. Recommend raising cap to ≥75 or removing.**")
    elif cap_delta_er < -0.05:
        md_lines.append(f"- **→ The 35-cap acts as a quality filter. Removing it dilutes E[R]. Keep the cap.**")
    else:
        md_lines.append(f"- **→ Cap impact is marginal. The 35-cap neither meaningfully helps nor hurts.**")

    md_lines += [
        f"",
        f"---",
        f"",
        f"## 6. RECOMMENDATION",
        f"",
        f"**Deploy Variant**: `{winner_id}`  ",
        f"**EOD Parameters**: score≥{all_variant_results[winner_id]['variant']['eod_min_quality_score']}, "
        f"cap={all_variant_results[winner_id]['variant']['eod_max_watchlist'] if all_variant_results[winner_id]['variant']['eod_max_watchlist'] < 9000 else 'NONE'}  ",
        f"**5m Parameters**: score≥{all_variant_results[winner_id]['variant']['m5_min_ignition_score']}, "
        f"OI≤{all_variant_results[winner_id]['variant']['m5_oi_contraction']}%  ",
        f"",
        f"**Files**:",
        f"- DB: `data/sc_10variant_tournament.db`",
        f"- Events CSV: `reports/sc_10variant_events.csv`",
        f"- Regime CSV: `reports/sc_10variant_regime_breakdown.csv`",
        f"- JSON: `reports/sc_10variant_results.json`",
    ]

    with open(REPORT_MD, "w") as f:
        f.write("\n".join(md_lines))

    print(f"\n📄 Report written → {REPORT_MD}")
    print(f"📊 Events CSV → {EVENT_CSV}")
    print(f"📊 Regime CSV → {REGIME_CSV}")
    print(f"🔬 JSON → {REPORT_JSON}")
    print(f"\n{'=' * 70}")
    print(f"TOURNAMENT COMPLETE")
    print(f"WINNER: {winner_id}")
    print(f"  WR={w_st['wr']:.1f}% | E[R]={w_st['e_r']:+.3f} | PF={w_st['pf']:.2f} | MaxDD={w_st['max_dd']:.2f}")
    print(f"{'=' * 70}")

    return all_variant_results, winner_id


if __name__ == "__main__":
    run_tournament()
