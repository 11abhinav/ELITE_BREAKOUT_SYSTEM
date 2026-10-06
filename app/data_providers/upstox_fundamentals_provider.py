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

try:
    from app.data_providers.fundamental_models import (
        ConsolidationType,
        FundamentalStatus,
        RawFinancialRecord,
        StatementType,
    )
except ImportError:
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


def _normalize_upstox_period(period_str: str) -> str:
    """Convert 'Mar 2026' -> '2026-03-31', '31-Mar-2025' -> '2025-03-31', etc."""
    if not period_str:
        return ""
    p_str = period_str.strip()
    month_map = {
        "jan": ("01", "31"), "feb": ("02", "28"), "mar": ("03", "31"),
        "apr": ("04", "30"), "may": ("05", "31"), "jun": ("06", "30"),
        "jul": ("07", "31"), "aug": ("08", "31"), "sep": ("09", "30"),
        "oct": ("10", "31"), "nov": ("11", "30"), "dec": ("12", "31"),
    }
    # Pattern 1: 'Mar 2026' or 'March 2026'
    parts = p_str.split()
    if len(parts) == 2:
        month_str, year_str = parts[0].lower(), parts[1]
        if month_str[:3] in month_map:
            m, d = month_map[month_str[:3]]
            return f"{year_str}-{m}-{d}"
    # Pattern 2: '31-Mar-2025' or '31/Mar/2025' or '2025-03-31'
    for sep in ("-", "/", " "):
        if sep in p_str:
            sub = p_str.split(sep)
            if len(sub) == 3:
                # day, month, year e.g. 31, Mar, 2025
                if sub[1].lower()[:3] in month_map and len(sub[2]) == 4:
                    m, d = month_map[sub[1].lower()[:3]]
                    return f"{sub[2]}-{m}-{sub[0].zfill(2)}"
                # year, month, day e.g. 2025, 03, 31
                elif len(sub[0]) == 4 and sub[1].isdigit() and sub[2].isdigit():
                    return f"{sub[0]}-{sub[1].zfill(2)}-{sub[2].zfill(2)}"
    return p_str


class UpstoxFundamentalsProvider:
    """
    Fetches structured fundamental data from the Upstox Company Fundamentals API.
    Maps raw API fields to RawFinancialRecord using explicit, documented field names.
    Provides telemetry counters for observability.
    """

    def __init__(self):
        token = os.environ.get("UPSTOX_ACCESS_TOKEN")
        if not token:
            try:
                from app import config
                token = getattr(config, "UPSTOX_ACCESS_TOKEN", None)
            except ImportError:
                try:
                    import config
                    token = getattr(config, "UPSTOX_ACCESS_TOKEN", None)
                except Exception:
                    token = None
        if not token:
            try:
                from dotenv import load_dotenv
                load_dotenv()
                token = os.environ.get("UPSTOX_ACCESS_TOKEN")
            except Exception:
                pass
        self.token = token
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
        data_resp = self._get(url)
        if not data_resp:
            return []

        records = []
        data = data_resp.get("data", data_resp) if isinstance(data_resp, dict) else data_resp

        # Format A: Real Upstox API dict schema: {"type": ..., "time_period": ..., "income_statement": [...]}
        if isinstance(data, dict) and "income_statement" in data and isinstance(data["income_statement"], list):
            type_str = data.get("type", "consolidated")
            consolidation = _consolidation(type_str)
            time_period = str(data.get("time_period", "yearly")).lower()
            period_type = "ANNUAL" if any(x in time_period for x in ("year", "fy", "annual")) else ("QUARTERLY" if "quarter" in time_period else "UNKNOWN")
            unit_str = str(data.get("units_in", "crore"))

            period_map: Dict[str, dict] = {}
            for cat_item in data["income_statement"]:
                cat_name = str(cat_item.get("category", "")).lower()
                for h in cat_item.get("history", []):
                    p_raw = h.get("period", "")
                    if not p_raw:
                        continue
                    p_norm = _normalize_upstox_period(p_raw)
                    if p_norm not in period_map:
                        period_map[p_norm] = {
                            "period_end_date": p_norm,
                            "period_type": period_type,
                            "consolidation": consolidation,
                            "revenue": None,
                            "net_profit": None,
                            "ebit": None,
                            "eps": None,
                            "unit": unit_str,
                        }
                    val = _safe_float(h.get("value"))
                    if any(k in cat_name for k in ("revenue", "net_revenue", "total_revenue", "income", "sales")):
                        period_map[p_norm]["revenue"] = val
                    elif any(k in cat_name for k in ("operating_profit", "ebit", "operating_income", "pbit")):
                        period_map[p_norm]["ebit"] = val
                    elif any(k in cat_name for k in ("net_profit", "profit_after_tax", "pat")):
                        period_map[p_norm]["net_profit"] = val

            for p_norm, p_data in period_map.items():
                rec = RawFinancialRecord(
                    symbol=symbol,
                    source="UPSTOX_API",
                    period_end_date=p_data["period_end_date"],
                    period_type=p_data["period_type"],
                    consolidation=p_data["consolidation"],
                    revenue=p_data["revenue"],
                    net_profit=p_data["net_profit"],
                    ebit=p_data["ebit"],
                    eps=p_data["eps"],
                    availability_date=None,
                    unit=p_data["unit"],
                    currency="INR",
                )
                records.append(rec)
            records.sort(key=lambda r: r.period_end_date)
            logger.debug(f"[UPSTOX] Income stmt: {symbol} → {len(records)} records")
            return records

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
        data_resp = self._get(url)
        if not data_resp:
            return income_records

        data = data_resp.get("data", data_resp) if isinstance(data_resp, dict) else data_resp

        # Format A1: Real Upstox API category list schema: {"type": ..., "balance_sheet": [{"category": ..., "history": [...]}]}
        bs_list = None
        if isinstance(data, dict):
            for k in ("balance_sheet", "balance_sheets", "balanceSheets"):
                if k in data and isinstance(data[k], list):
                    bs_list = data[k]
                    break

        if bs_list is not None:
            period_bs_map: Dict[str, dict] = {}
            for cat_item in bs_list:
                cat_name = str(cat_item.get("category", "")).lower()
                for h in cat_item.get("history", []):
                    p_norm = _normalize_upstox_period(h.get("period", ""))
                    if not p_norm:
                        continue
                    if p_norm not in period_bs_map:
                        period_bs_map[p_norm] = {
                            "total_assets": None,
                            "total_liabilities": None,
                            "total_debt": None,
                            "total_equity": None,
                            "cash": None,
                        }
                    val = _safe_float(h.get("value"))
                    if any(k in cat_name for k in ("total debt", "borrowings", "debt", "total_debt", "long term borrowings", "short term borrowings")):
                        if period_bs_map[p_norm]["total_debt"] is None or "total" in cat_name:
                            period_bs_map[p_norm]["total_debt"] = val
                    elif any(k in cat_name for k in ("shareholders' equity", "shareholders equity", "total equity", "equity", "net worth", "share capital")):
                        if period_bs_map[p_norm]["total_equity"] is None or "total" in cat_name:
                            period_bs_map[p_norm]["total_equity"] = val
                    elif any(k in cat_name for k in ("total assets", "total asset", "assets")):
                        if period_bs_map[p_norm]["total_assets"] is None or "total" in cat_name:
                            period_bs_map[p_norm]["total_assets"] = val
                    elif any(k in cat_name for k in ("total liabilities", "total current liabilities", "liabilities")):
                        if period_bs_map[p_norm]["total_liabilities"] is None or "total" in cat_name:
                            period_bs_map[p_norm]["total_liabilities"] = val
                    elif any(k in cat_name for k in ("cash and cash equivalents", "cash & cash equivalents", "cash")):
                        period_bs_map[p_norm]["cash"] = val

            for p_norm, bs_vals in period_bs_map.items():
                t_assets = bs_vals["total_assets"]
                t_liab = bs_vals["total_liabilities"]
                t_debt = bs_vals["total_debt"]
                t_eq = bs_vals["total_equity"]
                if t_eq is None and t_assets is not None and t_liab is not None:
                    t_eq = t_assets - t_liab
                cap_emp = None
                if t_assets is not None and t_liab is not None:
                    cap_emp = t_assets - t_liab

                for rec in income_records:
                    if rec.period_end_date == p_norm:
                        rec.total_debt = t_debt
                        rec.total_equity = t_eq
                        rec.capital_employed = cap_emp
            return income_records

        # Format A2: Root history list schema: {"type": ..., "time_period": ..., "history": [...]}
        if isinstance(data, dict) and "history" in data and isinstance(data["history"], list):
            for row in data["history"]:
                p_norm = _normalize_upstox_period(row.get("period", ""))
                if not p_norm:
                    continue
                total_assets = _safe_float(row.get("total_asset") or row.get("total_assets") or row.get("total_assets_crore"))
                total_liabilities = _safe_float(row.get("total_liability") or row.get("total_liabilities") or row.get("total_current_liabilities"))
                total_debt = _safe_float(row.get("total_debt") or row.get("debt") or row.get("borrowings") or row.get("total_borrowings"))
                total_equity = _safe_float(row.get("total_equity") or row.get("equity") or row.get("shareholders_equity"))
                if total_equity is None and total_assets is not None and total_liabilities is not None:
                    total_equity = total_assets - total_liabilities
                cash = _safe_float(row.get("cash_and_cash_equivalents") or row.get("cash_and_equivalents") or row.get("cash"))

                capital_employed = None
                if total_assets is not None and total_liabilities is not None:
                    capital_employed = total_assets - total_liabilities

                for rec in income_records:
                    if rec.period_end_date == p_norm:
                        rec.total_debt = total_debt
                        rec.total_equity = total_equity
                        rec.capital_employed = capital_employed
            return income_records

        # Format B: Flat list of row dicts fallback
        rows = data if isinstance(data, list) else []
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
        data_resp = self._get(url)
        if not data_resp:
            return records

        data = data_resp.get("data", data_resp) if isinstance(data_resp, dict) else data_resp

        # Format A: Real Upstox API dict schema: {"type": ..., "time_period": ..., "cash_flow": [...]}
        if isinstance(data, dict) and "cash_flow" in data and isinstance(data["cash_flow"], list):
            for cat_item in data["cash_flow"]:
                cat_name = str(cat_item.get("category", "")).lower()
                if any(x in cat_name for x in ("operating", "cfo", "operations", "cash_flow_from_operations")):
                    for h in cat_item.get("history", []):
                        p_norm = _normalize_upstox_period(h.get("period", ""))
                        val = _safe_float(h.get("value"))
                        for rec in records:
                            if rec.period_end_date == p_norm:
                                rec.operating_cash_flow = val
            return records

        # Format B: Flat list of row dicts fallback
        rows = data if isinstance(data, list) else []
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

    # [RULE 67 CHANGE-RATIONALE: Official Upstox Key Ratios endpoint recovery for current_ev_ebitda, current PE, ROCE]
    def fetch_key_ratios(self, isin: str, symbol: str = "") -> Dict[str, Any]:
        """
        Fetches official Key Ratios from Upstox Company Fundamentals API:
          GET /fundamentals/{isin}/key-ratios
        Exposes current valuation and operating ratios:
          EV/EBITDA, P/E, P/B, ROE, ROCE, ROA, Quick Ratio.
        Enforces:
          - Real Upstox market data only (no synthetic data)
          - Captures cryptographic source provenance hash
          - Caches locally in data/upstox_key_ratios/{isin}.json for auditability
        """
        if not self.token:
            logger.warning(f"[UPSTOX] {symbol or isin}: UPSTOX_ACCESS_TOKEN not set. Cannot fetch key ratios.")
            return {}

        url = f"{self.base_url}/{isin}/key-ratios"
        data_resp = self._get(url)
        if not data_resp:
            logger.warning(f"[UPSTOX] {symbol or isin}: No key-ratios returned from {url}.")
            return {}

        import hashlib, json
        from datetime import datetime
        try:
            from zoneinfo import ZoneInfo
            ist_tz = ZoneInfo("Asia/Kolkata")
        except Exception:
            ist_tz = None

        raw_serialized = json.dumps(data_resp, sort_keys=True, separators=(",", ":")).encode("utf-8")
        prov_hash = hashlib.sha256(raw_serialized).hexdigest()

        items = data_resp.get("data", []) if isinstance(data_resp, dict) else []
        retrieved_ts = datetime.now(ist_tz).isoformat() if ist_tz else datetime.utcnow().isoformat()
        ratios = {
            "symbol": symbol,
            "isin": isin,
            "source": "UPSTOX_API",
            "endpoint": f"/v2/fundamentals/{isin}/key-ratios",
            "retrieved_at": retrieved_ts,
            "source_hash": prov_hash,
            "current_ev_ebitda": None,
            "current_pe": None,
            "current_pb": None,
            "current_roce": None,
            "current_roe": None,
            "current_roa": None,
            "quick_ratio": None,
        }

        def _clean_ratio_val(val_str: Optional[str]) -> Optional[float]:
            if val_str is None:
                return None
            s = str(val_str).replace("%", "").replace(",", "").strip()
            try:
                return float(s)
            except (ValueError, TypeError):
                return None

        for it in items:
            name = str(it.get("name", "")).strip().upper()
            c_val = it.get("company_value")
            num_val = _clean_ratio_val(c_val)
            if num_val is None:
                continue

            if "EV/EBITDA" in name:
                ratios["current_ev_ebitda"] = round(num_val, 2)
            elif name in ("P/E", "PE"):
                ratios["current_pe"] = round(num_val, 2)
            elif name in ("P/B", "PB"):
                ratios["current_pb"] = round(num_val, 2)
            elif name == "ROCE":
                ratios["current_roce"] = round(num_val, 2)
            elif name == "ROE":
                ratios["current_roe"] = round(num_val, 2)
            elif name == "ROA":
                ratios["current_roa"] = round(num_val, 2)
            elif "QUICK" in name:
                ratios["quick_ratio"] = round(num_val, 2)

        # Cache locally for provenance audit
        try:
            base_d = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
            cache_dir = os.path.join(base_d, "data", "upstox_key_ratios")
            os.makedirs(cache_dir, exist_ok=True)
            cache_file = os.path.join(cache_dir, f"{isin}.json")
            with open(cache_file, "w", encoding="utf-8") as f:
                json.dump(ratios, f, indent=2)
            if symbol:
                sym_file = os.path.join(cache_dir, f"{symbol}.json")
                with open(sym_file, "w", encoding="utf-8") as f:
                    json.dump(ratios, f, indent=2)
        except Exception as _ce:
            logger.debug(f"[UPSTOX] Key ratios cache write notice: {_ce}")

        logger.info(
            f"[UPSTOX] {symbol or isin}: Key ratios parsed | "
            f"EV/EBITDA={ratios['current_ev_ebitda']} | PE={ratios['current_pe']} | "
            f"ROCE={ratios['current_roce']} | ROE={ratios['current_roe']}"
        )
        return ratios

