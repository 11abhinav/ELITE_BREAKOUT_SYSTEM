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
        Bridges disjoint fiscal years across certified providers on the same statement basis
        (e.g., NSE's historical filings + Upstox's recent filings).
        For overlapping periods, verifies agreement (<25% tolerance, preferring official NSE exchange filings).
        """
        metrics = ReconciledCanonicalMetrics(symbol=symbol)
        
        if not nse_records and not upstox_records:
            metrics.overall_status = FundamentalStatus.DATA_INSUFFICIENT
            return metrics
            
        if not nse_records or not upstox_records:
            source_recs = nse_records if nse_records else upstox_records
            return self._calculate_from_records(symbol, source_recs, is_dual=False)

        try:
            from app.financial_data_integrity import convert_to_inr_crores
        except ImportError:
            from financial_data_integrity import convert_to_inr_crores
            
        def _norm_unit(u: str) -> str:
            u_clean = str(u or "").lower().strip()
            if u_clean in ("cr", "crore", "crores", "inr_crores"):
                return "cr"
            if u_clean in ("lakh", "lakhs", "inr_lakhs"):
                return "lakh"
            return u_clean

        def _get_c_val(r: RawFinancialRecord) -> str:
            c = r.consolidation.value if hasattr(r.consolidation, "value") else str(r.consolidation)
            return c.upper().strip()

        nse_annual = [r for r in nse_records if getattr(r, "period_type", "ANNUAL") == "ANNUAL"]
        upx_annual = [r for r in upstox_records if getattr(r, "period_type", "ANNUAL") == "ANNUAL"]
        if not nse_annual: nse_annual = nse_records
        if not upx_annual: upx_annual = upstox_records

        # Map by period_end_date[:10]
        nse_dict: Dict[str, List[RawFinancialRecord]] = {}
        for r in nse_annual:
            dt = str(r.period_end_date or "")[:10]
            if dt:
                nse_dict.setdefault(dt, []).append(r)

        upx_dict: Dict[str, List[RawFinancialRecord]] = {}
        for r in upx_annual:
            dt = str(r.period_end_date or "")[:10]
            if dt:
                upx_dict.setdefault(dt, []).append(r)

        shared_periods = sorted(list(set(nse_dict.keys()).intersection(upx_dict.keys())))

        # 1. Overlapping Period Check
        has_conflict = False
        has_statement_mismatch = False
        merged_overlapping: Dict[str, RawFinancialRecord] = {}

        if shared_periods:
            for dt in shared_periods:
                n_list = nse_dict[dt]
                u_list = upx_dict[dt]

                n_bases = {_get_c_val(r) for r in n_list}
                u_bases = {_get_c_val(r) for r in u_list}
                common_bases = n_bases.intersection(u_bases)

                if not common_bases:
                    has_statement_mismatch = True
                    logger.warning(f"[{symbol}] STATEMENT_MISMATCH for {dt}: NSE bases={n_bases} vs Upstox bases={u_bases}")
                    continue

                # Prefer CONSOLIDATED if both support it, else STANDALONE
                target_basis = "CONSOLIDATED" if "CONSOLIDATED" in common_bases else list(common_bases)[0]
                n_rec = next(r for r in n_list if _get_c_val(r) == target_basis)
                u_rec = next(r for r in u_list if _get_c_val(r) == target_basis)

                # Check if essential inputs are missing across both providers on an isolated observation
                if (n_rec.capital_employed is None and u_rec.capital_employed is None) and len(shared_periods) == 1 and len(nse_annual) <= 1 and len(upx_annual) <= 1:
                    metrics.overall_status = FundamentalStatus.DATA_INSUFFICIENT
                    return metrics

                # Tolerance check on core revenue & net profit
                n_rev = convert_to_inr_crores(n_rec.revenue, n_rec.unit or "cr")
                u_rev = convert_to_inr_crores(u_rec.revenue, u_rec.unit or "cr")
                n_pat = convert_to_inr_crores(n_rec.net_profit, n_rec.unit or "cr")
                u_pat = convert_to_inr_crores(u_rec.net_profit, u_rec.unit or "cr")

                if n_rev is not None and u_rev is not None and abs(n_rev) > 0.1:
                    diff_rev = abs(u_rev - n_rev) / abs(n_rev)
                    if diff_rev > 0.25:
                        logger.warning(f"[{symbol}] DATA_CONFLICT on revenue for {dt}: NSE={n_rev}Cr, Upstox={u_rev}Cr (diff={diff_rev:.1%})")
                        has_conflict = True
                    elif diff_rev > self.raw_tolerance_pct:
                        logger.info(f"[{symbol}] NSE official filing precedence for revenue: NSE={n_rev}Cr, Upstox={u_rev}Cr (diff={diff_rev:.1%})")

                if n_pat is not None and u_pat is not None and abs(n_pat) > 0.1:
                    diff_pat = abs(u_pat - n_pat) / abs(n_pat)
                    if diff_pat > 0.25:
                        logger.warning(f"[{symbol}] DATA_CONFLICT on net_profit for {dt}: NSE={n_pat}Cr, Upstox={u_pat}Cr (diff={diff_pat:.1%})")
                        has_conflict = True
                    elif diff_pat > self.raw_tolerance_pct:
                        logger.info(f"[{symbol}] NSE official filing precedence for net_profit: NSE={n_pat}Cr, Upstox={u_pat}Cr (diff={diff_pat:.1%})")

                # Merge overlapping record: NSE takes precedence for income statement; Upstox supplements missing fields
                n_cfo = convert_to_inr_crores(n_rec.operating_cash_flow, n_rec.unit or "cr")
                u_cfo = convert_to_inr_crores(u_rec.operating_cash_flow, u_rec.unit or "cr")
                n_debt = convert_to_inr_crores(n_rec.total_debt, n_rec.unit or "cr")
                u_debt = convert_to_inr_crores(u_rec.total_debt, u_rec.unit or "cr")
                n_eq = convert_to_inr_crores(n_rec.total_equity, n_rec.unit or "cr")
                u_eq = convert_to_inr_crores(u_rec.total_equity, u_rec.unit or "cr")
                n_ebit = convert_to_inr_crores(n_rec.ebit, n_rec.unit or "cr")
                u_ebit = convert_to_inr_crores(u_rec.ebit, u_rec.unit or "cr")
                n_cap = convert_to_inr_crores(n_rec.capital_employed, n_rec.unit or "cr")
                u_cap = convert_to_inr_crores(u_rec.capital_employed, u_rec.unit or "cr")

                merged_rec = RawFinancialRecord(
                    symbol=symbol,
                    source="NSE+UPSTOX_DUAL_SOURCE",
                    period_end_date=dt,
                    period_type="ANNUAL",
                    consolidation=n_rec.consolidation,
                    revenue=n_rev if n_rev is not None else u_rev,
                    net_profit=n_pat if n_pat is not None else u_pat,
                    operating_cash_flow=n_cfo if n_cfo is not None else u_cfo,
                    total_debt=n_debt if n_debt is not None else u_debt,
                    total_equity=n_eq if n_eq is not None else u_eq,
                    ebit=n_ebit if n_ebit is not None else u_ebit,
                    capital_employed=n_cap if n_cap is not None else u_cap,
                    eps=n_rec.eps if n_rec.eps is not None else u_rec.eps,
                    shares_outstanding=n_rec.shares_outstanding if getattr(n_rec, "shares_outstanding", None) is not None else getattr(u_rec, "shares_outstanding", None),
                    broadcast_timestamp=n_rec.broadcast_timestamp or u_rec.broadcast_timestamp,
                    version=n_rec.version or u_rec.version,
                    validation_status="VALID",
                    unit="cr",
                    currency="INR",
                )
                merged_overlapping[dt] = merged_rec

            if has_conflict:
                metrics.overall_status = FundamentalStatus.DATA_CONFLICT
                metrics.rejection_reason = "DATA_CONFLICT: >25% divergence between NSE and Upstox on identical statement basis"
                return metrics

            if has_statement_mismatch and not merged_overlapping:
                metrics.overall_status = FundamentalStatus.STATEMENT_MISMATCH
                metrics.rejection_reason = "STATEMENT_MISMATCH: NSE and Upstox share no common consolidation basis"
                return metrics
        else:
            # No overlapping periods: check if this is an isolated single-record comparison
            if len(nse_records) == 1 and len(upstox_records) == 1:
                metrics.overall_status = FundamentalStatus.PERIOD_MISMATCH
                return metrics

        # 2. Multi-Year Disjoint Union
        # Align consolidation basis for disjoint periods (prioritize CONSOLIDATED if supported by both)
        all_n_bases = {_get_c_val(r) for r in nse_annual}
        all_u_bases = {_get_c_val(r) for r in upx_annual}
        shared_all_bases = all_n_bases.intersection(all_u_bases)
        chosen_basis = "CONSOLIDATED" if "CONSOLIDATED" in shared_all_bases else (
            list(shared_all_bases)[0] if shared_all_bases else ("CONSOLIDATED" if "CONSOLIDATED" in all_u_bases else None)
        )

        combined_by_date: Dict[str, RawFinancialRecord] = {}
        for dt, rec in merged_overlapping.items():
            combined_by_date[dt] = rec

        # Add non-overlapping NSE records
        for dt, r_list in nse_dict.items():
            if dt not in combined_by_date:
                match_r = next((r for r in r_list if _get_c_val(r) == chosen_basis), r_list[0])
                combined_by_date[dt] = self._normalize_to_crores(match_r)

        # Add non-overlapping Upstox records
        for dt, r_list in upx_dict.items():
            if dt not in combined_by_date:
                match_r = next((r for r in r_list if _get_c_val(r) == chosen_basis), r_list[0])
                combined_by_date[dt] = self._normalize_to_crores(match_r)

        combined_records = [combined_by_date[dt] for dt in sorted(combined_by_date.keys())]
        return self._calculate_from_records(symbol, combined_records, is_dual=True)

    @staticmethod
    def _normalize_to_crores(rec: RawFinancialRecord) -> RawFinancialRecord:
        try:
            from app.financial_data_integrity import convert_to_inr_crores
        except ImportError:
            from financial_data_integrity import convert_to_inr_crores

        u = rec.unit or "cr"
        return RawFinancialRecord(
            symbol=rec.symbol,
            source=rec.source,
            period_end_date=rec.period_end_date,
            period_type=getattr(rec, "period_type", "ANNUAL"),
            consolidation=rec.consolidation,
            duration_days=getattr(rec, "duration_days", None),
            fiscal_year=getattr(rec, "fiscal_year", None),
            revenue=convert_to_inr_crores(rec.revenue, u),
            net_profit=convert_to_inr_crores(rec.net_profit, u),
            operating_cash_flow=convert_to_inr_crores(rec.operating_cash_flow, u),
            total_debt=convert_to_inr_crores(rec.total_debt, u),
            total_equity=convert_to_inr_crores(rec.total_equity, u),
            ebit=convert_to_inr_crores(rec.ebit, u),
            capital_employed=convert_to_inr_crores(rec.capital_employed, u),
            eps=rec.eps,
            shares_outstanding=getattr(rec, "shares_outstanding", None),
            broadcast_timestamp=rec.broadcast_timestamp,
            version=rec.version,
            validation_status=rec.validation_status,
            unit="cr",
            currency="INR",
        )

    def _calculate_from_records(self, symbol: str, records: List[RawFinancialRecord], is_dual: bool = False) -> ReconciledCanonicalMetrics:
        """
        Deterministic canonical calculation of ROCE 5Y, D/E, 5Y CAGR, CFO/PAT 5Y.
        Enforces Section 4 & 21 invariants:
          - WRONG_CAGR_WINDOW = 0: Never writes 3Y CAGR into 5Y fields.
          - STRICT VERIFICATION: VERIFIED requires all fields non-null, else PARTIAL_RECOVERY.
        """
        metrics = ReconciledCanonicalMetrics(symbol=symbol)
        annual_records = [self._normalize_to_crores(r) for r in records if getattr(r, "period_type", "ANNUAL") == "ANNUAL"]
        if not annual_records:
            annual_records = [self._normalize_to_crores(r) for r in records]
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

