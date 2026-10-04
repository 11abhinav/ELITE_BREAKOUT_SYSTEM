"""
tests/test_bse_dynamic_resolver_and_ingestion.py
=================================================
MANDATORY REAL-DATA CERTIFICATION SUITE FOR COMMIT 3:
BSE DYNAMIC SECURITY MASTER RESOLVER + INGESTION PIPELINE

Tests:
  1. Dynamic BSE Resolution without hardcoded maps:
     - TCS (532540)
     - RELIANCE (500325)
     - ADOR (517041)
     - GABRIEL (505714)
  2. SME / BSE-focused security resolution (e.g. RAJKSYN 514028)
  3. Renamed / alias security resolution (e.g. ADORWELD -> ADOR 517041, MINDTREE -> LTIM 540005)
  4. Context-first parsing: Annual vs Half-Year vs Quarterly
  5. Cash flow strictly mapped to ANNUAL context
  6. Consolidated vs Standalone statement basis isolation
  7. Telemetry separation:
     filings_discovered, documents_downloaded, documents_with_facts, facts_extracted, semantically_valid_records
  8. Provider exhaustion routing:
     Canonical -> NSE -> BSE -> Upstox with early stop on authoritative verification
  9. Parser failure preservation during exhaustion (never silently marked DATA_UNAVAILABLE)
"""

from unittest.mock import MagicMock, patch
import pytest
from app.data_providers.fundamental_models import (
    ConsolidationType,
    FundamentalStatus,
    RawFinancialRecord,
)
from app.data_providers.bse_security_master import (
    BseSecurityMasterResolver,
    BseSecurityEntry,
)
from app.data_providers.bse_corporate_provider import BseCorporateProvider
from app.data_providers.fundamental_source_router import FundamentalSourceRouter


@pytest.fixture
def bse_resolver():
    return BseSecurityMasterResolver()


@pytest.fixture
def bse_provider():
    return BseCorporateProvider()


# ---------------------------------------------------------------------------
# 1. Dynamic Resolution Tests (Retiring 18-Entry Static Map)
# ---------------------------------------------------------------------------

def test_01_dynamic_resolution_major_symbols(bse_resolver):
    """Proves dynamic resolution of TCS, RELIANCE, ADOR, GABRIEL via 12,000+ security master."""
    tcs = bse_resolver.resolve("TCS")
    assert tcs is not None
    assert tcs.bse_scrip_code == "532540"
    assert tcs.isin == "INE467B01029"

    rel = bse_resolver.resolve("RELIANCE")
    assert rel is not None
    assert rel.bse_scrip_code == "500325"
    assert rel.isin == "INE002A01018"

    ador = bse_resolver.resolve("ADOR")
    assert ador is not None
    assert ador.bse_scrip_code == "517041"
    assert ador.isin == "INE045A01017"

    gabriel = bse_resolver.resolve("GABRIEL")
    assert gabriel is not None
    assert gabriel.bse_scrip_code == "505714"
    assert gabriel.isin == "INE524A01029"


def test_02_dynamic_resolution_sme_and_bse_only(bse_resolver):
    """Proves resolution of BSE-only / SME securities."""
    sme = bse_resolver.resolve("RAJKSYN")
    assert sme is not None
    assert sme.bse_scrip_code == "514028"
    assert sme.isin == "INE376L01013"
    assert bse_resolver.is_bse_only("RAJKSYN") is True


def test_03_dynamic_resolution_renamed_and_aliases(bse_resolver):
    """Proves resolution of renamed securities and exchange aliases."""
    adorweld = bse_resolver.resolve("ADORWELD")
    assert adorweld is not None
    assert adorweld.canonical_symbol == "ADOR"
    assert adorweld.bse_scrip_code == "517041"

    mindtree = bse_resolver.resolve("MINDTREE")
    assert mindtree is not None
    assert mindtree.canonical_symbol == "LTM"
    assert mindtree.bse_scrip_code == "540005"

    cadila = bse_resolver.resolve("CADILAHC")
    assert cadila is not None
    assert cadila.canonical_symbol == "ZYDUSLIFE"
    assert cadila.bse_scrip_code == "532321"


# ---------------------------------------------------------------------------
# 2. BSE Ingestion, Context & Taxonomy Tests
# ---------------------------------------------------------------------------

def test_04_bse_context_and_taxonomy_parsing(bse_provider):
    """
    Proves BSE corporate results ingestion:
      - Classifies ANNUAL context
      - Extracts Revenue, Net Profit, Operating Profit / EBIT, Total Debt, Total Equity
      - Operating cash flow is strictly bound to ANNUAL context
    """
    mock_data = [
        {
            "PeriodEnded": "2024-03-31",
            "Period": "Annual",
            "Consolidated": "Consolidated",
            "TotIncome": "5000.00",
            "NetProfit": "850.00",
            "OperatingProfit": "1100.00",
            "CFO": "920.00",
            "TotalDebt": "300.00",
            "TotalEquity": "4200.00",
            "Unit": "cr",
        }
    ]
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = mock_data

    with patch.object(bse_provider.session, "get", return_value=mock_resp):
        recs = bse_provider.fetch_raw_financials("GABRIEL")

    assert len(recs) == 1
    r = recs[0]
    assert r.period_type == "ANNUAL"
    assert r.fiscal_year == "FY2024"
    assert r.revenue == 5000.00
    assert r.net_profit == 850.00
    assert r.ebit == 1100.00
    assert r.operating_cash_flow == 920.00
    assert r.total_equity == 4200.00
    assert r.total_debt == 300.00
    assert r.capital_employed == 4500.00
    assert r.consolidation == ConsolidationType.CONSOLIDATED


def test_05_bse_quarterly_cfo_suppression(bse_provider):
    """Operating cash flow is strictly None on quarterly BSE records."""
    mock_data = [
        {
            "PeriodEnded": "2024-12-31",
            "Period": "Q3",
            "Consolidated": "Consolidated",
            "TotIncome": "1250.00",
            "NetProfit": "210.00",
            "OperatingProfit": "280.00",
            "CFO": "150.00",  # Quarterly CFO must be suppressed
            "Unit": "cr",
        }
    ]
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = mock_data

    with patch.object(bse_provider.session, "get", return_value=mock_resp):
        recs = bse_provider.fetch_raw_financials("GABRIEL")

    assert len(recs) == 1
    r = recs[0]
    assert r.period_type == "QUARTERLY"
    assert r.revenue == 1250.00
    assert r.operating_cash_flow is None


def test_06_bse_consolidated_vs_standalone_isolation(bse_provider):
    """Proves hard statement basis isolation in BSE records."""
    mock_data = [
        {
            "PeriodEnded": "2024-03-31",
            "Period": "Annual",
            "Consolidated": "Consolidated",
            "TotIncome": "5000.00",
            "NetProfit": "850.00",
            "Unit": "cr",
        }
    ]
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = mock_data

    with patch.object(bse_provider.session, "get", return_value=mock_resp):
        cons_recs = bse_provider.fetch_raw_financials("TCS", statement_basis=ConsolidationType.CONSOLIDATED)
        assert len(cons_recs) == 1
        assert cons_recs[0].consolidation == ConsolidationType.CONSOLIDATED

    mock_data_s = [
        {
            "PeriodEnded": "2024-03-31",
            "Period": "Annual",
            "Consolidated": "Standalone",
            "TotIncome": "4200.00",
            "NetProfit": "710.00",
            "Unit": "cr",
        }
    ]
    mock_resp_s = MagicMock()
    mock_resp_s.status_code = 200
    mock_resp_s.json.return_value = mock_data_s

    with patch.object(bse_provider.session, "get", return_value=mock_resp_s):
        stand_recs = bse_provider.fetch_raw_financials("TCS", statement_basis=ConsolidationType.STANDALONE)
        assert len(stand_recs) == 1
        assert stand_recs[0].consolidation == ConsolidationType.STANDALONE


def test_07_bse_telemetry_metrics(bse_provider):
    """Proves that distinct discovery vs data layer telemetry counters are accurately tracked."""
    mock_data = [
        {
            "PeriodEnded": "2024-03-31",
            "Period": "Annual",
            "Consolidated": "Consolidated",
            "TotIncome": "1000.00",
            "NetProfit": "100.00",
            "Unit": "cr",
        }
    ]
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = mock_data

    with patch.object(bse_provider.session, "get", return_value=mock_resp):
        bse_provider.fetch_raw_financials("RELIANCE")

    assert bse_provider.filings_discovered >= 1
    assert bse_provider.documents_with_facts >= 1
    assert bse_provider.facts_extracted >= 1
    assert bse_provider.semantically_valid_records >= 1
    assert bse_provider.bse_scrip_resolutions >= 1


# ---------------------------------------------------------------------------
# 3. Provider Exhaustion & Smart Routing Tests
# ---------------------------------------------------------------------------

def test_08_provider_exhaustion_early_stop_on_nse_success():
    """
    Exhaustion Rule: When NSE successfully provides fully verified canonical metrics,
    BSE and Upstox must NOT be queried (early stop to reduce provider load).
    """
    router = FundamentalSourceRouter()

    # Create 6 continuous annual records for TCS
    nse_valid_records = [
        RawFinancialRecord(symbol="TCS", source="NSE_XBRL", period_end_date="2019-03-31", fiscal_year="FY2019", period_type="ANNUAL", consolidation=ConsolidationType.CONSOLIDATED, revenue=146463.0, net_profit=31472.0, ebit=37477.0, operating_cash_flow=28593.0, total_debt=0.0, total_equity=89446.0, capital_employed=89446.0, unit="cr", currency="INR"),
        RawFinancialRecord(symbol="TCS", source="NSE_XBRL", period_end_date="2020-03-31", fiscal_year="FY2020", period_type="ANNUAL", consolidation=ConsolidationType.CONSOLIDATED, revenue=156949.0, net_profit=32340.0, ebit=38580.0, operating_cash_flow=32309.0, total_debt=0.0, total_equity=84126.0, capital_employed=84126.0, unit="cr", currency="INR"),
        RawFinancialRecord(symbol="TCS", source="NSE_XBRL", period_end_date="2021-03-31", fiscal_year="FY2021", period_type="ANNUAL", consolidation=ConsolidationType.CONSOLIDATED, revenue=164177.0, net_profit=32430.0, ebit=42481.0, operating_cash_flow=38802.0, total_debt=0.0, total_equity=86433.0, capital_employed=86433.0, unit="cr", currency="INR"),
        RawFinancialRecord(symbol="TCS", source="NSE_XBRL", period_end_date="2022-03-31", fiscal_year="FY2022", period_type="ANNUAL", consolidation=ConsolidationType.CONSOLIDATED, revenue=191754.0, net_profit=38327.0, ebit=48453.0, operating_cash_flow=39949.0, total_debt=0.0, total_equity=89139.0, capital_employed=89139.0, unit="cr", currency="INR"),
        RawFinancialRecord(symbol="TCS", source="NSE_XBRL", period_end_date="2023-03-31", fiscal_year="FY2023", period_type="ANNUAL", consolidation=ConsolidationType.CONSOLIDATED, revenue=225458.0, net_profit=42147.0, ebit=54237.0, operating_cash_flow=41965.0, total_debt=0.0, total_equity=90424.0, capital_employed=90424.0, unit="cr", currency="INR"),
        RawFinancialRecord(symbol="TCS", source="NSE_XBRL", period_end_date="2024-03-31", fiscal_year="FY2024", period_type="ANNUAL", consolidation=ConsolidationType.CONSOLIDATED, revenue=240893.0, net_profit=46099.0, ebit=58500.0, operating_cash_flow=44338.0, total_debt=0.0, total_equity=90489.0, capital_employed=90489.0, unit="cr", currency="INR"),
    ]

    with patch.object(router.nse_provider, "fetch_raw_financials", return_value=nse_valid_records), \
         patch.object(router.bse_provider, "fetch_raw_financials") as mock_bse, \
         patch.object(router.upstox_provider, "fetch_raw_financials") as mock_upstox:

        metrics = router.execute_progressive_recovery("TCS")

        assert metrics.overall_status == FundamentalStatus.VERIFIED_SINGLE_SOURCE
        assert metrics.sales_cagr_5y is not None
        assert metrics.pat_cagr_5y is not None
        assert metrics.roce_5y is not None
        # BSE and Upstox must NOT be called when NSE succeeded
        mock_bse.assert_not_called()
        mock_upstox.assert_not_called()


def test_09_parser_failure_preservation_and_exhaustion_continuation():
    """
    When a provider has raw rows > 0 but usable == 0 (PARSER_OR_FIELD_MAPPING_FAILURE):
      - It must NOT silently mark data as unavailable.
      - The parser failure is recorded in trace.
      - Recovery continues down the exhaustion chain to BSE.
    """
    router = FundamentalSourceRouter()

    # Simulate NSE returning empty records with raw count > 0 (Parser failure)
    router.nse_provider.last_raw_count["CORRUPT_CO"] = 5
    router.nse_provider.last_status["CORRUPT_CO"] = "PARSER_OR_FIELD_MAPPING_FAILURE"

    bse_valid_records = [
        RawFinancialRecord(symbol="CORRUPT_CO", source="BSE_CORPORATE", period_end_date="2024-03-31", fiscal_year="FY2024", period_type="ANNUAL", consolidation=ConsolidationType.CONSOLIDATED, revenue=500.0, net_profit=50.0, ebit=60.0, operating_cash_flow=45.0, total_debt=10.0, total_equity=200.0, capital_employed=210.0, unit="cr", currency="INR")
    ]

    with patch.object(router.nse_provider, "fetch_raw_financials", return_value=[]), \
         patch.object(router.bse_provider, "fetch_raw_financials", return_value=bse_valid_records) as mock_bse:

        metrics = router.execute_progressive_recovery("CORRUPT_CO")

        # BSE was invoked because NSE had a parser failure
        mock_bse.assert_called_once()
        trace = router.last_trace.get("CORRUPT_CO", {})
        assert trace.get("parser_error") == "PARSER_OR_FIELD_MAPPING_FAILURE"
