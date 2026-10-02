#!/usr/bin/env python3
"""
scripts/rehydrate_bucket_b.py
=============================
Rehydrates the 40 non-structural Bucket B equities to extract balance-sheet
cash sub-schedules, total debt, and depreciation/amortization lines.
"""

import os
import sys
import json
import time
import requests
import logging
from pathlib import Path
import pandas as pd

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))
sys.path.insert(0, str(BASE_DIR / 'scripts'))

from scripts.rehydrate_missing_pit import rehydrate_symbol_full

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("bucket_b_rehydrate")

def run():
    csv_path = BASE_DIR / 'data' / 'reports' / 'bucket_b_forensic_table.csv'
    if not csv_path.exists():
        logger.error(f"Missing {csv_path}")
        return

    df = pd.read_csv(csv_path)
    non_bfsi = df[~df['is_bfsi']].copy()
    symbols = non_bfsi['symbol'].tolist()

    logger.info(f"🚀 Rehydrating {len(symbols)} non-structural Bucket B equities...")
    sess = requests.Session()
    success = 0
    fail = 0

    for idx, sym in enumerate(symbols, 1):
        try:
            recs = rehydrate_symbol_full(sym, sess, {})
            if recs:
                ann = [r for r in recs if r.get("statement_type") == "ANNUAL"]
                latest = ann[-1] if ann else {}
                cash = latest.get("cash_and_equivalents")
                debt = latest.get("total_debt")
                op = latest.get("operating_profit")
                da = latest.get("depreciation_amortization")
                logger.info(f"  [{idx}/{len(symbols)}] ✅ {sym:<12} Cash={cash} | Debt={debt} | OP={op}, DA={da}")
                success += 1
            else:
                logger.warning(f"  [{idx}/{len(symbols)}] ❌ {sym:<12} UNRESOLVED")
                fail += 1
        except Exception as e:
            logger.error(f"  [{idx}/{len(symbols)}] ❌ {sym:<12} Error: {e}")
            fail += 1
        time.sleep(0.4)

    logger.info(f"✅ Bucket B Rehydration Complete: {success}/{len(symbols)} succeeded, {fail} unresolved.")

if __name__ == "__main__":
    run()
