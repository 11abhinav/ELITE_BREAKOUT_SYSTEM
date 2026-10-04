"""
app/data_providers/bse_corporate_provider.py
============================================
Fetches raw corporate financial results from official BSE India APIs and maps
every parseable field to RawFinancialRecord with strict semantic validation.

BSE endpoints:
  1. Primary Financial Results:
     GET https://api.bseindia.com/BseIndiaAPI/api/FinResults/w?scripcode={scrip_code}&flag=C
  2. Corporate Announcements / Results Filings:
     GET https://api.bseindia.com/BseIndiaAPI/api/AnnSubCategoryGetData/w?scrip_code={scrip_code}&category=Financial%20Results

Status reporting:
  - BSE_AVAILABLE: >= 1 semantically valid RawFinancialRecord extracted
  - BSE_NO_DATA: Scrip code queried successfully, but no financial records returned
  - BSE_HTTP_ERROR: Network timeout, 403, or 5xx response
  - BSE_SYMBOL_NOT_FOUND: Scrip code could not be resolved for symbol
  - BSE_PARSE_FAILURE: HTTP 200 raw data returned, but 0 semantically valid records extracted
  - BSE_NOT_APPLICABLE: Security is unmapped or NSE-only with no BSE listing

Invariants:
  - P0 Invariant: HTTP 200 + raw rows > 0 + usable == 0 -> BSE_PARSE_FAILURE (never BSE_NO_DATA)
  - P0 Semantic Correctness Invariant: usable_count > 0 != valid_data
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, date, timedelta
from typing import Any, Dict, List, Optional, Tuple

import requests

try:
    from app.data_providers.fundamental_models import (
        ConsolidationType,
        RawFinancialRecord,
    )
    from app.data_providers.nse_xbrl_provider import (
        _parse_date_to_iso,
        _safe_float,
        validate_semantic_record,
    )
except ImportError:
    from data_providers.fundamental_models import (
        ConsolidationType,
        RawFinancialRecord,
    )
    from data_providers.nse_xbrl_provider import (
        _parse_date_to_iso,
        _safe_float,
        validate_semantic_record,
    )

logger = logging.getLogger(__name__)

_BSE_FIN_RESULTS_URL = "https://api.bseindia.com/BseIndiaAPI/api/FinResults/w"
_BSE_ANN_URL = "https://api.bseindia.com/BseIndiaAPI/api/AnnSubCategoryGetData/w"
_TIMEOUT = 12
_MAX_RETRIES = 2

_BSE_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Origin": "https://www.bseindia.com",
    "Referer": "https://www.bseindia.com/",
    "Connection": "keep-alive",
}

# Known initial fallback scrip codes for prominent equities
_STATIC_SCRIP_CODE_MAP: Dict[str, str] = {
    "ADOR": "517041",
    "BASF": "500042",
    "GABRIEL": "505714",
    "GLOBUSSPR": "533104",
    "STYRENIX": "506285",
    "MAHLIFE": "532383",
    "MANYAVAR": "543463",
    "MOLDTKPAC": "533080",
    "JUSTDIAL": "535648",
    "HEXT": "543253",
    "KENNAMET": "505890",
    "VIMTALABS": "524394",
    "ABSLAMC": "543374",
    "RELIANCE": "500325",
    "TCS": "532540",
    "INFY": "500209",
    "HDFCBANK": "500180",
    "ICICIBANK": "532174",
    "SBIN": "500112",
}


def _period_type_bse(row: dict, to_date_iso: Optional[str] = None) -> str:
    """Classifies period type as ANNUAL or QUARTERLY for BSE records."""
    period_label = str(
        row.get("Period") or row.get("period") or row.get("FinPeriod") or row.get("Quarter") or ""
    ).upper()
    if any(k in period_label for k in ("YEAR", "ANNUAL", "AUDITED", "FY")):
        return "ANNUAL"
    if any(k in period_label for k in ("Q1", "Q2", "Q3", "Q4", "QUARTER", "UNAUDITED")):
        return "QUARTERLY"
    # Default to ANNUAL if period date indicates full fiscal year (March 31)
    if to_date_iso and to_date_iso.endswith("-03-31"):
        return "ANNUAL"
    return "QUARTERLY"


def _consolidation_bse(row: dict) -> ConsolidationType:
    val = str(row.get("Consolidated") or row.get("type") or row.get("Flag") or "").lower()
    if "standalone" in val or val == "s":
        return ConsolidationType.STANDALONE
    return ConsolidationType.CONSOLIDATED


def _parse_bse_row(symbol: str, row: dict) -> Optional[RawFinancialRecord]:
    """Parse one BSE corporate financial results row into a RawFinancialRecord."""
    lower_row = {str(k).lower().strip(): v for k, v in row.items()}

    period_end_raw = (
        row.get("PeriodEnded")
        or row.get("period_ended")
        or row.get("toDate")
        or row.get("ToDate")
        or row.get("period")
        or lower_row.get("periodended")
        or lower_row.get("todate")
        or lower_row.get("period")
        or ""
    )
    period_end = _parse_date_to_iso(period_end_raw)
    if not period_end:
        period_end = str(period_end_raw).strip() if period_end_raw else None
    if not period_end:
        return None

    # Revenue
    revenue = _safe_float(
        row.get("TotIncome")
        or row.get("NetSales")
        or row.get("TotalIncome")
        or row.get("Revenue")
        or lower_row.get("totincome")
        or lower_row.get("netsales")
        or lower_row.get("totalincome")
        or lower_row.get("revenue")
    )

    # Net Profit (PAT)
    net_profit = _safe_float(
        row.get("NetProfit")
        or row.get("ProfitAfterTax")
        or row.get("PAT")
        or lower_row.get("netprofit")
        or lower_row.get("profitaftertax")
        or lower_row.get("pat")
    )

    # Operating Profit / EBIT
    ebit = _safe_float(
        row.get("PBIT")
        or row.get("OperatingProfit")
        or row.get("EBIT")
        or row.get("PBDT")
        or lower_row.get("pbit")
        or lower_row.get("operatingprofit")
        or lower_row.get("ebit")
    )

    # Operating Cash Flow
    operating_cash_flow = _safe_float(
        row.get("CFO")
        or row.get("CashFlowOps")
        or lower_row.get("cfo")
        or lower_row.get("cashflowops")
    )

    # Balance Sheet Fields
    total_debt = _safe_float(
        row.get("TotalDebt")
        or row.get("Borrowings")
        or lower_row.get("totaldebt")
        or lower_row.get("borrowings")
    )
    total_equity = _safe_float(
        row.get("TotalEquity")
        or row.get("NetWorth")
        or row.get("EquityShareCapital")
        or lower_row.get("totalequity")
        or lower_row.get("networth")
    )

    capital_employed = None
    if total_equity is not None and total_debt is not None:
        capital_employed = total_equity + total_debt
    elif total_equity is not None:
        capital_employed = total_equity

    eps = _safe_float(row.get("EPS") or row.get("BasicEPS") or lower_row.get("eps"))

    availability_date = (
        row.get("BroadcastDate")
        or row.get("FilingDate")
        or row.get("Date")
        or lower_row.get("broadcastdate")
        or lower_row.get("filingdate")
        or ""
    )

    unit = str(row.get("Unit") or lower_row.get("unit") or "cr").lower()

    return RawFinancialRecord(
        symbol=symbol,
        source="BSE_CORPORATE",
        period_end_date=str(period_end),
        period_type=_period_type_bse(row, to_date_iso=period_end),
        consolidation=_consolidation_bse(row),
        revenue=revenue,
        net_profit=net_profit,
        ebit=ebit,
        operating_cash_flow=operating_cash_flow,
        total_debt=total_debt,
        total_equity=total_equity,
        capital_employed=capital_employed,
        eps=eps,
        availability_date=str(availability_date) if availability_date else None,
        unit=unit,
        currency="INR",
    )


class BseCorporateProvider:
    """
    Official BSE Corporate Results Provider.
    Implements scrip-code resolution, multi-endpoint fallback, date normalization,
    and strict semantic validation.
    """

    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update(_BSE_HEADERS)

        # Telemetry & Audit state
        self.http_success_count: int = 0
        self.http_error_count: int = 0
        self.parse_success_count: int = 0
        self.semantically_valid_count: int = 0
        self.last_status: Dict[str, str] = {}
        self.last_raw_count: Dict[str, int] = {}
        self.last_usable_count: Dict[str, int] = {}
        self.last_error: Dict[str, Optional[str]] = {}

    def resolve_bse_scrip_code(self, symbol: str) -> Optional[str]:
        """Resolves BSE 6-digit scrip code for the given security symbol."""
        sym = str(symbol).strip().upper()
        if sym.isdigit() and len(sym) == 6:
            return sym

        if sym in _STATIC_SCRIP_CODE_MAP:
            return _STATIC_SCRIP_CODE_MAP[sym]

        # Check SecurityIdentityResolver if available
        try:
            from app.security_identity_resolver import SecurityIdentityResolver
            resolver = SecurityIdentityResolver()
            sec = resolver.resolve(sym)
            if sec and sec.bse_scrip_code:
                return str(sec.bse_scrip_code).strip()
        except Exception:
            pass

        return None

    def fetch_raw_financials(self, symbol: str) -> List[RawFinancialRecord]:
        """
        Fetches corporate financial results from BSE India API for symbol.
        Returns list of semantically valid RawFinancialRecords.
        """
        scrip_code = self.resolve_bse_scrip_code(symbol)
        if not scrip_code:
            self.last_status[symbol] = "BSE_SYMBOL_NOT_FOUND"
            self.last_raw_count[symbol] = 0
            self.last_usable_count[symbol] = 0
            self.last_error[symbol] = f"No BSE scrip code resolvable for {symbol}"
            logger.debug(f"[BSE] {symbol}: No BSE scrip code resolvable.")
            return []

        url = f"{_BSE_FIN_RESULTS_URL}?scripcode={scrip_code}&flag=C"
        raw_rows = []

        try:
            resp = self.session.get(url, timeout=_TIMEOUT)
            if resp.status_code == 200:
                self.http_success_count += 1
                try:
                    data = resp.json()
                    if isinstance(data, list):
                        raw_rows = data
                    elif isinstance(data, dict):
                        raw_rows = data.get("Table", data.get("data", data.get("results", [])))
                except Exception:
                    pass
            elif resp.status_code in (404, 400):
                self.last_status[symbol] = "BSE_NO_DATA"
                self.last_raw_count[symbol] = 0
                self.last_usable_count[symbol] = 0
                return []
            else:
                self.http_error_count += 1
                self.last_status[symbol] = "BSE_HTTP_ERROR"
                self.last_error[symbol] = f"HTTP {resp.status_code}"
                return []
        except requests.RequestException as req_err:
            self.http_error_count += 1
            self.last_status[symbol] = "BSE_HTTP_ERROR"
            self.last_error[symbol] = str(req_err)
            logger.debug(f"[BSE] {symbol} (scrip={scrip_code}) HTTP error: {req_err}")
            return []

        # If primary endpoint returned empty, attempt standalone flag or announcements endpoint
        if not raw_rows:
            try:
                url_s = f"{_BSE_FIN_RESULTS_URL}?scripcode={scrip_code}&flag=S"
                resp_s = self.session.get(url_s, timeout=_TIMEOUT)
                if resp_s.status_code == 200:
                    data_s = resp_s.json()
                    if isinstance(data_s, list):
                        raw_rows = data_s
                    elif isinstance(data_s, dict):
                        raw_rows = data_s.get("Table", data_s.get("data", []))
            except Exception:
                pass

        if not raw_rows:
            self.last_status[symbol] = "BSE_NO_DATA"
            self.last_raw_count[symbol] = 0
            self.last_usable_count[symbol] = 0
            return []

        parsed_records = []
        semantically_valid = []
        semantic_errors = []

        for row in raw_rows:
            if not isinstance(row, dict):
                continue
            rec = _parse_bse_row(symbol, row)
            if rec is None:
                continue
            self.parse_success_count += 1
            parsed_records.append(rec)

            is_valid, err_reason = validate_semantic_record(rec)
            if is_valid:
                semantically_valid.append(rec)
                self.semantically_valid_count += 1
            else:
                semantic_errors.append(f"{rec.period_end_date}: {err_reason}")

        self.last_raw_count[symbol] = len(raw_rows)
        self.last_usable_count[symbol] = len(semantically_valid)

        if raw_rows and not semantically_valid:
            # P0 Invariant: HTTP 200 + raw rows > 0 with 0 semantically valid records -> BSE_PARSE_FAILURE
            self.last_status[symbol] = "BSE_PARSE_FAILURE"
            err_summary = "; ".join(semantic_errors[:3])
            logger.warning(
                f"[BSE] {symbol} (scrip={scrip_code}): HTTP 200 + {len(raw_rows)} raw rows returned != data unavailable "
                f"({len(parsed_records)} parsed, 0 semantically valid: {err_summary}). "
                f"Tagging as BSE_PARSE_FAILURE."
            )
        elif semantically_valid:
            self.last_status[symbol] = "BSE_AVAILABLE"
            logger.info(f"[BSE] {symbol} (scrip={scrip_code}): Extracted {len(semantically_valid)} valid records.")
        else:
            self.last_status[symbol] = "BSE_NO_DATA"

        return semantically_valid
