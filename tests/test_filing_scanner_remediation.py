import json
import os
import sys
from datetime import date, datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from app.data_providers import exchange_pdf_parser as pdf_mod
from app.data_providers.exchange_pdf_parser import (
    ConsolidationType,
    ExchangePdfParser,
    _normalize_ocr_text,
    score_page_content,
)
from app.data_providers import nse_xbrl_provider as nse_mod
from app.data_providers.nse_xbrl_provider import NseXbrlProvider


def test_normalize_ocr_text_preserves_lakh_units_and_fixes_spaced_commas():
    """
    Ensures OCR normalization never strips `(Zin lacs)` / `(₹in lacs)` as a spreadsheet cell ref (e.g. N28),
    repairs spaced Indian commas like `2,32, 139.94`, and normalizes `l'rofil` -> `profit`.
    """
    sample = (
        "STATEMENT OF UNAUDITED FINANCIAL RESULTS (Zin lacs)\n"
        "Also reported in (₹in lacs)\n"
        "Total Income   2,32, 139.94   1,98, 420.10\n"
        "Net l'rofil for the period   14,250.55\n"
    )
    norm = _normalize_ocr_text(sample)
    norm_low = norm.lower()

    assert "lacs" in norm_low, f"Unit indicator '(Zin lacs)' was unexpectedly stripped: {norm}"
    assert "2,32,139.94" in norm or "232139.94" in norm, f"Spaced Indian comma not repaired: {norm}"
    assert "profit" in norm_low, f"OCR 'l'rofil' not repaired: {norm}"


def test_score_page_content_ranks_pnl_above_auditor_cf_bs_and_ratios():
    """
    Verifies score_page_content strictly ranks actual P&L financial result tables above
    Limited Review Reports, Cash Flows, Balance Sheets, and Reg 32/52(4) Ratio pages.
    """
    pnl_text = (
        "STATEMENT OF UNAUDITED CONSOLIDATED FINANCIAL RESULTS FOR THE QUARTER ENDED 30.09.2026\n"
        "(Rs. in Lakhs)\n"
        "Particulars | Quarter ended 30.09.2026 | Quarter ended 30.06.2026 | Year ended 31.03.2026\n"
        "1. Revenue from operations | 45,604.75 | 41,200.10 | 1,68,500.00\n"
        "2. Other income | 350.00 | 310.00 | 1,200.00\n"
        "3. Total income | 45,954.75 | 41,510.10 | 1,69,700.00\n"
        "4. Expenses | 35,100.00 | 32,000.00 | 1,30,000.00\n"
        "5. Profit before tax | 10,854.75 | 9,510.10 | 39,700.00\n"
        "6. Tax expense | 2,773.86 | 2,400.00 | 9,900.00\n"
        "7. Net Profit for the period | 8,080.89 | 7,110.10 | 29,800.00\n"
        "8. Paid-up equity share capital | 2,400.00 | 2,400.00 | 2,400.00\n"
        "9. Basic EPS | 33.67 | 29.63 | 124.17\n"
    )
    auditor_text = (
        "Independent Auditor's Review Report on Unaudited Consolidated Financial Results\n"
        "To the Board of Directors\n"
        "We have reviewed the accompanying Statement of Unaudited Consolidated Financial Results "
        "in accordance with Standard on Review Engagements (SRE) 2410. Nothing has come to our attention.\n"
    )
    cash_flow_text = (
        "UNAUDITED STATEMENT OF CASH FLOWS FOR THE HALF YEAR ENDED 30.09.2026\n"
        "A. Cash flow from operating activities: 12,450.00\n"
        "B. Cash flow from investing activities: (3,100.00)\n"
        "C. Cash flow from financing activities: (2,000.00)\n"
    )
    balance_sheet_text = (
        "STATEMENT OF ASSETS AND LIABILITIES AS AT 30.09.2026\n"
        "Assets: Non-current assets 45,000.00 | Current assets 22,000.00\n"
        "Total Equity and Liabilities: 67,000.00\n"
    )
    ratios_text = (
        "Disclosure pursuant to Regulation 52(4) of SEBI LODR Regulations\n"
        "Debt Equity Ratio: 0.42 | Current Ratio: 1.85 | Interest Service Coverage Ratio: 6.40\n"
        "Statement of deviation / variation under Regulation 32\n"
    )

    pnl_score, pnl_cons = score_page_content(pnl_text)
    aud_score, _ = score_page_content(auditor_text)
    cf_score, _ = score_page_content(cash_flow_text)
    bs_score, _ = score_page_content(balance_sheet_text)
    rat_score, _ = score_page_content(ratios_text)

    assert pnl_cons == ConsolidationType.CONSOLIDATED
    assert pnl_score > aud_score
    assert pnl_score > cf_score
    assert pnl_score > bs_score
    assert pnl_score > rat_score


def test_pdf_parser_digital_first_skips_pre_ocr_preserves_units_and_ignores_phone_cols(tmp_path, monkeypatch):
    """
    Verifies hybrid PDFs with a digital P&L page:
    1. Do NOT run expensive Phase 2 pre-OCR on blank cover/signature pages.
    2. Preserve `(Zin lacs)` unit scaling (/100 -> Crores).
    3. Do NOT treat contact phone numbers as extra date columns.
    4. Do NOT let exceptional `(profit)/loss on sale` override Net Profit.
    """
    parser = ExchangePdfParser(cache_dir=str(tmp_path / "pdf_cache"))

    p1_digital = (
        "SYNTHETIC ENTERPRISES LIMITED | Ph: +91-44-22445566 | Email: ir@example.com\n"
        "STATEMENT OF UNAUDITED STANDALONE FINANCIAL RESULTS FOR THE QUARTER ENDED 30.09.2026\n"
        "(Zin lacs)\n"
        "Particulars   30.09.2026   30.06.2026   30.09.2025   31.03.2026\n"
        "1. Total revenue from operations   45,604.75   41,200.00   38,900.00   1,60,000.00\n"
        "2. Other income   400.00   350.00   300.00   1,200.00\n"
        "3. Total expenses   34,800.00   31,900.00   30,100.00   1,22,000.00\n"
        "4. Profit before exceptional items and tax   11,204.75   9,650.00   9,100.00   39,200.00\n"
        "5. Exceptional items - Profit/(loss) on sale of asset   120.00   0.00   0.00   50.00\n"
        "6. Profit before tax   11,324.75   9,650.00   9,100.00   39,250.00\n"
        "7. Tax expense   2,542.85   2,400.00   2,275.00   9,800.00\n"
        "8. Profit from ordinary activities after tax   8,781.90   7,250.00   6,825.00   29,450.00\n"
        "9. Paid-up equity share capital   1,500.00   1,500.00   1,500.00   1,500.00\n"
        "10. Basic Earnings per share (Rs.)   58.55   48.33   45.50   196.33\n"
    )
    p2_consolidated = p1_digital.replace("STANDALONE", "CONSOLIDATED")
    pages = {0: "", 1: p1_digital, 2: p2_consolidated, 3: ""}
    render_calls = []

    class DummyReader:
        pages = [0, 1, 2, 3]

    monkeypatch.setattr(pdf_mod, "_PDF_READER_CLS", lambda *a, **kw: DummyReader())
    monkeypatch.setattr(pdf_mod, "_HAS_PDFTOTEXT", False)
    monkeypatch.setattr(pdf_mod, "_HAS_PDFTOPPM", True)
    monkeypatch.setattr(pdf_mod, "_OCR_AVAILABLE", True)
    monkeypatch.setattr(pdf_mod, "_extract_pdf_pages_fast", lambda *a, **kw: dict(pages))
    monkeypatch.setattr(
        pdf_mod,
        "_safe_extract_text",
        lambda page, timeout=3.5, extraction_mode=None, pdf_path=None, page_idx=None: pages.get(
            page_idx if page_idx is not None else page, ""
        ),
    )
    monkeypatch.setattr(
        pdf_mod,
        "_render_page_image_fast",
        lambda *a, **kw: render_calls.append(a) or None,
    )

    recs = parser.parse_sebi_reg33_pdf(
        b"%PDF-1.4\n% synth_digital_first\n%%EOF",
        "TEST_SYNTH_UNIT",
        announcement_metadata={
            "desc": "Financial Results for the quarter ended September 30, 2026",
            "broadcast_timestamp": "2026-10-28T18:00:00",
        },
    )

    assert len(render_calls) == 0, f"Phase 2/3 OCR should not run once digital Standalone+Consolidated P&Ls succeed, got {len(render_calls)} calls"
    by_cons = {r.consolidation: r for r in recs}
    assert ConsolidationType.STANDALONE in by_cons
    assert ConsolidationType.CONSOLIDATED in by_cons
    for rec in (by_cons[ConsolidationType.STANDALONE], by_cons[ConsolidationType.CONSOLIDATED]):
        assert rec.period_end_date == "2026-09-30"
        assert pytest.approx(rec.revenue, rel=1e-2) == 456.0475
        assert pytest.approx(rec.net_profit, rel=1e-2) == 87.8190


def test_pdf_parser_bank_interest_earned_next_line_and_pat_outlier_guard(tmp_path, monkeypatch):
    """
    Verifies Bank `Interest Earned` revenue matching, next-line numeric lookahead,
    and sanity rejection of impossible PAT outliers vs revenue.
    """
    parser = ExchangePdfParser(cache_dir=str(tmp_path / "pdf_cache"))

    p_bank = (
        "SYNTHETIC BANK LIMITED\n"
        "UNAUDITED STANDALONE FINANCIAL RESULTS FOR THE QUARTER ENDED 30.09.2026\n"
        "(Rs. in Crores)\n"
        "Particulars   30.09.2026   30.06.2026   30.09.2025\n"
        "1. Interest Earned (a)+(b)+(c)+(d)   15,947.73   15,100.00   14,200.00\n"
        "2. Other Income   2,900.00   2,600.00   2,400.00\n"
        "3. Total Income (1+2)   18,847.73   17,700.00   16,600.00\n"
        "4. Operating Expenses   10,100.00   9,500.00   9,000.00\n"
        "5. Profit before tax   5,610.00   5,200.00   4,900.00\n"
        "6. Net Profit for the period\n"
        "4,211.97   3,910.00   3,680.00\n"
        "7. Paid-up equity share capital   994.00   994.00   994.00\n"
    )
    pages_bank = {0: p_bank}

    class DummyReader1:
        pages = [0]

    monkeypatch.setattr(pdf_mod, "_PDF_READER_CLS", lambda *a, **kw: DummyReader1())
    monkeypatch.setattr(pdf_mod, "_HAS_PDFTOTEXT", False)
    monkeypatch.setattr(pdf_mod, "_OCR_AVAILABLE", False)
    monkeypatch.setattr(pdf_mod, "_extract_pdf_pages_fast", lambda *a, **kw: dict(pages_bank))
    monkeypatch.setattr(
        pdf_mod,
        "_safe_extract_text",
        lambda page, timeout=3.5, extraction_mode=None, pdf_path=None, page_idx=None: pages_bank.get(
            page_idx if page_idx is not None else page, ""
        ),
    )

    bank_recs = parser.parse_sebi_reg33_pdf(
        b"%PDF-1.4\n% synth_bank\n%%EOF",
        "TEST_BANK_SYNTH",
        announcement_metadata={
            "desc": "Standalone Financial Results for quarter ended September 30, 2026",
            "broadcast_timestamp": "2026-10-25T16:00:00",
        },
    )
    assert len(bank_recs) >= 1
    assert pytest.approx(bank_recs[0].revenue, rel=1e-2) == 15947.73
    assert pytest.approx(bank_recs[0].net_profit, rel=1e-2) == 4211.97

    # Also verify outlier PAT (> max(500, rev * 2.5)) is rejected cleanly instead of stored verbatim
    p_outlier = (
        "SYNTHETIC FINANCE LIMITED\n"
        "STATEMENT OF UNAUDITED STANDALONE FINANCIAL RESULTS FOR THE QUARTER ENDED 30.09.2026\n"
        "(Rs. in Crores)\n"
        "Particulars   30.09.2026   30.06.2026   30.09.2025\n"
        "1. Total revenue from operations   5,484.42   5,210.00   4,900.00\n"
        "2. Net Profit for the period   25,895.00   910.00   850.00\n"
    )
    pages_outlier = {0: p_outlier}
    monkeypatch.setattr(pdf_mod, "_extract_pdf_pages_fast", lambda *a, **kw: dict(pages_outlier))
    monkeypatch.setattr(
        pdf_mod,
        "_safe_extract_text",
        lambda page, timeout=3.5, extraction_mode=None, pdf_path=None, page_idx=None: pages_outlier.get(
            page_idx if page_idx is not None else page, ""
        ),
    )

    out_recs = parser.parse_sebi_reg33_pdf(
        b"%PDF-1.4\n% synth_outlier\n%%EOF",
        "TEST_OUTLIER_SYNTH",
        announcement_metadata={
            "desc": "Financial Results for quarter ended September 30, 2026",
            "broadcast_timestamp": "2026-10-26T16:00:00",
        },
    )
    for r in out_recs:
        assert r.net_profit is None or r.net_profit < 10000.0


def test_download_pdf_range_resume_retries_after_chunk_error_and_tracks_failed_url(tmp_path, monkeypatch):
    """
    Verifies download_pdf recovers from transient Range chunk interruption instead of breaking
    immediately, and fetch_pdf_fallback_records records failed URLs in last_attempted_urls.
    """
    parser = ExchangePdfParser(cache_dir=str(tmp_path / "pdf_cache"))
    full_pdf = b"%PDF-1.4\n" + (b"0" * 4096) + b"\n%%EOF"
    split_idx = 2048

    class DummyInitResp:
        status_code = 200
        headers = {"Content-Length": str(len(full_pdf))}

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def iter_content(self, chunk_size=65536):
            yield full_pdf[:split_idx]

    class DummyRangeResp:
        def __init__(self, chunk):
            self.status_code = 206
            self._chunk = chunk
            self.headers = {"Content-Range": f"bytes {split_idx}-{len(full_pdf)-1}/{len(full_pdf)}"}

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def iter_content(self, chunk_size=65536):
            yield self._chunk

    range_calls = {"n": 0}

    def fake_range_get(url, headers=None, **kwargs):
        range_calls["n"] += 1
        if range_calls["n"] == 1:
            raise pdf_mod.requests.exceptions.ConnectionError("transient chunk drop")
        return DummyRangeResp(full_pdf[split_idx:])

    monkeypatch.setitem(sys.modules, "curl_cffi", None)
    monkeypatch.setattr(parser.session, "get", lambda *a, **kw: DummyInitResp())
    monkeypatch.setattr(pdf_mod.requests, "get", fake_range_get)
    monkeypatch.setattr("app.data_providers.exchange_pdf_parser.time.sleep", lambda *a: None)

    got = parser.download_pdf(
        "https://nsearchives.nseindia.com/corporate/SYNTH_RESULT_Q2.pdf",
        announcement_metadata={"desc": "Financial Results for quarter ended Sep 30, 2026"},
    )
    assert got == full_pdf
    assert range_calls["n"] >= 2

    # Verify failed downloads still get appended to last_attempted_urls
    monkeypatch.setattr(parser, "download_pdf", lambda *a, **kw: None)
    anns = [{
        "desc": "Outcome of Board Meeting - Financial Results for quarter ended September 30, 2026",
        "attchmntFile": "https://nsearchives.nseindia.com/corporate/SYNTH_DEAD.pdf",
        "an_dt": "28-Oct-2026 17:00:00",
    }]
    recs = parser.fetch_pdf_fallback_records(
        symbol="TEST_DEAD_URL",
        announcements=anns,
        existing_xml_periods=set(),
    )
    assert recs == []
    assert "https://nsearchives.nseindia.com/corporate/SYNTH_DEAD.pdf" in parser.last_attempted_urls["TEST_DEAD_URL"]


def test_nse_market_sweep_disk_cache_ttl_reuse(tmp_path, monkeypatch):
    """
    Verifies NseXbrlProvider.discover_market_filings_sweep writes data/nse_market_sweep_cache.json
    and reuses it across fresh provider instances when in-memory cache is cleared.
    """
    monkeypatch.setattr(nse_mod, "BASE_DIR", str(tmp_path))
    nse_mod._GLOBAL_MARKET_SWEEP_CACHE.clear()

    api_calls = {"n": 0}

    def fake_get(self, url, symbol, timeout=None):
        api_calls["n"] += 1
        if api_calls["n"] == 1:
            return [
                {
                    "symbol": "TEST_SWEEP_SYM",
                    "desc": "Financial Results",
                    "attchmntText": "Unaudited financial results for the quarter ended September 30, 2026",
                    "attchmntFile": "https://nsearchives.nseindia.com/corporate/SWEEP_Q2.pdf",
                    "an_dt": "28-Oct-2026 18:05:00",
                    "seq_id": "998877",
                }
            ]
        return []

    monkeypatch.setattr(NseXbrlProvider, "_init_session", lambda self: True)
    monkeypatch.setattr(NseXbrlProvider, "_get", fake_get)

    p1 = NseXbrlProvider(cache_dir=str(tmp_path / "nse_cache"))
    res1 = p1.discover_market_filings_sweep(as_of_date="2026-11-27", lookback_days=7)
    assert "TEST_SWEEP_SYM" in res1
    cache_file = tmp_path / "data" / "nse_market_sweep_cache.json"
    assert cache_file.exists()

    # Simulate process/worker restart: clear in-memory cache, forbid _get calls
    nse_mod._GLOBAL_MARKET_SWEEP_CACHE.clear()
    calls_before_p2 = api_calls["n"]
    p2 = NseXbrlProvider(cache_dir=str(tmp_path / "nse_cache"))
    res2 = p2.discover_market_filings_sweep(as_of_date="2026-11-27", lookback_days=7)
    assert "TEST_SWEEP_SYM" in res2
    assert api_calls["n"] == calls_before_p2


def test_database_none_snap_item_lightweight_restore_and_atomic_manifest(tmp_path, monkeypatch):
    """
    Verifies:
    1. _is_symbol_restored_or_exempt safely handles snap_item=None (no AttributeError).
    2. check_and_restore_stale_stock accepts rebuild_snapshot=False, sync_raw_cache=False, clear_health_cache=False.
    3. sync_raw_filings_cache_for_symbol atomically writes pit_raw_filings_manifest.json outside _AUDIT_RAW_LOCK.
    """
    import app.database as db

    assert db._is_symbol_restored_or_exempt("TEST_NONE_SNAP", None, "MISSING_QUARTERLY") is False

    raw_dir = tmp_path / "data" / "pit_raw_filings"
    raw_dir.mkdir(parents=True, exist_ok=True)
    raw_path = raw_dir / "TEST_MANIFEST_SYNTH.json"
    raw_path.write_text(
        json.dumps([
            {
                "period_end_date": "2026-03-31",
                "statement_type": "ANNUAL",
                "revenue": 1000.0,
                "net_profit": 120.0,
                "total_debt": 10.0,
                "total_equity": 500.0,
            },
            {
                "period_end_date": "2026-09-30",
                "statement_type": "QUARTERLY",
                "revenue": 280.0,
                "net_profit": 35.0,
                "total_debt": 10.0,
                "total_equity": 520.0,
            },
        ]),
        encoding="utf-8",
    )

    monkeypatch.setenv("ELITE_BASE_DIR", str(tmp_path))
    orig_cache = db._AUDIT_RAW_FILINGS_CACHE
    try:
        db._AUDIT_RAW_FILINGS_CACHE = {}
        entry = db.sync_raw_filings_cache_for_symbol("TEST_MANIFEST_SYNTH")
        assert entry is not None
        assert entry["latest_quarterly"] == "2026-09-30"
        assert entry["latest_annual"] == "2026-03-31"
        manifest_path = tmp_path / "data" / "pit_raw_filings_manifest.json"
        assert manifest_path.exists()
        assert not (tmp_path / "data" / f"pit_raw_filings_manifest.json.tmp.{os.getpid()}").exists()
    finally:
        db._AUDIT_RAW_FILINGS_CACHE = orig_cache


def test_watcher_records_active_poll_cooldown_when_quarter_overdue(tmp_path, monkeypatch):
    """
    Verifies that after SEBI LODR deadline (e.g. 2026-11-27 for Q2 2026-09-30),
    an active exchange poll that returns 0 new filings records `last_active_poll_at`,
    so an immediate scheduled follow-up within 6h skips cleanly in 0ms instead of tight-looping.
    """
    from scripts.financial_filing_watcher import FinancialFilingWatcher

    raw_dir = tmp_path / "pit_raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    sym = "TEST_OVRD_SYNTH"
    filings = []
    for y in range(2020, 2027):
        filings.append({
            "period_end_date": f"{y}-03-31",
            "statement_type": "ANNUAL",
            "revenue": 1000.0,
            "net_profit": 100.0,
            "total_debt": 20.0,
            "total_equity": 400.0,
        })
    filings.append({
        "period_end_date": "2026-06-30",
        "statement_type": "QUARTERLY",
        "revenue": 250.0,
        "net_profit": 25.0,
        "total_debt": 20.0,
        "total_equity": 400.0,
    })
    (raw_dir / f"{sym}.json").write_text(json.dumps(filings), encoding="utf-8")

    watcher = FinancialFilingWatcher(
        state_file=tmp_path / "watcher_state.json",
        events_log=tmp_path / "events.jsonl",
        pit_raw_dir=raw_dir,
    )
    watcher.approved_symbols = [sym]

    nse_calls = []

    class MockNSE:
        def discover_market_filings_sweep(self, as_of_date=None, lookback_days=45):
            return {}

        def fetch_raw_financials(self, symbol, min_period_end_date=None, **kwargs):
            nse_calls.append(min_period_end_date)
            return []

        def discover_filings(self, symbol):
            return []

    snap_df = pd.DataFrame([{
        "symbol": sym,
        "latest_annual_period": "2026-03-31",
        "pit_freshness_status": "VALID",
        "sales_cagr_5y": 12.0,
        "current_ev_ebitda": 10.0,
        "debt_to_equity": 0.05,
        "roce_5y_avg": 20.0,
        "provenance_status": "CERTIFIED",
        "is_bfsi": False,
        "is_structural_ineligible": False,
        "filing_gap_detected": False,
    }])

    with patch("pandas.read_parquet", return_value=snap_df), \
         patch("app.data_providers.nse_xbrl_provider.NseXbrlProvider", return_value=MockNSE()), \
         patch("app.data_providers.bse_corporate_provider.BseCorporateProvider", return_value=None):
        watcher.poll_exchange_filings(target_symbols=[sym], as_of_date=date(2026, 11, 27), max_workers=1)
        assert len(nse_calls) == 1
        assert "last_active_poll_at" in watcher.state[sym]

        nse_calls.clear()
        watcher.poll_exchange_filings(target_symbols=[sym], as_of_date=date(2026, 11, 27), max_workers=1)
        assert len(nse_calls) == 0, f"Expected 6h cooldown skip after active poll, got calls={nse_calls}"


def test_non_financial_sdd_trading_window_and_zip_rejected():
    """
    Verifies SDD compliance certificates, trading window closure notices, and non-PDF .zip archives
    are rejected unconditionally so they never trigger false DATA_NOT_FOUND_IN_PDF alerts,
    while BSE uppercase HEADLINE/NEWSSUB financial results are accepted.
    """
    from app.data_providers.exchange_pdf_parser import is_financial_result_announcement

    assert is_financial_result_announcement(
        {
            "desc": "Structural Digital Database",
            "attchmntText": "Compliance Certificate for Structured Digital Database for the Quarter ended 30th June, 2026.",
        },
        "https://nsearchives.nseindia.com/corporate/SYNTH_SDD.pdf",
    ) is False

    assert is_financial_result_announcement(
        {
            "desc": "Closure of trading window",
            "attchmntText": "Closure of trading window for declaration of financial results for quarter ended Sep 30, 2026",
        },
        "https://nsearchives.nseindia.com/corporate/SYNTH_TradingWindow_Sep2026.pdf",
    ) is False

    assert is_financial_result_announcement(
        {"desc": "Financial Result Updates"},
        "https://nsearchives.nseindia.com/corporate/SYNTH_Results.zip",
    ) is False

    assert is_financial_result_announcement(
        {"HEADLINE": "Unaudited standalone & consolidated financial results for quarter ended 30th September 2026"},
        "https://www.bseindia.com/stockinfo/AnnPdfOpen.aspx?Pname=synth123.pdf",
    ) is True


def test_garbled_ocr_overlay_and_cashflow_with_finance_costs_handled_properly(tmp_path, monkeypatch):
    """
    Models SAMHI / AIMTRON / VIDHIING PDFs:
    - Cover letter (p0) and Cash Flow with 'Profit before tax' + 'Finance costs' + 'Interest income' (p1)
      and Balance Sheet with 'Assets & Liabilities' (p2) and Notes (p3) must be skipped with 0 OCR calls
      and never leak Cash Flow numbers as revenue.
    - Garbled P&L overlays (p4 '(N in millicn)' + '0 Sepicmber 2036' + 'Revenve Gom', p5 short fragment noise)
      are detected by _is_garbled_embedded_ocr and invoke fresh 200 DPI RE_OCR -> extract clean Consol+Stand.
    """
    from app.data_providers.exchange_pdf_parser import _is_garbled_embedded_ocr

    parser = ExchangePdfParser(cache_dir=str(tmp_path / "pdf_cache"))

    p0_cover = (
        "Dear Sir/Madam,\nCorporate Relationship Department, BSE Limited\nListing Department, NSE\n"
        "Outcome of Board Meeting: approved Unaudited Standalone and Consolidated financial results for quarter ended 30 September 2026.\n"
        "Yours faithfully,\nCompany Secretary"
    )
    p1_cashflow = (
        "UNAUDITED CONSOLIDATED STATEMENT OF CASH FLOWS FOR SIX MONTHS ENDED 30 SEPTEMBER 2026\n(INR in million)\n"
        "A. Cash flows from operating activities\n"
        "Profit before tax from continuing operations   1,250.45   980.20\n"
        "Finance costs   329.22   119.19\n"
        "Interest income   (64.40)   (33.24)\n"
        "Net cash generated from operating activities   2,357.80   1,656.38\n"
    )
    p2_bs = (
        "STATEMENT OF UNAUDITED CONSOLIDATED ASSETS & LIABILITIES AS AT 30 SEPTEMBER 2026\n"
        "Property, plant and equipment   24,440.01   24,766.72\n"
        "Total Equity and Liabilities   45,246.05   41,976.34\n"
    )
    p3_notes = (
        "STATEMENT OF UNAUDITED CONSOLIDATED FINANCIAL RESULTS\n"
        "[Note to the Statement of unaudited financial results]\n"
        "The above results were reviewed by the Audit Committee and approved by the Board of Directors.\n"
        "Chief Operating Decision Maker evaluates hospitality segment.\n"
    )
    p4_garbled_cons = (
        "STATEMENT OF UNAUDITED CONSOLIDATED FINANCIAL RESULTS FOR THE QUARTER ENDED 30 SEPTEMBER 2026\n"
        "(N in millicn)\n"
        "Particulars   0 Sepicmber 2036   30 june 2006   31 march 2026\n"
        "1. Revenve Gom operations   338070   305206   124796\n"
        "Totalincome   342676   310000   127899\n"
        "12. brofit for the period   10411   4107   5670\n"
    )
    p5_garbled_stand = (
        "STATEMENT OF UNAUDITED STANDALONE FINANCIAL RESULTS FOR THE QUARTER ENDED 30 SEPTEMBER 2026\n"
        "i S e s Pt 112 e 7wt M Y0 ey e e Sy e T\n"
        "e s o & w i s 0 - 3 ot o o s i s b s o i s 3 S o o T\n"
        "R — o s e v s e s o o l o s i e m\n"
    )

    assert _is_garbled_embedded_ocr(p4_garbled_cons) is True
    assert _is_garbled_embedded_ocr(p5_garbled_stand) is True

    raw_pages = {0: p0_cover, 1: p1_cashflow, 2: p2_bs, 3: p3_notes, 4: p4_garbled_cons, 5: p5_garbled_stand}
    ocr_rendered_pages = []

    clean_ocr_cons = (
        "STATEMENT OF UNAUDITED CONSOLIDATED FINANCIAL RESULTS FOR THE QUARTER ENDED 30 SEPTEMBER 2026\n"
        "(INR in million)\n"
        "Particulars   30.09.2026   30.06.2026   31.03.2026\n"
        "1. Revenue from operations   3,380.70   3,052.06   12,479.60\n"
        "2. Total expenses   3,100.00   2,850.00   11,400.00\n"
        "3. Profit before tax   280.70   202.06   1,079.60\n"
        "4. Profit for the period   184.11   121.50   567.00\n"
    )
    clean_ocr_stand = (
        "STATEMENT OF UNAUDITED STANDALONE FINANCIAL RESULTS FOR THE QUARTER ENDED 30 SEPTEMBER 2026\n"
        "(INR in million)\n"
        "Particulars   30.09.2026   30.06.2026   31.03.2026\n"
        "1. Revenue from operations   1,120.50   1,040.20   4,500.00\n"
        "2. Total expenses   950.00   890.00   3,900.00\n"
        "3. Profit before tax   170.50   150.20   600.00\n"
        "4. Profit for the period   124.30   108.10   420.00\n"
    )

    class DummyReader:
        pages = [0, 1, 2, 3, 4, 5]

    monkeypatch.setattr(pdf_mod, "_PDF_READER_CLS", lambda *a, **kw: DummyReader())
    monkeypatch.setattr(pdf_mod, "_HAS_PDFTOTEXT", False)
    monkeypatch.setattr(pdf_mod, "_HAS_PDFTOPPM", True)
    monkeypatch.setattr(pdf_mod, "_OCR_AVAILABLE", True)
    monkeypatch.setattr(pdf_mod, "_extract_pdf_pages_fast", lambda *a, **kw: dict(raw_pages))
    monkeypatch.setattr(
        pdf_mod,
        "_safe_extract_text",
        lambda page, timeout=3.5, extraction_mode=None, pdf_path=None, page_idx=None: raw_pages.get(
            page_idx if page_idx is not None else page, ""
        ),
    )

    class DummyImg:
        width = 1200
        height = 1600

        def thumbnail(self, sz):
            return None

    def fake_render(pdf_path, page_idx, **kw):
        ocr_rendered_pages.append(page_idx)
        DummyImg._cur_page = page_idx
        return DummyImg()

    monkeypatch.setattr(pdf_mod, "_render_page_image_fast", fake_render)
    monkeypatch.setattr(
        pdf_mod,
        "_ocr_image_with_osd",
        lambda img, **kw: clean_ocr_cons if getattr(DummyImg, "_cur_page", -1) == 4 else clean_ocr_stand,
    )

    recs = parser.parse_sebi_reg33_pdf(
        b"%PDF-1.4\n% synth_garbled_cf\n%%EOF",
        "TEST_GARBLED_SYNTH",
        announcement_metadata={
            "desc": "Outcome of Board Meeting - Financial Results for quarter ended September 30, 2026",
            "broadcast_timestamp": "2026-11-12T19:07:00",
        },
    )

    # Only pages 4 & 5 (garbled P&L tables) should ever trigger OCR — 0 OCR calls wasted on Cover/CF/BS/Notes!
    assert set(ocr_rendered_pages) == {4, 5}, f"Unexpected OCR pages rendered: {ocr_rendered_pages}"
    by_cons = {r.consolidation: r for r in recs}
    assert ConsolidationType.CONSOLIDATED in by_cons
    assert ConsolidationType.STANDALONE in by_cons
    assert pytest.approx(by_cons[ConsolidationType.CONSOLIDATED].revenue, rel=1e-2) == 338.07
    assert pytest.approx(by_cons[ConsolidationType.CONSOLIDATED].net_profit, rel=1e-2) == 18.411
    assert pytest.approx(by_cons[ConsolidationType.STANDALONE].revenue, rel=1e-2) == 112.05
    assert pytest.approx(by_cons[ConsolidationType.STANDALONE].net_profit, rel=1e-2) == 12.43


def test_ocr_image_with_osd_probes_rotated_table_when_osd_returns_zero(monkeypatch):
    """
    Verifies _ocr_image_with_osd probes orthogonal rotations when OSD returns Rotate: 0
    on a hybrid page (e.g. upright company letterhead above a 90/270-deg rotated financial table).
    """
    from PIL import Image

    class TaggedImg:
        def __init__(self, angle=0, width=1000, height=1400):
            self.angle = angle
            self.width = width
            self.height = height

        def rotate(self, deg, expand=True):
            return TaggedImg(angle=(self.angle + (-deg)) % 360, width=self.height, height=self.width)

    def fake_ocr(img, timeout=12.0, config="--psm 6"):
        if getattr(img, "angle", 0) == 270:
            return (
                "STATEMENT OF UNAUDITED FINANCIAL RESULTS FOR QUARTER ENDED 30.09.2026\n"
                "1. Revenue from operations   14,250.00\n"
                "2. Profit before tax   3,200.00\n"
            )
        return "Company Letterhead Mumbai suomesado suadx3 papu3 400034"

    monkeypatch.setattr(pdf_mod, "_OCR_AVAILABLE", True)
    monkeypatch.setattr(pdf_mod.pytesseract, "image_to_string", fake_ocr)
    monkeypatch.setattr(pdf_mod.pytesseract, "image_to_osd", lambda img, timeout=4.0: "Rotate: 0\nOrientation confidence: 0.2")

    txt = pdf_mod._ocr_image_with_osd(TaggedImg(), timeout=8.0)
    assert "Revenue from operations" in txt
    assert "Profit before tax" in txt
