"""
tests/test_exchange_pdf_parser.py
==================================
Unit tests for Tier-3 Fallback Exchange PDF Statement Parser.
Verifies:
  1. Strict governance invariant: PDF is NEVER touched if XML exists for that period.
  2. Extraction of SEBI LODR Regulation 33 statement tables from PDF bytes.
  3. Scaling to Crores and accounting equation validation (PBT - Tax = PAT).
  4. Proper fallback for Standalone vs Consolidated quarterly filings.
"""

import os
import io
import PyPDF2
import pytest

from app.data_providers.exchange_pdf_parser import ExchangePdfParser
from app.data_providers.fundamental_models import ConsolidationType, RawFinancialRecord


class TestExchangePdfParser:

    def test_xml_coverage_guard_skips_pdf(self):
        """
        STRICT GOVERNANCE INVARIANT:
        If XML exists for a period on NSE or BSE, the PDF is NEVER parsed or returned.
        """
        parser = ExchangePdfParser()
        announcements = [
            {
                "desc": "Outcome of Board Meeting - Financial Results for Quarter Ended 30-Jun-2026",
                "attchmntFile": "https://nsearchives.nseindia.com/corporate/TEST_Results.pdf",
                "toDate": "2026-06-30",
                "period_end_date": "2026-06-30",
            }
        ]
        # Period 2026-06-30 is already covered by XML
        existing_xml = {"2026-06-30"}
        records = parser.fetch_pdf_fallback_records(
            symbol="TESTSYM",
            announcements=announcements,
            existing_xml_periods=existing_xml,
        )
        assert len(records) == 0, "Expected 0 records because 2026-06-30 is already covered by XML"

    def test_pdf_parsed_only_when_xml_missing(self):
        """
        When XML is absent from both NSE and BSE for 2026-06-30,
        and local cached PDF exists, it successfully parses and extracts facts.
        """
        pdf_path = os.path.join("data", "pdf_cache", "2638fe003eada0fd_BFUTILITIE_14082026124214_BFUL_Standalone_Unaudited_Results_Jun_2026_14082026.pdf")
        if not os.path.exists(pdf_path):
            pytest.skip("Cached PDF not available in test environment")

        with open(pdf_path, "rb") as f:
            pdf_bytes = f.read()

        parser = ExchangePdfParser()
        records = parser.parse_sebi_reg33_pdf(
            pdf_bytes,
            symbol="BFUTILITIE",
            announcement_metadata={
                "toDate": "2026-06-30",
                "attchmntFile": pdf_path,
                "filingDate": "2026-08-14",
            }
        )

        assert len(records) >= 1
        rec = records[0]
        assert rec.symbol == "BFUTILITIE"
        assert rec.period_end_date == "2026-06-30"
        assert rec.period_type in ("QUARTERLY", "ANNUAL")
        assert rec.currency == "INR"
        assert rec.unit == "crores"
        # Revenue should be 5.8805 Cr (588.05 Lakhs)
        assert abs(rec.revenue - 5.8805) < 0.05
        # PAT should be -5.0298 Cr (-502.98 Lakhs)
        assert abs(rec.net_profit - (-5.0298)) < 0.05

    def test_parse_executes_and_releases_semaphore(self, monkeypatch):
        """
        Verifies that parse_sebi_reg33_pdf acquires and releases the semaphore properly.
        """
        from app.data_providers.exchange_pdf_parser import _PARSE_SEMAPHORE

        parser = ExchangePdfParser()

        dummy_pdf = b"%PDF-1.4\n%%EOF"
        recs = parser.parse_sebi_reg33_pdf(dummy_pdf, "TESTSYM")
        assert recs == []

        # Ensure semaphore can still be acquired (no leak)
        assert _PARSE_SEMAPHORE.acquire(blocking=False) is True
        _PARSE_SEMAPHORE.release()

    def test_month_first_date_and_decoupled_layout_ahfl(self):
        """
        Verifies parsing of Month-First date format ('September 30, 2026')
        and decoupled column table layout for Aadhar Housing Finance (AHFL).
        """
        pdf_path = os.path.join("data", "pdf_cache", "f1f102bd391438bb_AHFL2021_05112026162550_Final_Combined_resultssd.pdf")
        if not os.path.exists(pdf_path):
            pytest.skip("Cached AHFL PDF not available")

        with open(pdf_path, "rb") as f:
            pdf_bytes = f.read()

        parser = ExchangePdfParser()
        records = parser.parse_sebi_reg33_pdf(pdf_bytes, symbol="AHFL")
        assert len(records) >= 1
        rec = records[0]
        assert rec.period_end_date == "2026-09-30"
        assert rec.revenue is not None and rec.revenue > 900.0
        assert rec.net_profit is not None and rec.net_profit > 250.0

    def test_typo_date_and_decoupled_layout_aartiind(self):
        """
        Verifies parsing of dates with typographical errors ('30TH SEPTERMBER, 2026'),
        filtering out line-item index numbers from row labels, and resolving PAT
        via accounting equation (PBT - Tax = PAT) in decoupled columnar layouts.
        """
        pdf_path = os.path.join("data", "pdf_cache", "a8f44d09f1a50541_AARTIIND_30102026190215_AARTIIND_Results.pdf")
        if not os.path.exists(pdf_path):
            pytest.skip("Cached AARTIIND PDF not available")

        with open(pdf_path, "rb") as f:
            pdf_bytes = f.read()

        parser = ExchangePdfParser()
        records = parser.parse_sebi_reg33_pdf(pdf_bytes, symbol="AARTIIND")
        assert len(records) >= 2
        # Standalone record
        sa = [r for r in records if r.consolidation == ConsolidationType.STANDALONE][0]
        assert sa.period_end_date == "2026-09-30"
        assert abs(sa.revenue - 3942.0) < 1.0
        assert abs(sa.net_profit - 186.0) < 1.0

        # Consolidated record
        con = [r for r in records if r.consolidation == ConsolidationType.CONSOLIDATED][0]
        assert con.period_end_date == "2026-09-30"
        assert abs(con.revenue - 3668.0) < 1.0
        assert abs(con.net_profit - 170.0) < 1.0
