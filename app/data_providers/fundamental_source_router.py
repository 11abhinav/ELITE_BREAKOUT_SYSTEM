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
import os
from typing import Any, Dict, List, Optional

try:
    from app.data_providers.fundamental_models import (
        ConsolidationType,
        FundamentalStatus,
        RawFinancialRecord,
        ReconciledCanonicalMetrics,
    )
    from app.data_providers.fundamental_reconciler import FundamentalReconciler
    from app.data_providers.nse_xbrl_provider import NseXbrlProvider
    from app.data_providers.bse_corporate_provider import BseCorporateProvider
    from app.data_providers.upstox_fundamentals_provider import UpstoxFundamentalsProvider
except ImportError:
    from data_providers.fundamental_models import (
        ConsolidationType,
        FundamentalStatus,
        RawFinancialRecord,
        ReconciledCanonicalMetrics,
    )
    from data_providers.fundamental_reconciler import FundamentalReconciler
    from data_providers.nse_xbrl_provider import NseXbrlProvider
    from data_providers.bse_corporate_provider import BseCorporateProvider
    from data_providers.upstox_fundamentals_provider import UpstoxFundamentalsProvider

try:
    from app.financial_data_integrity import record_source_watermark
except ImportError:
    try:
        from financial_data_integrity import record_source_watermark
    except ImportError:
        record_source_watermark = None

logger = logging.getLogger(__name__)


class FundamentalSourceRouter:
    """
    Progressive multi-source router for fundamental data recovery:
    Exhaustion order: Canonical PIT -> NSE -> BSE -> Upstox -> FYERS -> Screener.
    """

    def __init__(self):
        self.upstox_provider = UpstoxFundamentalsProvider()
        self.nse_provider = NseXbrlProvider()
        self.bse_provider = BseCorporateProvider()
        self.reconciler = FundamentalReconciler()
        # [RULE 67 CHANGE-RATIONALE: Per-symbol Tier-1 provider trace consumed by the
        # DataAvailabilityAuditor to distinguish "provider returned periods but field unresolved"
        # (parser/calculation discrepancy) from "provider returned nothing" (possible genuine gap).
        # Diagnostic metadata only — never used to compute or route values.]
        self.last_trace: Dict[str, Dict[str, Any]] = {}

    def _resolve_isin(self, symbol: str) -> Optional[str]:
        """Resolve NSE symbol to ISIN for Upstox API. Returns None if unresolvable."""
        # 1. Primary: Official Upstox instrument mapper
        try:
            try:
                from app.market_data.providers.upstox_instrument_mapper import get_upstox_instrument_key
            except ImportError:
                from market_data.providers.upstox_instrument_mapper import get_upstox_instrument_key
            key = get_upstox_instrument_key(symbol)
            if key and "|" in key:
                parts = key.split("|")
                for p in parts:
                    if p.startswith("INE") and len(p) == 12:
                        return p
        except Exception as e:
            logger.debug(f"[ROUTER] Upstox mapper ISIN resolution error for {symbol}: {e}")

        # 2. Fallback: Security identity resolver
        try:
            try:
                from app.security_identity_resolver import SecurityIdentityResolver
            except ImportError:
                from security_identity_resolver import SecurityIdentityResolver
            identity = SecurityIdentityResolver().resolve(symbol)
            if identity and identity.isin:
                return identity.isin
            if identity and identity.upstox_instrument_key:
                parts = identity.upstox_instrument_key.split("|")
                for p in parts:
                    if p.startswith("INE") and len(p) == 12:
                        return p
        except Exception as e:
            logger.debug(f"[ROUTER] SecurityIdentityResolver ISIN resolution error for {symbol}: {e}")
        return None

    def _fetch_local_raw_filings(self, symbol: str) -> List[RawFinancialRecord]:
        """Load certified local statement filings from data/pit_raw_filings/<symbol>.json."""
        for base in ["data", "/app/data", os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "data")]:
            path = os.path.join(base, "pit_raw_filings", f"{symbol}.json")
            if os.path.exists(path):
                try:
                    import json
                    with open(path, "r", encoding="utf-8") as f:
                        raw_list = json.load(f)
                    records = []
                    if isinstance(raw_list, list):
                        for row in raw_list:
                            period_end = str(row.get("period_end_date") or row.get("date") or "")
                            if not period_end:
                                continue
                            st_type = str(row.get("statement_type", "ANNUAL")).upper()
                            rev = row.get("revenue")
                            net_p = row.get("net_profit")
                            cfo = row.get("operating_cash_flow")
                            td = row.get("total_debt")
                            te = row.get("total_equity")
                            op = row.get("operating_profit")
                            ebit = float(op) if op is not None else None
                            cap_emp = None
                            if te is not None and td is not None:
                                cap_emp = float(te) + float(td)
                            elif te is not None:
                                cap_emp = float(te)

                            sh_raw = row.get("shares_outstanding") or row.get("shares_outstanding_m") or row.get("paid_up_capital")
                            sh_val = float(sh_raw) if sh_raw is not None and not (isinstance(sh_raw, float) and math.isnan(sh_raw)) else None

                            rec = RawFinancialRecord(
                                symbol=symbol,
                                source="LOCAL_RAW_FILINGS",
                                period_end_date=period_end[:10],
                                period_type=st_type,
                                consolidation=ConsolidationType.CONSOLIDATED,
                                revenue=float(rev) if rev is not None else None,
                                net_profit=float(net_p) if net_p is not None else None,
                                operating_cash_flow=float(cfo) if cfo is not None else None,
                                total_debt=float(td) if td is not None else None,
                                total_equity=float(te) if te is not None else None,
                                ebit=ebit,
                                capital_employed=cap_emp,
                                shares_outstanding=sh_val,
                                eps=float(row.get("eps")) if row.get("eps") is not None else None,
                                unit=str(row.get("unit", "cr")),
                                currency="INR",
                            )
                            records.append(rec)
                    return records
                except Exception as e:
                    logger.debug(f"[ROUTER] Error reading local raw filing for {symbol}: {e}")
        return []

    def _persist_raw_filings(self, symbol: str, records: List[RawFinancialRecord]) -> None:
        """
        # [RULE 67 CHANGE-RATIONALE: Merge newly fetched raw filing records with existing local records
        # by period_end_date, preserving multi-year historical statements (10-12 years) instead of
        # overwriting with short 4-year API responses. Calculates non-empty SHA-256 digest.]
        """
        if not records:
            return
        try:
            import json, hashlib
            base_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
            out_dir = os.path.join(base_dir, "data", "pit_raw_filings")
            os.makedirs(out_dir, exist_ok=True)
            out_path = os.path.join(out_dir, f"{symbol}.json")

            existing_by_period = {}
            if os.path.exists(out_path):
                try:
                    with open(out_path, "r", encoding="utf-8") as f:
                        old_list = json.load(f)
                    if isinstance(old_list, list):
                        for row in old_list:
                            p_end = str(row.get("period_end_date") or row.get("date") or "")
                            if p_end:
                                existing_by_period[p_end[:10]] = row
                except Exception as _e:
                    logger.debug(f"[ROUTER] Could not read existing raw filings for {symbol}: {_e}")

            for r in records:
                p_end = r.period_end_date[:10] if r.period_end_date else ""
                if not p_end:
                    continue
                d = {
                    "symbol": r.symbol,
                    "source": r.source,
                    "period_end_date": r.period_end_date,
                    "period_type": r.period_type,
                    "consolidation": r.consolidation.value if hasattr(r.consolidation, "value") else str(r.consolidation),
                    "revenue": r.revenue,
                    "net_profit": r.net_profit,
                    "operating_cash_flow": r.operating_cash_flow,
                    "total_debt": r.total_debt,
                    "total_equity": r.total_equity,
                    "ebit": r.ebit,
                    "capital_employed": r.capital_employed,
                    "eps": r.eps,
                    "shares_outstanding": r.shares_outstanding,
                    "unit": r.unit,
                    "currency": r.currency
                }
                # If existing record had fields that the new record is missing, keep them
                if p_end in existing_by_period:
                    old_rec = existing_by_period[p_end]
                    for k, v in old_rec.items():
                        if k not in d or d[k] is None:
                            d[k] = v
                existing_by_period[p_end] = d

            # Sort chronologically by period_end_date
            serializable = []
            for p_end in sorted(existing_by_period.keys()):
                d = existing_by_period[p_end]
                # Canonical serialization for record-level cryptographic hash
                clean = {k: v for k, v in sorted(d.items()) if v is not None and k != "source_record_hash"}
                serialized = json.dumps(clean, sort_keys=True, separators=(",", ":")).encode("utf-8")
                h = hashlib.sha256(serialized).hexdigest()
                d["source_record_hash"] = h
                serializable.append(d)

            with open(out_path, "w", encoding="utf-8") as f:
                json.dump(serializable, f, indent=2)
            logger.info(f"💾 [ROUTER] Persisted {len(serializable)} raw filings to {out_path} with cryptographic provenance hashes.")
        except Exception as e:
            logger.warning(f"[ROUTER] Could not persist raw filings for {symbol}: {e}")

    def _select_active_pit_records(
        self,
        records: List[RawFinancialRecord],
        as_of_timestamp: Optional[str] = None,
        statement_type: str = "ANNUAL",
    ) -> List[RawFinancialRecord]:
        """
        MANDATORY POINT-IN-TIME SELECTION INVARIANT:
        For any scanner as_of timestamp T:
          1. Excludes any filing broadcast after T (broadcast_timestamp <= T).
          2. Only includes validated records (validation_status == 'VALID').
          3. For records with identical economic period (same period_end_date), selects
             the latest valid broadcast/version as of T (e.g. amended filing superseding original).
          4. Returns chronologically sorted unique economic periods up to T.
        """
        matching = [
            r for r in records
            if getattr(r, "period_type", "ANNUAL") == statement_type
            and (
                statement_type != "ANNUAL"
                or str(getattr(r, "period_type", "ANNUAL")).upper() not in ("QUARTERLY", "HALF_YEARLY", "Q1", "Q2", "Q3", "Q4", "H1", "H2")
            )
        ]
        if not matching and statement_type != "ANNUAL":
            matching = records

        cutoff = str(as_of_timestamp) if as_of_timestamp else "9999-12-31T23:59:59"

        eligible = []
        for r in matching:
            b_time = getattr(r, "broadcast_timestamp", None) or getattr(r, "availability_date", None) or r.period_end_date
            val_st = getattr(r, "validation_status", "VALID")
            if str(b_time)[:len(cutoff)] <= cutoff and val_st == "VALID":
                eligible.append(r)

        if not eligible:
            return []

        by_period: Dict[str, RawFinancialRecord] = {}
        for r in eligible:
            p_end = r.period_end_date
            if p_end not in by_period:
                by_period[p_end] = r
            else:
                existing = by_period[p_end]
                ex_b = getattr(existing, "broadcast_timestamp", None) or getattr(existing, "availability_date", None) or existing.period_end_date
                cur_b = getattr(r, "broadcast_timestamp", None) or getattr(r, "availability_date", None) or r.period_end_date
                ex_v = getattr(existing, "version", "v1")
                cur_v = getattr(r, "version", "v1")
                if (cur_b, cur_v) > (ex_b, ex_v):
                    by_period[p_end] = r

        active_sorted = sorted(by_period.values(), key=lambda r: r.period_end_date)
        return active_sorted

    def _single_source_metrics(
        self,
        symbol: str,
        records: List[RawFinancialRecord],
        source_name: str,
        as_of_timestamp: Optional[str] = None,
    ) -> ReconciledCanonicalMetrics:
        """
        Build a VERIFIED_SINGLE_SOURCE result from one provider's records.
        Uses annual records filtered strictly by PIT cutoff and sorted ascending.
        """
        metrics = ReconciledCanonicalMetrics(symbol=symbol)

        # Enforce PIT selection invariant (broadcast <= as_of_timestamp, latest version per period)
        annual = self._select_active_pit_records(records, as_of_timestamp=as_of_timestamp, statement_type="ANNUAL")
        if not annual:
            metrics.overall_status = FundamentalStatus.DATA_INSUFFICIENT
            return metrics

        latest = annual[-1]

        # 1. 5Y ROCE average (up to trailing 5 annual filings)
        roce_vals = []
        for r in annual[-5:]:
            if r.ebit is not None and r.capital_employed is not None and r.capital_employed > 0:
                roce_vals.append((r.ebit / r.capital_employed) * 100.0)
        if roce_vals:
            metrics.roce_5y = round(sum(roce_vals) / len(roce_vals), 2)

        # 2. Debt to Equity (latest annual)
        if latest.total_debt is not None and latest.total_equity is not None and latest.total_equity > 0:
            metrics.debt_to_equity = round(latest.total_debt / latest.total_equity, 3)
        elif latest.total_debt == 0.0 and latest.total_equity and latest.total_equity > 0:
            metrics.debt_to_equity = 0.0

        # 3. 5Y CAGR (Sales & PAT) — STRICT 5Y REQUIREMENT (6+ annual observations needed)
        # [RULE 67 CHANGE-RATIONALE: Prevent writing 3Y CAGR into 5Y fields. Enforce continuous annual series.]
        try:
            from app.data_providers.nse_xbrl_provider import NseXbrlProvider
        except ImportError:
            from data_providers.nse_xbrl_provider import NseXbrlProvider

        is_cont, gap_err, window_recs = NseXbrlProvider.validate_continuous_annual_series(
            annual, target_years=5
        )
        if is_cont and len(window_recs) >= 6:
            ann_dicts = []
            for r in window_recs:
                ann_dicts.append({
                    "period_end_date": r.period_end_date,
                    "revenue": r.revenue,
                    "net_profit": r.net_profit,
                })
            try:
                try:
                    from app.financial_data_integrity import compute_cagr_pit
                except ImportError:
                    from financial_data_integrity import compute_cagr_pit
                cagr_rev = compute_cagr_pit(ann_dicts, metric="revenue", symbol=symbol, target_years=5)
                cagr_pat = compute_cagr_pit(ann_dicts, metric="net_profit", symbol=symbol, target_years=5)
                if cagr_rev.ok:
                    metrics.sales_cagr_5y = cagr_rev.cagr
                elif cagr_rev.reason and any(x in str(cagr_rev.reason) for x in ("NON_POSITIVE", "NEGATIVE", "END_VALUE_NON_POSITIVE", "BASE_NON_POSITIVE")):
                    metrics.sales_cagr_5y = -999.0

                if cagr_pat.ok:
                    metrics.pat_cagr_5y = cagr_pat.cagr
                elif cagr_pat.reason and any(x in str(cagr_pat.reason) for x in ("NON_POSITIVE", "NEGATIVE", "END_VALUE_NON_POSITIVE", "BASE_NON_POSITIVE")):
                    metrics.pat_cagr_5y = -999.0
            except Exception as _ce:
                logger.debug(f"[ROUTER] CAGR calculation exception for {symbol}: {_ce}")
        else:
            # Insufficient annual observations or filing gap: leave as None. Do NOT substitute 3Y.
            metrics.sales_cagr_5y = None
            metrics.pat_cagr_5y = None
            if gap_err:
                logger.info(f"[ROUTER] {symbol}: 5Y CAGR blocked by annual series validation ({gap_err}). sales_cagr_5y and pat_cagr_5y remain None.")

        # 4. 5Y Cumulative CFO / PAT Ratio
        cfo_vals = [r.operating_cash_flow for r in annual[-5:] if r.operating_cash_flow is not None]
        pat_vals = [r.net_profit for r in annual[-5:] if r.net_profit is not None]
        if cfo_vals and pat_vals:
            sum_cfo = sum(cfo_vals)
            sum_pat = sum(pat_vals)
            if sum_pat > 0:
                metrics.cfo_pat_5y = round(sum_cfo / sum_pat, 2)
            else:
                metrics.cfo_pat_5y = -999.0

        # 5. 3Y Share Dilution calculation
        dilution_dicts = []
        for r in annual:
            shs = getattr(r, "shares_outstanding", getattr(r, "shares_outstanding_m", None))
            dilution_dicts.append({
                "period_end_date": r.period_end_date,
                "shares_outstanding_m": shs,
                "filing_date": r.broadcast_timestamp or getattr(r, "filing_date", None),
            })
        try:
            try:
                from app.financial_data_integrity import compute_share_dilution_3y
            except ImportError:
                from financial_data_integrity import compute_share_dilution_3y
            dilution_res = compute_share_dilution_3y(dilution_dicts, symbol=symbol)
            if dilution_res.ok:
                metrics.share_dilution_3y = dilution_res.share_dilution_3y
            else:
                logger.info(f"[ROUTER] {symbol}: 3Y Share dilution not verified ({dilution_res.reason}). share_dilution_3y remains None.")
        except Exception as _de:
            logger.debug(f"[ROUTER] Share dilution calculation exception for {symbol}: {_de}")

        # [RULE 67 CHANGE-RATIONALE: VERIFIED with NULL field = 0.
        # Check if all 6 core metrics are populated. If any core metric is missing,
        # status must NOT be VERIFIED_SINGLE_SOURCE; it must be PARTIAL_RECOVERY.]
        core_fields = [
            metrics.roce_5y,
            metrics.sales_cagr_5y,
            metrics.pat_cagr_5y,
            metrics.cfo_pat_5y,
            metrics.debt_to_equity,
            metrics.share_dilution_3y,
        ]
        populated_count = sum(1 for f in core_fields if f is not None)
        if populated_count == len(core_fields):
            metrics.overall_status = FundamentalStatus.VERIFIED_SINGLE_SOURCE
        elif populated_count > 0:
            metrics.overall_status = FundamentalStatus.PARTIAL_RECOVERY
        else:
            metrics.overall_status = FundamentalStatus.DATA_INSUFFICIENT

        logger.info(
            f"[ROUTER] {symbol}: status={metrics.overall_status.name} from {source_name} | "
            f"roce={metrics.roce_5y} sales_cagr={metrics.sales_cagr_5y} pat_cagr={metrics.pat_cagr_5y} "
            f"cfo/pat={metrics.cfo_pat_5y} d/e={metrics.debt_to_equity} dilution_3y={metrics.share_dilution_3y}"
        )
        return metrics

    def _record_recovery_trace(
        self,
        symbol: str,
        is_bse_only: bool = False,
        isin_resolved: bool = False,
        canonical_hit: bool = False,
        local_records: Optional[List[RawFinancialRecord]] = None,
        nse_records: Optional[List[RawFinancialRecord]] = None,
        nse_raw_cnt: int = 0,
        nse_parser_status: str = "NOT_CHECKED",
        bse_records: Optional[List[RawFinancialRecord]] = None,
        bse_raw_cnt: int = 0,
        bse_status: str = "NOT_CHECKED",
        upstox_records: Optional[List[RawFinancialRecord]] = None,
        recovered_source: Optional[str] = None,
        field_sources: Optional[Dict[str, str]] = None,
    ) -> Dict[str, Any]:
        local_recs = local_records or []
        nse_recs = nse_records or []
        bse_recs = bse_records or []
        up_recs = upstox_records or []

        def _ann(rs):
            return sum(1 for r in rs if getattr(r, "period_type", "ANNUAL") == "ANNUAL")

        trace = self.last_trace.setdefault(symbol, {})
        if field_sources is not None:
            trace["field_sources"] = field_sources
        elif "field_sources" not in trace:
            trace["field_sources"] = {}
        trace.update({
            "symbol": symbol,
            "canonical_hit": canonical_hit,
            "canonical_status": "HIT" if canonical_hit else "MISS",
            "is_bse_only": is_bse_only,
            "isin_resolved": isin_resolved,
            "local_records": len(local_recs),
            "local_annual": _ann(local_recs),
            "nse_records": len(nse_recs),
            "nse_annual": _ann(nse_recs),
            "nse_raw_count": nse_raw_cnt,
            "nse_usable_count": len(nse_recs),
            "nse_parser_status": nse_parser_status,
            "bse_records": len(bse_recs),
            "bse_annual": _ann(bse_recs),
            "bse_raw_count": bse_raw_cnt,
            "bse_usable_count": len(bse_recs),
            "bse_status": bse_status,
            "upstox_records": len(up_recs),
            "upstox_annual": _ann(up_recs),
            "fyers_api_classification": "UNSUPPORTED_FIELD",
            "fyers_status": "UNSUPPORTED_FIELD",
            "fyers_api_reason": (
                "FYERS adapter classifies fields as UNSUPPORTED_FIELD when the API surface "
                "available to the application does not expose the required fundamental field."
            ),
            "fyers_ui_fundamentals": "REFERENCE_ONLY",
            "raw_rows_returned": nse_raw_cnt or bse_raw_cnt or len(up_recs) or len(local_recs),
            "usable_fields": len(nse_recs) or len(bse_recs) or len(up_recs) or len(local_recs),
            "parser_error": (
                "PARSER_OR_FIELD_MAPPING_FAILURE"
                if ((nse_raw_cnt > 0 and len(nse_recs) == 0) or (bse_raw_cnt > 0 and len(bse_recs) == 0))
                else None
            ),
            "recovered_source": recovered_source,
        })
        return trace

    def _fields_satisfied(self, metrics: Optional[ReconciledCanonicalMetrics], required_fields: List[str]) -> bool:
        if metrics is None or metrics.overall_status in (FundamentalStatus.DATA_INSUFFICIENT, FundamentalStatus.DATA_CONFLICT):
            return False
        for fld in required_fields:
            val = getattr(metrics, fld, None)
            if val is None or val == -999.0:
                return False
        return True

    def execute_progressive_recovery(
        self,
        symbol: str,
        as_of_timestamp: Optional[str] = None,
        skip_canonical_pit: bool = False,
        required_fields: Optional[List[str]] = None,
    ) -> ReconciledCanonicalMetrics:
        """
        Progressive field-level provider recovery with certified local raw filing fallback:
          Early stop ONLY when ALL required fields are verified and non-null.
          Provider chain:
            Canonical PIT -> NSE XBRL -> BSE Corporate -> Upstox API -> Reconciled Combinations.
        """
        if required_fields is None:
            required_fields = [
                "roce_5y",
                "sales_cagr_5y",
                "pat_cagr_5y",
                "cfo_pat_5y",
                "debt_to_equity",
                "share_dilution_3y",
            ]

        if not as_of_timestamp:
            logger.error(f"🚨 [FUNDAMENTAL_RECOVERY] {symbol}: RECOVERY_CONTEXT_INVALID — missing mandatory as_of_timestamp. Failing closed.")
            metrics = ReconciledCanonicalMetrics(symbol=symbol)
            metrics.overall_status = FundamentalStatus.INVALID
            metrics.rejection_reason = "RECOVERY_CONTEXT_INVALID: as_of_timestamp is mandatory for PIT recovery"
            return metrics

        logger.info(f"[FUNDAMENTAL_RECOVERY] {symbol}: initiating progressive recovery (as_of={as_of_timestamp}, required={required_fields})...")
        if record_source_watermark is not None:
            try:
                record_source_watermark("NSE", symbol=symbol)
                record_source_watermark("BSE", symbol=symbol)
            except Exception:
                pass

        # --- Step 0: Canonical Local PIT Raw Filings (Zero Network Check) ---
        local_records = self._fetch_local_raw_filings(symbol)
        canonical_hit = False
        if local_records:
            loc_metrics = self._single_source_metrics(symbol, local_records, "LOCAL_RAW_FILINGS", as_of_timestamp=as_of_timestamp)
            if loc_metrics.overall_status == FundamentalStatus.VERIFIED_SINGLE_SOURCE:
                canonical_hit = True
                if not skip_canonical_pit and self._fields_satisfied(loc_metrics, required_fields):
                    self._record_recovery_trace(
                        symbol,
                        canonical_hit=True,
                        local_records=local_records,
                        recovered_source="CANONICAL_LOCAL",
                        field_sources={f: "CANONICAL_LOCAL" for f in required_fields},
                    )
                    logger.info(f"⚡ [ROUTER] {symbol}: Authoritative verified data for all required fields present in Canonical PIT. Stopping recovery.")
                    return loc_metrics

        # --- Step 1: Listing Awareness (BSE-only vs NSE) ---
        is_bse_only = False
        try:
            try:
                from app.security_identity_resolver import SecurityIdentityResolver
            except ImportError:
                from security_identity_resolver import SecurityIdentityResolver
            sec_id = SecurityIdentityResolver().resolve(symbol)
            if sec_id and sec_id.exchange_primary == "BSE" and not (sec_id.upstox_instrument_key and "NSE" in str(sec_id.upstox_instrument_key)):
                is_bse_only = True
        except Exception as _res_err:
            logger.debug(f"[ROUTER] Security identity check for {symbol}: {_res_err}")

        # --- Step 2: NSE Ingestion (Primary for NSE Listed) ---
        nse_records: List[RawFinancialRecord] = []
        nse_raw_cnt = 0
        nse_parser_status = "NOT_APPLICABLE" if is_bse_only else "NO_DATA_RETURNED"
        nse_metrics: Optional[ReconciledCanonicalMetrics] = None
        if not is_bse_only:
            nse_records = self.nse_provider.fetch_raw_financials(symbol)
            nse_raw_cnt = self.nse_provider.last_raw_count.get(symbol, 0)
            nse_parser_status = self.nse_provider.last_status.get(symbol, "NO_DATA_RETURNED")
            logger.info(
                f"[NSE] {symbol}: {len(nse_records)} usable records (raw={nse_raw_cnt}, status={nse_parser_status})"
            )
            if nse_records:
                nse_metrics = self._single_source_metrics(symbol, nse_records, "NSE_XBRL", as_of_timestamp=as_of_timestamp)
                # Field-Level Invariant: Early-stop ONLY if ALL required fields are satisfied
                if self._fields_satisfied(nse_metrics, required_fields):
                    self._persist_raw_filings(symbol, nse_records)
                    self._record_recovery_trace(
                        symbol,
                        is_bse_only=is_bse_only,
                        isin_resolved=bool(self._resolve_isin(symbol)),
                        canonical_hit=canonical_hit,
                        local_records=local_records,
                        nse_records=nse_records,
                        nse_raw_cnt=nse_raw_cnt,
                        nse_parser_status=nse_parser_status,
                        bse_status="NSE_SUFFICIENT",
                        recovered_source="NSE",
                        field_sources={f: "NSE" for f in required_fields},
                    )
                    logger.info(f"✅ [ROUTER] {symbol}: All required fields satisfied via NSE XBRL. Stopping provider exhaustion.")
                    return nse_metrics
                else:
                    logger.info(
                        f"⏳ [ROUTER] {symbol}: NSE produced {len(nse_records)} records, but required fields not fully satisfied. "
                        f"Continuing progressive recovery to BSE..."
                    )

        # --- Step 3: BSE Ingestion (Secondary / BSE-only / Continuation) ---
        bse_records: List[RawFinancialRecord] = []
        bse_raw_cnt = 0
        bse_parser_status = "NOT_CHECKED"
        bse_metrics: Optional[ReconciledCanonicalMetrics] = None
        # Query BSE if BSE-only OR if NSE did not satisfy all required fields
        if is_bse_only or not self._fields_satisfied(nse_metrics, required_fields):
            bse_records = self.bse_provider.fetch_raw_financials(symbol)
            bse_raw_cnt = self.bse_provider.last_raw_count.get(symbol, 0)
            bse_parser_status = self.bse_provider.last_status.get(symbol, "NO_DATA_RETURNED")
            logger.info(
                f"[BSE] {symbol}: {len(bse_records)} usable records (raw={bse_raw_cnt}, status={bse_parser_status})"
            )
            if bse_records:
                bse_metrics = self._single_source_metrics(symbol, bse_records, "BSE_CORPORATE", as_of_timestamp=as_of_timestamp)
                if self._fields_satisfied(bse_metrics, required_fields):
                    self._persist_raw_filings(symbol, bse_records)
                    self._record_recovery_trace(
                        symbol,
                        is_bse_only=is_bse_only,
                        isin_resolved=bool(self._resolve_isin(symbol)),
                        canonical_hit=canonical_hit,
                        local_records=local_records,
                        nse_records=nse_records,
                        nse_raw_cnt=nse_raw_cnt,
                        nse_parser_status=nse_parser_status,
                        bse_records=bse_records,
                        bse_raw_cnt=bse_raw_cnt,
                        bse_status=bse_parser_status,
                        recovered_source="BSE",
                        field_sources={f: "BSE" for f in required_fields},
                    )
                    logger.info(f"✅ [ROUTER] {symbol}: All required fields satisfied via BSE Corporate. Stopping provider exhaustion.")
                    return bse_metrics
                else:
                    logger.info(
                        f"⏳ [ROUTER] {symbol}: BSE produced {len(bse_records)} records, but required fields not fully satisfied. "
                        f"Continuing progressive recovery to Upstox..."
                    )
        else:
            bse_parser_status = "NSE_SUFFICIENT"

        # --- Step 4: Upstox Ingestion (Tertiary Provider) ---
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

        # Persist newly fetched live records to pit_raw_filings for future reuse
        if upstox_records:
            self._persist_raw_filings(symbol, upstox_records)
        elif nse_records:
            self._persist_raw_filings(symbol, nse_records)
        elif bse_records:
            self._persist_raw_filings(symbol, bse_records)

        self._record_recovery_trace(
            symbol,
            is_bse_only=is_bse_only,
            isin_resolved=bool(isin),
            canonical_hit=canonical_hit,
            local_records=local_records,
            nse_records=nse_records,
            nse_raw_cnt=nse_raw_cnt,
            nse_parser_status=nse_parser_status,
            bse_records=bse_records,
            bse_raw_cnt=bse_raw_cnt,
            bse_status=bse_parser_status,
            upstox_records=upstox_records,
        )

        # --- Step 5: Field-Level Multi-Provider Progressive Recovery (§ Pending Item 4) ---
        # Compose field values in strict approved hierarchy:
        # Dual-Source Verified -> Canonical PIT -> NSE XBRL -> BSE Corporate -> Upstox API
        has_local = bool(local_records)
        has_nse = bool(nse_records)
        has_bse = bool(bse_records)
        has_upstox = bool(upstox_records)

        loc_metrics = self._single_source_metrics(symbol, local_records, "LOCAL_RAW_FILINGS", as_of_timestamp=as_of_timestamp) if has_local else None
        nse_metrics = self._single_source_metrics(symbol, nse_records, "NSE_XBRL", as_of_timestamp=as_of_timestamp) if has_nse else None
        bse_metrics = self._single_source_metrics(symbol, bse_records, "BSE_CORPORATE", as_of_timestamp=as_of_timestamp) if has_bse else None
        upstox_metrics = self._single_source_metrics(symbol, upstox_records, "UPSTOX", as_of_timestamp=as_of_timestamp) if has_upstox else None

        composed_metrics = ReconciledCanonicalMetrics(symbol=symbol)
        field_sources: Dict[str, str] = {}

        # 1. Dual-source cross-validations (if multiple sources available)
        dual_candidates = []
        if has_upstox and has_nse:
            dual_candidates.append((self.reconciler.reconcile_and_calculate(symbol, nse_records, upstox_records), "NSE+UPSTOX"))
        if has_upstox and has_bse:
            dual_candidates.append((self.reconciler.reconcile_and_calculate(symbol, bse_records, upstox_records), "BSE+UPSTOX"))
        if has_nse and has_bse:
            dual_candidates.append((self.reconciler.reconcile_and_calculate(symbol, nse_records, bse_records), "NSE+BSE"))
        if has_upstox and has_local:
            dual_candidates.append((self._reconcile_upstox_and_local(symbol, upstox_records, local_records, as_of_timestamp=as_of_timestamp), "UPSTOX+LOCAL"))
        if has_nse and has_local:
            dual_candidates.append((self._reconcile_upstox_and_local(symbol, nse_records, local_records, as_of_timestamp=as_of_timestamp), "NSE+LOCAL"))
        if has_bse and has_local:
            dual_candidates.append((self._reconcile_upstox_and_local(symbol, bse_records, local_records, as_of_timestamp=as_of_timestamp), "BSE+LOCAL"))

        for dual_met, dual_src in dual_candidates:
            if dual_met and dual_met.overall_status == FundamentalStatus.DATA_CONFLICT:
                logger.error(f"🚨 [ROUTER] {symbol}: Unresolved DATA_CONFLICT (>25% divergence) in {dual_src}. Hard blocking recovery.")
                composed_metrics.overall_status = FundamentalStatus.DATA_CONFLICT
                composed_metrics.rejection_reason = f"DATA_CONFLICT (>25% divergence) between {dual_src}"
                return composed_metrics
            elif dual_met and dual_met.overall_status in (FundamentalStatus.VERIFIED, FundamentalStatus.PARTIAL_RECOVERY):
                for fld in required_fields:
                    val = getattr(dual_met, fld, None)
                    if val is not None and getattr(composed_metrics, fld, None) is None:
                        setattr(composed_metrics, fld, val)
                        field_sources[fld] = dual_src
                        for p in getattr(dual_met, "provenance_chain", []):
                            if getattr(p, "metric", "") == fld and p not in composed_metrics.provenance_chain:
                                composed_metrics.provenance_chain.append(p)

        # 2. Approved single-provider hierarchy fallback for each unresolved field
        provider_hierarchy = [
            ("CANONICAL_LOCAL", loc_metrics),
            ("NSE", nse_metrics),
            ("BSE", bse_metrics),
            ("UPSTOX", upstox_metrics),
        ]

        for fld in required_fields:
            if getattr(composed_metrics, fld, None) is not None:
                continue
            for prov_name, p_met in provider_hierarchy:
                if p_met is not None and getattr(p_met, fld, None) is not None:
                    val = getattr(p_met, fld)
                    setattr(composed_metrics, fld, val)
                    field_sources[fld] = prov_name
                    for p in getattr(p_met, "provenance_chain", []):
                        if getattr(p, "metric", "") == fld and p not in composed_metrics.provenance_chain:
                            composed_metrics.provenance_chain.append(p)
                    break

        satisfied_count = sum(1 for f in required_fields if getattr(composed_metrics, f, None) is not None)
        if satisfied_count == len(required_fields):
            composed_metrics.overall_status = FundamentalStatus.VERIFIED
        elif satisfied_count > 0:
            composed_metrics.overall_status = FundamentalStatus.PARTIAL_RECOVERY
        else:
            composed_metrics.overall_status = FundamentalStatus.DATA_INSUFFICIENT

        # Update trace for auditor and telemetry
        primary_source = field_sources.get(required_fields[0]) if field_sources else (
            "NSE" if has_nse else ("BSE" if has_bse else ("UPSTOX" if has_upstox else ("CANONICAL_LOCAL" if has_local else "NONE")))
        )
        self.last_trace.setdefault(symbol, {})["field_sources"] = field_sources
        self.last_trace[symbol]["all_providers_exhausted"] = True
        self.last_trace[symbol]["recovered_source"] = primary_source or "NONE"

        if composed_metrics.overall_status == FundamentalStatus.DATA_INSUFFICIENT:
            logger.warning(f"[ROUTER] {symbol}: All approved providers exhausted. None of the required fields satisfied. DATA_INSUFFICIENT.")
        else:
            logger.info(
                f"[ROUTER] {symbol}: Field-level composition complete: status={composed_metrics.overall_status.name} | "
                f"sources={field_sources}"
            )

        return composed_metrics

    def _reconcile_upstox_and_local(
        self,
        symbol: str,
        live_records: List[RawFinancialRecord],
        local_records: List[RawFinancialRecord],
        as_of_timestamp: Optional[str] = None,
    ) -> ReconciledCanonicalMetrics:
        """
        Dual-source reconciliation between Upstox/NSE API records and certified local filings.
        Where annual periods overlap, validates Revenue and PAT consistency.
        Supplements missing balance-sheet / cash-flow fields from certified audited filings.
        """
        def _rec_key(r: RawFinancialRecord) -> Tuple[str, str]:
            c_val = r.consolidation.value if hasattr(r.consolidation, "value") else str(r.consolidation)
            return (r.period_end_date, c_val.upper())

        live_annual = {_rec_key(r): r for r in live_records if getattr(r, "period_type", "ANNUAL") == "ANNUAL"}
        local_annual = {_rec_key(r): r for r in local_records if getattr(r, "period_type", "ANNUAL") == "ANNUAL"}

        overlap_keys = set(live_annual.keys()).intersection(local_annual.keys())
        has_conflict = False

        for k in overlap_keys:
            u_rec = live_annual[k]
            l_rec = local_annual[k]
            dt, c_type = k
            # Check revenue tolerance (prefer Live NSE filings when divergence <= 25%)
            if u_rec.revenue is not None and l_rec.revenue is not None and abs(l_rec.revenue) > 1.0:
                diff = abs(u_rec.revenue - l_rec.revenue) / abs(l_rec.revenue)
                if diff > 0.05:
                    logger.info(
                        f"[{symbol}] Revenue divergence for {dt} ({c_type}): Live={u_rec.revenue}, Local={l_rec.revenue} (diff={diff:.1%}) - using Live NSE filing"
                    )
                    if diff > 0.25:
                        has_conflict = True

            # Check PAT tolerance (prefer Live NSE filings when divergence <= 25%)
            if u_rec.net_profit is not None and l_rec.net_profit is not None and abs(l_rec.net_profit) > 1.0:
                diff = abs(u_rec.net_profit - l_rec.net_profit) / abs(l_rec.net_profit)
                if diff > 0.05:
                    logger.info(
                        f"[{symbol}] PAT divergence for {dt} ({c_type}): Live={u_rec.net_profit}, Local={l_rec.net_profit} (diff={diff:.1%}) - using Live NSE filing"
                    )
                    if diff > 0.25:
                        has_conflict = True

        if has_conflict:
            logger.warning(f"⚠️ [{symbol}] DATA_CONFLICT (>25% divergence) between Live and Local filings on identical statement basis.")
            metrics = ReconciledCanonicalMetrics(symbol=symbol)
            metrics.overall_status = FundamentalStatus.DATA_CONFLICT
            return metrics

        # Merge records across periods, with live taking precedence and local supplementing missing metrics
        all_keys = sorted(set(live_annual.keys()).union(local_annual.keys()), key=lambda x: (x[0], x[1]))
        merged_records: List[RawFinancialRecord] = []
        for k in all_keys:
            u = live_annual.get(k)
            l = local_annual.get(k)
            dt, c_type = k
            if u and l:
                # Merge: prefer live for income, use local to supplement missing balance sheet/cash flow
                rec = RawFinancialRecord(
                    symbol=symbol,
                    source="UPSTOX+LOCAL_DUAL_SOURCE",
                    period_end_date=dt,
                    period_type="ANNUAL",
                    consolidation=u.consolidation,
                    revenue=u.revenue if u.revenue is not None else l.revenue,
                    net_profit=u.net_profit if u.net_profit is not None else l.net_profit,
                    operating_cash_flow=u.operating_cash_flow if u.operating_cash_flow is not None else l.operating_cash_flow,
                    total_debt=u.total_debt if u.total_debt is not None else l.total_debt,
                    total_equity=u.total_equity if u.total_equity is not None else l.total_equity,
                    ebit=u.ebit if u.ebit is not None else l.ebit,
                    capital_employed=u.capital_employed if u.capital_employed is not None else l.capital_employed,
                    eps=u.eps if u.eps is not None else l.eps,
                    broadcast_timestamp=u.broadcast_timestamp or l.broadcast_timestamp,
                    version=u.version or l.version,
                    validation_status="VALID",
                    unit="cr",
                    currency="INR",
                )
            elif u:
                rec = u
            else:
                rec = l
            merged_records.append(rec)

        metrics = self._single_source_metrics(symbol, merged_records, "UPSTOX+LOCAL_DUAL_SOURCE", as_of_timestamp=as_of_timestamp)
        # [RULE 67 CHANGE-RATIONALE: VERIFIED with NULL field = 0.
        # Even when dual sources overlap, status is VERIFIED only if all core metrics are populated.
        # If any core metric is missing, status must be PARTIAL_RECOVERY.]
        core_fields = [
            metrics.roce_5y,
            metrics.sales_cagr_5y,
            metrics.pat_cagr_5y,
            metrics.cfo_pat_5y,
            metrics.debt_to_equity,
            metrics.share_dilution_3y,
        ]
        populated_count = sum(1 for f in core_fields if f is not None)
        if populated_count == len(core_fields):
            metrics.overall_status = FundamentalStatus.VERIFIED if overlap_dates else FundamentalStatus.VERIFIED_SINGLE_SOURCE
        elif populated_count > 0:
            metrics.overall_status = FundamentalStatus.PARTIAL_RECOVERY
        else:
            metrics.overall_status = FundamentalStatus.DATA_INSUFFICIENT
        return metrics

    def recover_valuation_ratios(self, symbol: str) -> Dict[str, Any]:
        """
        # [RULE 67 CHANGE-RATIONALE: Progressive recovery of key valuation ratios (EV/EBITDA, P/E)
        # from authenticated Upstox Key Ratios API for symbols with missing valuation fields.
        # Provenance is cryptographically recorded in data/upstox_key_ratios/.]
        """
        trace = self.last_trace.setdefault(symbol, {})
        trace["key_ratios_attempted"] = True
        isin = self._resolve_isin(symbol)
        if not isin:
            logger.warning(f"[ROUTER] Cannot resolve ISIN for {symbol} for valuation recovery.")
            trace["key_ratios_status"] = "ISIN_UNRESOLVED"
            return {}
        ratios = self.upstox_provider.fetch_key_ratios(isin, symbol)
        trace["key_ratios_status"] = (
            "AVAILABLE" if ratios and ratios.get("current_ev_ebitda") is not None else "MISSING"
        )

        # [MANDATORY GOVERNANCE: FYERS APPROVED SOURCE INTEGRATION]
        # If Upstox Key Ratios does not have the field, check FYERS as an approved source.
        # Uses official FYERS API v3 (https://myapi.fyers.in/docsv3).
        if not ratios or ratios.get("current_ev_ebitda") is None:
            try:
                try:
                    from app.fyers_auth import get_fyers_client
                    from app.data_providers.fyers_symbol_mapper import FyersSymbolMapper
                except ImportError:
                    from fyers_auth import get_fyers_client
                    from data_providers.fyers_symbol_mapper import FyersSymbolMapper

                fyers_client = get_fyers_client()
                if fyers_client:
                    mapper = FyersSymbolMapper()
                    fyers_sym = mapper.get_fyers_symbol(symbol) if hasattr(mapper, "get_fyers_symbol") else f"NSE:{symbol}-EQ"
                    q_res = fyers_client.quotes({"symbols": fyers_sym})
                    if q_res and q_res.get("s") == "ok" and q_res.get("d"):
                        quote_data = q_res["d"][0].get("v", {})
                        trace["fyers_quote_data"] = quote_data
                        trace["fyers_quotes_api_checked"] = True
                        trace["fyers_quotes_endpoint"] = "https://api-t1.fyers.in/data/quotes"
                        # [PROVENANCE INVARIANT: FYERS API v3 Key Ratios Boundary]
                        # FYERS Quotes API v3 returns market quote fields (lp, volume, open_price, high_price, low_price, etc).
                        # FYERS adapter classifies fields as UNSUPPORTED_FIELD when the API surface
                        # available to the application does not expose the required fundamental field.
                        # (Key ratios displayed in FYERS platform/product UI serve as forensic reference only).
                        trace["fyers_api_status"] = "UNSUPPORTED_FIELD"
                        trace["fyers_ui_fundamentals"] = "REFERENCE_ONLY"
                        trace["fyers_key_ratios_status"] = "UNSUPPORTED_FIELD"
                        trace["fyers_status"] = "UNSUPPORTED_FIELD"
                        trace["fyers_reason"] = (
                            "FYERS adapter classifies fields as UNSUPPORTED_FIELD when the API surface "
                            "available to the application does not expose the required fundamental field."
                        )
                        logger.info(f"⚡ [ROUTER: FYERS_APPROVED_SOURCE] Retrieved Fyers API v3 market quotes for {symbol} (LTP={quote_data.get('lp')}). FYERS adapter classifies fields as UNSUPPORTED_FIELD when the API surface available to the application does not expose the required fundamental field.")
            except Exception as _fe:
                logger.debug(f"[ROUTER] FYERS recovery attempt notice for {symbol}: {_fe}")

        return ratios

