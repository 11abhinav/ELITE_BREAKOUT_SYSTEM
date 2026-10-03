"""
app/data_providers/upstox_fundamentals_provider.py
====================================================
Fetches real fundamental financial data from the Upstox Company Fundamentals API
(introduced May 2026) and maps it to RawFinancialRecord.

Endpoints used:
  GET /fundamentals/{isin}/income-statement
  GET /fundamentals/{isin}/balance-sheet
  GET /fundamentals/{isin}/cash-flow
  GET /fundamentals/{isin}/key-ratios  (QA / cross-check only)

Upstox API docs:
  https://upstox.com/developer/api-documentation/announcements/company-fundamentals-api/
  https://upstox.com/developer/api-documentation/get-income-statement/
  https://upstox.com/developer/api-documentation/get-balance-sheet/
  https://upstox.com/developer/api-documentation/get-cash-flow/
  https://upstox.com/developer/api-documentation/get-key-ratios/

CANONICAL DERIVATION RULES
---------------------------
EBIT  = operating_income (if explicitly labelled), NOT operating_profit proxy.
EBITDA is NOT derived here; it is calculated by the canonical builder from
        (EBIT + D&A from cash-flow schedule).  Never invent EBITDA = operating_profit.
capital_employed = total_assets - current_liabilities (from balance sheet).
"""

from __future__ import annotations

import logging
import os
import time
from typing import Dict, List, Optional, Tuple

import requests

from data_providers.fundamental_models import (
    ConsolidationType,
    FundamentalStatus,
    RawFinancialRecord,
    StatementType,
)

logger = logging.getLogger(__name__)

_BASE = "https://api.upstox.com/v2/fundamentals"
_TIMEOUT = 15
_MAX_RETRIES = 3
_RETRY_BACKOFF = [1.0, 2.0, 4.0]


def _consolidation(raw: str) -> ConsolidationType:
    if raw and "standalone" in raw.lower():
        return ConsolidationType.STANDALONE
    return ConsolidationType.CONSOLIDATED


def _period_type(period_str: str) -> str:
    """Infer ANNUAL / QUARTERLY from period label returned by Upstox."""
    if not period_str:
        return "UNKNOWN"
    p = period_str.upper()
    if any(x in p for x in ("FY", "ANNUAL", "YEARLY", "YEAR")):
        return "ANNUAL"
    if any(x in p for x in ("Q1", "Q2", "Q3", "Q4", "QTR", "QUARTER")):
        return "QUARTERLY"
    return "UNKNOWN"


def _safe_float(val) -> Optional[float]:
    """Convert Upstox value (may be string / None / int) to float."""
    if val is None:
        return None
    try:
        return float(val)
    except (TypeError, ValueError):
        return None


class UpstoxFundamentalsProvider:
    """
    Fetches structured fundamental data from the Upstox Company Fundamentals API.
    Maps raw API fields to RawFinancialRecord using explicit, documented field names.
    Provides telemetry counters for observability.
    """

    def __init__(self):
        self.token = os.environ.get("UPSTOX_ACCESS_TOKEN")
        self.base_url = _BASE
        # Telemetry
        self.api_call_count: int = 0
        self.retry_count: int = 0
        self.error_count: int = 0

    def _headers(self) -> Dict[str, str]:
        return {
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/json",
        }

    def _get(self, url: str) -> Optional[dict]:
        """GET with bounded retry + exponential backoff."""
        if not self.token:
            return None
        for attempt in range(_MAX_RETRIES):
            try:
                self.api_call_count += 1
                res = requests.get(url, headers=self._headers(), timeout=_TIMEOUT)
                if res.status_code == 200:
                    return res.json()
                if res.status_code in (401, 403):
                    # Token expired — nothing to retry without re-auth
                    logger.warning(f"[UPSTOX] Auth error {res.status_code} on {url}")
                    self.error_count += 1
                    return None
                if res.status_code == 429:
                    wait = _RETRY_BACKOFF[min(attempt, len(_RETRY_BACKOFF) - 1)]
                    logger.warning(f"[UPSTOX] Rate limited. Waiting {wait}s...")
                    time.sleep(wait)
                    self.retry_count += 1
                    continue
                logger.warning(f"[UPSTOX] HTTP {res.status_code} on {url}: {res.text[:200]}")
                self.error_count += 1
                return None
            except requests.exceptions.Timeout:
                logger.warning(f"[UPSTOX] Timeout (attempt {attempt+1}) on {url}")
                self.retry_count += 1
                if attempt < _MAX_RETRIES - 1:
                    time.sleep(_RETRY_BACKOFF[attempt])
            except Exception as e:
                logger.error(f"[UPSTOX] Request error on {url}: {e}")
                self.error_count += 1
                return None
        return None

    # ------------------------------------------------------------------
    # Income Statement parsing
    # ------------------------------------------------------------------
    def _parse_income_statements(
        self, isin: str, symbol: str
    ) -> List[RawFinancialRecord]:
        url = f"{self.base_url}/{isin}/income-statement"
        data = self._get(url)
        if not data:
            return []

        records = []
        rows = data.get("data", data) if isinstance(data, dict) else data
        if not isinstance(rows, list):
            rows = []

        for row in rows:
            period_end = row.get("period_end_date") or row.get("date") or row.get("period") or ""
            if not period_end:
                continue

            period_type = _period_type(
                row.get("period_type") or row.get("period_label") or period_end
            )
            consolidation = _consolidation(
                row.get("type") or row.get("consolidation") or ""
            )

            # Revenue — use net_revenue or total_revenue explicitly
            revenue = _safe_float(
                row.get("net_revenue") or row.get("total_revenue") or row.get("revenue")
            )
            net_profit = _safe_float(
                row.get("profit_after_tax")
                or row.get("net_profit")
                or row.get("pat")
            )
            # EBIT: use operating_income if labelled; do NOT use operating_profit as proxy
            ebit = _safe_float(
                row.get("operating_income") or row.get("ebit")
            )
            eps = _safe_float(row.get("eps") or row.get("basic_eps"))

            # Availability date — when Upstox ingested this filing
            availability_date = (
                row.get("filing_date")
                or row.get("availability_date")
                or row.get("published_at")
                or ""
            )

            rec = RawFinancialRecord(
                symbol=symbol,
                source="UPSTOX_API",
                period_end_date=str(period_end),
                period_type=period_type,
                consolidation=consolidation,
                revenue=revenue,
                net_profit=net_profit,
                ebit=ebit,
                eps=eps,
                availability_date=str(availability_date) if availability_date else None,
                unit=str(row.get("unit", "cr")),
                currency=str(row.get("currency", "INR")),
            )
            records.append(rec)

        logger.debug(f"[UPSTOX] Income stmt: {symbol} → {len(records)} records")
        return records

    # ------------------------------------------------------------------
    # Balance Sheet parsing
    # ------------------------------------------------------------------
    def _parse_balance_sheets(
        self, isin: str, symbol: str, income_records: List[RawFinancialRecord]
    ) -> List[RawFinancialRecord]:
        url = f"{self.base_url}/{isin}/balance-sheet"
        data = self._get(url)
        if not data:
            return income_records  # return income records without enrichment

        rows = data.get("data", data) if isinstance(data, dict) else data
        if not isinstance(rows, list):
            return income_records

        # Build lookup: period_end_date + consolidation → balance sheet row
        bs_lookup: Dict[str, dict] = {}
        for row in rows:
            period_end = str(row.get("period_end_date") or row.get("date") or row.get("period") or "")
            cons = _consolidation(row.get("type") or row.get("consolidation") or "")
            key = f"{period_end}|{cons.value}"
            bs_lookup[key] = row

        enriched = []
        for rec in income_records:
            key = f"{rec.period_end_date}|{rec.consolidation.value}"
            bs_row = bs_lookup.get(key)
            if bs_row:
                total_assets = _safe_float(
                    bs_row.get("total_assets") or bs_row.get("total_assets_crore")
                )
                current_liabilities = _safe_float(
                    bs_row.get("total_current_liabilities")
                    or bs_row.get("current_liabilities")
                )
                total_debt = _safe_float(
                    bs_row.get("total_debt")
                    or bs_row.get("total_borrowings")
                    or bs_row.get("debt")
                )
                total_equity = _safe_float(
                    bs_row.get("shareholders_equity")
                    or bs_row.get("total_equity")
                    or bs_row.get("equity")
                )
                cash = _safe_float(
                    bs_row.get("cash_and_cash_equivalents")
                    or bs_row.get("cash_and_equivalents")
                    or bs_row.get("cash")
                )

                # capital_employed = total_assets - current_liabilities
                capital_employed = None
                if total_assets is not None and current_liabilities is not None:
                    capital_employed = total_assets - current_liabilities

                rec.total_debt = total_debt
                rec.total_equity = total_equity
                rec.capital_employed = capital_employed
            enriched.append(rec)

        logger.debug(f"[UPSTOX] Balance sheet enrichment: {symbol} → {len(enriched)} records")
        return enriched

    # ------------------------------------------------------------------
    # Cash Flow parsing
    # ------------------------------------------------------------------
    def _parse_cash_flows(
        self, isin: str, symbol: str, records: List[RawFinancialRecord]
    ) -> List[RawFinancialRecord]:
        url = f"{self.base_url}/{isin}/cash-flow"
        data = self._get(url)
        if not data:
            return records

        rows = data.get("data", data) if isinstance(data, dict) else data
        if not isinstance(rows, list):
            return records

        cf_lookup: Dict[str, dict] = {}
        for row in rows:
            period_end = str(row.get("period_end_date") or row.get("date") or row.get("period") or "")
            cons = _consolidation(row.get("type") or row.get("consolidation") or "")
            key = f"{period_end}|{cons.value}"
            cf_lookup[key] = row

        for rec in records:
            key = f"{rec.period_end_date}|{rec.consolidation.value}"
            cf_row = cf_lookup.get(key)
            if cf_row:
                rec.operating_cash_flow = _safe_float(
                    cf_row.get("cash_flow_from_operations")
                    or cf_row.get("operating_cash_flow")
                    or cf_row.get("net_cash_from_operating_activities")
                )
        return records

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------
    def fetch_raw_financials(self, isin: str, symbol: str) -> List[RawFinancialRecord]:
        """
        Fetches income statement + balance sheet + cash flow for the given ISIN.
        Returns a list of RawFinancialRecord with all parseable fields populated.
        A 200 response alone does NOT mean VERIFIED — the caller (reconciler)
        must confirm financial fields are non-None before certifying.
        """
        if not self.token:
            logger.warning(f"[{symbol}] UPSTOX_ACCESS_TOKEN not set. Cannot fetch.")
            return []

        # Step 1: Income statement (revenue, net_profit, ebit, eps)
        records = self._parse_income_statements(isin, symbol)
        if not records:
            logger.warning(f"[UPSTOX] {symbol}: No income statement records returned.")
            return []

        # Step 2: Enrich with balance sheet (capital_employed, total_debt, cash)
        records = self._parse_balance_sheets(isin, symbol, records)

        # Step 3: Enrich with cash flow (operating_cash_flow)
        records = self._parse_cash_flows(isin, symbol, records)

        # Step 4: Filter out records with no usable financial fields
        usable = [
            r for r in records
            if any(
                v is not None
                for v in [r.revenue, r.net_profit, r.ebit, r.operating_cash_flow, r.total_debt]
            )
        ]

        if not usable:
            logger.warning(
                f"[UPSTOX] {symbol}: HTTP success but zero financial fields parsed. "
                f"Returning DATA_INSUFFICIENT. Check API response schema."
            )

        logger.info(
            f"[UPSTOX] {symbol}: {len(usable)}/{len(records)} records with financial data "
            f"(api_calls={self.api_call_count}, retries={self.retry_count})"
        )
        return usable
