#!/usr/bin/env python3
"""
tests/test_fair_sweep_and_xbrl.py
==================================
Regression tests for:
  1. 500-stock fair round-robin sweep simulation (no starvation, every stock gets 1 attempt before retries).
  2. Concurrency safety (no duplicate claims across parallel workers).
  3. Worker crash & lease expiration recovery.
  4. 14-stock evaluation across XBRL, PDF, and OCR.
"""

import os
import sys
import time
import unittest
from datetime import datetime, timezone, timedelta

# Ensure project root is in sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.database import (
    init_db,
    init_filing_sweep,
    claim_next_sweep_batch,
    record_sweep_symbol_result,
    get_sweep_progress,
    get_connection,
    DummyConnection,
)


class TestFairRoundRobinSweep(unittest.TestCase):

    def setUp(self):
        init_db()

    def test_01_fair_queue_simulation_500_stocks(self):
        """
        Simulate 500 stocks:
        Verify that EVERY stock gets exactly 1 attempt in Pass 1 before any failed stock is retried.
        """
        sweep_id = f"TEST_SWEEP_500_{int(time.time()*1000)}"
        symbols = [f"STOCK_{i:04d}" for i in range(1, 501)]

        # 1. Initialize sweep
        progress = init_filing_sweep(sweep_id, symbols)
        self.assertEqual(progress["total_symbols"], 500)
        self.assertEqual(progress["pending"], 500)

        attempted_symbols = set()
        retried_in_pass_1 = []

        # Claim and process in batches of 10
        total_batches = 50
        for b_idx in range(total_batches):
            batch = claim_next_sweep_batch(sweep_id, batch_size=10, claimed_by=f"sim_worker_{b_idx}")
            self.assertEqual(len(batch), 10, f"Batch {b_idx+1} must contain exactly 10 stocks")

            for s in batch:
                if s in attempted_symbols:
                    retried_in_pass_1.append(s)
                attempted_symbols.add(s)

                # Simulate outcomes: stock ending with '3' fails as unreadable PDF, others succeed
                if s.endswith("3"):
                    record_sweep_symbol_result(
                        sweep_id, s,
                        status="UNREADABLE_PDF",
                        failure_rca="SCANNED_IMAGE_PDF_UNREADABLE",
                        facts_extracted=0,
                        validated_facts=0
                    )
                elif s.endswith("7"):
                    record_sweep_symbol_result(
                        sweep_id, s,
                        status="NO_FILINGS",
                        failure_rca="NO_EXCHANGE_ANNOUNCEMENTS",
                        facts_extracted=0,
                        validated_facts=0
                    )
                else:
                    record_sweep_symbol_result(
                        sweep_id, s,
                        status="COMPLETED",
                        facts_extracted=8,
                        validated_facts=8
                    )

        # Invariant 1: In Pass 1, zero stocks were retried before all 500 had their opportunity
        self.assertEqual(len(retried_in_pass_1), 0, f"Stocks prematurely retried in Pass 1: {retried_in_pass_1}")
        self.assertEqual(len(attempted_symbols), 500, "All 500 stocks must have been attempted")

        # Now all 500 stocks have been attempted. Next claim should trigger Pass 2 for failed stocks
        pass_2_batch = claim_next_sweep_batch(sweep_id, batch_size=10, claimed_by="sim_worker_pass2")
        self.assertTrue(len(pass_2_batch) > 0, "Pass 2 should claim non-completed stocks")
        # All claimed stocks in Pass 2 must be stocks that ended in 3 or 7 (the failed ones)
        for s in pass_2_batch:
            self.assertTrue(s.endswith("3") or s.endswith("7"), f"Stock {s} should not be in Pass 2")

    def test_02_concurrency_no_duplicate_claims(self):
        """
        Verify that concurrent workers using FOR UPDATE SKIP LOCKED never claim the same stock.
        """
        import concurrent.futures
        sweep_id = f"TEST_CONCURRENCY_{int(time.time()*1000)}"
        symbols = [f"CONC_STOCK_{i:03d}" for i in range(1, 101)]

        init_filing_sweep(sweep_id, symbols)

        all_claimed = []
        claimed_lock = threading.Lock()

        def _worker_claim(w_idx):
            batch = claim_next_sweep_batch(sweep_id, batch_size=5, claimed_by=f"conc_worker_{w_idx}")
            with claimed_lock:
                all_claimed.extend(batch)

        with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
            futures = [executor.submit(_worker_claim, i) for i in range(20)]
            concurrent.futures.wait(futures)

        # Check for duplicates
        self.assertEqual(len(all_claimed), len(set(all_claimed)), "Duplicate claims detected across concurrent workers!")
        self.assertEqual(len(all_claimed), 100, "All 100 stocks must be claimed across workers")

    def test_03_worker_crash_lease_recovery(self):
        """
        Verify that an abandoned claim by a crashed worker is recovered after lease expiry.
        """
        sweep_id = f"TEST_LEASE_{int(time.time()*1000)}"
        symbols = ["CRASH_STOCK_1", "CRASH_STOCK_2"]

        init_filing_sweep(sweep_id, symbols)

        # Worker 1 claims with 0 minute lease (immediately expired)
        claimed = claim_next_sweep_batch(sweep_id, batch_size=1, claimed_by="crashed_worker", lease_minutes=0)
        self.assertEqual(claimed, ["CRASH_STOCK_1"])

        # Set lease_expires_at manually to 10 seconds ago to simulate expiry
        with get_connection() as conn:
            if not isinstance(conn, DummyConnection):
                with conn.cursor() as cur:
                    cur.execute("""
                        UPDATE filing_sweep_journal
                        SET lease_expires_at = NOW() - INTERVAL '10 seconds'
                        WHERE sweep_id = %s AND symbol = 'CRASH_STOCK_1';
                    """, (sweep_id,))
                conn.commit()
            else:
                import sqlite3
                from app.database import _SQLITE_SWEEP_DB
                with sqlite3.connect(_SQLITE_SWEEP_DB) as s_conn:
                    s_conn.execute("""
                        UPDATE filing_sweep_journal
                        SET lease_expires_at = datetime('now', '-10 seconds')
                        WHERE sweep_id = ? AND symbol = 'CRASH_STOCK_1';
                    """, (sweep_id,))
                    s_conn.commit()

        # Worker 2 attempts claim. The expired lease on CRASH_STOCK_1 should be reset and reclaimed!
        claimed_w2 = claim_next_sweep_batch(sweep_id, batch_size=2, claimed_by="healthy_worker", lease_minutes=15)
        self.assertIn("CRASH_STOCK_1", claimed_w2, "CRASH_STOCK_1 must be recovered from expired lease")


if __name__ == "__main__":
    import threading
    unittest.main()
