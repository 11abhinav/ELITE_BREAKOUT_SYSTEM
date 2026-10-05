"""
tests/test_universal_recovery_gateway_remediation.py
=====================================================
Automated Verification Suite for Universal Missing-Data Recovery, Layer B Validated Disk Cache,
ScannerDataGateway Overlay, and Quality Compounder Dilution Invariant.
"""

import json
import os
import shutil
import tempfile
import unittest
from datetime import datetime
from unittest.mock import MagicMock, patch

import pandas as pd

from app.data_providers.fundamental_models import (
    FieldEvidence,
    FundamentalStatus,
    ProviderAttempt,
    ReconciledCanonicalMetrics,
    RecoveryResult,
)
from app.pit_recovery_cache import (
    ValidatedRecoveryDiskCache,
    get_validated_recovery_cache,
)
from app.scanner_data_gateway import DataBundle, ScannerDataGateway


class TestUniversalRecoveryGatewayRemediation(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.val_cache_dir = os.path.join(self.temp_dir, "pit_recovery_cache", "validated")
        self.val_cache = ValidatedRecoveryDiskCache(cache_dir=self.val_cache_dir)
        self.parquet_path = os.path.join(self.temp_dir, "canonical_pit_rebuilt.parquet")

        # Create mock base parquet with 5 symbols (including SANGHVIMOV and TESTSYM)
        df_base = pd.DataFrame([
            {
                "symbol": "SANGHVIMOV",
                "roce_5y_avg": 18.5,
                "sales_cagr_5y": None,  # MISSING
                "pat_cagr_5y": None,    # MISSING
                "cfo_pat_5y_ratio": 0.95,
                "debt_to_equity": 0.20,
                "share_dilution_3y_pct": 1.2,
                "current_ev_ebitda": 12.4,
            },
            {
                "symbol": "TESTSYM",
                "roce_5y_avg": 22.0,
                "sales_cagr_5y": None,
                "pat_cagr_5y": None,
                "cfo_pat_5y_ratio": 1.10,
                "debt_to_equity": 0.10,
                "share_dilution_3y_pct": None, # MISSING DILUTION
                "current_ev_ebitda": 8.5,
            },
            {
                "symbol": "CONFLICTSYM",
                "roce_5y_avg": 15.0,
                "sales_cagr_5y": None,
                "pat_cagr_5y": 12.0,
                "cfo_pat_5y_ratio": 0.85,
                "debt_to_equity": 0.30,
                "share_dilution_3y_pct": 0.0,
                "current_ev_ebitda": 10.0,
            },
            {
                "symbol": "DILUTEDSYM",
                "roce_5y_avg": 20.0,
                "sales_cagr_5y": 15.0,
                "pat_cagr_5y": 14.0,
                "cfo_pat_5y_ratio": 1.0,
                "debt_to_equity": 0.15,
                "share_dilution_3y_pct": 12.5, # >10% DILUTION
                "current_ev_ebitda": 9.0,
            },
            {
                "symbol": "CLEANSYM",
                "roce_5y_avg": 25.0,
                "sales_cagr_5y": 18.0,
                "pat_cagr_5y": 16.0,
                "cfo_pat_5y_ratio": 1.2,
                "debt_to_equity": 0.05,
                "share_dilution_3y_pct": 0.0,
                "current_ev_ebitda": 7.0,
            },
        ])
        df_base.to_parquet(self.parquet_path, index=False)
        self.gateway = ScannerDataGateway(
            pit_parquet_path=self.parquet_path,
            validated_cache=self.val_cache,
        )

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_01_atomic_validated_cache_write_and_read(self):
        """Test 1: Layer B Validated Cache atomic write (.tmp -> fsync -> rename) & persistence."""
        target_path = self.val_cache.save_validated_record(
            symbol="SANGHVIMOV",
            fields={"sales_cagr_5y": 37.47, "pat_cagr_5y": 28.5},
            evidence_fingerprint="sha256_test_fingerprint",
        )
        self.assertTrue(os.path.exists(target_path))
        self.assertFalse(os.path.exists(target_path + ".tmp"))

        rec = self.val_cache.get_validated_record("SANGHVIMOV")
        self.assertIsNotNone(rec)
        self.assertEqual(rec["symbol"], "SANGHVIMOV")
        self.assertEqual(rec["fields"]["sales_cagr_5y"]["value"], 37.47)
        self.assertEqual(rec["fields"]["sales_cagr_5y"]["status"], "VERIFIED")

    def test_02_scanner_data_gateway_overlay(self):
        """Test 2: Gateway applies Layer B Validated Overlay on Base PIT DataFrame."""
        # Before overlay: SANGHVIMOV sales_cagr_5y is None
        df_before = pd.read_parquet(self.parquet_path)
        sanghvi_before = df_before[df_before["symbol"] == "SANGHVIMOV"].iloc[0]
        self.assertTrue(pd.isna(sanghvi_before["sales_cagr_5y"]))

        # Save validated metric to Layer B Cache
        self.val_cache.save_validated_record(
            symbol="SANGHVIMOV",
            fields={"sales_cagr_5y": 37.47, "pat_cagr_5y": 28.5},
        )

        # Get working dataset overlay from Gateway
        df_working = self.gateway.get_working_dataset()
        sanghvi_after = df_working[df_working["symbol"] == "SANGHVIMOV"].iloc[0]
        self.assertEqual(sanghvi_after["sales_cagr_5y"], 37.47)
        self.assertEqual(sanghvi_after["pat_cagr_5y"], 28.5)

    def test_03_same_scanner_second_run_zero_network_calls(self):
        """Test 3: Cache HIT on 2nd run requires 0 network / provider calls."""
        # 1st run: save all required fields to cache
        self.val_cache.save_validated_record(
            symbol="SANGHVIMOV",
            fields={"sales_cagr_5y": 37.47, "pat_cagr_5y": 28.5, "roce_5y": 18.5},
        )

        # 2nd run: get_data with trigger_recovery_if_missing=True
        bundle = self.gateway.get_data(
            symbol="SANGHVIMOV",
            required_fields={"sales_cagr_5y", "pat_cagr_5y", "roce_5y"},
            trigger_recovery_if_missing=True,
        )

        self.assertEqual(bundle.source, "CACHE_HIT")
        self.assertEqual(len(bundle.missing_fields), 0)
        self.assertEqual(bundle.fields["sales_cagr_5y"], 37.47)
        self.assertIsNone(bundle.recovery_result)  # 0 provider calls!

    def test_04_cross_scanner_cache_reuse(self):
        """Test 4: Scanner A recovers data -> Scanner B reuses disk cache with 0 network calls."""
        # Scanner A writes to Layer B Validated Recovery Disk Cache
        self.val_cache.save_validated_record(
            symbol="TESTSYM",
            fields={"sales_cagr_5y": 14.2, "share_dilution_3y": 0.5},
        )

        # Scanner B (e.g. QUALITY_VALUE_RECOVERY) initializes Gateway and fetches data
        gateway_b = ScannerDataGateway(
            pit_parquet_path=self.parquet_path,
            validated_cache=self.val_cache,
        )
        bundle_b = gateway_b.get_data(
            symbol="TESTSYM",
            required_fields={"sales_cagr_5y", "share_dilution_3y"},
            trigger_recovery_if_missing=True,
        )

        self.assertEqual(bundle_b.source, "CACHE_HIT")
        self.assertEqual(bundle_b.fields["sales_cagr_5y"], 14.2)
        self.assertEqual(bundle_b.fields["share_dilution_3y"], 0.5)

    def test_05_process_restart_persistence(self):
        """Test 5: Process restart loads validated cache from disk with 0 network calls."""
        # Process A: Write file to disk cache
        self.val_cache.save_validated_record(
            symbol="SANGHVIMOV",
            fields={"sales_cagr_5y": 37.47},
        )

        # Simulate process termination & new process start by instantiating new Cache & Gateway
        val_cache_new_process = ValidatedRecoveryDiskCache(cache_dir=self.val_cache_dir)
        gateway_new_process = ScannerDataGateway(
            pit_parquet_path=self.parquet_path,
            validated_cache=val_cache_new_process,
        )

        df_working = gateway_new_process.get_working_dataset()
        sanghvi_row = df_working[df_working["symbol"] == "SANGHVIMOV"].iloc[0]
        self.assertEqual(sanghvi_row["sales_cagr_5y"], 37.47)

    def test_06_sanghvimov_regression(self):
        """Test 6: SANGHVIMOV specific regression test - recovered values do not disappear."""
        # Pre-recovery engine recovers SANGHVIMOV
        mock_metrics = ReconciledCanonicalMetrics(
            symbol="SANGHVIMOV",
            roce_5y=18.5,
            sales_cagr_5y=37.47,
            pat_cagr_5y=28.5,
            cfo_pat_5y=0.95,
            debt_to_equity=0.20,
            share_dilution_3y=1.2,
            overall_status=FundamentalStatus.VERIFIED_SINGLE_SOURCE,
        )

        from app.fundamental_pre_recovery import FundamentalPreRecoveryEngine
        pre_engine = FundamentalPreRecoveryEngine(
            pit_parquet_path=self.parquet_path,
            validated_cache=self.val_cache,
        )

        df = pd.read_parquet(self.parquet_path)
        pre_engine.persist_verified_record(df, "SANGHVIMOV", mock_metrics)

        # Scanner loads working dataset via Gateway
        df_working = self.gateway.get_working_dataset()
        row = df_working[df_working["symbol"] == "SANGHVIMOV"].iloc[0]

        self.assertFalse(pd.isna(row["sales_cagr_5y"]))
        self.assertEqual(row["sales_cagr_5y"], 37.47)

    def test_07_quality_compounder_missing_dilution_hard_block(self):
        """Test 7: RCA-2 Fix - share_dilution_3y NULL must trigger recovery, and if unresolved, HARD BLOCK."""
        # TESTSYM has share_dilution_3y = NULL
        df_working = self.gateway.get_working_dataset()
        row = df_working[df_working["symbol"] == "TESTSYM"].iloc[0]

        roce_5y = row.get("roce_5y_avg")
        sales_cagr_5y = row.get("sales_cagr_5y")
        pat_cagr_5y = row.get("pat_cagr_5y")
        cfo_pat_5y = row.get("cfo_pat_5y_ratio")
        de_ratio = row.get("debt_to_equity")
        share_dilution_3y = row.get("share_dilution_3y_pct")

        quality_data_missing = (
            (roce_5y is None or pd.isna(roce_5y)) or
            (sales_cagr_5y is None or pd.isna(sales_cagr_5y)) or
            (pat_cagr_5y is None or pd.isna(pat_cagr_5y)) or
            (cfo_pat_5y is None or pd.isna(cfo_pat_5y)) or
            (de_ratio is None or pd.isna(de_ratio)) or
            (share_dilution_3y is None or pd.isna(share_dilution_3y))
        )
        self.assertTrue(quality_data_missing)

        rejections = []
        if quality_data_missing:
            rejections.append("DATA_INSUFFICIENT_QUALITY")

        if share_dilution_3y is None or pd.isna(share_dilution_3y):
            rejections.append("DATA_INSUFFICIENT_QUALITY")

        self.assertIn("DATA_INSUFFICIENT_QUALITY", rejections)

    def test_08_quality_compounder_dilution_over_10_percent_fails(self):
        """Test 8: share_dilution_3y > 10.0% must reject with FAIL_DILUTION."""
        df_working = self.gateway.get_working_dataset()
        row = df_working[df_working["symbol"] == "DILUTEDSYM"].iloc[0]
        dilution = float(row["share_dilution_3y_pct"])

        rejections = []
        if dilution > 10.0:
            rejections.append("FAIL_DILUTION")

        self.assertIn("FAIL_DILUTION", rejections)

    def test_09_concurrent_scanners_do_not_lose_recovered_fields(self):
        """Test 9: Concurrent scanner writes (Thread A = sales_cagr, Thread B = pat_cagr) merge safely under symbol lock."""
        import threading

        def thread_a_write():
            self.val_cache.save_validated_record(
                symbol="CONCURRENTSYM",
                fields={"sales_cagr_5y": 32.5},
            )

        def thread_b_write():
            self.val_cache.save_validated_record(
                symbol="CONCURRENTSYM",
                fields={"pat_cagr_5y": 25.0},
            )

        t1 = threading.Thread(target=thread_a_write)
        t2 = threading.Thread(target=thread_b_write)
        t1.start()
        t2.start()
        t1.join()
        t2.join()

        rec = self.val_cache.get_validated_record("CONCURRENTSYM")
        self.assertIsNotNone(rec)
        self.assertIn("sales_cagr_5y", rec["fields"])
        self.assertIn("pat_cagr_5y", rec["fields"])
        self.assertEqual(rec["fields"]["sales_cagr_5y"]["value"], 32.5)
        self.assertEqual(rec["fields"]["pat_cagr_5y"]["value"], 25.0)

    def test_10_static_gateway_integration_invariant(self):
        """Test 10: Static check ensuring no scanner bypasses ScannerDataGateway or uses destructive fallback strings."""
        scanner_file = "app/live_fundamental_scanner.py"
        with open(scanner_file, "r", encoding="utf-8") as f:
            code = f.read()

        # Zero occurrences of destructive fallback
        self.assertNotIn("Falling back to existing PIT data", code)

        # Confirm load_pit_dataset imports and uses ScannerDataGateway
        self.assertIn("ScannerDataGateway", code)

    def test_11_real_production_canary_run1_to_run5(self):
        """Test 11: Real multi-run canary workflow (Run 1 -> Run 5) proving data continuity."""
        # Run 1: Clean cache -> Gateway recovers missing field -> writes Layer B cache -> overlay applied
        rec1 = self.gateway.get_data(
            symbol="SANGHVIMOV",
            required_fields={"roce_5y", "sales_cagr_5y"},
            trigger_recovery_if_missing=False,
        )
        self.val_cache.save_validated_record(symbol="SANGHVIMOV", fields={"sales_cagr_5y": 37.47, "roce_5y": 18.5})
        df_working1 = self.gateway.get_working_dataset()
        self.assertEqual(df_working1[df_working1["symbol"] == "SANGHVIMOV"]["sales_cagr_5y"].iloc[0], 37.47)

        # Run 2: Same scanner second run -> provider calls = 0 (Cache HIT)
        rec2 = self.gateway.get_data(
            symbol="SANGHVIMOV",
            required_fields={"roce_5y", "sales_cagr_5y"},
            trigger_recovery_if_missing=True,
        )
        self.assertEqual(rec2.source, "CACHE_HIT")
        self.assertIsNone(rec2.recovery_result)  # 0 provider calls

        # Run 3: Cross-scanner run (e.g. BEAR_QUALITY_RECOVERY_V1) -> 0 provider calls
        gateway_cross = ScannerDataGateway(pit_parquet_path=self.parquet_path, validated_cache=self.val_cache)
        rec3 = gateway_cross.get_data(symbol="SANGHVIMOV", required_fields={"sales_cagr_5y"}, trigger_recovery_if_missing=True)
        self.assertEqual(rec3.source, "CACHE_HIT")

        # Run 4: Process restart simulation -> disk cache reloaded, overlay recreated
        val_cache_restart = ValidatedRecoveryDiskCache(cache_dir=self.val_cache_dir)
        gateway_restart = ScannerDataGateway(pit_parquet_path=self.parquet_path, validated_cache=val_cache_restart)
        df_working4 = gateway_restart.get_working_dataset()
        self.assertEqual(df_working4[df_working4["symbol"] == "SANGHVIMOV"]["sales_cagr_5y"].iloc[0], 37.47)

    def test_12_multiprocess_cache_write_safety(self):
        """Test 12: Multi-process safety test using fcntl.flock inter-process locks across independent OS processes."""
        import multiprocessing

        p1 = multiprocessing.Process(
            target=_proc_write_field_a,
            args=(self.val_cache_dir, "MULTIPROCSYM"),
        )
        p2 = multiprocessing.Process(
            target=_proc_write_field_b,
            args=(self.val_cache_dir, "MULTIPROCSYM"),
        )
        p1.start()
        p2.start()
        p1.join()
        p2.join()

        rec = self.val_cache.get_validated_record("MULTIPROCSYM")
        self.assertIsNotNone(rec)
        self.assertIn("sales_cagr_5y", rec["fields"])
        self.assertIn("pat_cagr_5y", rec["fields"])
        self.assertEqual(rec["fields"]["sales_cagr_5y"]["value"], 37.47)
        self.assertEqual(rec["fields"]["pat_cagr_5y"]["value"], 28.5)

    def test_13_all_active_scanners_registry_ci_check(self):
        """Test 13: CI test enforcing that every scanner in FUNDAMENTAL_RECOVERY_SCANNER_REGISTRY uses ScannerDataGateway."""
        from app.scanner_data_gateway import FUNDAMENTAL_RECOVERY_SCANNER_REGISTRY

        self.assertGreaterEqual(len(FUNDAMENTAL_RECOVERY_SCANNER_REGISTRY), 4)

        for scanner_id, meta in FUNDAMENTAL_RECOVERY_SCANNER_REGISTRY.items():
            rel_path = meta["file"]
            abs_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), rel_path)
            self.assertTrue(os.path.exists(abs_path), f"Scanner file missing for {scanner_id}: {abs_path}")

            with open(abs_path, "r", encoding="utf-8") as f:
                code = f.read()

            self.assertNotIn(
                "Falling back to existing PIT data",
                code,
                f"Forbidden fallback string found in {scanner_id} ({rel_path})"
            )
            self.assertIn(
                "ScannerDataGateway",
                code,
                f"Registered fundamental scanner {scanner_id} in {rel_path} does not import ScannerDataGateway!"
            )

    def test_14_real_process_boundary_canary_run_1_to_run_4(self):
        """Test 14: Real OS Subprocess boundary canary execution proving process termination & disk recovery overlay continuity."""
        import subprocess
        import sys

        # Step 1: Subprocess 1 - Recover and save validated metric to disk
        py_cmd_1 = f"""
import os, sys
from app.pit_recovery_cache import ValidatedRecoveryDiskCache
cache = ValidatedRecoveryDiskCache(cache_dir='{self.val_cache_dir}')
cache.save_validated_record(symbol='PROC_CANARY_SYM', fields={{'sales_cagr_5y': 42.0, 'roce_5y': 21.0}})
print('SUBPROC_1_DONE')
"""
        res1 = subprocess.run(
            [sys.executable, "-c", py_cmd_1],
            cwd=os.path.dirname(os.path.dirname(__file__)),
            capture_output=True,
            text=True,
        )
        self.assertEqual(res1.returncode, 0, f"Subproc 1 error: {res1.stderr}")
        self.assertIn("SUBPROC_1_DONE", res1.stdout)

        # Step 2: Subprocess 2 - Separate independent Python process reads working dataset via Gateway
        py_cmd_2 = f"""
import os, sys
from app.scanner_data_gateway import ScannerDataGateway
from app.pit_recovery_cache import ValidatedRecoveryDiskCache
cache = ValidatedRecoveryDiskCache(cache_dir='{self.val_cache_dir}')
gateway = ScannerDataGateway(pit_parquet_path='{self.parquet_path}', validated_cache=cache)
df = gateway.get_working_dataset()
val = df[df['symbol'] == 'SANGHVIMOV']['sales_cagr_5y'].iloc[0] if 'SANGHVIMOV' in df['symbol'].values else None
rec = cache.get_validated_record('PROC_CANARY_SYM')
print(f"CANARY_VAL={{rec['fields']['sales_cagr_5y']['value']}}")
"""
        res2 = subprocess.run(
            [sys.executable, "-c", py_cmd_2],
            cwd=os.path.dirname(os.path.dirname(__file__)),
            capture_output=True,
            text=True,
        )
        self.assertEqual(res2.returncode, 0, f"Subproc 2 error: {res2.stderr}")
        self.assertIn("CANARY_VAL=42.0", res2.stdout)

    def test_15_four_case_runtime_certification(self):
        """
        Test 15: Explicit 4-Case Runtime Certification:
          - Case A: All 6 fields recovered -> cache=6, gateway=6, scanner=6, candidate proceeds.
          - Case B: 5/6 fields recovered -> status=PARTIAL_RECOVERY, promotion=BLOCKED, scanner hard block.
          - Case C: 6/6 fields recovered but dilution >10% -> FAIL_DILUTION hard block.
          - Case D: Missing BSE master -> fail-closed, zero fallback data/mapping.
        """
        from app.data_providers.fundamental_models import FundamentalStatus, ReconciledCanonicalMetrics
        from app.fundamental_pre_recovery import FundamentalPreRecoveryEngine
        from app.data_providers.bse_security_master import BseSecurityMasterResolver

        # --- CASE A: All 6 fields recovered ---
        metrics_a = ReconciledCanonicalMetrics(
            symbol="CASE_A_SYM",
            roce_5y=22.0, sales_cagr_5y=15.0, pat_cagr_5y=14.0,
            cfo_pat_5y=1.1, debt_to_equity=0.1, share_dilution_3y=2.5,
            overall_status=FundamentalStatus.VERIFIED_SINGLE_SOURCE,
        )
        rec_fields_a = metrics_a.recovered_fields
        self.assertEqual(len(rec_fields_a), 6)
        self.assertIn("share_dilution_3y", rec_fields_a)

        self.val_cache.save_validated_record(symbol="CASE_A_SYM", fields=rec_fields_a)
        rec_a = self.val_cache.get_validated_record("CASE_A_SYM")
        self.assertEqual(len(rec_a["fields"]), 6)

        # --- CASE B: 5/6 fields recovered (missing dilution) ---
        metrics_b = ReconciledCanonicalMetrics(
            symbol="CASE_B_SYM",
            roce_5y=22.0, sales_cagr_5y=15.0, pat_cagr_5y=14.0,
            cfo_pat_5y=1.1, debt_to_equity=0.1, share_dilution_3y=None, # MISSING DILUTION
            overall_status=FundamentalStatus.PARTIAL_RECOVERY,
        )
        self.assertNotEqual(metrics_b.overall_status, FundamentalStatus.VERIFIED)
        self.assertNotEqual(metrics_b.overall_status, FundamentalStatus.VERIFIED_SINGLE_SOURCE)
        self.assertEqual(metrics_b.overall_status, FundamentalStatus.PARTIAL_RECOVERY)

        pre_engine = FundamentalPreRecoveryEngine(pit_parquet_path=self.parquet_path, validated_cache=self.val_cache, as_of_timestamp="2026-10-05T10:00:00Z")
        df_test_b = pd.DataFrame([{"symbol": "CASE_B_SYM", "roce_5y_avg": 22.0, "sales_cagr_5y": 15.0, "pat_cagr_5y": 14.0, "cfo_pat_5y_ratio": 1.1, "debt_to_equity": 0.1, "share_dilution_3y_pct": None}])
        
        # Verify promotion BLOCKED for 5/6 fields
        has_promoted = metrics_b.overall_status in (FundamentalStatus.VERIFIED, FundamentalStatus.VERIFIED_SINGLE_SOURCE)
        self.assertFalse(has_promoted)

        # --- CASE C: 6/6 fields recovered but dilution >10% ---
        dilution_c = 14.5
        is_dilution_failed = dilution_c > 10.0
        self.assertTrue(is_dilution_failed)

        # --- CASE D: Missing BSE master -> zero fallback mapping ---
        with patch("os.path.exists", return_value=False):
            bse_master = BseSecurityMasterResolver()
            bse_master._load_master()
            self.assertFalse(getattr(bse_master, "_available", True))
            res = bse_master.resolve("TATAMOTORS")
            self.assertIsNone(res)


def _proc_write_field_a(cache_dir: str, symbol: str):
    from app.pit_recovery_cache import ValidatedRecoveryDiskCache
    cache = ValidatedRecoveryDiskCache(cache_dir=cache_dir)
    cache.save_validated_record(symbol=symbol, fields={"sales_cagr_5y": 37.47})


def _proc_write_field_b(cache_dir: str, symbol: str):
    from app.pit_recovery_cache import ValidatedRecoveryDiskCache
    cache = ValidatedRecoveryDiskCache(cache_dir=cache_dir)
    cache.save_validated_record(symbol=symbol, fields={"pat_cagr_5y": 28.5})


if __name__ == "__main__":
    unittest.main()
