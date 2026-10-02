#!/usr/bin/env python3
"""
scripts/backfill_bucket_a_exchange_payloads.py
================================================
Bucket A (724 Equities) Exchange Payload Acquisition & Backfill Pipeline.

Steps:
  1. Loads exact 724 Bucket A symbol list from data/reports/bucket_a_724_symbols.json.
  2. Multi-threaded processing (16 workers) to fetch/convert raw exchange filing statements.
  3. Ensures statement basis is explicit (CONSOLIDATED/STANDALONE).
  4. Generates SHA256 payload fingerprint.
  5. Atomically persists raw payload: data/exchange_financials/{symbol}/raw/{symbol}_filings_v1.payload.json.
  6. Emits detailed progress logging and summary audit report.
"""

from __future__ import annotations

import os
import sys
import json
import hashlib
import logging
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, List, Any, Optional

BASE_DIR = Path(__file__).resolve().parent.parent
for _p in [str(BASE_DIR), str(BASE_DIR / "app")]:
    if _p not in sys.path:
        sys.path.insert(0, _p)

try:
    from app.live_fundamental_scanner import _get_pit_filings
except ImportError:
    from live_fundamental_scanner import _get_pit_filings

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("bucket_a_backfill")


def process_bucket_a_symbol(sym: str) -> Dict[str, Any]:
    sym_u = sym.strip().upper()
    filings = _get_pit_filings(sym_u, allow_live_refresh=False)
    if not filings:
        return {"symbol": sym_u, "status": "NO_FILINGS", "count": 0}

    out_dir = BASE_DIR / "data" / "exchange_financials" / sym_u / "raw"
    os.makedirs(out_dir, exist_ok=True)
    payload_file = out_dir / f"{sym_u}_filings_v1.payload.json"

    # Enforce explicit statement basis
    for f_item in filings:
        if not f_item.get("basis"):
            f_item["basis"] = "CONSOLIDATED"

    payload_bytes = json.dumps(filings, indent=2).encode("utf-8")
    sha256_hash = hashlib.sha256(payload_bytes).hexdigest()

    tmp_p = f"{payload_file}.tmp.{os.getpid()}"
    with open(tmp_p, "wb") as pf:
        pf.write(payload_bytes)
    os.replace(tmp_p, payload_file)

    return {
        "symbol": sym_u,
        "status": "SUCCESS",
        "count": len(filings),
        "sha256": sha256_hash,
        "file_path": str(payload_file),
    }


def run_bucket_a_backfill(max_workers: int = 16) -> Dict[str, Any]:
    bucket_a_json = BASE_DIR / "data" / "reports" / "bucket_a_724_symbols.json"
    if not bucket_a_json.exists():
        logger.error(f"Bucket A list not found at {bucket_a_json}. Run export_bucket_a_symbols first.")
        return {"status": "FAILED", "reason": "MISSING_BUCKET_A_JSON"}

    with open(bucket_a_json) as f:
        bucket_a_syms = json.load(f).get("symbols", [])

    total_syms = len(bucket_a_syms)
    logger.info(f"🚀 Starting Bucket A Exchange Backfill for {total_syms} equities (workers={max_workers})...")

    success_count = 0
    no_data_count = 0

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_map = {executor.submit(process_bucket_a_symbol, sym): sym for sym in bucket_a_syms}
        for idx, future in enumerate(as_completed(future_map), 1):
            try:
                res = future.result()
                if res["status"] == "SUCCESS":
                    success_count += 1
                else:
                    no_data_count += 1
            except Exception as e:
                failed_sym = future_map[future]
                logger.error(f"❌ Failed to backfill exchange payload for {failed_sym}: {e}")

            if idx % 100 == 0 or idx == total_syms:
                logger.info(f"⚡ Progress: {idx}/{total_syms} ({idx * 100 // total_syms}%) | Saved: {success_count} | No Data: {no_data_count}")

    logger.info(f"✅ Bucket A Exchange Backfill Complete: {success_count}/{total_syms} payloads persisted to disk.")
    return {
        "status": "COMPLETED",
        "total": total_syms,
        "success": success_count,
        "no_data": no_data_count,
    }


if __name__ == "__main__":
    run_bucket_a_backfill()
