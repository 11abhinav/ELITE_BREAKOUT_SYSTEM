#!/usr/bin/env python3
"""
scripts/run_continuous_stale_sweep.py
======================================
Automated continuous batch runner for filing watcher sweep across all STALE stocks.
Processes stocks in batches of 25, extracts digital filings, merges facts,
rebuilds canonical PIT snapshot, syncs to DB parquet_cache, and updates health status.
"""

import os
import sys
import time
import json
import logging
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo

# Ensure unbuffered output
sys.stdout.reconfigure(line_buffering=True)
sys.stderr.reconfigure(line_buffering=True)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("ContinuousStaleSweep")

def main():
    IST = ZoneInfo("Asia/Kolkata")
    logger.info("🚀 [STALE_SWEEP] Initializing continuous filing watcher sweep...")

    from app.database import get_unified_stock_data_health_audit, clear_unified_stock_data_health_cache
    from scripts.financial_filing_watcher import FinancialFilingWatcher

    BATCH_SIZE = int(os.getenv("STALE_SWEEP_BATCH_SIZE", "25"))
    watcher = FinancialFilingWatcher()

    # Pre-warm market sweep announcements once
    logger.info("🌐 [STALE_SWEEP] Pre-warming 45-day NSE corporate announcements sweep...")
    from app.data_providers.nse_xbrl_provider import NseXbrlProvider
    nse_prov = NseXbrlProvider()
    market_sweep = nse_prov.discover_market_filings_sweep(lookback_days=45)
    logger.info(f"✅ [STALE_SWEEP] Market announcements loaded: {len(market_sweep)} symbols.")

    batch_num = 0
    total_restored = 0
    total_processed = 0
    attempted_symbols: set = set()

    while True:
        batch_num += 1
        stale_list, fresh_list = get_unified_stock_data_health_audit(force=True)
        stale_syms = [s["symbol"].strip().upper() for s in stale_list if s.get("symbol")]

        unattempted = [s for s in stale_syms if s not in attempted_symbols]

        logger.info(
            f"📊 [STALE_SWEEP: BATCH {batch_num}] Universe Status: "
            f"FRESH={len(fresh_list)}, STALE={len(stale_syms)}, UNATTEMPTED={len(unattempted)}"
        )

        if not unattempted:
            logger.info("🎉 [STALE_SWEEP] All stale stocks in universe have been evaluated! Sweep complete.")
            break

        current_batch = unattempted[:BATCH_SIZE]
        attempted_symbols.update(current_batch)

        logger.info(
            f"🎯 [STALE_SWEEP: BATCH {batch_num}] Processing next {len(current_batch)} stocks: "
            f"{current_batch[:8]}..."
        )

        batch_start = time.time()
        try:
            # Run watcher cycle for this batch
            sym_arg = ",".join(current_batch)
            result = watcher.run_watcher_cycle(
                trigger_type="MANUAL",
                scheduler_name="BATCH_SWEEP",
                symbol=sym_arg,
                force_rebuild=True,
                stale_only=False,
            )
            logger.info(f"💾 [STALE_SWEEP: BATCH {batch_num}] Watcher cycle status: {result.get('status')}")
        except Exception as batch_err:
            logger.error(f"❌ [STALE_SWEEP: BATCH {batch_num}] Error in watcher cycle: {batch_err}", exc_info=True)

        batch_dur = time.time() - batch_start
        total_processed += len(current_batch)

        # Re-audit to check how many became fresh
        new_stale, new_fresh = get_unified_stock_data_health_audit(force=True)
        new_fresh_syms = {s["symbol"].strip().upper() for s in new_fresh if s.get("symbol")}
        batch_restored = [s for s in current_batch if s in new_fresh_syms]
        total_restored += len(batch_restored)

        logger.info(
            f"✅ [STALE_SWEEP: BATCH {batch_num} COMPLETE in {batch_dur:.1f}s] "
            f"Restored to FRESH in this batch ({len(batch_restored)}): {batch_restored} | "
            f"Total FRESH now: {len(new_fresh)} | Total STALE remaining: {len(new_stale)}"
        )

        # Small pause between batches
        time.sleep(1)

    logger.info(
        f"🏁 [STALE_SWEEP: ALL BATCHES COMPLETE] "
        f"Processed: {total_processed} | Newly Restored: {total_restored}"
    )

if __name__ == "__main__":
    main()
