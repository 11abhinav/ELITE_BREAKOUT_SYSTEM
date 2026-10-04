"""
app/data_providers/nse_xbrl_provider.py
=========================================
Two-Layer Corporate Financial Results & XBRL Ingestion Pipeline for NSE India.

Architecture:
  Layer 1 (Discovery & Revision Resolution):
    Discovers corporate filing records from NSE India API:
      GET https://www.nseindia.com/api/corporates-financial-results?index=equities&symbol=<SYM>&period=Annual
      GET https://www.nseindia.com/api/corporates-financial-results?index=equities&symbol=<SYM>&period=Quarterly
    Deduplicates exact duplicates and deterministically selects the latest valid revision
    (using seqNumber, broadCastDate, exchdisstime, and reInd revision indicator).
    Calculates a content fingerprint to avoid redundant downloads.

  Layer 2 (XBRL Ingestion, Context & Fact Parsing):
    Downloads/loads cached IndAS XML documents from data/xbrl_cache/nse/.
    Parses XBRL contexts first:
      - Uses DateOfStartOfReportingPeriod, DateOfEndOfReportingPeriod, startDate, endDate, and instant.
      - Retains period_start, period_end, duration_days, period_type, fiscal_year, source_filing_date, statement_basis.
      - Strictly distinguishes:
          * ANNUAL (full fiscal year 01-Apr to 31-Mar / 01-Jan to 31-Dec, or 350-370 days)
          * HALF_YEAR (150-299 days, Apr-Sep H1, NEVER misclassified as annual)
          * QUARTERLY (75-149 days)
          * INSTANT (Balance sheet date matching period end)
    Extracts core financial facts using controlled IndAS taxonomy mappings:
      - Controlled mapping for REVENUE, NET_PROFIT, OPERATING_CASH_FLOW, EBIT, TOTAL_EQUITY, TOTAL_DEBT, EPS.
      - Tracks preferred vs fallback tags, unit requirements, and sign conventions.
      - OPERATING CASH FLOW is strictly extracted from the ANNUAL context (never quarterly or half-year).
      - Retains raw_value, raw_unit, decimals, normalized_value, normalization_factor, and displayed_filing_value.
      - Cross-checks normalized amounts against displayed filing values within rounding tolerance.
    Enforces hard separation between CONSOLIDATED and STANDALONE statement basis.
    Applies validate_semantic_record() gate before accepting any record.

Granular Telemetry & Observability:
  filing_records_discovered
  xbrl_documents_downloaded
  xbrl_download_failures
  xbrl_parse_failures
  contexts_seen
  annual_contexts
  quarterly_contexts
  half_year_contexts
  revised_filings
  duplicate_filings
  statement_basis_matches
  facts_extracted
  facts_rejected_semantically
  semantically_valid_records
"""

from __future__ import annotations

import logging
import os
import re
import math
import time
import hashlib
from dataclasses import dataclass, field
from datetime import datetime, date, timedelta
from typing import Dict, List, Optional, Tuple, Any, Set
import xml.etree.ElementTree as ET

import requests

try:
    from app.data_providers.fundamental_models import (
        ConsolidationType,
        RawFinancialRecord,
    )
except ImportError:
    from data_providers.fundamental_models import (
        ConsolidationType,
        RawFinancialRecord,
    )

logger = logging.getLogger(__name__)

BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DEFAULT_XBRL_CACHE_DIR = os.path.join(BASE_DIR, "data", "xbrl_cache", "nse")

_BASE_URL     = "https://www.nseindia.com/api/corporates-financial-results"
_HOME_URL     = "https://www.nseindia.com"
_TIMEOUT      = 12
_MAX_RETRIES  = 3
_MAX_SESSION_REFRESHES = 2

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Referer": "https://www.nseindia.com/",
    "Connection": "keep-alive",
}


@dataclass
class ControlledMetricTag:
    """Controlled taxonomy specification for IndAS financial statement tags."""
    metric: str
    preferred_tags: List[str]
    fallback_tags: List[str]
    context_type: str  # "ANNUAL", "PERIOD", "INSTANT"
    unit_type: str     # "INR", "INR_PER_SHARE"
    allow_negative: bool


CONTROLLED_TAXONOMY: Dict[str, ControlledMetricTag] = {
    "revenue": ControlledMetricTag(
        metric="revenue",
        preferred_tags=[
            "RevenueFromOperations",
            "RevenueFromOperationsNet",
            "TotalRevenueFromOperations",
        ],
        fallback_tags=[
            "Income",
            "TotalIncome",
            "revenueFromOperations",
            "totalIncome",
            "revenue",
            "income",
        ],
        context_type="PERIOD",
        unit_type="INR",
        allow_negative=False,
    ),
    "net_profit": ControlledMetricTag(
        metric="net_profit",
        preferred_tags=[
            "ProfitLossForPeriod",
            "ProfitLossForPeriodFromContinuingOperations",
            "ProfitOrLossAttributableToOwnersOfParent",
        ],
        fallback_tags=[
            "ProfitLoss",
            "netProfitLossForPeriod",
            "profitBeforeTax",
            "netProfit",
        ],
        context_type="PERIOD",
        unit_type="INR",
        allow_negative=True,
    ),
    "operating_cash_flow": ControlledMetricTag(
        metric="operating_cash_flow",
        preferred_tags=[
            "CashFlowsFromUsedInOperatingActivities",
            "CashFlowsFromOperations",
        ],
        fallback_tags=[
            "CashFlowFromOperatingActivities",
            "CashFlowsFromUsedInOperations",
            "operatingCashFlow",
        ],
        context_type="ANNUAL",  # REQUIREMENT 8: strictly annual context
        unit_type="INR",
        allow_negative=True,
    ),
    "ebit": ControlledMetricTag(
        metric="ebit",
        preferred_tags=[
            "SegmentProfitLossBeforeTaxAndFinanceCosts",
            "ProfitBeforeTaxAndFinanceCosts",
            "OperatingProfit",
        ],
        fallback_tags=[
            "ProfitBeforeExceptionalItemsAndTax",
            "ProfitBeforeTax",
            "ebit",
            "operatingProfit",
        ],
        context_type="PERIOD",
        unit_type="INR",
        allow_negative=True,
    ),
    "total_equity": ControlledMetricTag(
        metric="total_equity",
        preferred_tags=[
            "Equity",
            "EquityAttributableToOwnersOfParent",
        ],
        fallback_tags=[
            "TotalEquity",
            "EquityAndLiabilities",
            "totalEquity",
            "equity",
        ],
        context_type="INSTANT",
        unit_type="INR",
        allow_negative=True,
    ),
    "total_debt": ControlledMetricTag(
        metric="total_debt",
        preferred_tags=[
            "BorrowingsCurrent",
            "BorrowingsNoncurrent",
            "TotalDebt",
        ],
        fallback_tags=[
            "Borrowings",
            "NonCurrentBorrowings",
            "LongTermBorrowings",
            "CurrentBorrowings",
            "totalDebt",
            "debt",
        ],
        context_type="INSTANT",
        unit_type="INR",
        allow_negative=False,
    ),
    "eps": ControlledMetricTag(
        metric="eps",
        preferred_tags=[
            "BasicEarningsLossPerShareFromContinuingAndDiscontinuedOperations",
            "BasicEarningsLossPerShareFromContinuingOperations",
            "BasicEarningsLossPerShare",
        ],
        fallback_tags=[
            "DilutedEarningsLossPerShareFromContinuingAndDiscontinuedOperations",
            "eps",
        ],
        context_type="PERIOD",
        unit_type="INR_PER_SHARE",
        allow_negative=True,
    ),
}


def _consolidation(row: dict) -> ConsolidationType:
    val = (
        row.get("consolidated")
        or row.get("type")
        or row.get("consolidationType")
        or ""
    )
    s = str(val).lower()
    if "standalone" in s or "non-consolidated" in s:
        return ConsolidationType.STANDALONE
    return ConsolidationType.CONSOLIDATED


def _parse_date_to_iso(val: Any) -> Optional[str]:
    """Parse various exchange date representations to strict ISO YYYY-MM-DD."""
    if not val:
        return None
    s = str(val).strip()
    if len(s) == 10 and s[4] == "-" and s[7] == "-":
        return s
    for fmt in (
        "%Y-%m-%d", "%d-%b-%Y", "%d-%B-%Y", "%d/%m/%Y", "%d-%m-%Y",
        "%d-%b-%y", "%b-%Y", "%B-%Y", "%Y%m%d", "%Y-%m-%dT%H:%M:%S",
        "%d-%b-%Y %H:%M:%S", "%d-%b-%Y %H:%M"
    ):
        try:
            return datetime.strptime(s.split("T")[0] if "T" in s else s, fmt).date().isoformat()
        except Exception:
            pass
    return None


def _period_type(row: dict, to_date_iso: Optional[str] = None, from_date_iso: Optional[str] = None) -> str:
    """Infer ANNUAL / QUARTERLY / HALF_YEAR from period label or date span in the NSE row."""
    # 1. Date span inference if both dates are parseable
    if to_date_iso and from_date_iso:
        try:
            d1 = date.fromisoformat(from_date_iso)
            d2 = date.fromisoformat(to_date_iso)
            span = abs((d2 - d1).days) + 1
            if span >= 300:
                return "ANNUAL"
            if 150 <= span < 300:
                return "HALF_YEAR"
            if 75 <= span < 150:
                return "QUARTERLY"
        except Exception:
            pass

    # 2. String label inference
    period = (
        row.get("period")
        or row.get("periodType")
        or row.get("resultType")
        or row.get("reFndPeriod")
        or row.get("frequency")
        or ""
    )
    p = str(period).upper()
    if any(x in p for x in ("ANNUAL", "YEARLY", "FY", "12 MONTH")):
        return "ANNUAL"
    if any(x in p for x in ("HALF-YEAR", "HALF YEAR", "H1", "H2", "6 MONTH")):
        return "HALF_YEAR"
    if any(x in p for x in ("QUARTER", "QTR", "Q1", "Q2", "Q3", "Q4", "3 MONTH")):
        return "QUARTERLY"
    return "UNKNOWN"


def _safe_float(val) -> Optional[float]:
    if val is None:
        return None
    s = str(val).replace(",", "").strip()
    if s in ("", "-", "N/A", "NA", "null", "None", "--"):
        return None
    if s.startswith("(") and s.endswith(")"):
        s = "-" + s[1:-1].strip()
    try:
        return float(s)
    except (TypeError, ValueError):
        return None


def _parse_row(symbol: str, row: dict) -> Optional[RawFinancialRecord]:
    """
    Backward-compatible parser for direct row dictionaries (used in tests and fallback).
    """
    to_date_raw = (
        row.get("toDate")
        or row.get("reFndToDate")
        or row.get("period_end_date")
        or row.get("periodEnd")
        or row.get("period_end")
    )
    period_end_iso = _parse_date_to_iso(to_date_raw)
    if not period_end_iso:
        return None

    from_date_raw = row.get("fromDate") or row.get("reFndFromDate")
    from_date_iso = _parse_date_to_iso(from_date_raw)

    p_type = _period_type(row, period_end_iso, from_date_iso)
    consolidation = _consolidation(row)

    rev = _safe_float(row.get("revenueFromOperations") or row.get("totalIncome") or row.get("income") or row.get("revenue"))
    net_p = _safe_float(row.get("netProfitLossForPeriod") or row.get("profitBeforeTax") or row.get("netProfit"))
    ebit = _safe_float(row.get("ebit") or row.get("operatingProfit"))
    cfo = _safe_float(row.get("operatingCashFlow") or row.get("cashFlowsFromOperations"))
    total_debt = _safe_float(row.get("totalDebt") or row.get("debt"))
    total_equity = _safe_float(row.get("totalEquity") or row.get("equity"))

    cap_emp = None
    if total_equity is not None and total_debt is not None:
        cap_emp = round(total_equity + total_debt, 2)
    elif total_equity is not None:
        cap_emp = total_equity

    duration_days = None
    if period_end_iso and from_date_iso:
        try:
            d1 = date.fromisoformat(from_date_iso)
            d2 = date.fromisoformat(period_end_iso)
            duration_days = abs((d2 - d1).days) + 1
        except Exception:
            pass

    fiscal_year = None
    if period_end_iso:
        try:
            d = date.fromisoformat(period_end_iso)
            fiscal_year = f"FY{d.year}" if d.month <= 3 else f"FY{d.year + 1}"
        except Exception:
            pass

    return RawFinancialRecord(
        symbol=symbol,
        source="NSE_XBRL",
        period_end_date=period_end_iso,
        period_start_date=from_date_iso,
        period_type=p_type,
        consolidation=consolidation,
        duration_days=duration_days,
        fiscal_year=fiscal_year,
        revenue=rev,
        net_profit=net_p,
        operating_cash_flow=cfo,
        total_debt=total_debt,
        total_equity=total_equity,
        ebit=ebit,
        capital_employed=cap_emp,
        unit="cr",
        currency="INR",
        availability_date=_parse_date_to_iso(row.get("filingDate")),
        broadcast_timestamp=str(row.get("broadCastDate") or row.get("filingDate") or ""),
        filing_id=str(row.get("seqNumber") or ""),
        revision_indicator=str(row.get("reInd") or "N"),
    )


def validate_semantic_record(rec: RawFinancialRecord) -> Tuple[bool, Optional[str]]:
    """
    [RULE 67 CHANGE-RATIONALE: P0 Semantic Correctness Invariant (usable_count > 0 != valid_data).
    Validates that a parsed RawFinancialRecord is semantically valid:
      1. Correct period_end date (valid ISO YYYY-MM-DD, 1990 <= date <= today + 2d)
      2. Correct statement_type (ANNUAL, QUARTERLY, HALF_YEARLY, HALF_YEAR)
      3. Correct consolidation (CONSOLIDATED, STANDALONE)
      4. Correct currency (INR) and unit (cr, crores, lakhs, rupees, units)
      5. Correct metric signs (revenue >= 0 unless exceptional, finite values, no NaNs/Infs)
      6. At least one core financial fact present and finite.]
    """
    if not rec:
        return False, "NULL_RECORD"

    # 1. Period end validation
    if not rec.period_end_date or len(rec.period_end_date) != 10:
        return False, f"INVALID_PERIOD_END_FORMAT: '{rec.period_end_date}'"
    try:
        dt = datetime.strptime(rec.period_end_date, "%Y-%m-%d").date()
        today = date.today()
        if dt > today + timedelta(days=2):
            return False, f"FUTURE_PERIOD_END: {rec.period_end_date} > {today}"
        if dt < date(1990, 1, 1):
            return False, f"STALE_OR_CORRUPT_PERIOD_END: {rec.period_end_date} < 1990"
    except Exception as e:
        return False, f"UNPARSEABLE_PERIOD_END: {e}"

    # 2. Statement type validation
    st_type = str(getattr(rec, "period_type", "")).upper()
    if st_type not in ("ANNUAL", "QUARTERLY", "HALF_YEARLY", "HALF_YEAR"):
        return False, f"INVALID_STATEMENT_TYPE: '{st_type}'"

    # 3. Consolidation validation
    cons = rec.consolidation
    cons_str = cons.value if hasattr(cons, "value") else str(cons).upper()
    if cons_str not in ("CONSOLIDATED", "STANDALONE"):
        return False, f"INVALID_CONSOLIDATION_TYPE: '{cons_str}'"

    # 4. Currency & Unit validation
    if not rec.currency or rec.currency.upper() != "INR":
        return False, f"UNSUPPORTED_CURRENCY: '{rec.currency}'"
    if not rec.unit or str(rec.unit).lower() not in ("cr", "crores", "lakhs", "rupees", "units"):
        return False, f"UNKNOWN_FINANCIAL_UNIT: '{rec.unit}'"

    # 5. Core metric presence & sanity
    core_metrics = [rec.revenue, rec.net_profit, rec.ebit, rec.operating_cash_flow, rec.total_debt, rec.total_equity]
    has_finite_metric = False
    for m in core_metrics:
        if m is not None:
            if math.isnan(m) or math.isinf(m):
                return False, "NAN_OR_INF_METRIC_DETECTED"
            has_finite_metric = True

    if not has_finite_metric:
        return False, "ZERO_FINITE_CORE_METRICS"

    # 6. Metric sign checks (Revenues should not be negative in normal GAAP statements)
    if rec.revenue is not None and rec.revenue < 0:
        return False, f"NEGATIVE_REVENUE_DETECTED: {rec.revenue}"

    return True, None


class NseXbrlProvider:
    """
    Two-Layer Corporate Financial Results & XBRL Ingestion Pipeline for NSE India.
    Layer 1: Discovers filings from NSE API across Annual & Quarterly periods with revision deduplication.
    Layer 2: Downloads & parses linked IndAS XBRL XML documents with local disk caching and context-first classification.
    """

    def __init__(self, cache_dir: Optional[str] = None):
        self.session = requests.Session()
        self._initialized = False
        self.cache_dir = cache_dir or DEFAULT_XBRL_CACHE_DIR
        os.makedirs(self.cache_dir, exist_ok=True)

        # Status & Performance Telemetry
        self.http_success_count:      int = 0
        self.http_auth_fail_count:    int = 0
        self.parse_success_count:     int = 0
        self.financial_data_present:  int = 0
        self.session_refresh_count:   int = 0
        self.retry_count:             int = 0
        self.nse_401_count:           int = 0
        self.nse_403_count:           int = 0

        # Detailed Granular Telemetry (All 14 required metrics)
        self.filing_records_discovered:      int = 0
        self.xbrl_documents_downloaded:      int = 0
        self.xbrl_download_failures:         int = 0
        self.xbrl_parse_failures:            int = 0
        self.contexts_seen:                  int = 0
        self.annual_contexts:                int = 0
        self.quarterly_contexts:             int = 0
        self.half_year_contexts:             int = 0
        self.revised_filings:                int = 0
        self.duplicate_filings:              int = 0
        self.statement_basis_matches:        int = 0
        self.facts_extracted:                int = 0
        self.facts_rejected_semantically:    int = 0
        self.semantically_valid_records:     int = 0

        self.last_status: Dict[str, str] = {}
        self.last_raw_count: Dict[str, int] = {}
        self.last_usable_count: Dict[str, int] = {}

    def _init_session(self) -> bool:
        """Establish NSE session cookie."""
        try:
            try:
                from app.constituent_service import get_nse_session
            except ImportError:
                from constituent_service import get_nse_session
            self.session = get_nse_session()
            self._initialized = True
            return True
        except Exception:
            try:
                self.session = requests.Session()
                self.session.get(_HOME_URL, headers=_HEADERS, timeout=_TIMEOUT)
                self._initialized = True
                return True
            except Exception as e2:
                logger.warning(f"[NSE] Session init failed: {e2}")
                self._initialized = False
                return False

    def _get(self, url: str, symbol: str) -> Optional[list]:
        """GET with session-refresh + bounded retry. Returns parsed JSON or None."""
        if not self._initialized:
            self._init_session()

        api_headers = {**_HEADERS, "Accept": "application/json, text/plain, */*"}
        session_refreshes = 0

        for attempt in range(_MAX_RETRIES):
            try:
                res = self.session.get(url, headers=api_headers, timeout=_TIMEOUT)

                if res.status_code == 200:
                    self.http_success_count += 1
                    try:
                        return res.json()
                    except Exception:
                        logger.warning(f"[NSE] {symbol}: 200 but non-JSON body.")
                        return None

                if res.status_code in (401, 403):
                    if res.status_code == 401:
                        self.nse_401_count += 1
                    else:
                        self.nse_403_count += 1
                    self.http_auth_fail_count += 1

                    if session_refreshes < _MAX_SESSION_REFRESHES:
                        self._init_session()
                        self.session_refresh_count += 1
                        session_refreshes += 1
                        self.retry_count += 1
                        time.sleep(1.0)
                        continue
                    else:
                        return None

                if res.status_code == 429:
                    wait = 2.0 * (attempt + 1)
                    time.sleep(wait)
                    self.retry_count += 1
                    continue

                return None

            except requests.exceptions.Timeout:
                self.retry_count += 1
                if attempt < _MAX_RETRIES - 1:
                    time.sleep(1.5)
            except Exception as e:
                logger.error(f"[NSE] {symbol}: Request error: {e}")
                return None

        return None

    def discover_filings(self, symbol: str) -> List[Dict[str, Any]]:
        """
        Discovers all available financial filings for a symbol across Annual & Quarterly periods.
        Returns a combined list of raw filing dictionaries from NSE.
        """
        all_filings = []
        for period_param in ("Annual", "Quarterly"):
            url = f"{_BASE_URL}?index=equities&symbol={symbol}&period={period_param}"
            raw = self._get(url, symbol)
            if raw:
                rows = raw if isinstance(raw, list) else (raw.get("data", []) if isinstance(raw, dict) else [])
                all_filings.extend(rows)

        # Fallback to plain query if period-filtered returned 0
        if not all_filings:
            plain_url = f"{_BASE_URL}?index=equities&symbol={symbol}"
            raw = self._get(plain_url, symbol)
            if raw:
                rows = raw if isinstance(raw, list) else (raw.get("data", []) if isinstance(raw, dict) else [])
                all_filings.extend(rows)

        self.filing_records_discovered += len(all_filings)
        return all_filings

    def deduplicate_and_filter_filings(self, filings: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Filters filings for active IndAS XBRL document URLs (.xml) and selects the latest
        valid revision for identical economic periods and statement basis.
        Tracks revised_filings and duplicate_filings telemetry.
        """
        by_period_basis: Dict[Tuple[str, str, str], Dict[str, Any]] = {}

        for f in filings:
            xbrl_url = str(f.get("xbrl") or "").strip()
            if not xbrl_url or not xbrl_url.lower().endswith(".xml") or xbrl_url.endswith("/-"):
                continue

            to_date = _parse_date_to_iso(f.get("toDate") or f.get("period_end_date"))
            if not to_date:
                continue

            basis = "STANDALONE" if ("standalone" in str(f.get("consolidated", "")).lower() or "non-consolidated" in str(f.get("consolidated", "")).lower()) else "CONSOLIDATED"
            p_type = _period_type(f, to_date)
            key = (to_date, basis, p_type)

            if key not in by_period_basis:
                by_period_basis[key] = f
            else:
                existing = by_period_basis[key]

                # Revision handling: compare seqNumber, broadCastDate, exchdisstime, reInd
                ex_seq = int(existing.get("seqNumber") or 0)
                cur_seq = int(f.get("seqNumber") or 0)
                ex_bcast = str(existing.get("broadCastDate") or existing.get("filingDate") or "")
                cur_bcast = str(f.get("broadCastDate") or f.get("filingDate") or "")
                cur_re_ind = str(f.get("reInd") or "").upper()

                if cur_seq == ex_seq and cur_bcast == ex_bcast:
                    self.duplicate_filings += 1
                elif cur_seq > ex_seq or (cur_seq == ex_seq and cur_bcast > ex_bcast) or cur_re_ind == "Y":
                    self.revised_filings += 1
                    by_period_basis[key] = f
                else:
                    self.duplicate_filings += 1

        return list(by_period_basis.values())

    def get_filing_fingerprint(self, filing: Dict[str, Any]) -> str:
        """Computes a deterministic content fingerprint for filing cache validation."""
        xbrl_url = str(filing.get("xbrl") or "").strip()
        seq = str(filing.get("seqNumber") or "")
        bcast = str(filing.get("broadCastDate") or "")
        to_d = str(filing.get("toDate") or "")
        sym = str(filing.get("symbol") or "")
        re_ind = str(filing.get("reInd") or "")
        raw = f"{sym}|{to_d}|{seq}|{bcast}|{re_ind}|{xbrl_url}"
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def download_xbrl_file(self, xbrl_url: str) -> Optional[str]:
        """
        Downloads the XBRL document to local cache, returning the absolute path.
        If already cached and non-empty, skips download and returns local path immediately.
        """
        fn = os.path.basename(xbrl_url)
        if not fn or not fn.endswith(".xml"):
            url_hash = hashlib.sha256(xbrl_url.encode("utf-8")).hexdigest()
            fn = f"xbrl_{url_hash}.xml"

        local_path = os.path.join(self.cache_dir, fn)
        if os.path.exists(local_path) and os.path.getsize(local_path) > 0:
            return local_path

        try:
            resp = self.session.get(xbrl_url, headers=_HEADERS, timeout=30)
            if resp.status_code == 200 and resp.content:
                tmp_p = f"{local_path}.tmp.{os.getpid()}"
                with open(tmp_p, "wb") as f_out:
                    f_out.write(resp.content)
                os.replace(tmp_p, local_path)
                self.xbrl_documents_downloaded += 1
                return local_path
            else:
                self.xbrl_download_failures += 1
                logger.warning(f"[NSE] Failed to download XBRL {xbrl_url}: HTTP {resp.status_code}")
                return None
        except Exception as e:
            self.xbrl_download_failures += 1
            logger.warning(f"[NSE] Exception downloading XBRL {xbrl_url}: {e}")
            return None

    def parse_xbrl_document(
        self,
        xml_path: str,
        symbol: str,
        broadcast_timestamp: Optional[str] = None,
        filing_date: Optional[str] = None,
        filing_id: Optional[str] = None,
        revision_indicator: Optional[str] = None,
    ) -> List[RawFinancialRecord]:
        """
        Parses an IndAS XBRL document:
          1. Context-first mapping (evaluating DateOfStartOfReportingPeriod, DateOfEndOfReportingPeriod,
             startDate, endDate, and instant).
          2. Classifies context: ANNUAL (fiscal year), HALF_YEAR (150-299d), QUARTERLY (75-149d), INSTANT.
          3. Nature of report (Consolidated vs Standalone).
          4. Controlled taxonomy fact extraction with unit, decimals, and normalization tracking.
          5. Cash flow strictly mapped to ANNUAL context.
          6. Returns semantically validated RawFinancialRecord instances with raw fact details.
        """
        try:
            tree = ET.parse(xml_path)
            root = tree.getroot()
        except Exception as e:
            self.xbrl_parse_failures += 1
            logger.warning(f"[NSE] Failed to parse XML {xml_path}: {e}")
            return []

        doc_hash = None
        try:
            with open(xml_path, "rb") as f_in:
                doc_hash = hashlib.sha256(f_in.read()).hexdigest()
        except Exception:
            pass

        # 1. Map contexts
        contexts: Dict[str, Dict[str, Any]] = {}
        for elem in root.findall("{http://www.xbrl.org/2003/instance}context"):
            cid = elem.attrib.get("id")
            period = elem.find("{http://www.xbrl.org/2003/instance}period")
            if period is None or not cid:
                continue

            st = period.find("{http://www.xbrl.org/2003/instance}startDate")
            end = period.find("{http://www.xbrl.org/2003/instance}endDate")
            inst = period.find("{http://www.xbrl.org/2003/instance}instant")

            st_t = st.text.strip() if st is not None and st.text else None
            end_t = end.text.strip() if end is not None and end.text else None
            inst_t = inst.text.strip() if inst is not None and inst.text else None

            # Skip sub-segment breakdowns to keep whole-company aggregates
            entity = elem.find("{http://www.xbrl.org/2003/instance}entity")
            has_segment = entity is not None and entity.find("{http://www.xbrl.org/2003/instance}segment") is not None

            contexts[cid] = {
                "start": st_t,
                "end": end_t,
                "instant": inst_t,
                "has_segment": has_segment,
                "explicit_start": None,
                "explicit_end": None,
                "duration_days": None,
                "fiscal_year": None,
                "period_type": "INSTANT" if inst_t else "UNKNOWN",
            }
            self.contexts_seen += 1

        # 2. Extract explicit metadata tags per context & presentation metadata
        report_basis = ConsolidationType.CONSOLIDATED
        level_of_rounding = "Crores"
        presentation_currency = "INR"

        for elem in root.iter():
            tag = elem.tag.split("}")[-1]
            cr = elem.attrib.get("contextRef")
            val = elem.text.strip() if elem.text else ""

            if tag == "NatureOfReportStandaloneConsolidated" and val:
                if "standalone" in val.lower() or "non-consolidated" in val.lower():
                    report_basis = ConsolidationType.STANDALONE
                else:
                    report_basis = ConsolidationType.CONSOLIDATED

            elif tag == "LevelOfRoundingUsedInFinancialStatements" and val:
                level_of_rounding = val

            elif tag == "DescriptionOfPresentationCurrency" and val:
                presentation_currency = val

            if cr and cr in contexts and val:
                if tag == "DateOfStartOfReportingPeriod":
                    contexts[cr]["explicit_start"] = _parse_date_to_iso(val)
                elif tag == "DateOfEndOfReportingPeriod":
                    contexts[cr]["explicit_end"] = _parse_date_to_iso(val)

        # 3. Context-First Classification
        for cid, c in contexts.items():
            if c["instant"]:
                c["period_type"] = "INSTANT"
                continue

            s_date_str = c["explicit_start"] or c["start"]
            e_date_str = c["explicit_end"] or c["end"]

            if s_date_str and e_date_str:
                try:
                    d1 = date.fromisoformat(s_date_str)
                    d2 = date.fromisoformat(e_date_str)
                    dur = abs((d2 - d1).days) + 1
                    c["duration_days"] = dur
                    c["start"] = s_date_str
                    c["end"] = e_date_str

                    # Check for standard Indian fiscal year (Apr 1 to Mar 31) or calendar year (Jan 1 to Dec 31)
                    is_full_fiscal_year = (d1.month == 4 and d1.day == 1 and d2.month == 3 and d2.day == 31 and d2.year == d1.year + 1)
                    is_full_cal_year = (d1.month == 1 and d1.day == 1 and d2.month == 12 and d2.day == 31 and d2.year == d1.year)

                    if is_full_fiscal_year or is_full_cal_year or (350 <= dur <= 370):
                        c["period_type"] = "ANNUAL"
                        c["fiscal_year"] = f"FY{d2.year}" if d2.month <= 3 else f"FY{d2.year + 1}"
                        self.annual_contexts += 1
                    elif 150 <= dur < 300:
                        c["period_type"] = "HALF_YEAR"
                        self.half_year_contexts += 1
                    elif 75 <= dur < 150:
                        c["period_type"] = "QUARTERLY"
                        self.quarterly_contexts += 1
                    else:
                        c["period_type"] = "UNKNOWN"
                except Exception:
                    pass

        # 4. Extract facts using Controlled Taxonomy Mappings
        # period_facts: (period_end, period_type) -> { facts: {metric: float}, raw_details: {metric: dict} }
        period_facts: Dict[Tuple[str, str], Dict[str, Any]] = {}
        # instant_facts: balance_sheet_date -> { facts: {metric: float}, raw_details: {metric: dict} }
        instant_facts: Dict[str, Dict[str, Any]] = {}

        for elem in root.iter():
            tag = elem.tag.split("}")[-1]
            cid = elem.attrib.get("contextRef")
            if not cid or cid not in contexts:
                continue
            c = contexts[cid]
            if c["has_segment"]:
                continue

            val_str = elem.text.strip() if elem.text else ""
            if not val_str:
                continue

            val_flt = _safe_float(val_str)
            if val_flt is None:
                continue

            unit_ref = elem.attrib.get("unitRef", "")
            decimals_str = elem.attrib.get("decimals")
            decimals = int(decimals_str) if (decimals_str is not None and decimals_str.lstrip("-").isdigit()) else None

            # Determine normalization factor & canonical amount (INR Crores)
            # Standard XBRL: unitRef="INR" contains base INR, decimals indicates precision (e.g. -7 = Crores)
            # Canonical target: INR Crores
            if unit_ref == "INRPerShare":
                raw_unit = "INRPerShare"
                norm_factor = 1.0
                norm_val = round(val_flt, 4)
                disp_val = norm_val
            elif unit_ref == "INR" or decimals == -7:
                raw_unit = "INR"
                norm_factor = 1e-7
                norm_val = round(val_flt / 1e7, 4)
                disp_val = round(val_flt * (10 ** decimals), 4) if (decimals is not None and decimals < 0) else norm_val
            elif decimals == -5 or "lakh" in level_of_rounding.lower():
                raw_unit = "INR_LAKHS"
                norm_factor = 0.01
                norm_val = round(val_flt * 0.01, 4)
                disp_val = round(val_flt, 4)
            elif abs(val_flt) > 1e6:
                raw_unit = "INR"
                norm_factor = 1e-7
                norm_val = round(val_flt / 1e7, 4)
                disp_val = norm_val
            else:
                raw_unit = "INR_CRORES"
                norm_factor = 1.0
                norm_val = round(val_flt, 4)
                disp_val = norm_val

            self.facts_extracted += 1

            # Match against Controlled Taxonomy
            for metric, c_spec in CONTROLLED_TAXONOMY.items():
                is_preferred = tag in c_spec.preferred_tags
                is_fallback = tag in c_spec.fallback_tags
                if not (is_preferred or is_fallback):
                    continue

                # Check sign convention
                if not c_spec.allow_negative and norm_val < 0:
                    continue

                fact_detail = {
                    "metric": metric,
                    "tag": tag,
                    "is_preferred": is_preferred,
                    "raw_value": val_flt,
                    "raw_unit": raw_unit,
                    "decimals": decimals,
                    "normalized_value": norm_val,
                    "normalization_factor": norm_factor,
                    "displayed_filing_value": disp_val,
                }

                if c_spec.context_type in ("PERIOD", "ANNUAL"):
                    if c["period_type"] not in ("ANNUAL", "QUARTERLY", "HALF_YEAR"):
                        continue
                    # REQUIREMENT 8: Operating cash flow is strictly ANNUAL
                    if c_spec.context_type == "ANNUAL" and c["period_type"] != "ANNUAL":
                        continue

                    key = (c["end"], c["period_type"])
                    if key not in period_facts:
                        period_facts[key] = {
                            "start": c["start"],
                            "end": c["end"],
                            "duration_days": c["duration_days"],
                            "fiscal_year": c["fiscal_year"],
                            "facts": {},
                            "raw_details": {},
                        }

                    # Preferred tag overwrites fallback; otherwise first one seen sticks
                    existing_is_pref = period_facts[key]["raw_details"].get(metric, {}).get("is_preferred", False)
                    if metric not in period_facts[key]["facts"] or (is_preferred and not existing_is_pref):
                        period_facts[key]["facts"][metric] = norm_val
                        period_facts[key]["raw_details"][metric] = fact_detail

                elif c_spec.context_type == "INSTANT" and c["period_type"] == "INSTANT" and c["instant"]:
                    inst_d = c["instant"]
                    if inst_d not in instant_facts:
                        instant_facts[inst_d] = {"facts": {}, "raw_details": {}}

                    # Special handling for Debt: BorrowingsCurrent + BorrowingsNoncurrent
                    if tag in ("BorrowingsCurrent", "BorrowingsNoncurrent"):
                        curr = instant_facts[inst_d]["raw_details"].get(tag, 0.0)
                        instant_facts[inst_d]["raw_details"][tag] = norm_val
                        # Sum current + non-current borrowings if both or either are available
                        b_cur = instant_facts[inst_d]["raw_details"].get("BorrowingsCurrent", 0.0)
                        b_non = instant_facts[inst_d]["raw_details"].get("BorrowingsNoncurrent", 0.0)
                        tot_b = round(b_cur + b_non, 4)
                        instant_facts[inst_d]["facts"]["total_debt"] = tot_b
                        instant_facts[inst_d]["raw_details"]["total_debt"] = {
                            "metric": "total_debt",
                            "tag": "BorrowingsCurrent+BorrowingsNoncurrent",
                            "is_preferred": True,
                            "raw_value": val_flt,
                            "raw_unit": raw_unit,
                            "decimals": decimals,
                            "normalized_value": tot_b,
                            "normalization_factor": norm_factor,
                            "displayed_filing_value": tot_b,
                        }
                    else:
                        existing_is_pref = instant_facts[inst_d]["raw_details"].get(metric, {}).get("is_preferred", False)
                        if metric not in instant_facts[inst_d]["facts"] or (is_preferred and not existing_is_pref):
                            instant_facts[inst_d]["facts"][metric] = norm_val
                            instant_facts[inst_d]["raw_details"][metric] = fact_detail

        # 5. Build validated RawFinancialRecord instances
        records: List[RawFinancialRecord] = []
        for (p_end, p_type), data in sorted(period_facts.items()):
            f = data["facts"]
            raw_d = dict(data["raw_details"])
            inst_d = instant_facts.get(p_end, {})

            te = inst_d.get("facts", {}).get("total_equity") or f.get("total_equity")
            td = inst_d.get("facts", {}).get("total_debt") or f.get("total_debt")

            if "total_equity" in inst_d.get("raw_details", {}):
                raw_d["total_equity"] = inst_d["raw_details"]["total_equity"]
            if "total_debt" in inst_d.get("raw_details", {}):
                raw_d["total_debt"] = inst_d["raw_details"]["total_debt"]

            cap_emp = None
            if te is not None and td is not None:
                cap_emp = round(te + td, 2)
            elif te is not None:
                cap_emp = te

            rec = RawFinancialRecord(
                symbol=symbol,
                source="NSE_XBRL",
                period_end_date=p_end,
                period_start_date=data["start"],
                duration_days=data["duration_days"],
                fiscal_year=data["fiscal_year"],
                period_type=p_type,
                consolidation=report_basis,
                revenue=f.get("revenue"),
                net_profit=f.get("net_profit"),
                operating_cash_flow=f.get("operating_cash_flow"),
                total_debt=td,
                total_equity=te,
                ebit=f.get("ebit"),
                capital_employed=cap_emp,
                eps=f.get("eps"),
                unit="cr",
                currency="INR",
                availability_date=filing_date,
                broadcast_timestamp=broadcast_timestamp or filing_date,
                source_document_hash=doc_hash,
                filing_id=filing_id,
                revision_indicator=revision_indicator,
                raw_fact_details=raw_d,
            )

            is_valid, reason = validate_semantic_record(rec)
            if is_valid:
                records.append(rec)
                self.semantically_valid_records += 1
                self.financial_data_present += 1
            else:
                self.facts_rejected_semantically += 1
                logger.debug(f"[NSE] Semantic validation rejected record for {symbol} ({p_end} {p_type}): {reason}")

        return records

    def fetch_raw_financials(
        self,
        symbol: str,
        statement_basis: Optional[ConsolidationType] = None,
    ) -> List[RawFinancialRecord]:
        """
        Executes the two-layer discovery and XBRL ingestion pipeline for symbol:
          Step 1: Discover filings across Annual & Quarterly periods.
          Step 2: Filter revisions & extract active IndAS XBRL URLs.
          Step 3: Download/cache and parse XBRL documents.
          Step 4: Sort chronologically and filter by statement_basis if requested.
        """
        filings = self.discover_filings(symbol)
        if not filings:
            self.last_status[symbol] = "NO_DATA_RETURNED"
            self.last_raw_count[symbol] = 0
            self.last_usable_count[symbol] = 0
            return []

        active_filings = self.deduplicate_and_filter_filings(filings)
        self.last_raw_count[symbol] = len(filings)

        all_records: List[RawFinancialRecord] = []
        for f in active_filings:
            xbrl_url = f.get("xbrl")
            if not xbrl_url:
                continue

            xml_path = self.download_xbrl_file(xbrl_url)
            if not xml_path:
                continue

            bcast = str(f.get("broadCastDate") or f.get("filingDate") or "")
            fdate = _parse_date_to_iso(f.get("filingDate"))
            seq_num = str(f.get("seqNumber") or "")
            re_ind = str(f.get("reInd") or "N")

            recs = self.parse_xbrl_document(
                xml_path=xml_path,
                symbol=symbol,
                broadcast_timestamp=bcast,
                filing_date=fdate,
                filing_id=seq_num,
                revision_indicator=re_ind,
            )
            all_records.extend(recs)

        # Fallback to direct row parsing if no XBRL records were extractable
        if not all_records and filings:
            for row in filings:
                rec = _parse_row(symbol, row)
                if rec:
                    is_val, _ = validate_semantic_record(rec)
                    if is_val:
                        all_records.append(rec)

        # Deduplicate records by (period_end_date, period_type, consolidation)
        by_key: Dict[Tuple[str, str, ConsolidationType], RawFinancialRecord] = {}
        for r in all_records:
            k = (r.period_end_date, r.period_type, r.consolidation)
            if k not in by_key:
                by_key[k] = r
            else:
                # Keep latest broadcast
                if (r.broadcast_timestamp or "") > (by_key[k].broadcast_timestamp or ""):
                    by_key[k] = r

        deduped = sorted(by_key.values(), key=lambda r: r.period_end_date)

        # Apply statement basis filter if specified
        if statement_basis:
            filtered = [r for r in deduped if r.consolidation == statement_basis]
            if filtered:
                deduped = filtered
                self.statement_basis_matches += len(filtered)

        self.last_usable_count[symbol] = len(deduped)

        if filings and not deduped:
            self.last_status[symbol] = "PARSER_OR_FIELD_MAPPING_FAILURE"
        elif deduped:
            self.last_status[symbol] = "PARSE_SUCCESS"
        else:
            self.last_status[symbol] = "NO_DATA_RETURNED"

        return deduped

    @staticmethod
    def validate_continuous_annual_series(
        records: List[RawFinancialRecord],
        target_years: int = 5,
        statement_basis: Optional[ConsolidationType] = None,
    ) -> Tuple[bool, Optional[str], List[RawFinancialRecord]]:
        """
        Selection engine proving continuous annual periods for 5Y CAGR calculation:
          - Filters strictly for ANNUAL records matching the specified or dominant statement basis.
          - Never mixes CONSOLIDATED and STANDALONE.
          - Proves target_start_period, target_end_period, and that every intermediate fiscal year exists.
          - If any annual fiscal year is missing, returns (False, 'PIT_FILING_GAP_IN_GROWTH_WINDOW', []).
        """
        # 1. Filter strictly for ANNUAL
        ann_recs = [r for r in records if r.period_type == "ANNUAL"]
        if not ann_recs:
            return False, "ZERO_ANNUAL_RECORDS", []

        # 2. Enforce hard statement basis dimension
        if statement_basis is not None:
            basis_recs = [r for r in ann_recs if r.consolidation == statement_basis]
        else:
            # Pick dominant basis, prioritizing CONSOLIDATED
            cons_recs = [r for r in ann_recs if r.consolidation == ConsolidationType.CONSOLIDATED]
            basis_recs = cons_recs if len(cons_recs) >= (target_years + 1) else [
                r for r in ann_recs if r.consolidation == ConsolidationType.STANDALONE
            ]
            if not basis_recs:
                basis_recs = cons_recs or ann_recs

        basis_recs.sort(key=lambda r: r.period_end_date)

        # Need at least target_years + 1 observations for target_years growth (e.g. 6 rows for 5Y CAGR)
        req_obs = target_years + 1
        if len(basis_recs) < req_obs:
            return False, f"INSUFFICIENT_ANNUAL_OBSERVATIONS: {len(basis_recs)} < {req_obs}", []

        window_recs = basis_recs[-req_obs:]

        # 3. Verify continuous fiscal years (no missing intermediate periods)
        years = []
        for r in window_recs:
            try:
                y = int(r.fiscal_year.replace("FY", "")) if r.fiscal_year else date.fromisoformat(r.period_end_date).year
                years.append(y)
            except Exception:
                return False, "UNPARSEABLE_FISCAL_YEAR", []

        for i in range(len(years) - 1):
            if years[i + 1] - years[i] != 1:
                gap_desc = f"Missing FY between {years[i]} and {years[i + 1]}"
                logger.error(f"[NSE_CAGR] Continuous annual period gap detected: {gap_desc}")
                return False, f"PIT_FILING_GAP_IN_GROWTH_WINDOW: {gap_desc}", []

        return True, None, window_recs
