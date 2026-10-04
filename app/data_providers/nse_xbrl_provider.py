"""
app/data_providers/nse_xbrl_provider.py
=========================================
Fetches raw corporate financial results from NSE India API and maps
every parseable field to RawFinancialRecord.

NSE endpoint:
  GET https://www.nseindia.com/api/corporates-financial-results
      ?index=equities&symbol=<SYMBOL>

Session management:
  NSE requires a valid browser-like session cookie obtained by first visiting
  the homepage. Cookies expire; mid-sweep expiry causes silent 401/403.
  This provider implements bounded session-refresh + retry.

Provider status telemetry:
  http_success_count        - 200 responses
  http_auth_fail_count      - 401 / 403 responses
  parse_success_count       - rows where >= 1 financial field parsed
  financial_data_present    - records with all core fields present
  session_refresh_count     - number of session re-initializations
  retry_count               - bounded retry attempts

A 200 OK alone does NOT equal VERIFIED.
At least one core financial field (revenue / PAT / EBIT) must be non-None.
"""

from __future__ import annotations

import logging
import time
from typing import Dict, List, Optional

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


def _consolidation(row: dict) -> ConsolidationType:
    val = (
        row.get("consolidated")
        or row.get("type")
        or row.get("consolidationType")
        or ""
    )
    if "standalone" in str(val).lower():
        return ConsolidationType.STANDALONE
    return ConsolidationType.CONSOLIDATED


def _parse_date_to_iso(val: Any) -> Optional[str]:
    """Parse various exchange date representations to strict ISO YYYY-MM-DD."""
    if not val:
        return None
    s = str(val).strip()
    if len(s) == 10 and s[4] == "-" and s[7] == "-":
        return s
    from datetime import datetime
    for fmt in (
        "%Y-%m-%d", "%d-%b-%Y", "%d-%B-%Y", "%d/%m/%Y", "%d-%m-%Y",
        "%d-%b-%y", "%b-%Y", "%B-%Y", "%Y%m%d", "%Y-%m-%dT%H:%M:%S"
    ):
        try:
            return datetime.strptime(s.split("T")[0] if "T" in s else s, fmt).date().isoformat()
        except Exception:
            pass
    return None


def _period_type(row: dict, to_date_iso: Optional[str] = None, from_date_iso: Optional[str] = None) -> str:
    """Infer ANNUAL / QUARTERLY from period label or date span in the NSE row."""
    # 1. Date span inference if both dates are parseable
    if to_date_iso and from_date_iso:
        try:
            from datetime import date
            d1 = date.fromisoformat(from_date_iso)
            d2 = date.fromisoformat(to_date_iso)
            span = abs((d2 - d1).days)
            if span >= 300:
                return "ANNUAL"
            if span <= 125:
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
    if any(x in p for x in ("ANNUAL", "YEARLY", "FY", "12 MONTH", "AUDITED")):
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
    # [RULE 67 CHANGE-RATIONALE: Support standard financial statement parenthesis negative numbers: (12.34) -> -12.34]
    if s.startswith("(") and s.endswith(")"):
        s = "-" + s[1:-1].strip()
    try:
        return float(s)
    except (TypeError, ValueError):
        return None


def _parse_row(symbol: str, row: dict) -> Optional[RawFinancialRecord]:
    """
    Parse one NSE financial-results row into a RawFinancialRecord.
    Returns None only if period_end_date is missing (unidentifiable row).
    Returns a record even with partial data; the caller filters by usability.
    """
    lower_row = {str(k).lower().strip(): v for k, v in row.items()}

    period_end_raw = (
        row.get("toDate")
        or row.get("to_date")
        or row.get("todate")
        or row.get("periodEnd")
        or row.get("period_end")
        or row.get("period_end_date")
        or row.get("quarterEnding")
        or row.get("quarter_ending")
        or row.get("date")
        or row.get("reFndToDate")
        or row.get("reFndPeriodEnd")
        or row.get("yearEnding")
        or row.get("financialYear")
        or lower_row.get("todate")
        or lower_row.get("periodend")
        or lower_row.get("quarterending")
        or lower_row.get("refndtodate")
        or lower_row.get("refndperiodend")
        or lower_row.get("yearending")
        or lower_row.get("date")
        or ""
    )
    period_end = _parse_date_to_iso(period_end_raw)
    if not period_end:
        # Fallback to raw string if regex fails but non-empty
        period_end = str(period_end_raw).strip() if period_end_raw else None
    if not period_end:
        return None

    from_date_raw = (
        row.get("fromDate")
        or row.get("from_date")
        or row.get("fromdate")
        or row.get("reFndFromDate")
        or lower_row.get("fromdate")
        or lower_row.get("refndfromdate")
    )
    from_date_iso = _parse_date_to_iso(from_date_raw)

    # [RULE 67 CHANGE-RATIONALE: Exhaustive field name mapping covering official NSE XBRL API,
    # corporate financial results endpoint variations, and case-insensitive lower dictionary keys.]
    revenue = _safe_float(
        row.get("reFndRevOps")
        or row.get("reFndTotIncm")
        or row.get("revenueFromOperations")
        or row.get("totalRevenue")
        or row.get("totalIncome")
        or row.get("netSales")
        or row.get("income")
        or row.get("revenue")
        or row.get("totInc")
        or row.get("sales")
        or row.get("turnover")
        or lower_row.get("refndrevops")
        or lower_row.get("refndtotincm")
        or lower_row.get("revenuefromoperations")
        or lower_row.get("totalrevenue")
        or lower_row.get("totalincome")
        or lower_row.get("netsales")
        or lower_row.get("revenue")
        or lower_row.get("sales")
        or lower_row.get("income")
        or lower_row.get("turnover")
    )
    net_profit = _safe_float(
        row.get("reFndNetPftLoss")
        or row.get("netProfitLoss")
        or row.get("profitAfterTax")
        or row.get("netProfit")
        or row.get("profitLoss")
        or row.get("pat")
        or row.get("netProfitForPeriod")
        or lower_row.get("refndnetpftloss")
        or lower_row.get("profitaftertax")
        or lower_row.get("netprofit")
        or lower_row.get("profitloss")
        or lower_row.get("pat")
        or lower_row.get("netprofitforperiod")
    )
    # EBIT: use operating_profit only when explicitly labelled or derived
    ebit = _safe_float(
        row.get("reFndPbit")
        or row.get("pbit")
        or row.get("pbdit")
        or row.get("operatingProfit")
        or row.get("ebit")
        or lower_row.get("refndpbit")
        or lower_row.get("pbit")
        or lower_row.get("operatingprofit")
        or lower_row.get("ebit")
    )
    operating_cash_flow = _safe_float(
        row.get("reFndCfo")
        or row.get("cashFlowFromOperatingActivities")
        or row.get("cashFlowOperations")
        or row.get("operatingCashFlow")
        or row.get("cfo")
        or lower_row.get("refndcfo")
        or lower_row.get("operatingcashflow")
        or lower_row.get("cashflowfromoperatingactivities")
        or lower_row.get("cfo")
    )
    total_debt = _safe_float(
        row.get("reFndBorrowings")
        or row.get("totalBorrowings")
        or row.get("totalDebt")
        or row.get("borrowings")
        or row.get("debt")
        or lower_row.get("refndborrowings")
        or lower_row.get("totalborrowings")
        or lower_row.get("totaldebt")
        or lower_row.get("borrowings")
        or lower_row.get("debt")
    )
    total_equity = _safe_float(
        row.get("shareholderEquity")
        or row.get("equity")
        or row.get("netWorth")
        or row.get("equityShareCapital")
        or lower_row.get("shareholderequity")
        or lower_row.get("networth")
        or lower_row.get("equitysharecapital")
        or lower_row.get("equity")
    )
    eps = _safe_float(
        row.get("reFndBscEps")
        or row.get("reFndDilEps")
        or row.get("basicEps")
        or row.get("dilutedEps")
        or row.get("eps")
        or row.get("epsBasic")
        or row.get("earningsPerShare")
        or lower_row.get("refndbsceps")
        or lower_row.get("basiceps")
        or lower_row.get("eps")
        or lower_row.get("dilutedeps")
    )

    total_assets = _safe_float(row.get("totalAssets") or row.get("assets") or lower_row.get("totalassets") or lower_row.get("assets"))
    curr_liab = _safe_float(
        row.get("currentLiabilities") or row.get("totalCurrentLiabilities") or lower_row.get("currentliabilities") or lower_row.get("totalcurrentliabilities")
    )
    if total_assets is not None and curr_liab is not None:
        capital_employed = total_assets - curr_liab
    elif total_equity is not None and total_debt is not None:
        capital_employed = total_equity + total_debt
    else:
        capital_employed = None

    availability_date = (
        row.get("broadcastDate")
        or row.get("filingDate")
        or row.get("submissionDate")
        or lower_row.get("broadcastdate")
        or lower_row.get("filingdate")
        or ""
    )

    return RawFinancialRecord(
        symbol=symbol,
        source="NSE_XBRL",
        period_end_date=str(period_end),
        period_type=_period_type(row, to_date_iso=period_end, from_date_iso=from_date_iso),
        consolidation=_consolidation(row),
        revenue=revenue,
        net_profit=net_profit,
        ebit=ebit,
        operating_cash_flow=operating_cash_flow,
        total_debt=total_debt,
        total_equity=total_equity,
        capital_employed=capital_employed,
        eps=eps,
        availability_date=str(availability_date) if availability_date else None,
        unit=str(row.get("unit", "cr")),
        currency="INR",
    )



class NseXbrlProvider:
    """
    Fetches raw corporate financial results from NSE India API.
    Implements session-refresh + bounded retry for 401/403 responses.
    All telemetry counters are public for observability.
    """

    def __init__(self):
        self.session = requests.Session()
        self._initialized = False

        # Telemetry
        self.http_success_count:      int = 0
        self.http_auth_fail_count:    int = 0
        self.parse_success_count:     int = 0
        self.financial_data_present:  int = 0
        self.session_refresh_count:   int = 0
        self.retry_count:             int = 0
        self.nse_401_count:           int = 0
        self.nse_403_count:           int = 0
        self.last_status: Dict[str, str] = {}
        self.last_raw_count: Dict[str, int] = {}
        self.last_usable_count: Dict[str, int] = {}

    def _init_session(self) -> bool:
        """Establish NSE session cookie. Returns True on success."""
        try:
            try:
                from app.constituent_service import get_nse_session
            except ImportError:
                from constituent_service import get_nse_session
            self.session = get_nse_session()
            self._initialized = True
            logger.debug("[NSE] Session initialized with curl_cffi chrome impersonation.")
            return True
        except Exception as e:
            try:
                self.session = requests.Session()
                self.session.get(_HOME_URL, headers=_HEADERS, timeout=_TIMEOUT)
                self._initialized = True
                logger.debug("[NSE] Session initialized with standard requests.")
                return True
            except Exception as e2:
                logger.warning(f"[NSE] Session init failed: {e2}")
                self._initialized = False
                return False

    def _get(self, url: str, symbol: str) -> Optional[list]:
        """
        GET with session-refresh + bounded retry.
        Returns parsed JSON list or None.
        """
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
                        logger.warning(
                            f"[NSE] {symbol}: HTTP {res.status_code} — "
                            f"refreshing session (refresh #{session_refreshes + 1})."
                        )
                        self._init_session()
                        self.session_refresh_count += 1
                        session_refreshes += 1
                        self.retry_count += 1
                        time.sleep(1.0)
                        continue
                    else:
                        logger.error(
                            f"[NSE] {symbol}: PROVIDER_UNAVAILABLE after "
                            f"{session_refreshes} session refreshes."
                        )
                        return None

                if res.status_code == 429:
                    wait = 2.0 * (attempt + 1)
                    logger.warning(f"[NSE] {symbol}: Rate limited. Waiting {wait}s.")
                    time.sleep(wait)
                    self.retry_count += 1
                    continue

                logger.warning(f"[NSE] {symbol}: HTTP {res.status_code}: {res.text[:200]}")
                return None

            except requests.exceptions.Timeout:
                logger.warning(f"[NSE] {symbol}: Timeout (attempt {attempt+1}/{_MAX_RETRIES}).")
                self.retry_count += 1
                if attempt < _MAX_RETRIES - 1:
                    time.sleep(1.5)
            except Exception as e:
                logger.error(f"[NSE] {symbol}: Request error: {e}")
                return None

        return None

    def fetch_raw_financials(self, symbol: str) -> List[RawFinancialRecord]:
        """
        Fetches financial results for symbol from NSE India API.
        Returns list of RawFinancialRecord with all parseable fields populated.
        An empty list means PROVIDER_UNAVAILABLE or HTTP_FAIL — not DATA_INSUFFICIENT.
        A record with all-None financials means PARSE_SUCCESS but FINANCIAL_DATA_INSUFFICIENT.
        """
        url = f"{_BASE_URL}?index=equities&symbol={symbol}"
        raw = self._get(url, symbol)

        if raw is None:
            logger.warning(f"[NSE] {symbol}: No response data (session/network failure).")
            self.last_status[symbol] = "PROVIDER_UNAVAILABLE"
            return []

        if not isinstance(raw, list):
            # Some symbols return dict with a data key
            if isinstance(raw, dict):
                raw = raw.get("data", raw.get("results", []))
            if not isinstance(raw, list):
                logger.warning(f"[NSE] {symbol}: Unexpected response type: {type(raw)}")
                self.last_status[symbol] = "SOURCE_DATA_PRESENT_PARSE_OR_MAPPING_FAILURE"
                return []

        records = []
        for row in raw:
            rec = _parse_row(symbol, row)
            if rec is None:
                continue
            self.parse_success_count += 1

            # Determine if this record has usable financial data
            has_data = any(
                v is not None
                for v in [rec.revenue, rec.net_profit, rec.ebit, rec.operating_cash_flow]
            )
            if has_data:
                self.financial_data_present += 1

            records.append(rec)

        usable = [
            r for r in records
            if any(
                v is not None
                for v in [r.revenue, r.net_profit, r.ebit, r.operating_cash_flow, r.total_debt]
            )
        ]

        logger.info(
            f"[NSE] {symbol}: {len(usable)}/{len(records)} records with financial data "
            f"(401s={self.nse_401_count}, 403s={self.nse_403_count}, "
            f"refreshes={self.session_refresh_count}, retries={self.retry_count})"
        )

        self.last_raw_count[symbol] = len(raw)
        self.last_usable_count[symbol] = len(usable)

        if raw and not usable:
            # [RULE 67 CHANGE-RATIONALE: HTTP 200 + raw rows returned > 0 with usable == 0.
            # Explicitly classify as PARSER_OR_FIELD_MAPPING_FAILURE. Never DATA_UNAVAILABLE.]
            self.last_status[symbol] = "PARSER_OR_FIELD_MAPPING_FAILURE"
            logger.warning(
                f"[NSE] {symbol}: HTTP 200 + {len(raw)} raw rows returned != data unavailable "
                f"({len(records)} parsed, {len(usable)} usable). "
                f"Tagging as PARSER_OR_FIELD_MAPPING_FAILURE."
            )
        elif usable:
            self.last_status[symbol] = "PARSE_SUCCESS"
        else:
            self.last_status[symbol] = "NO_DATA_RETURNED"

        return usable
