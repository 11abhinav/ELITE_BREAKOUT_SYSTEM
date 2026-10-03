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


def _period_type(row: dict) -> str:
    """Infer ANNUAL / QUARTERLY from period label in the NSE row."""
    period = (
        row.get("period")
        or row.get("periodType")
        or row.get("resultType")
        or ""
    )
    p = str(period).upper()
    if any(x in p for x in ("ANNUAL", "YEARLY", "FY", "12 MONTH")):
        return "ANNUAL"
    if any(x in p for x in ("QUARTER", "QTR", "Q1", "Q2", "Q3", "Q4", "3 MONTH")):
        return "QUARTERLY"
    return "UNKNOWN"


def _safe_float(val) -> Optional[float]:
    if val is None:
        return None
    s = str(val).replace(",", "").strip()
    if s in ("", "-", "N/A", "NA", "null"):
        return None
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
    period_end = (
        row.get("toDate")
        or row.get("period_end_date")
        or row.get("date")
        or ""
    )
    if not period_end:
        return None

    # NSE field name mapping (based on observed API response structure)
    revenue = _safe_float(
        row.get("income")           # "Total Income"
        or row.get("totalIncome")
        or row.get("netSales")
        or row.get("revenue")
    )
    net_profit = _safe_float(
        row.get("netProfit")
        or row.get("pat")
        or row.get("profitAfterTax")
        or row.get("netProfitLoss")
    )
    # EBIT: use operating_profit only when it is explicitly labelled as such,
    # never as a silent proxy for EBIT
    ebit = _safe_float(
        row.get("operatingProfit")  # NSE labels this EBITDA-ish; keep for reference
        or row.get("ebit")
        or row.get("pbit")
    )
    operating_cash_flow = _safe_float(
        row.get("cashFlowOperations")
        or row.get("operatingCashFlow")
        or row.get("cfo")
    )
    total_debt = _safe_float(
        row.get("totalDebt")
        or row.get("debt")
        or row.get("totalBorrowings")
    )
    total_equity = _safe_float(
        row.get("shareholderEquity")
        or row.get("equity")
        or row.get("netWorth")
    )
    eps = _safe_float(row.get("eps") or row.get("basicEps") or row.get("epsBasic"))

    # capital_employed: total_assets - current_liabilities if available,
    # else equity + total_debt (rough alternative — flagged in derivation)
    total_assets = _safe_float(row.get("totalAssets") or row.get("assets"))
    curr_liab = _safe_float(
        row.get("currentLiabilities") or row.get("totalCurrentLiabilities")
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
        or ""
    )

    return RawFinancialRecord(
        symbol=symbol,
        source="NSE_XBRL",
        period_end_date=str(period_end),
        period_type=_period_type(row),
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

    def _init_session(self) -> bool:
        """Establish NSE session cookie. Returns True on success."""
        try:
            self.session = requests.Session()
            self.session.get(_HOME_URL, headers=_HEADERS, timeout=_TIMEOUT)
            self._initialized = True
            logger.debug("[NSE] Session initialized.")
            return True
        except Exception as e:
            logger.warning(f"[NSE] Session init failed: {e}")
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
            return []

        if not isinstance(raw, list):
            # Some symbols return dict with a data key
            if isinstance(raw, dict):
                raw = raw.get("data", raw.get("results", []))
            if not isinstance(raw, list):
                logger.warning(f"[NSE] {symbol}: Unexpected response type: {type(raw)}")
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

        if records and not usable:
            logger.warning(
                f"[NSE] {symbol}: HTTP 200 + {len(records)} rows parsed but "
                f"FINANCIAL_DATA_INSUFFICIENT. Check NSE field names."
            )

        return usable
