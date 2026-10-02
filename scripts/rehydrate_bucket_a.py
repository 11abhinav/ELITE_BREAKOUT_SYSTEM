#!/usr/bin/env python3
"""
scripts/rehydrate_bucket_a.py
=============================
Authoritative Rehydration Pipeline for the Un-certified Bucket A Equities.
Fetches full audited exchange filings and cash sub-schedules from company filings,
persisting immutable canonical payloads to:
  data/exchange_financials/{sym}/raw/{sym}_filings_v1.payload.json
with SHA256 checksums.

Design:
  - Single-worker sequential mode (default delay=2.0s) to strictly observe rate limits.
  - Prioritizes the 8 missing-annual equities first.
  - Automatically handles backoff if throttled.
  - Idempotent checkpointing (skips existing payloads on disk).
"""

from __future__ import annotations

import os
import sys
import json
import time
import argparse
import requests
import logging
from pathlib import Path
from typing import List, Dict, Any

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))
sys.path.insert(0, str(BASE_DIR / "scripts"))

from scripts.rehydrate_missing_pit import rehydrate_symbol_full

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("rehydrate_bucket_a")

MISSING_FILE = BASE_DIR / "data" / "reports" / "bucket_a_missing_714.json"
EXCHANGE_DIR = BASE_DIR / "data" / "exchange_financials"

# 8 symbols identified in audit as missing annual filings
PRIORITY_8_ANNUALS = [
    "ADANIGREEN", "ARIS", "ATULAUTO", "AURIONPRO",
    "BAYERCROP", "COLPAL", "ESABINDIA", "FRONTSP"
]


def run(max_symbols: int = 0, delay_sec: float = 2.0, max_retries: int = 3):
    if not MISSING_FILE.exists():
        logger.error(f"Missing file {MISSING_FILE}")
        return

    with open(MISSING_FILE) as f:
        missing_data = json.load(f)
    symbols: List[str] = missing_data.get("symbols", [])

    # Filter out already existing on disk
    to_scrape = []
    for s in symbols:
        p = EXCHANGE_DIR / s / "raw" / f"{s}_filings_v1.payload.json"
        if not p.exists() or p.stat().st_size == 0:
            to_scrape.append(s)

    # Sort so priority symbols come first, then alphabetical
    to_scrape.sort(key=lambda s: (0 if s in PRIORITY_8_ANNUALS else 1, s))

    logger.info(f"Total missing in file: {len(symbols)} | Remaining un-scraped: {len(to_scrape)}")

    if max_symbols > 0:
        to_scrape = to_scrape[:max_symbols]

    total = len(to_scrape)
    logger.info(f"🚀 Launching polite sequential rehydration for {total} symbols (delay={delay_sec}s)...")

    sess = requests.Session()
    sess.headers.update({
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    })

    success = 0
    unresolved = 0
    errors = 0

    for idx, sym in enumerate(to_scrape, 1):
        attempt = 0
        recs = None
        while attempt < max_retries:
            attempt += 1
            try:
                recs = rehydrate_symbol_full(sym, sess, {})
                if recs:
                    break
                time.sleep(delay_sec)
            except Exception as e:
                err_str = str(e)
                if "Connection refused" in err_str or "Max retries exceeded" in err_str:
                    logger.warning(f"  [{sym}] Server throttled (attempt {attempt}/{max_retries}). Backing off 30s...")
                    time.sleep(30)
                else:
                    logger.error(f"  [{sym}] Attempt {attempt} error: {e}")
                    time.sleep(delay_sec * 2)

        if recs:
            success += 1
            ann = [r for r in recs if r.get("statement_type") == "ANNUAL"]
            latest = ann[-1] if ann else {}
            cash = latest.get("cash_and_equivalents")
            debt = latest.get("total_debt")
            op = latest.get("operating_profit")
            da = latest.get("depreciation_amortization")
            logger.info(f"  [{idx}/{total}] ✅ {sym:<14} Cash={cash} | Debt={debt} | OP={op}, DA={da}")
        else:
            unresolved += 1
            logger.warning(f"  [{idx}/{total}] ⚠️ {sym:<14} UNRESOLVED after {max_retries} attempts")

        time.sleep(delay_sec)

    logger.info("=" * 60)
    logger.info(f"✨ Rehydration Run Complete!")
    logger.info(f"   Total Attempted: {total}")
    logger.info(f"   Success:         {success}")
    logger.info(f"   Unresolved:      {unresolved}")
    logger.info(f"   Errors:          {errors}")
    logger.info("=" * 60)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--max", type=int, default=0, help="Max symbols to process (0 = all)")
    parser.add_argument("--delay", type=float, default=2.0, help="Polite delay between requests in seconds")
    parser.add_argument("--retries", type=int, default=3, help="Max retries with backoff")
    args = parser.parse_args()

    run(max_symbols=args.max, delay_sec=args.delay, max_retries=args.retries)
