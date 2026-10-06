"""
tests/test_shripiston_pre_buy_fence_regression.py
==================================================
Regression test suite for SHRIPISTON Pre-BUY Integrity Gate and zero-fallback provenance.

Verifies:
1. Exact SHRIPISTON Situation:
   canonical latest annual period = 2026-03-31
   raw latest annual filing      = 2026-03-31
   Expected:
     UNPROCESSED_RAW_FILING = False
     PRE_BUY_GATE = PASS
     BUY_ALERT_SUPPRESSED = False
     BUY_ALERT = persisted (authoritative DB outbox)

2. Genuine newer raw filing:
   canonical = 2025-03-31
   raw        = 2026-03-31
   Expected:
     PRE_BUY_GATE = BLOCK
     reason includes UNPROCESSED_RAW_FILING

3. Missing canonical period (Zero-fallback invariant):
   canonical period = missing (None / empty / PROVENANCE_PERIOD_MISSING)
   raw period       = 2026-03-31
   Expected:
     BLOCK
     reason = PROVENANCE_PERIOD_MISSING

4. Dual-key representation propagation:
   funds_map and to_fundamental_dict() must populate both:
     - latest_annual_period
     - period_end
"""

import os
import json
import tempfile
import pytest
from unittest.mock import patch, MagicMock

from app.financial_data_integrity import (
    check_pre_buy_source_freshness_fence,
    commit_buy_alert_atomic,
    BUYEvidenceBundle,
    FieldProvenance,
    SharedFinancialSnapshot,
    SnapshotFreshnessStatus,
    DataStatus,
)


def test_dual_key_representation_in_funds_dict_and_snapshot():
    """Verify both 'latest_annual_period' and 'period_end' are populated in funds_dict and snapshot."""
    snap = SharedFinancialSnapshot(
        symbol="SHRIPISTON",
        isin="INE526E01018",
        as_of_date="2026-10-03",
        latest_annual_period="2026-03-31",
        snapshot_status=SnapshotFreshnessStatus.FRESH.value,
        pit_freshness_status=DataStatus.VALID.value,
        roce=23.4,
        roe=21.0,
        debt_equity=0.05,
        operating_cash_flow=350.0,
    )
    d = snap.to_fundamental_dict()
    assert d.get("latest_annual_period") == "2026-03-31"
    assert d.get("period_end") == "2026-03-31"
    assert d.get("filing_date") == "2026-10-03"


def test_shripiston_exact_case_pre_buy_gate_passes_and_persists():
    """
    Case 1: Exact SHRIPISTON Situation:
      canonical latest annual period = 2026-03-31
      raw latest annual filing      = 2026-03-31
    Expected:
      UNPROCESSED_RAW_FILING = False
      PRE_BUY_GATE = PASS
      BUY_ALERT_SUPPRESSED = False
      BUY_ALERT = persisted
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        raw_dir = os.path.join(tmpdir, "data", "pit_raw_filings")
        os.makedirs(raw_dir, exist_ok=True)
        raw_filing_path = os.path.join(raw_dir, "SHRIPISTON.json")

        # Raw filing has period_end_date 2026-03-31
        raw_filings = [
            {
                "filing_id": "SHRIPISTON_20260331_A_v1",
                "symbol": "SHRIPISTON",
                "period_end_date": "2026-03-31",
                "statement_type": "ANNUAL",
                "source": "NSE_XBRL",
                "revenue": 3000.0,
                "net_profit": 450.0,
            }
        ]
        with open(raw_filing_path, "w") as f:
            json.dump(raw_filings, f)

        # Mock base_dir to point to tmpdir
        with patch.dict(os.environ, {"ELITE_BASE_DIR": tmpdir}), \
             patch("app.financial_data_integrity.get_multi_source_exchange_watermark", return_value={"valid": True}):

            # 1. Direct Pre-BUY Freshness Fence evaluation
            passed, reason = check_pre_buy_source_freshness_fence(
                symbol="SHRIPISTON",
                canonical_period_end="2026-03-31",
                canonical_filing_timestamp="2026-10-03T10:00:00",
            )
            assert passed is True
            assert reason is None

            # 2. End-to-end atomic commit evaluation
            alerts_db = os.path.join(tmpdir, "buy_alerts_journal.db")
            alerts_pq = os.path.join(tmpdir, "buy_alerts.parquet")

            fp = FieldProvenance(
                symbol="SHRIPISTON", scanner="FUNDAMENTAL", field="roce",
                value_used=23.4, unit="PERCENT", basis="CONSOLIDATED",
                period_end="2026-03-31", validation_status="PASSED",
                source_used="PIT_FUNDAMENTALS"
            )
            bundle = BUYEvidenceBundle(
                scan_run_id="RUN_SHRIPISTON_1",
                scanner="FUNDAMENTAL",
                symbol="SHRIPISTON",
                cmp=2150.0,
                strategy_score=92.5,
                financial_metrics={"roce": fp},
                pit_timestamp="2026-10-03T10:00:00",
                pit_eligible_from="2026-10-03T10:00:00",
                data_integrity_status=DataStatus.VALID,
                financial_provenance_complete=True,
                pit_valid=True,
                period_integrity=True,
                basis_integrity=True,
                unit_integrity=True,
                required_metrics_complete=True,
                snapshot_version="v2026-10-03",
                snapshot_sha256="abcd1234ef567890abcd1234ef567890" * 2,
                evidence_hash="0123456789abcdef0123456789abcdef",
            )

            # Mock watcher to return FRESH
            mock_watcher = MagicMock()
            mock_watcher.get_symbol_freshness_status.return_value = SnapshotFreshnessStatus.FRESH
            with patch("scripts.financial_filing_watcher.FinancialFilingWatcher", return_value=mock_watcher):
                committed, commit_status = commit_buy_alert_atomic(
                    bundle=bundle,
                    alerts_parquet_path=alerts_pq,
                    alerts_db_path=alerts_db,
                )
                assert committed is True
                assert commit_status == "COMMITTED"

                # Verify outbox DB has the record
                import sqlite3
                with sqlite3.connect(alerts_db) as conn:
                    rows = conn.execute("SELECT symbol, scanner, status, materialized_to_parquet FROM buy_alerts_journal WHERE symbol='SHRIPISTON'").fetchall()
                    assert len(rows) == 1
                    assert rows[0][0] == "SHRIPISTON"
                    assert rows[0][2] == "COMMITTED"
                    assert rows[0][3] == 1

                # Verify Parquet materialization
                import pandas as pd
                df_alerts = pd.read_parquet(alerts_pq)
                assert len(df_alerts) == 1
                assert df_alerts.iloc[0]["symbol"] == "SHRIPISTON"
                assert df_alerts.iloc[0]["status"] == "COMMITTED"


def test_genuine_newer_raw_filing_blocks_pre_buy_gate():
    """
    Case 2: Genuine newer raw filing:
      canonical = 2025-03-31
      raw        = 2026-03-31
    Expected:
      PRE_BUY_GATE = BLOCK
      reason contains UNPROCESSED_RAW_FILING
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        raw_dir = os.path.join(tmpdir, "data", "pit_raw_filings")
        os.makedirs(raw_dir, exist_ok=True)
        raw_filing_path = os.path.join(raw_dir, "SHRIPISTON.json")

        raw_filings = [
            {
                "filing_id": "SHRIPISTON_20260331_A_v1",
                "symbol": "SHRIPISTON",
                "period_end_date": "2026-03-31",
                "statement_type": "ANNUAL",
                "source": "NSE_XBRL",
            }
        ]
        with open(raw_filing_path, "w") as f:
            json.dump(raw_filings, f)

        with patch.dict(os.environ, {"ELITE_BASE_DIR": tmpdir}), \
             patch("app.financial_data_integrity.get_multi_source_exchange_watermark", return_value={"valid": True}):

            passed, reason = check_pre_buy_source_freshness_fence(
                symbol="SHRIPISTON",
                canonical_period_end="2025-03-31",  # Stale canonical period
                canonical_filing_timestamp="2025-05-30T10:00:00",
            )
            assert passed is False
            assert "UNPROCESSED_RAW_FILING" in reason
            assert "2026-03-31 > canonical 2025-03-31" in reason


def test_missing_canonical_period_fails_closed_as_provenance_period_missing():
    """
    Case 3: Missing canonical period (Zero-fallback invariant):
      canonical period = missing (None / empty / PROVENANCE_PERIOD_MISSING)
      raw period       = 2026-03-31
    Expected:
      BLOCK
      reason = PROVENANCE_PERIOD_MISSING
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        with patch.dict(os.environ, {"ELITE_BASE_DIR": tmpdir}), \
             patch("app.financial_data_integrity.get_multi_source_exchange_watermark", return_value={"valid": True}):

            # Subcase A: canonical_period_end is None
            passed_none, reason_none = check_pre_buy_source_freshness_fence(
                symbol="SHRIPISTON",
                canonical_period_end=None,
            )
            assert passed_none is False
            assert "PROVENANCE_PERIOD_MISSING" in reason_none

            # Subcase B: canonical_period_end is empty string
            passed_empty, reason_empty = check_pre_buy_source_freshness_fence(
                symbol="SHRIPISTON",
                canonical_period_end="",
            )
            assert passed_empty is False
            assert "PROVENANCE_PERIOD_MISSING" in reason_empty

            # Subcase C: canonical_period_end is 'PROVENANCE_PERIOD_MISSING'
            passed_miss, reason_miss = check_pre_buy_source_freshness_fence(
                symbol="SHRIPISTON",
                canonical_period_end="PROVENANCE_PERIOD_MISSING",
            )
            assert passed_miss is False
            assert "PROVENANCE_PERIOD_MISSING" in reason_miss


if __name__ == "__main__":
    print("Testing dual key representation...")
    test_dual_key_representation_in_funds_dict_and_snapshot()
    print("Testing exact SHRIPISTON pre-buy gate pass and persist...")
    test_shripiston_exact_case_pre_buy_gate_passes_and_persists()
    print("Testing genuine newer raw filing blocks pre-buy gate...")
    test_genuine_newer_raw_filing_blocks_pre_buy_gate()
    print("Testing missing canonical period fails closed...")
    test_missing_canonical_period_fails_closed_as_provenance_period_missing()
    print("ALL TESTS PASSED!")
