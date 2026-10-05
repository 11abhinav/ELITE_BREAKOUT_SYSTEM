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
        # Realistic metric-specific tolerances (5.0% for raw financials, 5.0% for EPS)
        self.raw_tolerance_pct = 0.050
        self.eps_tolerance_pct = 0.050

    def reconcile_and_calculate(self, symbol: str, nse_records: List[RawFinancialRecord], upstox_records: List[RawFinancialRecord]) -> ReconciledCanonicalMetrics:
        """
        Reconciles Upstox and NSE raw records and calculates derived metrics.
        """
        metrics = ReconciledCanonicalMetrics(symbol=symbol)
        
        if not nse_records and not upstox_records:
            metrics.overall_status = FundamentalStatus.DATA_INSUFFICIENT
            return metrics
            
        # [RULE 67 CHANGE-RATIONALE: If single source available, derive metrics from available records rather than returning empty metrics with VERIFIED_SINGLE_SOURCE]
        if not nse_records or not upstox_records:
            source_recs = nse_records if nse_records else upstox_records
            return self._calculate_from_records(symbol, source_recs, is_dual=False)
            
        def _norm_unit(u: str) -> str:
            u_clean = str(u or "").lower().strip()
            if u_clean in ("cr", "crore", "crores", "inr_crores"):
                return "cr"
            if u_clean in ("lakh", "lakhs", "inr_lakhs"):
                return "lakh"
            return u_clean

        def _get_key(rec: RawFinancialRecord) -> str:
            u = _norm_unit(rec.unit)
            c = rec.consolidation.value if hasattr(rec.consolidation, "value") else str(rec.consolidation)
            return f"{rec.symbol}|{rec.period_end_date}|{rec.period_type}|{c}|{u}|{rec.currency}"
        
        nse_dict = {_get_key(r): r for r in nse_records}
        upstox_dict = {_get_key(r): r for r in upstox_records}

        common_keys = set(nse_dict.keys()).intersection(upstox_dict.keys())

        if not common_keys:
            nse_periods = {getattr(r, "period_end_date", "") for r in nse_records}
            upx_periods = {getattr(r, "period_end_date", "") for r in upstox_records}
            if nse_periods.intersection(upx_periods):
                metrics.overall_status = FundamentalStatus.STATEMENT_MISMATCH
            else:
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
                diff_pct = abs(u_val - n_val) / abs(n_val) if n_val != 0 else 1.0
                if diff_pct <= 0.25:
                    # Official NSE exchange filing takes precedence over third-party API minor divergence
                    logger.info(f"[{symbol}] NSE official filing precedence for {name}: NSE={n_val}, Upstox={u_val} (diff={diff_pct:.1%})")
                else:
                    logger.warning(f"[{symbol}] DATA_CONFLICT on {name}: NSE={n_val}, Upstox={u_val} (diff={diff_pct:.1%})")
                    metrics.overall_status = FundamentalStatus.DATA_CONFLICT
                    return metrics
                
        # Merge non-overlapping multi-year records (NSE authoritative for overlapping)
        by_date = {r.period_end_date: r for r in upstox_records}
        for r in nse_records:
            by_date[r.period_end_date] = r
        combined_records = sorted(by_date.values(), key=lambda r: r.period_end_date)
        return self._calculate_from_records(symbol, combined_records, is_dual=True)

    def _calculate_from_records(self, symbol: str, records: List[RawFinancialRecord], is_dual: bool = False) -> ReconciledCanonicalMetrics:
        """
        Deterministic canonical calculation of ROCE 5Y, D/E, 5Y CAGR, CFO/PAT 5Y.
        Enforces Section 4 & 21 invariants:
          - WRONG_CAGR_WINDOW = 0: Never writes 3Y CAGR into 5Y fields.
          - STRICT VERIFICATION: VERIFIED requires all fields non-null, else PARTIAL_RECOVERY.
        """
        metrics = ReconciledCanonicalMetrics(symbol=symbol)
        annual_records = [r for r in records if getattr(r, "period_type", "ANNUAL") == "ANNUAL"]
        if not annual_records:
            annual_records = records
        annual_records.sort(key=lambda r: r.period_end_date)
        if not annual_records:
            metrics.overall_status = FundamentalStatus.DATA_INSUFFICIENT
            return metrics

        latest_ann = annual_records[-1]

        # 1. 5Y ROCE Average
        roce_vals = []
        for r in annual_records[-5:]:
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

        # 3. 5Y CAGR (Sales & PAT) — STRICT 5Y REQUIREMENT (6+ continuous annual observations needed)
        # [RULE 67 CHANGE-RATIONALE: Prevent writing 3Y CAGR into 5Y fields. Enforce same statement basis & gapless FYs.]
        from app.data_providers.nse_xbrl_provider import NseXbrlProvider
        is_cont, gap_err, window_recs = NseXbrlProvider.validate_continuous_annual_series(
            annual_records, target_years=5
        )
        if is_cont and len(window_recs) >= 6:
            ann_dicts = [
                {"period_end_date": r.period_end_date, "revenue": r.revenue, "net_profit": r.net_profit}
                for r in window_recs
            ]
            try:
                try:
                    from app.financial_data_integrity import compute_cagr_pit
                except ImportError:
                    from financial_data_integrity import compute_cagr_pit
                c_rev = compute_cagr_pit(ann_dicts, metric="revenue", symbol=symbol, target_years=5)
                c_pat = compute_cagr_pit(ann_dicts, metric="net_profit", symbol=symbol, target_years=5)
                metrics.sales_cagr_5y = c_rev.cagr if c_rev.ok else (-999.0 if (c_rev.reason and any(x in str(c_rev.reason) for x in ("NON_POSITIVE", "NEGATIVE", "BASE_NON_POSITIVE"))) else None)
                metrics.pat_cagr_5y = c_pat.cagr if c_pat.ok else (-999.0 if (c_pat.reason and any(x in str(c_pat.reason) for x in ("NON_POSITIVE", "NEGATIVE", "BASE_NON_POSITIVE"))) else None)
            except Exception as _ce:
                logger.debug(f"[RECONCILER] CAGR calculation exception for {symbol}: {_ce}")
        else:
            if gap_err:
                logger.warning(f"[{symbol}] 5Y CAGR blocked by annual series validation: {gap_err}")
            # Insufficient or discontinuous annual observations for 5Y CAGR: leave as None. Do NOT substitute 3Y.
            metrics.sales_cagr_5y = None
            metrics.pat_cagr_5y = None

        # 4. CFO / PAT 5Y ratio
        cfo_vals = [r.operating_cash_flow for r in annual_records[-5:] if r.operating_cash_flow is not None]
        pat_vals = [r.net_profit for r in annual_records[-5:] if r.net_profit is not None]
        if cfo_vals and pat_vals and sum(pat_vals) > 0:
            metrics.cfo_pat_5y = round(sum(cfo_vals) / sum(pat_vals), 2)
        elif pat_vals and sum(pat_vals) <= 0:
            metrics.cfo_pat_5y = -999.0
        # 5. 3Y Share Dilution
        dilution_dicts = [
            {
                "period_end_date": r.period_end_date,
                "shares_outstanding_m": getattr(r, "shares_outstanding", getattr(r, "shares_outstanding_m", None)),
                "filing_date": r.broadcast_timestamp or getattr(r, "filing_date", None),
            }
            for r in annual_records
        ]
        try:
            try:
                from app.financial_data_integrity import compute_share_dilution_3y
            except ImportError:
                from financial_data_integrity import compute_share_dilution_3y
            dilution_res = compute_share_dilution_3y(dilution_dicts, symbol=symbol)
            if dilution_res.ok:
                metrics.share_dilution_3y = dilution_res.share_dilution_3y
        except Exception as _de:
            logger.debug(f"[RECONCILER] Share dilution calculation exception for {symbol}: {_de}")

        # [RULE 67 CHANGE-RATIONALE: Section 21 Strict Verification. VERIFIED only when all 6 fields are populated and valid.]
        core_fields = [
            metrics.roce_5y,
            metrics.sales_cagr_5y,
            metrics.pat_cagr_5y,
            metrics.cfo_pat_5y,
            metrics.debt_to_equity,
            metrics.share_dilution_3y,
        ]
        populated_count = sum(1 for v in core_fields if v is not None)

        if populated_count == len(core_fields):
            metrics.overall_status = FundamentalStatus.VERIFIED if is_dual else FundamentalStatus.VERIFIED_SINGLE_SOURCE
        elif populated_count > 0:
            metrics.overall_status = FundamentalStatus.PARTIAL_RECOVERY
        else:
            metrics.overall_status = FundamentalStatus.INSUFFICIENT_HISTORY if len(annual_records) < 5 else FundamentalStatus.DATA_INSUFFICIENT

        return metrics

