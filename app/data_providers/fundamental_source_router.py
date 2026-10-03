import logging
from typing import Dict, Any
from data_providers.fundamental_models import (
    ReconciledCanonicalMetrics, FundamentalStatus
)
from data_providers.upstox_fundamentals_provider import UpstoxFundamentalsProvider
from data_providers.nse_xbrl_provider import NseXbrlProvider
from data_providers.fundamental_reconciler import FundamentalReconciler
from security_identity_resolver import SecurityIdentityResolver

logger = logging.getLogger(__name__)

class FundamentalSourceRouter:
    """
    Manages the progressive dual-source pre-recovery fundamental pipeline.
    """
    def __init__(self):
        self.upstox_provider = UpstoxFundamentalsProvider()
        self.nse_provider = NseXbrlProvider()
        self.reconciler = FundamentalReconciler()
        self.identity_resolver = SecurityIdentityResolver()

    def execute_progressive_recovery(self, symbol: str) -> ReconciledCanonicalMetrics:
        """
        Executes the Upstox -> NSE fallback and reconciles the result.
        """
        logger.info(f"[FUNDAMENTAL_RECOVERY] symbol={symbol} initiating progressive pre-recovery...")
        
        identity = self.identity_resolver.resolve(symbol)
        
        # 1. Upstox API Fetch
        upstox_records = []
        if identity.upstox_instrument_key:
            # upstox fundamental API usually expects ISIN which is embedded in the key
            # format: NSE_EQ|INE123...
            parts = identity.upstox_instrument_key.split('|')
            isin = parts[1] if len(parts) > 1 else identity.upstox_instrument_key
            upstox_records = self.upstox_provider.fetch_raw_financials(isin, symbol)
            
        logger.info(f"[UPSTOX] status={'SUCCESS' if upstox_records else 'INCOMPLETE'} records={len(upstox_records)}")
        
        # 2. NSE XBRL API Fetch
        nse_records = self.nse_provider.fetch_raw_financials(symbol)
        logger.info(f"[NSE_XBRL] status={'SUCCESS' if nse_records else 'INSUFFICIENT'} records={len(nse_records)}")
        
        # 3. Reconcile and calculate derived metrics
        canonical_metrics = self.reconciler.reconcile_and_calculate(symbol, nse_records, upstox_records)
        
        logger.info(f"[RECONCILIATION] status={canonical_metrics.overall_status.name}")
        
        if canonical_metrics.overall_status == FundamentalStatus.VERIFIED:
            logger.info(
                f"[CANONICAL_CALC]\n"
                f"roce_5y={canonical_metrics.roce_5y}\n"
                f"sales_cagr_5y={canonical_metrics.sales_cagr_5y}\n"
                f"pat_cagr_5y={canonical_metrics.pat_cagr_5y}\n"
                f"cfo_pat_5y={canonical_metrics.cfo_pat_5y}"
            )
            
        logger.info(f"[FUNDAMENTAL_STATUS] {canonical_metrics.overall_status.name}")
        
        return canonical_metrics
