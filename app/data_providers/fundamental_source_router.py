"""
app/data_providers/fundamental_source_router.py
================================================
Dual-Source Progressive Recovery Router.

Attempt order:
  1. Upstox fundamentals API (primary — authenticated, structured)
  2. NSE XBRL API (secondary — session-based, HTML-scrape style)
  3. Reconcile both if available; single-source if only one succeeds

VERIFIED_SINGLE_SOURCE:
  A single provider returning valid financial fields produces
  VERIFIED_SINGLE_SOURCE, not DATA_INSUFFICIENT.
  This prevents a temporary NSE outage from silently blocking valid Upstox data.

Economic identity:
  Records are matched by (symbol, period_end_date, period_type, consolidation,
  unit, currency) — NOT by ingestion timestamp.
  NSE and Upstox may ingest on different dates; that is not a PERIOD_MISMATCH.
"""

from __future__ import annotations

import logging
from typing import List, Optional

from data_providers.fundamental_models import (
    FundamentalStatus,
    RawFinancialRecord,
    ReconciledCanonicalMetrics,
)
from data_providers.fundamental_reconciler import FundamentalReconciler
from data_providers.nse_xbrl_provider import NseXbrlProvider
from data_providers.upstox_fundamentals_provider import UpstoxFundamentalsProvider

logger = logging.getLogger(__name__)


class FundamentalSourceRouter:
    """
    Progressive dual-source router for fundamental data recovery.
    """

    def __init__(self):
        self.upstox_provider = UpstoxFundamentalsProvider()
        self.nse_provider = NseXbrlProvider()
        self.reconciler = FundamentalReconciler()

    def _resolve_isin(self, symbol: str) -> Optional[str]:
        """Resolve NSE symbol to ISIN for Upstox API. Returns None if unresolvable."""
        try:
            from security_identity_resolver import SecurityIdentityResolver
            identity = SecurityIdentityResolver().resolve(symbol)
            if identity and identity.upstox_instrument_key:
                parts = identity.upstox_instrument_key.split("|")
                return parts[1] if len(parts) > 1 else identity.upstox_instrument_key
        except Exception as e:
            logger.debug(f"[ROUTER] ISIN resolution failed for {symbol}: {e}")
        return None

    def _single_source_metrics(
        self,
        symbol: str,
        records: List[RawFinancialRecord],
        source_name: str,
    ) -> ReconciledCanonicalMetrics:
        """
        Build a VERIFIED_SINGLE_SOURCE result from one provider's records.
        Uses the latest ANNUAL record for metric calculation.
        """
        metrics = ReconciledCanonicalMetrics(symbol=symbol)

        # Filter to annual records only for 5Y metrics
        annual = [r for r in records if r.period_type == "ANNUAL"]
        if not annual:
            annual = records  # fall back to whatever is available

        # Sort by period_end descending
        annual.sort(key=lambda r: r.period_end_date, reverse=True)
        latest = annual[0]

        # ROCE = EBIT / capital_employed
        if (
            latest.ebit is not None
            and latest.capital_employed is not None
            and latest.capital_employed > 0
        ):
            metrics.roce_5y = (latest.ebit / latest.capital_employed) * 100.0

        if latest.total_debt is not None and latest.total_equity is not None and latest.total_equity > 0:
            metrics.debt_to_equity = latest.total_debt / latest.total_equity

        # CAGR and CFO/PAT require multi-year data — set None if < 2 annual records
        if len(annual) >= 2:
            oldest = annual[-1]
            years = max(
                1,
                (
                    int(latest.period_end_date[:4]) - int(oldest.period_end_date[:4])
                ),
            )
            if oldest.revenue and oldest.revenue > 0 and latest.revenue:
                metrics.sales_cagr_5y = (
                    ((latest.revenue / oldest.revenue) ** (1 / years)) - 1
                ) * 100
            if oldest.net_profit and oldest.net_profit > 0 and latest.net_profit:
                metrics.pat_cagr_5y = (
                    ((latest.net_profit / oldest.net_profit) ** (1 / years)) - 1
                ) * 100

        if latest.operating_cash_flow is not None and latest.net_profit and latest.net_profit != 0:
            metrics.cfo_pat_5y = latest.operating_cash_flow / latest.net_profit

        metrics.overall_status = FundamentalStatus.VERIFIED_SINGLE_SOURCE
        logger.info(
            f"[ROUTER] {symbol}: VERIFIED_SINGLE_SOURCE from {source_name} | "
            f"roce={metrics.roce_5y} d/e={metrics.debt_to_equity}"
        )
        return metrics

    def execute_progressive_recovery(self, symbol: str) -> ReconciledCanonicalMetrics:
        """
        Progressive dual-source recovery:
          1. Fetch Upstox
          2. Fetch NSE
          3. Reconcile both → VERIFIED
             or single source → VERIFIED_SINGLE_SOURCE
             or neither       → DATA_INSUFFICIENT
        """
        logger.info(f"[FUNDAMENTAL_RECOVERY] {symbol}: initiating progressive recovery...")

        # --- Step 1: Upstox ---
        isin = self._resolve_isin(symbol)
        upstox_records: List[RawFinancialRecord] = []
        if isin:
            upstox_records = self.upstox_provider.fetch_raw_financials(isin, symbol)
            logger.info(
                f"[UPSTOX] {symbol}: {len(upstox_records)} usable records "
                f"(api_calls={self.upstox_provider.api_call_count})"
            )
        else:
            logger.warning(f"[UPSTOX] {symbol}: ISIN not resolved. Skipping Upstox fetch.")

        # --- Step 2: NSE ---
        nse_records: List[RawFinancialRecord] = self.nse_provider.fetch_raw_financials(symbol)
        logger.info(
            f"[NSE] {symbol}: {len(nse_records)} usable records "
            f"(401s={self.nse_provider.nse_401_count}, "
            f"403s={self.nse_provider.nse_403_count}, "
            f"refreshes={self.nse_provider.session_refresh_count})"
        )

        # --- Step 3: Route ---
        has_upstox = len(upstox_records) > 0
        has_nse = len(nse_records) > 0

        if not has_upstox and not has_nse:
            logger.warning(f"[ROUTER] {symbol}: Both providers returned no data. DATA_INSUFFICIENT.")
            m = ReconciledCanonicalMetrics(symbol=symbol)
            m.overall_status = FundamentalStatus.DATA_INSUFFICIENT
            return m

        if has_upstox and has_nse:
            # Full dual-source reconciliation
            metrics = self.reconciler.reconcile_and_calculate(symbol, nse_records, upstox_records)
            logger.info(f"[RECONCILIATION] {symbol}: status={metrics.overall_status.name}")
            return metrics

        if has_upstox:
            return self._single_source_metrics(symbol, upstox_records, "UPSTOX")

        # has_nse only
        return self._single_source_metrics(symbol, nse_records, "NSE_XBRL")
