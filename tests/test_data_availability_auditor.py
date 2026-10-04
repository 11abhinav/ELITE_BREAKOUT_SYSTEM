"""
tests/test_data_availability_auditor.py
=======================================
Unit and regression tests for QUALITY_DATA_AVAILABILITY_AUDITOR:
  1. Condition 1: PRIMARY_RECOVERY_FAILURE_DATA_EXISTS_ELSEWHERE
  2. Condition 2: SCREENER_ONLY_DATA_SOURCE with custom admin message & BUY BLOCKED
  3. Condition 3: DATA_UNAVAILABLE_VERIFIED (Genuinely Unavailable)
  4. FYERS Approved Source Integration
  5. Value Boundary Protection (Files with value columns rejected)
  6. Invariants: production_value_written=False, buy_allowed=False
"""

import os
import tempfile
from app.data_providers.data_availability_auditor import (
    DataAvailabilityAuditor,
    AvailabilityClassification,
    OperatorAttestedReferenceSource,
    AuthorityTier,
    ReferenceStatus,
    normalize_field,
)


def test_condition_1_data_provider_discrepancy():
    """When both FYERS and Screener report data available, classify as discrepancy."""
    with tempfile.TemporaryDirectory() as tmpdir:
        fyers_p = os.path.join(tmpdir, "fyers.csv")
        screener_p = os.path.join(tmpdir, "screener.csv")
        with open(fyers_p, "w") as f:
            f.write("symbol,field,available,checked_at\nMAHLIFE,current_ev_ebitda,YES,2026-10-04\n")
        with open(screener_p, "w") as f:
            f.write("symbol,field,available,checked_at\nMAHLIFE,current_ev_ebitda,YES,2026-10-04\n")

        fyers = OperatorAttestedReferenceSource("FYERS", AuthorityTier.TIER_2_DIAGNOSTIC, fyers_p)
        screener = OperatorAttestedReferenceSource("SCREENER", AuthorityTier.TIER_3_FORENSIC, screener_p)

        auditor = DataAvailabilityAuditor(references=[fyers, screener], persist_to_db=False)
        rec = auditor.classify_field("MAHLIFE", "current_ev_ebitda", {
            "isin": "INE123",
            "upstox_records": 0,
            "nse_records": 0,
            "key_ratios_attempted": True,
            "key_ratios_status": "MISSING",
        })

        assert rec.classification == AvailabilityClassification.PRIMARY_RECOVERY_FAILURE_DATA_EXISTS_ELSEWHERE.value
        assert rec.severity == "CRITICAL"
        assert rec.production_value_written is False
        assert rec.buy_allowed is False
        assert "INVESTIGATE_UPSTOX_NSE" in rec.admin_action


def test_condition_2_screener_only_data_source():
    """When only Screener reports data available, classify as SCREENER_ONLY_DATA_SOURCE."""
    with tempfile.TemporaryDirectory() as tmpdir:
        fyers_p = os.path.join(tmpdir, "fyers.csv")
        screener_p = os.path.join(tmpdir, "screener.csv")
        with open(fyers_p, "w") as f:
            f.write("symbol,field,available,checked_at\nMANYAVAR,sales_cagr_5y,NO,2026-10-04\n")
        with open(screener_p, "w") as f:
            f.write("symbol,field,available,checked_at\nMANYAVAR,sales_cagr_5y,YES,2026-10-04\n")

        fyers = OperatorAttestedReferenceSource("FYERS", AuthorityTier.TIER_2_DIAGNOSTIC, fyers_p)
        screener = OperatorAttestedReferenceSource("SCREENER", AuthorityTier.TIER_3_FORENSIC, screener_p)

        auditor = DataAvailabilityAuditor(references=[fyers, screener], persist_to_db=False)
        rec = auditor.classify_field("MANYAVAR", "sales_cagr_5y", {
            "isin": "INE456",
            "upstox_records": 0,
            "nse_records": 0,
        })

        assert rec.classification == AvailabilityClassification.SCREENER_ONLY_DATA_SOURCE.value
        assert rec.severity == "HIGH"
        assert rec.production_value_written is False
        assert rec.buy_allowed is False
        assert rec.screener_status == "AVAILABLE"
        assert rec.fyers_status == "MISSING"
        assert "INVESTIGATE_WHY_VALUE_IN_SCREENER" in rec.admin_action


def test_condition_3_genuinely_unavailable():
    """When both FYERS and Screener confirm data missing, classify as DATA_UNAVAILABLE_VERIFIED."""
    with tempfile.TemporaryDirectory() as tmpdir:
        fyers_p = os.path.join(tmpdir, "fyers.csv")
        screener_p = os.path.join(tmpdir, "screener.csv")
        with open(fyers_p, "w") as f:
            f.write("symbol,field,available,checked_at\nBASF,sales_cagr_5y,NO,2026-10-04\n")
        with open(screener_p, "w") as f:
            f.write("symbol,field,available,checked_at\nBASF,sales_cagr_5y,NO,2026-10-04\n")

        fyers = OperatorAttestedReferenceSource("FYERS", AuthorityTier.TIER_2_DIAGNOSTIC, fyers_p)
        screener = OperatorAttestedReferenceSource("SCREENER", AuthorityTier.TIER_3_FORENSIC, screener_p)

        auditor = DataAvailabilityAuditor(references=[fyers, screener], persist_to_db=False)
        rec = auditor.classify_field("BASF", "sales_cagr_5y", {
            "isin": "INE789",
            "upstox_records": 0,
            "nse_records": 0,
        })

        assert rec.classification == AvailabilityClassification.DATA_UNAVAILABLE_VERIFIED.value
        assert rec.production_value_written is False
        assert rec.buy_allowed is False


def test_value_boundary_protection():
    """Files containing numeric or value-like columns must be rejected immediately."""
    with tempfile.TemporaryDirectory() as tmpdir:
        bad_csv = os.path.join(tmpdir, "bad.csv")
        with open(bad_csv, "w") as f:
            f.write("symbol,field,available,value\nABC,ROCE,YES,25.4\n")

        src = OperatorAttestedReferenceSource("LEAK_TEST", AuthorityTier.TIER_3_FORENSIC, bad_csv)
        assert src._load_status == ReferenceStatus.REJECTED
        check = src.check("ABC", "ROCE")
        assert check.status == ReferenceStatus.REJECTED


def test_field_normalization():
    assert normalize_field("roce_5y_avg") == "ROCE"
    assert normalize_field("cfo_pat_5y_ratio") == "cfo_pat_5y"
    assert normalize_field("debt_to_equity") == "debt"
    assert normalize_field("ev_ebitda") == "current_ev_ebitda"
