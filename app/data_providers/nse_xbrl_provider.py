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
_ANN_URL      = "https://www.nseindia.com/api/corporate-announcements"
_HOME_URL     = "https://www.nseindia.com"
_TIMEOUT      = 7
_MAX_RETRIES  = 2
_MAX_SESSION_REFRESHES = 1

import threading

_RATE_LIMIT_LOCK = threading.Lock()
_LAST_REQUEST_TIME = 0.0
_MIN_REQUEST_INTERVAL = 0.20  # Minimum 200ms spacing between direct requests to NSE API to prevent WAF drop

_CIRCUIT_LOCK = threading.Lock()
_CIRCUIT_BREAKER_UNTIL = 0.0
_CONSECUTIVE_TIMEOUTS = 0
_MAX_CONSECUTIVE_TIMEOUTS = 4
_COOLDOWN_PERIOD = 45.0  # seconds cooldown if NSE WAF drops multiple connections

# Known symbol mappings where company was renamed/restructured on NSE
_NSE_SYMBOL_ALIASES: Dict[str, str] = {
    "GUJGASLTD": "GUJENERGY",
    "GUJRATGAS": "GUJENERGY",
}


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
            "SaleOfProducts",
            "GrossSales",
            "SalesRevenueNet",
            "IncomeFromOperations",
            "Sales",
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
            "ProfitLossBeforeTax",
            "NetProfitLossAfterTax",
            "ProfitAfterTax",
            "NetProfitForPeriod",
            "PAT",
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
            "NetWorth",
            "EquityShareCapital",
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
            "TotalBorrowings",
            "DebtSecurities",
            "LongTermDebt",
            "ShortTermDebt",
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
    "shares_outstanding": ControlledMetricTag(
        metric="shares_outstanding",
        preferred_tags=[
            "NumberOfSharesOutstanding",
            "NumberOfEquitySharesOutstanding",
            "WeightedAverageNumberOfEquitySharesUsedInCalculatingBasicEarningsPerShare",
            "WeightedAverageNumberOfSharesOutstandingBasic",
            "WeightedAverageNumberOfEquitySharesOutstanding",
            "EquityShareCapital",
            "PaidUpEquityShareCapital",
        ],
        fallback_tags=[
            "numberOfShares",
            "sharesOutstanding",
            "equityShareCapital",
            "WeightedAverageNumberOfSharesOutstandingDiluted",
        ],
        context_type="INSTANT",
        unit_type="SHARES",
        allow_negative=False,
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
    """Parse various exchange date representations to strict ISO YYYY-MM-DD.
    [RULE 67 CHANGE-RATIONALE: Enforces strict calendar sanity (1990 <= year <= current_year + 1). Auto-heals
    clerical century keyboard slips (e.g. 3025 -> 2025 where clerk typed 3 instead of 2). Prevents future date corruptions.]
    """
    if not val:
        return None
    s = str(val).strip()
    from zoneinfo import ZoneInfo
    now_year = datetime.now(ZoneInfo("Asia/Kolkata")).year
    max_year = now_year + 1

    def _sanitize(iso_val: str) -> Optional[str]:
        if not iso_val or len(iso_val) != 10 or iso_val[4] != "-" or iso_val[7] != "-":
            return None
        try:
            parts = iso_val.split("-")
            y, m, d = int(parts[0]), int(parts[1]), int(parts[2])
            if 3000 <= y <= 3099:
                y = 2000 + (y - 3000)
                iso_val = f"{y:04d}-{m:02d}-{d:02d}"
            if 1990 <= y <= max_year:
                return iso_val
            return None
        except Exception:
            return None

    if len(s) == 10 and s[4] == "-" and s[7] == "-":
        return _sanitize(s)
    for fmt in (
        "%Y-%m-%d", "%d-%b-%Y", "%d-%B-%Y", "%d/%m/%Y", "%d-%m-%Y",
        "%d-%b-%y", "%b-%Y", "%B-%Y", "%Y%m%d", "%Y-%m-%dT%H:%M:%S",
        "%d-%b-%Y %H:%M:%S", "%d-%b-%Y %H:%M"
    ):
        try:
            cand = datetime.strptime(s.split("T")[0] if "T" in s else s, fmt).date().isoformat()
            san = _sanitize(cand)
            if san:
                return san
        except Exception:
            pass
    return None


def _parse_period_end_from_announcement_text(text: Optional[str]) -> Optional[str]:
    """Extracts ISO YYYY-MM-DD period end date from announcement headlines or attachment descriptions."""
    if not text:
        return None
    # Pattern 1: Month DD, YYYY or DD Month YYYY (supporting full and short month names like Jun/June)
    m1 = re.search(r"(?:ended|ended on|ending|period ended)\s+([A-Za-z0-9\s,]+?\d{4})", text, re.IGNORECASE)
    if m1:
        raw = m1.group(1).strip()
        clean = re.sub(r"(?i)(st|nd|rd|th)", "", raw).strip()
        clean = re.sub(r"\s+", " ", clean)
        for fmt in [
            "%B %d, %Y", "%b %d, %Y", "%B %d %Y", "%b %d %Y",
            "%d %B, %Y", "%d %b, %Y", "%d %B %Y", "%d %b %Y"
        ]:
            try:
                cand = datetime.strptime(clean, fmt).strftime("%Y-%m-%d")
                return _parse_date_to_iso(cand)
            except ValueError:
                pass
    # Pattern 2: DD-Mon-YYYY
    m2 = re.search(r"(?:ended|ended on|ending|period ended)\s+(\d{1,2}-[A-Za-z]{3}-\d{4})", text, re.IGNORECASE)
    if m2:
        try:
            cand = datetime.strptime(m2.group(1).strip(), "%d-%b-%Y").strftime("%Y-%m-%d")
            return _parse_date_to_iso(cand)
        except Exception:
            pass
    # Pattern 3: DD/MM/YYYY
    m3 = re.search(r"(?:ended|ended on|ending|period ended)\s+(\d{1,2}/\d{1,2}/\d{4})", text, re.IGNORECASE)
    if m3:
        try:
            cand = datetime.strptime(m3.group(1).strip(), "%d/%m/%Y").strftime("%Y-%m-%d")
            return _parse_date_to_iso(cand)
        except Exception:
            pass
    # Pattern 4: direct ISO date YYYY-MM-DD (including 30xx clerical slips)
    m4 = re.search(r"\b((?:19|20|30)\d{2}-(?:0[1-9]|1[0-2])-(?:0[1-9]|[12]\d|3[01]))\b", text)
    if m4:
        return _parse_date_to_iso(m4.group(1))
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
    try:
        from app.financial_data_integrity import classify_statement_type
        c_type = classify_statement_type(p, period_end_date=to_date_iso or "", from_date=from_date_iso or "", to_date=to_date_iso or "")
        return "HALF_YEAR" if c_type == "HALF_YEARLY" else c_type
    except Exception:
        pass

    if any(x in p for x in ("HALF-YEAR", "HALF YEAR", "H1", "H2", "6 MONTH")):
        return "HALF_YEAR"
    if any(x in p for x in ("ANNUAL", "YEARLY", "FY", "12 MONTH")):
        return "ANNUAL"
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

    eps = _safe_float(row.get("eps") or row.get("basicEps") or row.get("basicEPS"))
    shares = _safe_float(row.get("shares_outstanding") or row.get("sharesOutstanding") or row.get("numberOfShares"))
    if shares is None and net_p is not None and eps is not None and eps != 0:
        try:
            from app.financial_data_integrity import derive_and_validate_shares
            _sh_r = derive_and_validate_shares(symbol, net_p, eps, None, scanner="NSE_XBRL")
            if _sh_r.ok and _sh_r.shares_millions is not None and _sh_r.shares_millions > 0:
                shares = _sh_r.shares_millions * 1e6
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
        eps=eps,
        shares_outstanding=shares,
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
        self.last_xbrl_doc_count: Dict[str, int] = {}

    def _init_session(self) -> bool:
        """Establish NSE session with anti-bot cookies via homepage handshake."""
        try:
            try:
                from app.constituent_service import get_nse_session
            except ImportError:
                from constituent_service import get_nse_session
            self.session = get_nse_session()
            try:
                # Crucial: Must visit homepage first to prime Akamai anti-bot cookies (_abck, bm_sz, etc.)
                r_home = self.session.get(_HOME_URL, headers=_HEADERS, timeout=7)
                if r_home.status_code == 200:
                    self._initialized = True
                    return True
                else:
                    logger.warning(f"[NSE] Homepage handshake status {r_home.status_code}")
            except Exception as h_err:
                logger.warning(f"[NSE] curl_cffi homepage handshake failed: {h_err}")
            self._initialized = True
            return True
        except Exception:
            try:
                self.session = requests.Session()
                self.session.get(_HOME_URL, headers=_HEADERS, timeout=7)
                self._initialized = True
                return True
            except Exception as e2:
                logger.warning(f"[NSE] Session init failed: {e2}")
                self._initialized = False
                return False

    def _get(self, url: str, symbol: str) -> Optional[list]:
        """GET corporate results. Per user directive: Directly tries ScraperAPI first. Only if scraper token not working, falls back to direct NSE session."""
        global _LAST_REQUEST_TIME, _CONSECUTIVE_TIMEOUTS, _CIRCUIT_BREAKER_UNTIL

        # 1. DIRECTLY TRY WITH SCRAPER API FIRST
        try:
            try:
                from app.pledge_scraper import fetch_via_scraperapi, get_scraper_api_key
            except ImportError:
                from pledge_scraper import fetch_via_scraperapi, get_scraper_api_key
            if get_scraper_api_key():
                s_resp = fetch_via_scraperapi(url, timeout=20, country_code="in")
                if s_resp is not None and s_resp.status_code == 200:
                    try:
                        data = s_resp.json()
                        self.http_success_count += 1
                        logger.info(f"✅ [NSE:SCRAPERAPI] Directly fetched corporate results for {symbol} via ScraperAPI")
                        return data
                    except Exception as json_err:
                        logger.warning(f"[NSE:SCRAPERAPI] ScraperAPI returned 200 for {symbol} but non-JSON body: {json_err} (snippet: {s_resp.text[:120]!r})")
                else:
                    logger.warning(f"[NSE:SCRAPERAPI] ScraperAPI call for {symbol} returned status {getattr(s_resp, 'status_code', None)}. Falling back to direct session without scraper...")
        except Exception as s_err:
            logger.debug(f"[NSE:SCRAPERAPI] ScraperAPI failed for {symbol}: {s_err}. Falling back to direct session without scraper...")

        # 2. CIRCUIT BREAKER CHECK (Fail fast to BSE/fallback if NSE WAF is repeatedly dropping connections)
        now = time.time()
        with _CIRCUIT_LOCK:
            if now < _CIRCUIT_BREAKER_UNTIL:
                remaining = int(_CIRCUIT_BREAKER_UNTIL - now)
                logger.warning(f"[NSE] Circuit breaker active ({remaining}s remaining). Skipping NSE for {symbol} to fail fast to fallback.")
                return None

        # 3. ONLY IF SCRAPER TOKEN NOT WORKING / FAILS, TRY WITHOUT SCRAPER
        if not self._initialized:
            self._init_session()

        api_headers = {**_HEADERS, "Accept": "application/json, text/plain, */*"}
        session_refreshes = 0

        for attempt in range(_MAX_RETRIES):
            # Enforce pacing between direct NSE requests to avoid Akamai burst drop
            with _RATE_LIMIT_LOCK:
                t_now = time.time()
                elapsed = t_now - _LAST_REQUEST_TIME
                if elapsed < _MIN_REQUEST_INTERVAL:
                    time.sleep(_MIN_REQUEST_INTERVAL - elapsed)
                _LAST_REQUEST_TIME = time.time()

            try:
                res = self.session.get(url, headers=api_headers, timeout=_TIMEOUT)

                if res.status_code == 200:
                    self.http_success_count += 1
                    with _CIRCUIT_LOCK:
                        _CONSECUTIVE_TIMEOUTS = 0
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

            except Exception as e:
                self.retry_count += 1
                is_timeout = False
                err_str = str(e).lower()
                if "timed out" in err_str or "curl: (28)" in err_str or "timeout" in err_str:
                    is_timeout = True
                    with _CIRCUIT_LOCK:
                        _CONSECUTIVE_TIMEOUTS += 1
                        if _CONSECUTIVE_TIMEOUTS >= _MAX_CONSECUTIVE_TIMEOUTS:
                            _CIRCUIT_BREAKER_UNTIL = time.time() + _COOLDOWN_PERIOD
                            logger.error(
                                f"[NSE] {_MAX_CONSECUTIVE_TIMEOUTS} consecutive timeouts/drops encountered. "
                                f"Tripping circuit breaker for {_COOLDOWN_PERIOD}s to prevent thread blocking."
                            )

                if attempt < _MAX_RETRIES - 1:
                    logger.warning(f"[NSE] {symbol}: Request error on attempt {attempt+1}/{_MAX_RETRIES} ({e}). Re-initializing session...")
                    self._init_session()
                    time.sleep(1.0 if not is_timeout else 1.5)
                    continue
                logger.error(f"[NSE] {symbol}: Request error: {e}")

        return None



    def discover_filings(self, symbol: str) -> List[Dict[str, Any]]:
        """
        Discovers all available financial filings for a symbol across Annual & Quarterly periods.
        Returns a combined list of raw filing dictionaries from NSE.
        Caches in memory per session to eliminate duplicate requests between fetch_raw_financials and discover_filings.
        """
        if not hasattr(self, "_discovered_cache"):
            self._discovered_cache = {}
        if symbol in self._discovered_cache:
            return self._discovered_cache[symbol]

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

        # 3. Query NSE Corporate Announcements feed for real-time same-day filings
        ann_url = f"{_ANN_URL}?index=equities&symbol={symbol}"
        ann_raw = self._get(ann_url, symbol)
        if ann_raw:
            ann_rows = ann_raw if isinstance(ann_raw, list) else (ann_raw.get("data", []) if isinstance(ann_raw, dict) else [])
            existing_ann_keys = set()
            for item in ann_rows:
                desc = str(item.get("desc") or "")
                txt = str(item.get("attchmntText") or "")
                has_xbrl = bool(item.get("hasXbrl"))
                combined = f"{desc} {txt}".lower()

                is_fin_ann = (
                    has_xbrl
                    or any(k in combined for k in (
                        "financial result", "financial results", "outcome of board meeting",
                        "un-audited financial", "unaudited financial", "audited financial",
                        "quarterly result", "half year", "period ended"
                    ))
                )
                if not is_fin_ann:
                    continue

                p_end = _parse_period_end_from_announcement_text(txt) or _parse_period_end_from_announcement_text(desc)
                # [RULE 67 CHANGE-RATIONALE: HISTORICAL_LOOKBACK_6Y_v1.0]
                # Maximum scanner lookback is 5 years + 1 buffer year = 6 years (>= 2020-01-01 in 2026).
                if not p_end or p_end < "2020-01-01":
                    continue
                att_file = str(item.get("attchmntFile") or "")
                ann_key = (p_end, att_file)
                if ann_key in existing_ann_keys:
                    continue
                existing_ann_keys.add(ann_key)

                st_type = "ANNUAL" if any(k in combined for k in ("annual", "year ended", "audited financial results for the year", "fy2")) else "QUARTERLY"
                basis = "CONSOLIDATED" if "consolidated" in combined else "STANDALONE"

                all_filings.append({
                    "symbol": symbol,
                    "toDate": p_end,
                    "period_end_date": p_end,
                    "period": "Annual" if st_type == "ANNUAL" else "Quarterly",
                    "statement_type": st_type,
                    "consolidated": basis,
                    "seqNumber": str(item.get("seq_id") or ""),
                    "broadCastDate": str(item.get("an_dt") or item.get("exchdisstime") or item.get("sort_date") or ""),
                    "filingDate": str(item.get("an_dt") or "")[:11],
                    "xbrl": item.get("xbrl") or "",
                    "attchmntFile": item.get("attchmntFile") or "",
                    "desc": desc,
                    "attchmntText": txt,
                    "exchange": "NSE",
                })

        self.filing_records_discovered += len(all_filings)
        self._discovered_cache[symbol] = all_filings
        return all_filings

    def discover_market_filings_sweep(self) -> Dict[str, List[Dict[str, Any]]]:
        """
        Discovers all recently published corporate financial results and announcements
        across the entire market in 3 fast requests (Quarterly, Annual, Corporate Announcements).
        Returns a dict of symbol -> list of filing dictionaries.
        Also warms self._discovered_cache for discovered symbols.
        """
        if not hasattr(self, "_discovered_cache"):
            self._discovered_cache = {}

        market_filings: Dict[str, List[Dict[str, Any]]] = {}

        # 1. Fetch market-wide quarterly results
        try:
            q_url = f"{_BASE_URL}?index=equities&period=Quarterly"
            raw_q = self._get(q_url, "MARKET_QUARTERLY")
            if raw_q:
                rows = raw_q if isinstance(raw_q, list) else (raw_q.get("data", []) if isinstance(raw_q, dict) else [])
                for r in rows:
                    if isinstance(r, dict):
                        sym = str(r.get("symbol") or "").strip().upper()
                        if sym:
                            r_copy = dict(r)
                            r_copy["exchange"] = "NSE"
                            market_filings.setdefault(sym, []).append(r_copy)
        except Exception as _e_q:
            logger.debug(f"[NSE] Market sweep quarterly error: {_e_q}")

        # 2. Fetch market-wide annual results
        try:
            a_url = f"{_BASE_URL}?index=equities&period=Annual"
            raw_a = self._get(a_url, "MARKET_ANNUAL")
            if raw_a:
                rows = raw_a if isinstance(raw_a, list) else (raw_a.get("data", []) if isinstance(raw_a, dict) else [])
                for r in rows:
                    if isinstance(r, dict):
                        sym = str(r.get("symbol") or "").strip().upper()
                        if sym:
                            r_copy = dict(r)
                            r_copy["exchange"] = "NSE"
                            market_filings.setdefault(sym, []).append(r_copy)
        except Exception as _e_a:
            logger.debug(f"[NSE] Market sweep annual error: {_e_a}")

        # 3. Fetch market-wide corporate announcements (real-time same-day filings)
        try:
            ann_url = f"{_ANN_URL}?index=equities"
            raw_ann = self._get(ann_url, "MARKET_ANNOUNCEMENTS")
            if raw_ann:
                ann_rows = raw_ann if isinstance(raw_ann, list) else (raw_ann.get("data", []) if isinstance(raw_ann, dict) else [])
                for item in ann_rows:
                    if not isinstance(item, dict):
                        continue
                    sym = str(item.get("symbol") or "").strip().upper()
                    if not sym:
                        continue
                    desc = str(item.get("desc") or "")
                    txt = str(item.get("attchmntText") or "")
                    has_xbrl = bool(item.get("hasXbrl"))
                    combined = f"{desc} {txt}".lower()
                    is_fin_ann = (
                        has_xbrl
                        or any(k in combined for k in (
                            "financial result", "financial results", "outcome of board meeting",
                            "un-audited financial", "unaudited financial", "audited financial",
                            "quarterly result", "half year", "period ended"
                        ))
                    )
                    if not is_fin_ann:
                        continue
                    p_end = _parse_period_end_from_announcement_text(txt) or _parse_period_end_from_announcement_text(desc)
                    if not p_end or p_end < "2020-01-01":
                        continue
                    st_type = "ANNUAL" if any(k in combined for k in ("annual", "year ended", "audited financial results for the year", "fy2")) else "QUARTERLY"
                    basis = "CONSOLIDATED" if "consolidated" in combined else "STANDALONE"
                    market_filings.setdefault(sym, []).append({
                        "symbol": sym,
                        "toDate": p_end,
                        "period_end_date": p_end,
                        "period": "Annual" if st_type == "ANNUAL" else "Quarterly",
                        "statement_type": st_type,
                        "consolidated": basis,
                        "seqNumber": str(item.get("seq_id") or ""),
                        "broadCastDate": str(item.get("an_dt") or item.get("exchdisstime") or item.get("sort_date") or ""),
                        "filingDate": str(item.get("an_dt") or "")[:11],
                        "xbrl": item.get("xbrl") or "",
                        "attchmntFile": item.get("attchmntFile") or "",
                        "desc": desc,
                        "attchmntText": txt,
                        "exchange": "NSE",
                    })
        except Exception as _e_ann:
            logger.debug(f"[NSE] Market sweep announcements error: {_e_ann}")

        for s, flist in market_filings.items():
            self._discovered_cache[s] = flist
            self.filing_records_discovered += len(flist)

        return market_filings

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

                # Revision handling: compare machine-readable XBRL presence, seqNumber, broadCastDate, reInd
                ex_has_xbrl = bool(str(existing.get("xbrl") or "").strip())
                cur_has_xbrl = bool(str(f.get("xbrl") or "").strip())

                ex_seq = int(existing.get("seqNumber") or 0)
                cur_seq = int(f.get("seqNumber") or 0)
                ex_bcast = str(existing.get("broadCastDate") or existing.get("filingDate") or "")
                cur_bcast = str(f.get("broadCastDate") or f.get("filingDate") or "")
                cur_re_ind = str(f.get("reInd") or "").upper()

                if ex_has_xbrl and not cur_has_xbrl:
                    # Existing has valid machine-readable XBRL while current is PDF/announcement-only without XBRL.
                    # Retain the XBRL-rich existing filing!
                    self.duplicate_filings += 1
                elif cur_has_xbrl and not ex_has_xbrl:
                    # Current has valid XBRL while existing did not. Promote current!
                    self.revised_filings += 1
                    by_period_basis[key] = f
                elif cur_seq == ex_seq and cur_bcast == ex_bcast:
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
        Per user directive: Directly tries ScraperAPI first (bypasses NSE 30s timeouts & HTTP/2 stream errors).
        Only if scraper token not working / fails, falls back to direct fetch without scraper.
        """
        fn = os.path.basename(xbrl_url)
        if not fn or not fn.endswith(".xml"):
            url_hash = hashlib.sha256(xbrl_url.encode("utf-8")).hexdigest()
            fn = f"xbrl_{url_hash}.xml"

        local_path = os.path.join(self.cache_dir, fn)
        if os.path.exists(local_path) and os.path.getsize(local_path) > 0:
            return local_path

        # 1. DIRECT DOWNLOAD FIRST (Static XML archives work directly without proxy)
        try:
            resp = self.session.get(xbrl_url, headers=_HEADERS, timeout=10)
            if resp.status_code == 200 and resp.content:
                tmp_p = f"{local_path}.tmp.{os.getpid()}"
                with open(tmp_p, "wb") as f_out:
                    f_out.write(resp.content)
                os.replace(tmp_p, local_path)
                self.xbrl_documents_downloaded += 1
                logger.info(f"✅ [NSE:DIRECT] Directly downloaded XBRL {fn} without scraper ({len(resp.content)} bytes)")
                return local_path
            elif resp.status_code in (401, 403, 429):
                logger.debug(f"[NSE] Direct fetch blocked for XBRL {fn}: HTTP {resp.status_code} — trying ScraperAPI fallback")
        except Exception as direct_err:
            logger.debug(f"[NSE] Direct fetch failed for XBRL {fn}: {direct_err} — trying ScraperAPI fallback")

        # 2. FALLBACK TO SCRAPER API ONLY IF DIRECT DOWNLOAD FAILS OR IS BLOCKED
        try:
            try:
                from app.pledge_scraper import fetch_via_scraperapi, get_scraper_api_key
            except ImportError:
                from pledge_scraper import fetch_via_scraperapi, get_scraper_api_key
            if get_scraper_api_key():
                s_resp = fetch_via_scraperapi(xbrl_url, timeout=25, country_code="in")
                if s_resp is not None and s_resp.status_code == 200 and s_resp.content:
                    tmp_p = f"{local_path}.tmp.{os.getpid()}"
                    with open(tmp_p, "wb") as f_out:
                        f_out.write(s_resp.content)
                    os.replace(tmp_p, local_path)
                    self.xbrl_documents_downloaded += 1
                    logger.info(f"✅ [NSE:SCRAPERAPI] Downloaded XBRL {fn} via ScraperAPI fallback ({len(s_resp.content)} bytes)")
                    return local_path
                else:
                    self.xbrl_download_failures += 1
                    logger.warning(f"[NSE:SCRAPERAPI] ScraperAPI fallback returned status {getattr(s_resp, 'status_code', None)} for {fn}")
                    return None
        except Exception as s_err:
            self.xbrl_download_failures += 1
            logger.debug(f"[NSE:SCRAPERAPI] ScraperAPI download failed for {xbrl_url}: {s_err}")
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
        min_period_end_date: Optional[str] = None,
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
        doc_count = 0
        for f in active_filings:
            to_d = _parse_date_to_iso(f.get("toDate") or f.get("period_end_date"))
            if min_period_end_date and to_d and to_d < min_period_end_date:
                continue

            xbrl_url = f.get("xbrl")
            if not xbrl_url:
                continue

            xml_path = self.download_xbrl_file(xbrl_url)
            if not xml_path:
                continue
            doc_count += 1

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
        self.last_xbrl_doc_count[symbol] = doc_count

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

        # Deduplicate by fiscal year (keeping latest amendment/publication in sorted sequence)
        deduped_by_fy: Dict[int, Any] = {}
        for r in basis_recs:
            try:
                y = int(r.fiscal_year.replace("FY", "")) if r.fiscal_year else date.fromisoformat(r.period_end_date).year
                deduped_by_fy[y] = r
            except Exception:
                continue

        sorted_fy = sorted(deduped_by_fy.keys())

        # Need at least target_years + 1 distinct observations for target_years growth (e.g. 6 rows for 5Y CAGR)
        req_obs = target_years + 1
        if len(sorted_fy) < req_obs:
            return False, f"INSUFFICIENT_ANNUAL_OBSERVATIONS: {len(sorted_fy)} < {req_obs}", []

        trailing_fy = sorted_fy[-req_obs:]

        # 3. Verify continuous fiscal years (no missing intermediate periods)
        for i in range(len(trailing_fy) - 1):
            if trailing_fy[i + 1] - trailing_fy[i] != 1:
                gap_desc = f"Missing FY between {trailing_fy[i]} and {trailing_fy[i + 1]}"
                # [RULE 67 CHANGE-RATIONALE: Validation rejection of CAGR window logged at WARNING rather than ERROR]
                logger.warning(f"⚠️ [NSE_CAGR] Continuous annual period gap detected: {gap_desc}")
                return False, f"PIT_FILING_GAP_IN_GROWTH_WINDOW: {gap_desc}", []

        window_recs = [deduped_by_fy[y] for y in trailing_fy]
        return True, None, window_recs
