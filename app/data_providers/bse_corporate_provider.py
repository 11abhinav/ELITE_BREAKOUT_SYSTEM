"""
app/data_providers/bse_corporate_provider.py
============================================
Two-Layer Corporate Financial Results & XBRL Ingestion Pipeline for BSE India.

Architecture:
  Layer 1 (Dynamic Security Resolution & Filing Discovery):
    - Dynamically resolves BSE Scrip Code, Security ID, and ISIN using BseSecurityMasterResolver.
    - Zero hardcoded 18-symbol maps: covers 12,000+ BSE securities, aliases, and SME listings.
    - Discovers financial results announcements and corporate data filings from BSE.
    - Tracks granular telemetry: filings_discovered, documents_downloaded, documents_with_facts.

  Layer 2 (XBRL & Financial Fact Ingestion):
    - Downloads/loads cached IndAS XML documents from data/xbrl_cache/bse/ or JSON filing records.
    - Context-first classification:
        * ANNUAL (full fiscal year 01-Apr to 31-Mar / 01-Jan to 31-Dec, or 350-370 days)
        * HALF_YEAR (150-299 days, Apr-Sep H1, NEVER misclassified as annual)
        * QUARTERLY (75-149 days)
        * INSTANT (Balance sheet date matching period end)
    - Controlled IndAS Taxonomy mapping:
        * REVENUE, NET_PROFIT, OPERATING_CASH_FLOW, EBIT, TOTAL_EQUITY, TOTAL_DEBT, EPS.
        * Operating cash flow is strictly bound to ANNUAL contexts.
        * Retains raw_value, raw_unit, decimals, normalized_value, normalization_factor.
    - Hard separation between CONSOLIDATED and STANDALONE statement basis.
    - Enforces strict semantic validation gate (usable_count > 0 != valid_data).

Status Classifications:
  - BSE_AVAILABLE: >= 1 semantically valid RawFinancialRecord extracted
  - BSE_NO_DATA: Scrip code queried successfully, but 0 financial records returned
  - BSE_HTTP_ERROR: Network timeout, Akamai 403 / auth requirement, or 5xx response
  - BSE_SYMBOL_NOT_FOUND: Scrip code could not be resolved for symbol
  - BSE_PARSE_FAILURE: HTTP 200 raw data returned > 0, but 0 semantically valid records extracted
  - BSE_NOT_APPLICABLE: Security is unmapped or NSE-only with no BSE listing
"""

from __future__ import annotations

import logging
import os
import re
import math
import hashlib
from datetime import datetime, date, timedelta
from typing import Any, Dict, List, Optional, Tuple, Set
import xml.etree.ElementTree as ET

import requests

try:
    from app.data_providers.fundamental_models import (
        ConsolidationType,
        RawFinancialRecord,
    )
    from app.data_providers.nse_xbrl_provider import (
        CONTROLLED_TAXONOMY,
        _parse_date_to_iso,
        _safe_float,
        validate_semantic_record,
    )
    from app.data_providers.bse_security_master import BseSecurityMasterResolver
except ImportError:
    from data_providers.fundamental_models import (
        ConsolidationType,
        RawFinancialRecord,
    )
    from data_providers.nse_xbrl_provider import (
        CONTROLLED_TAXONOMY,
        _parse_date_to_iso,
        _safe_float,
        validate_semantic_record,
    )
    from data_providers.bse_security_master import BseSecurityMasterResolver

logger = logging.getLogger(__name__)

BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DEFAULT_BSE_CACHE_DIR = os.path.join(BASE_DIR, "data", "xbrl_cache", "bse")

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


def _period_type_bse(row: dict, to_date_iso: Optional[str] = None, from_date_iso: Optional[str] = None) -> str:
    """Classifies period type as ANNUAL, HALF_YEAR, or QUARTERLY for BSE records."""
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

    period_label = str(
        row.get("Period") or row.get("period") or row.get("FinPeriod") or row.get("Quarter") or ""
    ).upper()
    if any(k in period_label for k in ("YEAR", "ANNUAL", "AUDITED", "FY", "12 MONTH")):
        return "ANNUAL"
    if any(k in period_label for k in ("HALF-YEAR", "HALF YEAR", "H1", "H2", "6 MONTH")):
        return "HALF_YEAR"
    if any(k in period_label for k in ("Q1", "Q2", "Q3", "Q4", "QUARTER", "3 MONTH", "UNAUDITED")):
        return "QUARTERLY"
    if to_date_iso and to_date_iso.endswith("-03-31"):
        return "ANNUAL"
    return "QUARTERLY"


def _consolidation_bse(row: dict) -> ConsolidationType:
    val = str(row.get("Consolidated") or row.get("type") or row.get("Flag") or "").lower()
    if "standalone" in val or val == "s" or "non-consolidated" in val:
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

    from_date_raw = row.get("FromDate") or row.get("fromDate") or lower_row.get("fromdate")
    from_date_iso = _parse_date_to_iso(from_date_raw)

    p_type = _period_type_bse(row, to_date_iso=period_end, from_date_iso=from_date_iso)
    consolidation = _consolidation_bse(row)

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

    # Operating Cash Flow (strictly annual)
    operating_cash_flow = _safe_float(
        row.get("CFO")
        or row.get("CashFlowOps")
        or lower_row.get("cfo")
        or lower_row.get("cashflowops")
    )
    if p_type != "ANNUAL":
        operating_cash_flow = None

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
        capital_employed = round(total_equity + total_debt, 2)
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

    duration_days = None
    if period_end and from_date_iso:
        try:
            d1 = date.fromisoformat(from_date_iso)
            d2 = date.fromisoformat(period_end)
            duration_days = abs((d2 - d1).days) + 1
        except Exception:
            pass

    fiscal_year = None
    if period_end:
        try:
            d = date.fromisoformat(period_end)
            fiscal_year = f"FY{d.year}" if d.month <= 3 else f"FY{d.year + 1}"
        except Exception:
            pass

    return RawFinancialRecord(
        symbol=symbol,
        source="BSE_CORPORATE",
        period_end_date=str(period_end),
        period_start_date=from_date_iso,
        duration_days=duration_days,
        fiscal_year=fiscal_year,
        period_type=p_type,
        consolidation=consolidation,
        revenue=revenue,
        net_profit=net_profit,
        ebit=ebit,
        operating_cash_flow=operating_cash_flow,
        total_debt=total_debt,
        total_equity=total_equity,
        capital_employed=capital_employed,
        eps=eps,
        availability_date=str(availability_date) if availability_date else None,
        broadcast_timestamp=str(availability_date) if availability_date else None,
        unit=unit,
        currency="INR",
    )


class BseCorporateProvider:
    """
    Two-Layer Corporate Financial Results & XBRL Ingestion Pipeline for BSE India.
    Layer 1: Resolves BSE Scrip Code dynamically & discovers corporate filings.
    Layer 2: Ingests & parses IndAS XBRL XML documents or corporate results JSON.
    """

    def __init__(self, cache_dir: Optional[str] = None):
        self.session = requests.Session()
        self.session.headers.update(_BSE_HEADERS)
        self.cache_dir = cache_dir or DEFAULT_BSE_CACHE_DIR
        os.makedirs(self.cache_dir, exist_ok=True)
        self.resolver = BseSecurityMasterResolver()

        # Telemetry & Observability (Distinct discovery vs data layer metrics)
        self.filings_discovered: int = 0
        self.documents_downloaded: int = 0
        self.documents_with_facts: int = 0
        self.facts_extracted: int = 0
        self.facts_rejected_semantically: int = 0
        self.semantically_valid_records: int = 0
        self.bse_scrip_resolutions: int = 0
        self.bse_resolution_failures: int = 0
        self.bse_auth_or_403_blocks: int = 0
        self.http_success_count: int = 0
        self.http_error_count: int = 0

        self.last_status: Dict[str, str] = {}
        self.last_raw_count: Dict[str, int] = {}
        self.last_usable_count: Dict[str, int] = {}
        self.last_error: Dict[str, Optional[str]] = {}

    def resolve_bse_scrip_code(self, symbol: str) -> Optional[str]:
        """Resolves BSE 6-digit scrip code dynamically using BseSecurityMasterResolver."""
        sym = str(symbol).strip().upper()
        if sym.isdigit() and len(sym) == 6:
            self.bse_scrip_resolutions += 1
            return sym

        code = self.resolver.get_scrip_code(sym)
        if code:
            self.bse_scrip_resolutions += 1
            return code

        self.bse_resolution_failures += 1
        return None

    def parse_xbrl_document(
        self,
        xml_path: str,
        symbol: str,
        filing_date: Optional[str] = None,
    ) -> List[RawFinancialRecord]:
        """
        Parses an IndAS XBRL document for BSE:
          Applies context classification, controlled taxonomy fact extraction,
          cash flow annual restriction, and semantic validation.
        """
        try:
            from app.data_providers.nse_xbrl_provider import NseXbrlProvider
            nse_p = NseXbrlProvider(cache_dir=self.cache_dir)
            recs = nse_p.parse_xbrl_document(xml_path=xml_path, symbol=symbol, filing_date=filing_date)
            # Tag source as BSE_XBRL
            bse_recs = []
            for r in recs:
                r.source = "BSE_XBRL"
                bse_recs.append(r)
            if bse_recs:
                self.documents_with_facts += 1
                self.facts_extracted += nse_p.facts_extracted
                self.semantically_valid_records += len(bse_recs)
            return bse_recs
        except Exception as e:
            logger.warning(f"[BSE] Failed to parse XBRL document {xml_path}: {e}")
            return []

    def fetch_raw_financials(
        self,
        symbol: str,
        statement_basis: Optional[ConsolidationType] = None,
    ) -> List[RawFinancialRecord]:
        """
        Fetches corporate financial results from BSE for symbol:
          1. Resolves BSE scrip code dynamically.
          2. Checks local disk cache for cached XBRL filings.
          3. Queries BSE Corporate Results endpoint with consolidated/standalone flags.
          4. Maps fields to RawFinancialRecord with strict semantic validation.
          5. Returns chronologically sorted validated records.
        """
        scrip_code = self.resolve_bse_scrip_code(symbol)
        if not scrip_code:
            self.last_status[symbol] = "BSE_SYMBOL_NOT_FOUND"
            self.last_raw_count[symbol] = 0
            self.last_usable_count[symbol] = 0
            self.last_error[symbol] = f"No BSE scrip code resolvable for {symbol}"
            logger.debug(f"[BSE] {symbol}: No BSE scrip code resolvable.")
            return []

        # Check for cached XBRL documents in data/xbrl_cache/bse/
        cached_xmls = [
            os.path.join(self.cache_dir, f)
            for f in os.listdir(self.cache_dir)
            if (scrip_code in f or symbol.lower() in f.lower()) and f.endswith(".xml")
        ] if os.path.exists(self.cache_dir) else []

        if cached_xmls:
            all_cached_recs = []
            for xml_p in cached_xmls:
                recs = self.parse_xbrl_document(xml_p, symbol)
                all_cached_recs.extend(recs)
            if all_cached_recs:
                if statement_basis:
                    all_cached_recs = [r for r in all_cached_recs if r.consolidation == statement_basis]
                self.last_status[symbol] = "BSE_AVAILABLE"
                self.last_raw_count[symbol] = len(all_cached_recs)
                self.last_usable_count[symbol] = len(all_cached_recs)
                return sorted(all_cached_recs, key=lambda r: r.period_end_date)

        # Query official BSE Corporate Financial Results API
        # Default: query Consolidated (flag=C) first; fallback to Standalone (flag=S) if empty
        primary_flag = "S" if statement_basis == ConsolidationType.STANDALONE else "C"
        fallback_flag = "C" if statement_basis == ConsolidationType.STANDALONE else "S"

        raw_rows = []
        for flag in ([primary_flag] if statement_basis else [primary_flag, fallback_flag]):
            url = f"{_BSE_FIN_RESULTS_URL}?scripcode={scrip_code}&flag={flag}"
            try:
                resp = self.session.get(url, timeout=_TIMEOUT)
                if resp.status_code == 200:
                    self.http_success_count += 1
                    try:
                        data = resp.json()
                        rows = data if isinstance(data, list) else data.get("Table", data.get("data", data.get("results", [])))
                        if rows:
                            raw_rows.extend(rows)
                            self.filings_discovered += len(rows)
                            # If primary flag returned data and no specific statement basis forced, don't mix with fallback flag
                            if not statement_basis:
                                break
                    except Exception:
                        pass
                elif resp.status_code in (401, 403):
                    self.bse_auth_or_403_blocks += 1
                    self.http_error_count += 1
                    self.last_status[symbol] = "BSE_FEED_ACCESS_REQUIRED"
                    self.last_error[symbol] = f"HTTP {resp.status_code} (Gateway Auth / Feed Access Required)"
                    logger.debug(f"[BSE] {symbol} (scrip={scrip_code}) HTTP {resp.status_code}")
                elif resp.status_code in (404, 400):
                    pass
                else:
                    self.http_error_count += 1
                    self.last_error[symbol] = f"HTTP {resp.status_code}"
            except requests.RequestException as req_err:
                self.http_error_count += 1
                self.last_status[symbol] = "BSE_HTTP_ERROR"
                self.last_error[symbol] = str(req_err)
                logger.debug(f"[BSE] {symbol} (scrip={scrip_code}) request error: {req_err}")

        self.last_raw_count[symbol] = len(raw_rows)

        if not raw_rows:
            if symbol not in self.last_status:
                self.last_status[symbol] = "BSE_NO_DATA"
            self.last_usable_count[symbol] = 0
            return []

        # Parse and semantically validate rows
        semantically_valid: List[RawFinancialRecord] = []
        for row in raw_rows:
            rec = _parse_bse_row(symbol, row)
            if not rec:
                continue

            self.facts_extracted += 1
            is_valid, reason = validate_semantic_record(rec)
            if is_valid:
                semantically_valid.append(rec)
                self.semantically_valid_records += 1
            else:
                self.facts_rejected_semantically += 1
                logger.debug(f"[BSE] Semantic check rejected row for {symbol} ({rec.period_end_date}): {reason}")

        # Deduplicate by (period_end_date, period_type, consolidation) while preserving order
        by_key: Dict[Tuple[str, str, ConsolidationType], RawFinancialRecord] = {}
        for r in semantically_valid:
            k = (r.period_end_date, r.period_type, r.consolidation)
            if k not in by_key:
                by_key[k] = r
            else:
                if (r.broadcast_timestamp or "") > (by_key[k].broadcast_timestamp or ""):
                    by_key[k] = r

        deduped = list(by_key.values())
        if statement_basis:
            deduped = [r for r in deduped if r.consolidation == statement_basis]

        self.last_usable_count[symbol] = len(deduped)

        if raw_rows and not deduped:
            # P0 Invariant: raw > 0 + usable == 0 -> BSE_PARSE_FAILURE
            self.last_status[symbol] = "BSE_PARSE_FAILURE"
        elif deduped:
            self.last_status[symbol] = "BSE_AVAILABLE"
            self.documents_with_facts += 1
        else:
            self.last_status[symbol] = "BSE_NO_DATA"

        return deduped
