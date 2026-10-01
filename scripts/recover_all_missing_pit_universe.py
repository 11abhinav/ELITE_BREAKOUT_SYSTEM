#!/usr/bin/env python3
"""
scripts/recover_all_missing_pit_universe.py
===========================================
Recovers PIT financial filings for all missing symbols in certified_clean_universe_886.json.
Fetches authoritative statements, saves raw filings, and appends certified records
to data/pit_fundamentals_v1/pit_fundamentals_v1.parquet.
Also updates pit_valuation_history_cache.json with 3Y valuation medians.
"""

import os
import sys
import json
import time
import logging
import requests
import pandas as pd
import numpy as np
from datetime import datetime
from zoneinfo import ZoneInfo

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
logger = logging.getLogger("PIT_RECOVERY")

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(BASE_DIR, "data")
PIT_PARQUET = os.path.join(DATA_DIR, "pit_fundamentals_v1", "pit_fundamentals_v1.parquet")
RAW_DIR = os.path.join(DATA_DIR, "pit_raw_filings")
CLEAN_UNIV = os.path.join(DATA_DIR, "certified_clean_universe_886.json")
VAL_CACHE_PATH = os.path.join(DATA_DIR, "pit_valuation_history_cache.json")

IST = ZoneInfo("Asia/Kolkata")

# Import the existing authoritative parser from ingest_pit_quarterly_and_annual
sys.path.insert(0, BASE_DIR)
from scripts.ingest_pit_quarterly_and_annual import fetch_and_parse_symbol


def main():
    with open(CLEAN_UNIV) as f:
        clean_symbols = json.load(f)["symbols"]
    
    logger.info(f"Loaded {len(clean_symbols)} universe symbols from {CLEAN_UNIV}")
    
    existing_df = pd.DataFrame()
    existing_symbols = set()
    if os.path.exists(PIT_PARQUET):
        existing_df = pd.read_parquet(PIT_PARQUET)
        existing_symbols = set(existing_df["symbol"].str.strip().str.upper().unique())
        logger.info(f"Existing PIT dataset has {len(existing_df)} rows across {len(existing_symbols)} unique symbols")

    # Missing symbols
    missing_symbols = sorted([s for s in clean_symbols if s not in existing_symbols])
    logger.info(f"Identified {len(missing_symbols)} missing symbols to recover")

    sess = requests.Session()
    sess.headers.update({
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml",
        "Referer": "https://www.screener.in/"
    })

    recovered_records = []
    success_symbols = []
    failed_symbols = []

    for idx, sym in enumerate(missing_symbols, start=1):
        try:
            records, q_cnt, a_cnt = fetch_and_parse_symbol(sym, sess)
            if records and (q_cnt > 0 or a_cnt > 0):
                recovered_records.extend(records)
                success_symbols.append(sym)
                logger.info(f"[{idx}/{len(missing_symbols)}] ✅ {sym:<12}: {len(records)} records (Q={q_cnt}, A={a_cnt})")
            else:
                failed_symbols.append(sym)
                logger.warning(f"[{idx}/{len(missing_symbols)}] ⚠️ {sym:<12}: NO RECORDS PARSED")
        except Exception as e:
            failed_symbols.append(sym)
            logger.error(f"[{idx}/{len(missing_symbols)}] ❌ {sym:<12}: error={e}")

    logger.info(f"Recovery complete: {len(success_symbols)} succeeded, {len(failed_symbols)} failed")
    if failed_symbols:
        logger.info(f"Failed symbols: {failed_symbols}")

    if recovered_records:
        rec_df = pd.DataFrame(recovered_records)
        # Ensure column schemas align
        for col in existing_df.columns:
            if col not in rec_df.columns:
                rec_df[col] = np.nan
        for col in rec_df.columns:
            if col not in existing_df.columns:
                existing_df[col] = np.nan

        combined_df = pd.concat([existing_df, rec_df], ignore_index=True)
        # Deduplicate on filing_id
        if "filing_id" in combined_df.columns:
            combined_df = combined_df.drop_duplicates(subset=["filing_id"], keep="last")
        else:
            combined_df = combined_df.drop_duplicates(subset=["symbol", "period_end_date", "statement_type"], keep="last")

        combined_df = combined_df.sort_values(["symbol", "period_end_date"]).reset_index(drop=True)
        
        # Save updated parquet
        combined_df.to_parquet(PIT_PARQUET, index=False)
        logger.info(f"💾 Saved updated PIT dataset to {PIT_PARQUET}: {len(combined_df)} rows, {combined_df['symbol'].nunique()} unique symbols")

        # Also backup to data/pit_fundamentals_v1.parquet if it exists
        alt_parquet = os.path.join(DATA_DIR, "pit_fundamentals_v1.parquet")
        if os.path.exists(alt_parquet):
            combined_df.to_parquet(alt_parquet, index=False)
            logger.info(f"💾 Saved mirror backup to {alt_parquet}")

    # Rebuild PIT Valuation Cache with newly recovered symbols
    logger.info("⚡ Rebuilding PIT Valuation Medians Cache for all symbols...")
    from app.pit_valuation_history_builder import build_pit_valuation_cache
    new_val_cache = build_pit_valuation_cache(force_recompute=True)
    logger.info(f"✅ PIT Valuation Cache updated with {len(new_val_cache)} symbols")


if __name__ == "__main__":
    main()
