#!/usr/bin/env python3
"""
scripts/rehydrate_cash_sub_schedules.py
======================================
Targeted sweep for the ~306 industrial equities whose filings were saved
prior to cash sub-schedule extraction.
Extracts cash schedules, updates exchange payloads, and completes balance sheets.
"""

import os
import sys
import glob
import json
import time
import logging
import requests
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))
sys.path.insert(0, str(BASE_DIR / "scripts"))

from scripts.rehydrate_missing_pit import rehydrate_symbol_full

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("rehydrate_cash")

def find_target_symbols():
    industrials_no_cash = []
    for f in sorted(glob.glob(str(BASE_DIR / "data" / "exchange_financials" / "*" / "raw" / "*_filings_v1.payload.json"))):
        try:
            d = json.load(open(f))
            ann = [r for r in d if r.get("statement_type") == "ANNUAL"]
            if ann and ann[-1].get("cash_and_equivalents") is None and ann[-1].get("operating_profit") is not None:
                industrials_no_cash.append(ann[-1].get("symbol"))
        except Exception:
            pass
    return industrials_no_cash

def main():
    targets = find_target_symbols()
    logger.info(f"🚀 Found {len(targets)} industrial equities needing cash sub-schedule extraction")

    sess = requests.Session()
    sess.headers.update({
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    })

    success = 0
    failed = 0

    for idx, sym in enumerate(targets, 1):
        attempt = 0
        recs = None
        while attempt < 3:
            attempt += 1
            try:
                recs = rehydrate_symbol_full(sym, sess, {})
                if recs:
                    break
                time.sleep(2.0)
            except Exception as e:
                err_str = str(e)
                if "429" in err_str or "throttled" in err_str or "Connection" in err_str:
                    logger.warning(f"  [{sym}] Throttled (attempt {attempt}/3). Backing off 15s...")
                    time.sleep(15)
                else:
                    time.sleep(3.0)

        if recs:
            ann = [r for r in recs if r.get("statement_type") == "ANNUAL"]
            cash = ann[-1].get("cash_and_equivalents") if ann else None
            if cash is not None:
                logger.info(f"[{idx}/{len(targets)}] ✅ {sym:<12} Cash={cash}")
                success += 1
            else:
                logger.warning(f"[{idx}/{len(targets)}] ⚠️ {sym:<12} Cash schedule not found")
                failed += 1
        else:
            logger.warning(f"[{idx}/{len(targets)}] ❌ {sym:<12} Rehydration failed")
            failed += 1

        time.sleep(1.8)

    logger.info(f"============================================================")
    logger.info(f"✨ Sweep complete: Success={success}, Failed={failed}")
    logger.info(f"============================================================")

if __name__ == "__main__":
    main()
