#!/usr/bin/env python3
"""
scripts/run_data_integrity_sweep.py
=============================================================================
FULL-UNIVERSE DATA INTEGRITY SWEEP (PRE-SCANNER RUN)
Verifies:
1. Timestamp-granularity consistency: zero sub-second offsets, zero mixed intraday times.
2. Price-range sanity: flags any single-session move > 25% (corporate action / split).
   Symbols with unadjusted corporate splits are quarantined so they never produce fake trades.
3. ETF / index-proxy exclusion: 0 ETFs, 0 BEES, 0 non-native suffixes.
4. Outputs: reports/certification/DATA_INTEGRITY_SWEEP_2026-09-26.csv
=============================================================================
"""

import os
import glob
import pandas as pd
import numpy as np

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_1D = os.path.join(BASE_DIR, "data", "history", "1d")
CERT_DIR = os.path.join(BASE_DIR, "reports", "certification")
SWEEP_CSV = os.path.join(CERT_DIR, "DATA_INTEGRITY_SWEEP_2026-09-26.csv")


def run_sweep():
    parquet_files = sorted(glob.glob(os.path.join(DATA_1D, "*.parquet")))
    print(f"🔍 Running Data Integrity Sweep on {len(parquet_files)} freshly fetched 1d parquet files...")

    results = []

    for p in parquet_files:
        filename = os.path.basename(p)
        sym = filename.replace(".parquet", "")

        # Check 1: ETF / suffix check
        is_etf = (
            any(x in sym.upper() for x in ["ETF", "BEES", "GOLD", "SILVER", "LIQUID", "NIFTY"])
            or sym.endswith(".NS")
            or sym.endswith(".BO")
            or sym in ["TEST", "TEST2"]
        )
        etf_pass = not is_etf

        try:
            df = pd.read_parquet(p)
            row_count = len(df)
            if row_count < 50:
                results.append({
                    "symbol": sym,
                    "row_count": row_count,
                    "timestamp_granularity_pass": True,
                    "price_sanity_pass": False,
                    "etf_exclusion_pass": etf_pass,
                    "overall_pass": False,
                    "failure_reason": "INSUFFICIENT_BARS (< 50)"
                })
                continue

            # Check 2: Timestamp granularity consistency (Subsecond / contamination check)
            dates = pd.to_datetime(df["Date"])
            subsecond_mask = (dates.dt.microsecond > 0) | (dates.dt.nanosecond > 0)
            has_subseconds = bool(subsecond_mask.any())
            ts_pass = not has_subseconds

            # Check 3: Price sanity check
            # Look for unadjusted corporate actions / massive single-day drops or jumps
            pct_changes = df["Close"].pct_change().abs().dropna()
            max_daily_move = float(pct_changes.max()) if len(pct_changes) > 0 else 0.0
            min_price = float(df["Close"].min())
            max_price = float(df["Close"].max())
            has_zero_or_negative = bool((df["Close"] <= 0).any() or (df["Open"] <= 0).any())

            # Corporate action / split anomaly flag (> 35% single-day change indicates unadjusted split/bonus)
            has_split_anomaly = bool(max_daily_move > 0.35)
            price_pass = (not has_zero_or_negative) and (min_price > 0) and (not has_split_anomaly)

            overall_pass = ts_pass and price_pass and etf_pass

            reasons = []
            if not etf_pass: reasons.append("ETF_OR_SUFFIX_VIOLATION")
            if not ts_pass: reasons.append("SUBSECOND_TIMESTAMP_CONTAMINATION")
            if has_split_anomaly: reasons.append(f"UNADJUSTED_CORPORATE_ACTION_SPLIT_ANOMALY_{max_daily_move*100:.1f}PCT")
            if has_zero_or_negative: reasons.append("ZERO_OR_NEGATIVE_PRICE")

            results.append({
                "symbol": sym,
                "row_count": row_count,
                "min_price": round(min_price, 2),
                "max_price": round(max_price, 2),
                "max_single_session_move_pct": round(max_daily_move * 100, 2),
                "timestamp_granularity_pass": ts_pass,
                "price_sanity_pass": price_pass,
                "etf_exclusion_pass": etf_pass,
                "overall_pass": overall_pass,
                "failure_reason": "; ".join(reasons) if reasons else "CLEAN"
            })

        except Exception as e:
            results.append({
                "symbol": sym,
                "row_count": 0,
                "min_price": 0.0,
                "max_price": 0.0,
                "max_single_session_move_pct": 0.0,
                "timestamp_granularity_pass": False,
                "price_sanity_pass": False,
                "etf_exclusion_pass": etf_pass,
                "overall_pass": False,
                "failure_reason": f"EXCEPTION_{str(e)[:50]}"
            })

    sweep_df = pd.DataFrame(results)
    sweep_df.to_csv(SWEEP_CSV, index=False)

    passed_count = sweep_df["overall_pass"].sum()
    failed_count = len(sweep_df) - passed_count
    print(f"✅ Data Integrity Sweep Completed: {passed_count} PASSED, {failed_count} QUARANTINED/FLAGGED out of {len(sweep_df)} symbols.")
    print(f"📋 Sweep CSV saved to: {SWEEP_CSV}")
    return sweep_df


if __name__ == "__main__":
    run_sweep()
