"""
tests/test_nse_xbrl_ingestion.py
==================================
MANDATORY REAL-DATA CERTIFICATION SUITE FOR COMMIT 2 (NSE XBRL PIPELINE)

Tests the complete 14-item certification matrix:
  1.  TCS annual
  2.  TCS quarterly
  3.  RELIANCE annual
  4.  RELIANCE quarterly
  5.  GABRIEL
  6.  ADOR
  7.  consolidated
  8.  standalone
  9.  revised filing
  10. negative values
  11. annual vs half-year separation
  12. missing historical year
  13. duplicate filing
  14. bad XBRL/context
"""

import os
import glob
import pytest
from datetime import date
from app.data_providers.fundamental_models import (
    ConsolidationType,
    RawFinancialRecord,
)
from app.data_providers.nse_xbrl_provider import (
    NseXbrlProvider,
    validate_semantic_record,
)


@pytest.fixture
def provider():
    return NseXbrlProvider()


# ---------------------------------------------------------------------------
# Matrix 1 & 2: TCS Annual & Quarterly
# ---------------------------------------------------------------------------

def test_matrix_01_tcs_annual(provider):
    """
    1. TCS annual extraction:
       - Context-first classification as ANNUAL (FY2024).
       - Full revenue scale (~240,893 Cr), Net Profit (~46,099 Cr).
       - Full fiscal-year Operating Cash Flow (~44,338 Cr).
       - Semantically valid.
    """
    cached_tcs = [f for f in glob.glob("data/xbrl_cache/nse/*.xml") if "104549" in f]
    assert len(cached_tcs) > 0, "Cached TCS consolidated XBRL fixture must exist"
    
    records = provider.parse_xbrl_document(cached_tcs[0], "TCS")
    annual_recs = [r for r in records if r.period_type == "ANNUAL"]
    assert len(annual_recs) >= 1
    
    r = annual_recs[0]
    assert r.fiscal_year == "FY2024"
    assert r.consolidation == ConsolidationType.CONSOLIDATED
    assert 200000.0 < r.revenue < 300000.0
    assert 40000.0 < r.net_profit < 50000.0
    assert 40000.0 < r.operating_cash_flow < 50000.0
    assert r.total_equity > 80000.0


def test_matrix_02_tcs_quarterly(provider):
    """
    2. TCS quarterly extraction:
       - Context classified as QUARTERLY (duration 75-105 days).
       - Quarterly revenue (~61,237 Cr) and PAT (~12,502 Cr).
       - Operating cash flow is None (not misattributed to quarter).
    """
    cached_tcs = [f for f in glob.glob("data/xbrl_cache/nse/*.xml") if "104549" in f]
    records = provider.parse_xbrl_document(cached_tcs[0], "TCS")
    q_recs = [r for r in records if r.period_type == "QUARTERLY"]
    assert len(q_recs) >= 1
    
    q = q_recs[0]
    assert 50000.0 < q.revenue < 70000.0
    assert 10000.0 < q.net_profit < 15000.0
    assert q.operating_cash_flow is None


# ---------------------------------------------------------------------------
# Matrix 3 & 4: RELIANCE Annual & Quarterly
# ---------------------------------------------------------------------------

def test_matrix_03_reliance_annual(provider):
    """
    3. RELIANCE annual extraction:
       - Massive numbers (> 800,000 Cr) correctly parsed without heuristic corruption.
       - Annual CFO present (~158,788 Cr).
       - Total debt reflects sum of Current and NonCurrent borrowings.
    """
    cached_rel = [f for f in glob.glob("data/xbrl_cache/nse/*.xml") if "104634" in f]
    assert len(cached_rel) > 0, "Cached RELIANCE consolidated XBRL fixture must exist"
    
    records = provider.parse_xbrl_document(cached_rel[0], "RELIANCE")
    annual_recs = [r for r in records if r.period_type == "ANNUAL"]
    assert len(annual_recs) >= 1
    
    r = annual_recs[0]
    assert r.fiscal_year == "FY2024"
    assert r.consolidation == ConsolidationType.CONSOLIDATED
    assert 850000.0 < r.revenue < 1000000.0
    assert 70000.0 < r.net_profit < 90000.0
    assert 140000.0 < r.operating_cash_flow < 180000.0
    assert r.total_equity > 700000.0
    assert r.total_debt > 300000.0


def test_matrix_04_reliance_quarterly(provider):
    """
    4. RELIANCE quarterly extraction:
       - Quarterly revenue (~240,715 Cr) and PAT (~21,143 Cr).
    """
    cached_rel = [f for f in glob.glob("data/xbrl_cache/nse/*.xml") if "104634" in f]
    records = provider.parse_xbrl_document(cached_rel[0], "RELIANCE")
    q_recs = [r for r in records if r.period_type == "QUARTERLY"]
    assert len(q_recs) >= 1
    
    q = q_recs[0]
    assert 200000.0 < q.revenue < 300000.0
    assert 15000.0 < q.net_profit < 25000.0
    assert q.operating_cash_flow is None


# ---------------------------------------------------------------------------
# Matrix 5 & 6: GABRIEL and ADOR (Real Canary Diagnostics)
# ---------------------------------------------------------------------------

def test_matrix_05_gabriel_real_records(provider):
    """
    5. GABRIEL canary verification:
       - Eliminates old 'raw > 0, usable = 0' failure.
       - Discovers filings and extracts usable records.
    """
    recs = provider.fetch_raw_financials("GABRIEL")
    assert len(recs) > 0, "GABRIEL must have usable financial records from NSE XBRL"
    assert provider.last_status.get("GABRIEL") == "PARSE_SUCCESS"
    assert all(r.revenue > 0 for r in recs if r.revenue is not None)


def test_matrix_06_ador_real_records(provider):
    """
    6. ADOR canary verification:
       - Eliminates old 'raw > 0, usable = 0' failure.
       - Discovers filings and extracts usable records.
    """
    recs = provider.fetch_raw_financials("ADOR")
    assert len(recs) > 0, "ADOR must have usable financial records from NSE XBRL"
    assert provider.last_status.get("ADOR") == "PARSE_SUCCESS"
    assert all(r.revenue > 0 for r in recs if r.revenue is not None)


# ---------------------------------------------------------------------------
# Matrix 7 & 8: Hard Consolidated vs Standalone Separation
# ---------------------------------------------------------------------------

def test_matrix_07_consolidated_isolation(provider):
    """
    7. Consolidated records:
       - Explicitly requests CONSOLIDATED statement basis.
       - Verifies that zero STANDALONE records are mixed in.
    """
    recs = provider.fetch_raw_financials("TCS", statement_basis=ConsolidationType.CONSOLIDATED)
    assert len(recs) > 0
    assert all(r.consolidation == ConsolidationType.CONSOLIDATED for r in recs)


def test_matrix_08_standalone_isolation(provider):
    """
    8. Standalone records:
       - Explicitly requests STANDALONE statement basis.
       - Verifies that zero CONSOLIDATED records are mixed in.
    """
    recs = provider.fetch_raw_financials("TCS", statement_basis=ConsolidationType.STANDALONE)
    assert len(recs) > 0
    assert all(r.consolidation == ConsolidationType.STANDALONE for r in recs)


# ---------------------------------------------------------------------------
# Matrix 9: Revised Filing Deterministic Selection
# ---------------------------------------------------------------------------

def test_matrix_09_revised_filing_selection(provider):
    """
    9. Revised filing resolution:
       - When original and revised filings exist for the same period,
         the latest valid revision (higher seqNumber / reInd='Y') is selected.
    """
    filings = [
        {
            "symbol": "TEST",
            "toDate": "31-Mar-2024",
            "period": "Annual",
            "consolidated": "Consolidated",
            "seqNumber": 100,
            "broadCastDate": "12-Apr-2024 10:00:00",
            "reInd": "N",
            "xbrl": "https://nsearchives.nseindia.com/corporate/xbrl/original.xml",
        },
        {
            "symbol": "TEST",
            "toDate": "31-Mar-2024",
            "period": "Annual",
            "consolidated": "Consolidated",
            "seqNumber": 105,
            "broadCastDate": "15-Apr-2024 14:00:00",
            "reInd": "Y",
            "xbrl": "https://nsearchives.nseindia.com/corporate/xbrl/revised.xml",
        },
    ]
    active = provider.deduplicate_and_filter_filings(filings)
    assert len(active) == 1
    assert active[0]["seqNumber"] == 105
    assert active[0]["reInd"] == "Y"
    assert provider.revised_filings >= 1


# ---------------------------------------------------------------------------
# Matrix 10: Negative Values (Profits vs Revenues)
# ---------------------------------------------------------------------------

def test_matrix_10_negative_values():
    """
    10. Negative values handling:
        - Negative net profits (losses) and cash flows are accepted.
        - Negative revenue is strictly rejected.
    """
    loss_rec = RawFinancialRecord(
        symbol="LOSS_CO",
        source="NSE_XBRL",
        period_end_date="2024-03-31",
        period_type="ANNUAL",
        consolidation=ConsolidationType.CONSOLIDATED,
        revenue=500.0,
        net_profit=-45.0,
        operating_cash_flow=-12.0,
        unit="cr",
        currency="INR",
    )
    is_valid, reason = validate_semantic_record(loss_rec)
    assert is_valid is True
    assert reason is None

    bad_rev_rec = RawFinancialRecord(
        symbol="BAD_REV",
        source="NSE_XBRL",
        period_end_date="2024-03-31",
        period_type="ANNUAL",
        consolidation=ConsolidationType.CONSOLIDATED,
        revenue=-10.0,
        net_profit=-45.0,
        unit="cr",
        currency="INR",
    )
    is_valid_bad, reason_bad = validate_semantic_record(bad_rev_rec)
    assert is_valid_bad is False
    assert "NEGATIVE_REVENUE" in reason_bad


# ---------------------------------------------------------------------------
# Matrix 11: Annual vs Half-Year Separation
# ---------------------------------------------------------------------------

def test_matrix_11_annual_vs_half_year_separation(provider):
    """
    11. Annual vs Half-Year separation:
        - Apr-Sep 6-month period (~183 days) is classified as HALF_YEAR.
        - NEVER accepted as ANNUAL.
        - validate_continuous_annual_series rejects half-year records.
    """
    xml_content = """<?xml version="1.0" encoding="utf-8"?>
<xbrli:xbrl xmlns:xbrli="http://www.xbrl.org/2003/instance" xmlns:in-bse-fin="http://www.bseindia.com/xbrl/fin/2020-03-31/in-bse-fin">
  <xbrli:context id="HalfYearContext">
    <xbrli:entity><xbrli:identifier scheme="http://www.nseindia.com">TEST</xbrli:identifier></xbrli:entity>
    <xbrli:period>
      <xbrli:startDate>2023-04-01</xbrli:startDate>
      <xbrli:endDate>2023-09-30</xbrli:endDate>
    </xbrli:period>
  </xbrli:context>
  <xbrli:unit id="INR"><xbrli:measure>iso4217:INR</xbrli:measure></xbrli:unit>
  <in-bse-fin:RevenueFromOperations contextRef="HalfYearContext" unitRef="INR" decimals="-7">5000000000</in-bse-fin:RevenueFromOperations>
  <in-bse-fin:ProfitLossForPeriod contextRef="HalfYearContext" unitRef="INR" decimals="-7">500000000</in-bse-fin:ProfitLossForPeriod>
  <in-bse-fin:CashFlowsFromUsedInOperatingActivities contextRef="HalfYearContext" unitRef="INR" decimals="-7">200000000</in-bse-fin:CashFlowsFromUsedInOperatingActivities>
</xbrli:xbrl>"""
    test_path = os.path.join(provider.cache_dir, "test_half_year.xml")
    with open(test_path, "w", encoding="utf-8") as f:
        f.write(xml_content)
    
    try:
        recs = provider.parse_xbrl_document(test_path, "TEST")
        assert len(recs) == 1
        r = recs[0]
        assert r.period_type == "HALF_YEAR"
        # Half-year cash flow must NOT be accepted as annual CFO
        assert r.operating_cash_flow is None
        
        # Test series validator
        is_cont, err, _ = provider.validate_continuous_annual_series([r])
        assert is_cont is False
        assert "ZERO_ANNUAL_RECORDS" in err
    finally:
        if os.path.exists(test_path):
            os.remove(test_path)


# ---------------------------------------------------------------------------
# Matrix 12: Missing Historical Year (Gap Detection)
# ---------------------------------------------------------------------------

def test_matrix_12_missing_historical_year(provider):
    """
    12. Missing historical year detection:
        - Series missing FY2023 (FY2020, FY2021, FY2022, FY2024, FY2025, FY2026).
        - validate_continuous_annual_series detects the gap and blocks 5Y CAGR.
    """
    gapped_records = [
        RawFinancialRecord(symbol="GAP", source="NSE_XBRL", period_end_date="2020-03-31", fiscal_year="FY2020", period_type="ANNUAL", consolidation=ConsolidationType.CONSOLIDATED, revenue=100.0, net_profit=10.0, unit="cr", currency="INR"),
        RawFinancialRecord(symbol="GAP", source="NSE_XBRL", period_end_date="2021-03-31", fiscal_year="FY2021", period_type="ANNUAL", consolidation=ConsolidationType.CONSOLIDATED, revenue=110.0, net_profit=11.0, unit="cr", currency="INR"),
        RawFinancialRecord(symbol="GAP", source="NSE_XBRL", period_end_date="2022-03-31", fiscal_year="FY2022", period_type="ANNUAL", consolidation=ConsolidationType.CONSOLIDATED, revenue=120.0, net_profit=12.0, unit="cr", currency="INR"),
        # FY2023 is missing!
        RawFinancialRecord(symbol="GAP", source="NSE_XBRL", period_end_date="2024-03-31", fiscal_year="FY2024", period_type="ANNUAL", consolidation=ConsolidationType.CONSOLIDATED, revenue=140.0, net_profit=14.0, unit="cr", currency="INR"),
        RawFinancialRecord(symbol="GAP", source="NSE_XBRL", period_end_date="2025-03-31", fiscal_year="FY2025", period_type="ANNUAL", consolidation=ConsolidationType.CONSOLIDATED, revenue=150.0, net_profit=15.0, unit="cr", currency="INR"),
        RawFinancialRecord(symbol="GAP", source="NSE_XBRL", period_end_date="2026-03-31", fiscal_year="FY2026", period_type="ANNUAL", consolidation=ConsolidationType.CONSOLIDATED, revenue=160.0, net_profit=16.0, unit="cr", currency="INR"),
    ]
    is_cont, gap_err, window_recs = provider.validate_continuous_annual_series(gapped_records, target_years=5)
    assert is_cont is False
    assert "PIT_FILING_GAP_IN_GROWTH_WINDOW" in gap_err
    assert len(window_recs) == 0


# ---------------------------------------------------------------------------
# Matrix 13: Duplicate Filing Deduplication
# ---------------------------------------------------------------------------

def test_matrix_13_duplicate_filing_deduplication(provider):
    """
    13. Duplicate filing deduplication:
        - Exact duplicate filings with identical sequence numbers.
        - deduplicate_and_filter_filings eliminates duplicate and increments duplicate_filings counter.
    """
    filings = [
        {
            "symbol": "DUP",
            "toDate": "31-Mar-2024",
            "period": "Annual",
            "consolidated": "Consolidated",
            "seqNumber": 500,
            "broadCastDate": "12-Apr-2024 10:00:00",
            "xbrl": "https://nsearchives.nseindia.com/corporate/xbrl/doc1.xml",
        },
        {
            "symbol": "DUP",
            "toDate": "31-Mar-2024",
            "period": "Annual",
            "consolidated": "Consolidated",
            "seqNumber": 500,
            "broadCastDate": "12-Apr-2024 10:00:00",
            "xbrl": "https://nsearchives.nseindia.com/corporate/xbrl/doc1.xml",
        },
    ]
    active = provider.deduplicate_and_filter_filings(filings)
    assert len(active) == 1
    assert provider.duplicate_filings >= 1


# ---------------------------------------------------------------------------
# Matrix 14: Bad XBRL / Context Handling
# ---------------------------------------------------------------------------

def test_matrix_14_bad_xbrl_graceful_failure(provider):
    """
    14. Bad XBRL / Context handling:
        - Corrupted XML file fails closed without crashing.
        - xbrl_parse_failures counter is incremented.
        - Returns empty list of records.
    """
    bad_path = os.path.join(provider.cache_dir, "corrupted.xml")
    with open(bad_path, "w", encoding="utf-8") as f:
        f.write("<not_valid_xml>unclosed tag")
    
    try:
        recs = provider.parse_xbrl_document(bad_path, "CORRUPT")
        assert len(recs) == 0
        assert provider.xbrl_parse_failures >= 1
    finally:
        if os.path.exists(bad_path):
            os.remove(bad_path)
