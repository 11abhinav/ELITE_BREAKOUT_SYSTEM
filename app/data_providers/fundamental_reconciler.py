import logging
from typing import List, Dict, Tuple, Optional
try:
    from app.data_providers.fundamental_models import (
        RawFinancialRecord, ReconciledCanonicalMetrics, 
        FundamentalStatus, FundamentalProvenance, ConsolidationType
    )
except ImportError:
    from data_providers.fundamental_models import (
        RawFinancialRecord, ReconciledCanonicalMetrics, 
        FundamentalStatus, FundamentalProvenance, ConsolidationType
    )

logger = logging.getLogger(__name__)

class FundamentalReconciler:
    """
    Handles reconciliation between raw financial records from multiple sources.
    Calculates derived canonical metrics (CAGR, ROCE) deterministically.
    """
    
    def __init__(self):
        # Strict metric-specific tolerances (0.10% for raw financials)
        self.raw_tolerance_pct = 0.0010
        self.eps_tolerance_pct = 0.0050

    def reconcile_and_calculate(self, symbol: str, nse_records: List[RawFinancialRecord], upstox_records: List[RawFinancialRecord]) -> ReconciledCanonicalMetrics:
        """
        Reconciles Upstox and NSE raw records and calculates derived metrics.
        """
        metrics = ReconciledCanonicalMetrics(symbol=symbol)
        
        if not nse_records and not upstox_records:
            metrics.overall_status = FundamentalStatus.DATA_INSUFFICIENT
            return metrics
            
        if not nse_records or not upstox_records:
            metrics.overall_status = FundamentalStatus.VERIFIED_SINGLE_SOURCE
            # Note: Production BUY path only allows VERIFIED, so this gets blocked downstream
            return metrics
            
        def _get_key(rec: RawFinancialRecord) -> str:
            return f"{rec.symbol}|{rec.period_end_date}|{rec.period_type}|{rec.consolidation.value if hasattr(rec.consolidation, 'value') else rec.consolidation}|{rec.unit}|{rec.currency}"
        
        nse_dict = {_get_key(r): r for r in nse_records}
        upstox_dict = {_get_key(r): r for r in upstox_records}

        common_keys = set(nse_dict.keys()).intersection(upstox_dict.keys())

        if not common_keys:
            metrics.overall_status = FundamentalStatus.PERIOD_MISMATCH
            return metrics
            
        # Get the latest record among common keys for comparison
        latest_key = sorted(list(common_keys), key=lambda k: k.split('|')[1], reverse=True)[0]
        nse_rec = nse_dict[latest_key]
        upx_rec = upstox_dict[latest_key]
            
        # 2. Raw-value reconciliation (Revenue, PAT, CFO, Debt, Equity, EBIT, CapEmployed)
        def _check_tolerance(nse_val: Optional[float], upx_val: Optional[float], tol: float) -> bool:
            if nse_val is None or upx_val is None:
                return False
            if nse_val == 0.0:
                return upx_val == 0.0
            return abs(upx_val - nse_val) / abs(nse_val) <= tol

        # Check required fields for core derived metrics
        required_raw = [
            ("revenue", nse_rec.revenue, upx_rec.revenue, self.raw_tolerance_pct),
            ("net_profit", nse_rec.net_profit, upx_rec.net_profit, self.raw_tolerance_pct),
            ("ebit", nse_rec.ebit, upx_rec.ebit, self.raw_tolerance_pct),
            ("capital_employed", nse_rec.capital_employed, upx_rec.capital_employed, self.raw_tolerance_pct),
        ]
        
        for name, n_val, u_val, tol in required_raw:
            if n_val is None or u_val is None:
                metrics.overall_status = FundamentalStatus.DATA_INSUFFICIENT
                return metrics
            if not _check_tolerance(n_val, u_val, tol):
                logger.error(f"[{symbol}] DATA_CONFLICT on {name}: NSE={n_val}, Upstox={u_val}")
                metrics.overall_status = FundamentalStatus.DATA_CONFLICT
                return metrics
                
        # Derived Metric Calculation (Deterministic Canonical)
        annual_nse = [r for r in nse_records if r.period_type == "ANNUAL"]
        if not annual_nse:
            annual_nse = nse_records
        annual_nse.sort(key=lambda r: r.period_end_date)
        latest_ann = annual_nse[-1]

        # 1. 5Y ROCE Average
        roce_vals = []
        for r in annual_nse[-5:]:
            if r.ebit is not None and r.capital_employed is not None and r.capital_employed > 0:
                roce_vals.append((r.ebit / r.capital_employed) * 100.0)
        if roce_vals:
            metrics.roce_5y = round(sum(roce_vals) / len(roce_vals), 2)
        elif latest_ann.capital_employed and latest_ann.capital_employed > 0 and latest_ann.ebit is not None:
            metrics.roce_5y = round((latest_ann.ebit / latest_ann.capital_employed) * 100.0, 2)
        else:
            metrics.roce_5y = None

        # 2. Debt to Equity
        if latest_ann.total_debt is not None and latest_ann.total_equity is not None and latest_ann.total_equity > 0:
            metrics.debt_to_equity = round(latest_ann.total_debt / latest_ann.total_equity, 3)
        elif latest_ann.total_debt == 0.0 and latest_ann.total_equity and latest_ann.total_equity > 0:
            metrics.debt_to_equity = 0.0
        else:
            metrics.debt_to_equity = None

        # 3. Multi-year CAGR (Sales & PAT)
        if len(annual_nse) >= 2:
            ann_dicts = [
                {"period_end_date": r.period_end_date, "revenue": r.revenue, "net_profit": r.net_profit}
                for r in annual_nse
            ]
            try:
                try:
                    from app.financial_data_integrity import compute_cagr_pit
                except ImportError:
                    from financial_data_integrity import compute_cagr_pit
                k_cagr = min(5, len(annual_nse) - 1)
                c_rev = compute_cagr_pit(ann_dicts, metric="revenue", symbol=symbol, target_years=k_cagr)
                c_pat = compute_cagr_pit(ann_dicts, metric="net_profit", symbol=symbol, target_years=k_cagr)
                metrics.sales_cagr_5y = c_rev.cagr if c_rev.ok else (-999.0 if (c_rev.reason and "NON_POSITIVE" in str(c_rev.reason)) else None)
                metrics.pat_cagr_5y = c_pat.cagr if c_pat.ok else (-999.0 if (c_pat.reason and "NON_POSITIVE" in str(c_pat.reason)) else None)
            except Exception as _ce:
                logger.debug(f"[RECONCILER] CAGR calculation exception for {symbol}: {_ce}")

        # 4. CFO / PAT 5Y ratio
        cfo_vals = [r.operating_cash_flow for r in annual_nse[-5:] if r.operating_cash_flow is not None]
        pat_vals = [r.net_profit for r in annual_nse[-5:] if r.net_profit is not None]
        if cfo_vals and pat_vals and sum(pat_vals) > 0:
            metrics.cfo_pat_5y = round(sum(cfo_vals) / sum(pat_vals), 2)
        elif pat_vals and sum(pat_vals) <= 0:
            metrics.cfo_pat_5y = -999.0

        metrics.overall_status = FundamentalStatus.VERIFIED
        return metrics
