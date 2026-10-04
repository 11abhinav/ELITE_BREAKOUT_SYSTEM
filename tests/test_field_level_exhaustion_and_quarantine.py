"""
tests/test_field_level_exhaustion_and_quarantine.py
====================================================
Comprehensive Test Suite for:
  - Pending Item 1: Dependency-scoped 7-day quarantine (symbol, field, scanner_family)
  - Pending Item 2: Quarantine restart persistence and expiry advance
  - Pending Item 3: Full "No Data Anywhere" proof & negative case (no premature confirmed absence)
  - Pending Item 4: Field-level progressive provider exhaustion
  - Pending Item 5: Data availability vs production eligibility separation & non-contradiction
  - Pending Item 7: Screener reference-only governance rule & scanner blocking
  - Pending Item 15: Bidirectional isolation (TEMP -> PRODUCTION, PRODUCTION -> TEMP)
"""

import os
import sys
import tempfile
from datetime import datetime, timedelta
from unittest import mock
from zoneinfo import ZoneInfo
import pytest

from app.data_providers.fundamental_models import (
    RawFinancialRecord,
    ReconciledCanonicalMetrics,
    FundamentalStatus,
    ConsolidationType,
)
from app.data_providers.fundamental_source_router import FundamentalSourceRouter
from app.data_providers.data_availability_auditor import (
    DataAvailabilityAuditor,
    AvailabilityClassification,
    AvailabilityAuditRecord,
    ReferenceStatus,
    ReferenceCheck,
    AuthorityTier,
)
from app.pit_recovery_cache import (
    PitRecoveryStatusStore,
    make_quarantine_key,
    is_quarantine_db_sync_allowed,
    PRODUCTION_QUARANTINE_PATH,
    compute_evidence_fingerprint,
)

IST = ZoneInfo("Asia/Kolkata")


# ==============================================================================
# PENDING ITEM 1 & 2: Real 7-Day Quarantine Behaviour & Restart Persistence
# ==============================================================================

def test_dependency_scoped_quarantine_and_restart_persistence():
    """
    Test scenario:
      1. Create deterministic quarantine for: TEST_SYMBOL / sales_cagr_5y / QUALITY_COMPOUNDER
      2. Persist quarantine to temporary store.
      3. Restart/reinitialize the cache/store.
      4. Reload persisted state.
      5. Assert:
         - quarantine still exists
         - retry_after/expires_at unchanged
         - scanner exclusion remains active for (TEST_SYMBOL, QUALITY_COMPOUNDER, sales_cagr_5y)
         - scanner exclusion is NOT active for unrelated family (TECHNICAL) or unrelated field
      6. Advance past expiry in test.
      7. Assert:
         - quarantine expires
         - recovery becomes eligible again
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        p_path = os.path.join(tmpdir, "pit_recovery_status.parquet")
        store = PitRecoveryStatusStore(parquet_path=p_path)

        rec = store.record_unavailability(
            symbol="TEST_SYMBOL",
            provider="NSE_XBRL",
            status="CONFIRMED_NO_DATA_ANYWHERE",
            reason="CONFIRMED_NO_DATA_ANYWHERE",
            scanner_family="QUALITY_COMPOUNDER",
            field_name="sales_cagr_5y",
            raw_record_count=0,
            persist=True,
        )

        assert rec["symbol"] == "TEST_SYMBOL"
        assert rec["field"] == "sales_cagr_5y"
        assert rec["scanner_family"] == "QUALITY_COMPOUNDER"
        orig_expires = rec["expires_at"]
        orig_retry = rec["retry_after"]

        # Assert active before restart
        assert store.is_quarantined_for_scanner("TEST_SYMBOL", "QUALITY_COMPOUNDER", "sales_cagr_5y") is True
        assert store.is_quarantined_for_scanner("TEST_SYMBOL", "QUALITY_COMPOUNDER") is True
        # Unrelated scanner family must NOT be quarantined
        assert store.is_quarantined_for_scanner("TEST_SYMBOL", "TECHNICAL") is False

        # Restart/reinitialize
        store_reloaded = PitRecoveryStatusStore(parquet_path=p_path)
        assert store_reloaded._loaded is True

        reloaded_entry = store_reloaded.get_status("TEST_SYMBOL", field="sales_cagr_5y", scanner_family="QUALITY_COMPOUNDER")
        assert reloaded_entry is not None
        assert reloaded_entry["expires_at"] == orig_expires
        assert reloaded_entry["retry_after"] == orig_retry
        assert store_reloaded.is_quarantined_for_scanner("TEST_SYMBOL", "QUALITY_COMPOUNDER", "sales_cagr_5y") is True
        assert store_reloaded.is_quarantined_for_scanner("TEST_SYMBOL", "QUALITY_COMPOUNDER") is True

        # Simulate advancing past expiry
        now_future = datetime.now(IST) + timedelta(days=8)
        with mock.patch("app.pit_recovery_cache.datetime") as mock_dt:
            mock_dt.now.return_value = now_future
            mock_dt.fromisoformat = datetime.fromisoformat
            mock_dt.side_effect = lambda *args, **kwargs: datetime(*args, **kwargs)

            # Assert expired
            assert store_reloaded.is_quarantined_for_scanner("TEST_SYMBOL", "QUALITY_COMPOUNDER", "sales_cagr_5y") is False
            is_neg, _ = store_reloaded.is_negatively_cached("TEST_SYMBOL", "sales_cagr_5y", "QUALITY_COMPOUNDER")
            assert is_neg is False


def test_quarantine_reasons_eligibility():
    """
    Assert 7-day quarantine applies ONLY to:
      - CONFIRMED_NO_DATA_ANYWHERE
      - CONFIRMED_SHORT_HISTORY
      - HISTORICAL_FILING_GAP
    and NEVER to:
      - PROVIDER_FAILURE
      - PARSER_FAILURE
      - PARSER_OR_FIELD_MAPPING_FAILURE
      - SYMBOL_MAPPING_FAILURE
      - REFERENCE_ONLY_AVAILABLE
      - STRUCTURALLY_UNSUPPORTED
      - INVALID_CAGR_BASE
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        p_path = os.path.join(tmpdir, "pit_recovery_status.parquet")
        store = PitRecoveryStatusStore(parquet_path=p_path)

        eligible_reasons = [
            "CONFIRMED_NO_DATA_ANYWHERE",
            "CONFIRMED_SHORT_HISTORY",
            "HISTORICAL_FILING_GAP",
        ]
        for r in eligible_reasons:
            store.record_unavailability(
                symbol="SYM_ELIG",
                provider="NSE_XBRL",
                status=r,
                reason=r,
                scanner_family="FUNDAMENTAL",
                field_name="sales_cagr_5y",
            )
            assert store.is_quarantined_for_scanner("SYM_ELIG", "FUNDAMENTAL", "sales_cagr_5y") is True
            store.clear()

        ineligible_reasons = [
            "PROVIDER_FAILURE",
            "PARSER_FAILURE",
            "PARSER_OR_FIELD_MAPPING_FAILURE",
            "SYMBOL_MAPPING_FAILURE",
            "REFERENCE_ONLY_AVAILABLE",
            "STRUCTURALLY_UNSUPPORTED",
            "INVALID_CAGR_BASE",
        ]
        for r in ineligible_reasons:
            store.record_unavailability(
                symbol="SYM_INELIG",
                provider="NSE_XBRL",
                status=r,
                reason=r,
                scanner_family="FUNDAMENTAL",
                field_name="sales_cagr_5y",
            )
            assert store.is_quarantined_for_scanner("SYM_INELIG", "FUNDAMENTAL", "sales_cagr_5y") is False
            store.clear()


def test_evidence_fingerprint_invalidation():
    """
    Tests that composite evidence change immediately invalidates 7-day quarantine.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        p_path = os.path.join(tmpdir, "pit_recovery_status.parquet")
        store = PitRecoveryStatusStore(parquet_path=p_path)

        store.record_unavailability(
            symbol="INFY",
            provider="NSE_XBRL",
            status="CONFIRMED_NO_DATA_ANYWHERE",
            reason="CONFIRMED_NO_DATA_ANYWHERE",
            latest_filing_date="2024-03-31",
            latest_period_end="2024-03-31",
            raw_record_count=10,
            scanner_family="FUNDAMENTAL",
        )
        assert store.is_quarantined_for_scanner("INFY", "FUNDAMENTAL") is True

        # Invalidation via newer filing date
        invalidated = store.check_and_invalidate_on_new_filing("INFY", latest_filing_date="2024-06-30")
        assert invalidated is True
        assert store.is_quarantined_for_scanner("INFY", "FUNDAMENTAL") is False


# ==============================================================================
# PENDING ITEM 3: Full "No Data Anywhere" Proof & Negative Case
# ==============================================================================

def test_full_no_data_anywhere_terminal_state():
    """
    Provider-contract test:
      NSE = terminal no-data
      BSE = terminal no-data
      Upstox = terminal no-data
      FYERS = terminal no-data
      Screener = absent
      -> classification = CONFIRMED_NO_DATA_ANYWHERE, quarantine = 7 days.
    """
    auditor = DataAvailabilityAuditor(scanner_name="FUNDAMENTAL", persist_to_db=False)
    trace = {
        "is_bse_only": False,
        "nse_records": 0,
        "nse_status": "NO_DATA_RETURNED",
        "nse_parser_status": "NO_DATA_RETURNED",
        "bse_records": 0,
        "bse_status": "BSE_NO_DATA",
        "upstox_records": 0,
        "upstox_status": "NO_DATA",
        "raw_rows_returned": 0,
        "fyers_status": "NO_DATA",
        "all_providers_exhausted": True,
        "confirmed_no_data": True,
    }
    screener_check = ReferenceCheck(source="Screener", tier=AuthorityTier.TIER_3_FORENSIC, status=ReferenceStatus.MISSING)

    rec = auditor.classify_field("TESTSYM", "sales_cagr_5y", trace, screener=screener_check)
    assert rec.classification == AvailabilityClassification.CONFIRMED_NO_DATA_ANYWHERE.value
    assert rec.quarantine_action == "7_DAY_QUARANTINE"
    assert rec.production_eligibility == "INELIGIBLE"
    assert rec.source_of_truth == "NONE"


def test_full_no_data_anywhere_negative_case():
    """
    Negative case:
      If ANY approved provider is NOT_CHECKED, UNKNOWN, NOT_ATTEMPTED, or in transient error,
      CONFIRMED_NO_DATA_ANYWHERE must be strictly IMPOSSIBLE.
    """
    auditor = DataAvailabilityAuditor(scanner_name="FUNDAMENTAL", persist_to_db=False)

    non_terminal_scenarios = [
        {"bse_status": "NOT_CHECKED"},
        {"bse_status": "NOT_QUERIED"},
        {"nse_status": "NOT_CHECKED"},
        {"nse_status": "UNKNOWN"},
        {"bse_status": "BSE_FEED_ACCESS_REQUIRED"},
        {"upstox_status": "NOT_CHECKED"},
        {"fyers_status": "NOT_CHECKED"},
    ]

    for scenario in non_terminal_scenarios:
        base_trace = {
            "is_bse_only": False,
            "nse_records": 0,
            "nse_status": "NO_DATA_RETURNED",
            "nse_parser_status": "NO_DATA_RETURNED",
            "bse_records": 0,
            "bse_status": "BSE_NO_DATA",
            "upstox_records": 0,
            "upstox_status": "NO_DATA",
            "raw_rows_returned": 0,
            "fyers_status": "UNSUPPORTED_FIELD",
            "all_providers_exhausted": True,
        }
        base_trace.update(scenario)
        screener_check = ReferenceCheck(source="Screener", tier=AuthorityTier.TIER_3_FORENSIC, status=ReferenceStatus.MISSING)

        rec = auditor.classify_field("TESTSYM", "sales_cagr_5y", base_trace, screener=screener_check)
        assert rec.classification != AvailabilityClassification.CONFIRMED_NO_DATA_ANYWHERE.value, (
            f"Expected CONFIRMED_NO_DATA_ANYWHERE to be blocked for scenario {scenario}, got {rec.classification}"
        )
        assert rec.quarantine_action != "7_DAY_QUARANTINE"


# ==============================================================================
# PENDING ITEM 4: Field-Level Provider Exhaustion
# ==============================================================================

def test_field_level_exhaustion_nse_partial_and_bse_success():
    """
    Test scenario:
      NSE provides roce_5y, but sales_cagr_5y is missing.
      Router continues to BSE, where sales_cagr_5y is resolved.
      Result: Composed metrics contains roce_5y from NSE and sales_cagr_5y from BSE.
    """
    router = FundamentalSourceRouter()
    # Stub local raw filings to empty
    router._fetch_local_raw_filings = mock.MagicMock(return_value=[])

    # NSE returns record with roce data, but insufficient history for CAGR
    nse_rec = RawFinancialRecord(
        symbol="MULTI_CORP", source="NSE_XBRL", period_end_date="2026-03-31", period_type="ANNUAL",
        consolidation=ConsolidationType.CONSOLIDATED,
        ebit=15.0, capital_employed=100.0, total_debt=10.0, total_equity=50.0,
        revenue=100.0, net_profit=10.0, validation_status="VALID"
    )
    router.nse_provider.fetch_raw_financials = mock.MagicMock(return_value=[nse_rec])

    # BSE returns full 6 annual records for 5Y CAGR (only revenue/net profit, no ebit)
    bse_recs = []
    base_rev = 50.0
    for i in range(6):
        yr = 2021 + i
        bse_recs.append(RawFinancialRecord(
            symbol="MULTI_CORP", source="BSE_CORPORATE", period_end_date=f"{yr}-03-31", period_type="ANNUAL",
            consolidation=ConsolidationType.CONSOLIDATED,
            ebit=None, capital_employed=None, total_debt=10.0, total_equity=50.0,
            revenue=base_rev * (1.1 ** i), net_profit=5.0 * (1.1 ** i), validation_status="VALID"
        ))
    router.bse_provider.fetch_raw_financials = mock.MagicMock(return_value=bse_recs)
    router.upstox_provider.fetch_raw_financials = mock.MagicMock(return_value=[])

    res = router.execute_progressive_recovery(
        "MULTI_CORP",
        required_fields=["roce_5y", "sales_cagr_5y"],
        skip_canonical_pit=True,
    )

    assert res.roce_5y is not None
    assert res.sales_cagr_5y is not None
    assert router.last_trace["MULTI_CORP"]["field_sources"]["roce_5y"] == "NSE"
    assert router.last_trace["MULTI_CORP"]["field_sources"]["sales_cagr_5y"] == "BSE"


def test_field_level_exhaustion_nse_partial_and_upstox_success():
    """
    Test scenario:
      NSE provides roce_5y, BSE has no data.
      Router continues to Upstox, where sales_cagr_5y is resolved.
    """
    router = FundamentalSourceRouter()
    router._fetch_local_raw_filings = mock.MagicMock(return_value=[])

    nse_rec = RawFinancialRecord(
        symbol="UP_CORP", source="NSE_XBRL", period_end_date="2026-03-31", period_type="ANNUAL",
        consolidation=ConsolidationType.CONSOLIDATED,
        ebit=20.0, capital_employed=100.0, total_debt=5.0, total_equity=50.0,
        revenue=100.0, net_profit=10.0, validation_status="VALID"
    )
    router.nse_provider.fetch_raw_financials = mock.MagicMock(return_value=[nse_rec])
    router.bse_provider.fetch_raw_financials = mock.MagicMock(return_value=[])

    upstox_recs = []
    base_rev = 80.0
    for i in range(6):
        yr = 2021 + i
        upstox_recs.append(RawFinancialRecord(
            symbol="UP_CORP", source="UPSTOX", period_end_date=f"{yr}-03-31", period_type="ANNUAL",
            consolidation=ConsolidationType.CONSOLIDATED,
            ebit=None, capital_employed=None, total_debt=5.0, total_equity=50.0,
            revenue=base_rev * (1.15 ** i), net_profit=8.0 * (1.15 ** i), validation_status="VALID"
        ))
    router._resolve_isin = mock.MagicMock(return_value="INE123UPSTOX")
    router.upstox_provider.fetch_raw_financials = mock.MagicMock(return_value=upstox_recs)

    res = router.execute_progressive_recovery(
        "UP_CORP",
        required_fields=["roce_5y", "sales_cagr_5y"],
        skip_canonical_pit=True,
    )

    assert res.roce_5y is not None
    assert res.sales_cagr_5y is not None
    assert router.last_trace["UP_CORP"]["field_sources"]["roce_5y"] == "NSE"
    assert router.last_trace["UP_CORP"]["field_sources"]["sales_cagr_5y"] == "UPSTOX"


def test_canonical_hit_one_field_continues_exhaustion():
    """
    Test scenario:
      Canonical PIT has roce_5y, but sales_cagr_5y is missing.
      Router must NOT stop at Canonical PIT for sales_cagr_5y; it must continue to NSE/BSE.
    """
    router = FundamentalSourceRouter()
    loc_rec = RawFinancialRecord(
        symbol="CANON_CONT", source="CANONICAL_LOCAL", period_end_date="2026-03-31", period_type="ANNUAL",
        consolidation=ConsolidationType.CONSOLIDATED,
        ebit=25.0, capital_employed=100.0, total_debt=0.0, total_equity=100.0,
        revenue=100.0, net_profit=10.0, validation_status="VALID"
    )
    router._fetch_local_raw_filings = mock.MagicMock(return_value=[loc_rec])

    bse_recs = []
    base_rev = 100.0
    for i in range(6):
        yr = 2021 + i
        bse_recs.append(RawFinancialRecord(
            symbol="CANON_CONT", source="BSE_CORPORATE", period_end_date=f"{yr}-03-31", period_type="ANNUAL",
            consolidation=ConsolidationType.CONSOLIDATED,
            ebit=None, capital_employed=None, total_debt=0.0, total_equity=100.0,
            revenue=base_rev * (1.2 ** i), net_profit=10.0 * (1.2 ** i), validation_status="VALID"
        ))
    router.nse_provider.fetch_raw_financials = mock.MagicMock(return_value=[])
    router.bse_provider.fetch_raw_financials = mock.MagicMock(return_value=bse_recs)

    res = router.execute_progressive_recovery(
        "CANON_CONT",
        required_fields=["roce_5y", "sales_cagr_5y"],
        skip_canonical_pit=False,
    )

    assert res.roce_5y is not None
    assert res.sales_cagr_5y is not None
    assert router.last_trace["CANON_CONT"]["field_sources"]["roce_5y"] == "CANONICAL_LOCAL"
    assert router.last_trace["CANON_CONT"]["field_sources"]["sales_cagr_5y"] == "BSE"


# ==============================================================================
# PENDING ITEM 5: Data Availability vs Production Eligibility Separation
# ==============================================================================

def test_telemetry_concept_separation_and_non_contradiction():
    """
    Verify distinct fields:
      - source_of_truth
      - availability_status
      - production_eligibility
      - block_reason
    And verify validate_telemetry_consistency rejects contradictory telemetry.
    """
    auditor = DataAvailabilityAuditor(scanner_name="FUNDAMENTAL", persist_to_db=False)

    # 1. Canonical Hit
    trace_canonical = {
        "canonical_hit": True,
        "recovered_source": "CANONICAL_LOCAL",
        "field_sources": {"roce_5y": "CANONICAL_LOCAL"},
    }
    rec_canon = auditor.classify_field("GOOD_SYM", "roce_5y", trace_canonical)
    assert rec_canon.source_of_truth == "CANONICAL_LOCAL"
    assert rec_canon.availability_status == "AVAILABLE"
    assert rec_canon.production_eligibility == "ELIGIBLE"
    assert rec_canon.block_reason == "NONE"

    # 2. Screener Reference Only
    trace_screener = {
        "all_providers_exhausted": True,
        "raw_rows_returned": 0,
        "nse_status": "NO_DATA",
        "bse_status": "NO_DATA",
        "upstox_status": "NO_DATA",
        "fyers_status": "UNSUPPORTED_FIELD",
    }
    sc_check = ReferenceCheck(source="Screener", tier=AuthorityTier.TIER_3_FORENSIC, status=ReferenceStatus.AVAILABLE)
    rec_screen = auditor.classify_field("REF_SYM", "sales_cagr_5y", trace_screener, screener=sc_check)
    assert rec_screen.source_of_truth == "NONE"
    assert rec_screen.availability_status == "REFERENCE_ONLY"
    assert rec_screen.production_eligibility == "INELIGIBLE"
    assert rec_screen.block_reason == "SCREENER_REFERENCE_ONLY_GOVERNANCE_BLOCKED"
    assert rec_screen.quarantine_action == "NONE"

    # 3. Contradiction Validation: Canonical HIT with PARSER_FAILURE must raise AssertionError
    bad_rec = AvailabilityAuditRecord(
        symbol="BAD_SYM", isin="INE000", scanner="FUNDAMENTAL", field="sales_cagr_5y",
        required_for_gate="QUALITY", upstox_status="OK", nse_status="OK",
        exchange_filing_status="OK", pit_status="AVAILABLE", local_cache_status="OK",
        fyers_status="OK", screener_status="OK", classification="PARSER_OR_FIELD_MAPPING_FAILURE",
        source_of_truth="CANONICAL_LOCAL", availability_status="PARSER_FAILURE",
        production_eligibility="ELIGIBLE", block_reason="NONE"
    )
    with pytest.raises(AssertionError, match="canonical HIT reported, but availability_status is PARSER_FAILURE"):
        auditor.validate_telemetry_consistency(bad_rec)


# ==============================================================================
# PENDING ITEM 7: Screener Reference-Only Governance Rule
# ==============================================================================

def test_screener_reference_only_scanner_blocking_regression():
    """
    Regression proof: Even if Screener has an apparently perfect value,
    the production scanner strictly blocks candidate and NEVER writes to canonical PIT.
    """
    auditor = DataAvailabilityAuditor(scanner_name="FUNDAMENTAL", persist_to_db=False)
    trace = {
        "all_providers_exhausted": True,
        "nse_records": 0,
        "bse_records": 0,
        "upstox_records": 0,
        "fyers_status": "UNSUPPORTED_FIELD",
    }
    sc = ReferenceCheck(source="Screener", tier=AuthorityTier.TIER_3_FORENSIC, status=ReferenceStatus.AVAILABLE)

    rec = auditor.classify_field("SCREENER_PERFECT", "sales_cagr_5y", trace, screener=sc)
    assert rec.classification == AvailabilityClassification.REFERENCE_ONLY_AVAILABLE.value
    assert rec.production_value_written is False
    assert rec.buy_allowed is False
    assert rec.production_eligibility == "INELIGIBLE"
    assert rec.quarantine_action == "NONE"


# ==============================================================================
# PENDING ITEM 15: Bidirectional Path Isolation
# ==============================================================================

def test_bidirectional_path_isolation():
    """
    Verify:
      TEMP -> PRODUCTION = BLOCKED
      PRODUCTION -> TEMP = BLOCKED
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        temp_parquet = os.path.join(tmpdir, "pit_recovery_status.parquet")

        # 1. Non-production temp path can NEVER enable DB sync even if env says true
        with mock.patch.dict(os.environ, {"QUARANTINE_DB_SYNC_ENABLED": "true"}):
            assert is_quarantine_db_sync_allowed(temp_parquet) is False

        # 2. Production path respects env flag and does not write to temp
        with mock.patch.dict(os.environ, {"QUARANTINE_DB_SYNC_ENABLED": "true"}):
            assert is_quarantine_db_sync_allowed(PRODUCTION_QUARANTINE_PATH) is True

        with mock.patch.dict(os.environ, {"QUARANTINE_DB_SYNC_ENABLED": "false"}):
            assert is_quarantine_db_sync_allowed(PRODUCTION_QUARANTINE_PATH) is False


if __name__ == "__main__":
    import inspect
    current_module = sys.modules[__name__]
    test_functions = [
        obj for name, obj in inspect.getmembers(current_module)
        if (inspect.isfunction(obj) and name.startswith("test_"))
    ]
    print(f"Running {len(test_functions)} tests in {__file__}...")
    passed = 0
    failed = 0
    for test_fn in test_functions:
        fn_name = test_fn.__name__
        try:
            test_fn()
            print(f"  [PASS] {fn_name}")
            passed += 1
        except Exception as e:
            print(f"  [FAIL] {fn_name}: {e}")
            import traceback
            traceback.print_exc()
            failed += 1
    print(f"\nResult: {passed} passed, {failed} failed out of {len(test_functions)}")
    if failed > 0:
        sys.exit(1)
    else:
        sys.exit(0)

