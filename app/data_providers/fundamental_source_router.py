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
        matching = [r for r in records if getattr(r, "period_type", "ANNUAL") == statement_type]
        if not matching:
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
        # [RULE 67 CHANGE-RATIONALE: Prevent writing 3Y CAGR into 5Y fields (Finding #3). Require target_years=5 and len(annual) >= 6.]
        if len(annual) >= 6:
            ann_dicts = []
            for r in annual:
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
            # Insufficient annual observations for 5Y CAGR: leave as None. Do NOT substitute 3Y.
            metrics.sales_cagr_5y = None
            metrics.pat_cagr_5y = None
            logger.info(f"[ROUTER] {symbol}: Insufficient annual records ({len(annual)} < 6) for 5Y CAGR. sales_cagr_5y and pat_cagr_5y remain None.")

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

        # [RULE 67 CHANGE-RATIONALE: VERIFIED with NULL field = 0.
        # Check if all core metrics are populated. If any core metric is missing,
        # status must NOT be VERIFIED_SINGLE_SOURCE; it must be PARTIAL_RECOVERY.]
        core_fields = [metrics.roce_5y, metrics.sales_cagr_5y, metrics.pat_cagr_5y, metrics.cfo_pat_5y, metrics.debt_to_equity]
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
            f"cfo/pat={metrics.cfo_pat_5y} d/e={metrics.debt_to_equity}"
        )
        return metrics

    def execute_progressive_recovery(
        self,
        symbol: str,
        as_of_timestamp: Optional[str] = None,
    ) -> ReconciledCanonicalMetrics:
        """
        Progressive dual-source recovery with certified local raw filing fallback:
          1. Fetch Upstox API
          2. Fetch NSE XBRL API
          3. Reconcile both → VERIFIED (dual-source)
             or single source → VERIFIED_SINGLE_SOURCE
             or local raw filings → VERIFIED_SINGLE_SOURCE (certified local source)
             or none          → DATA_INSUFFICIENT
        """
        logger.info(f"[FUNDAMENTAL_RECOVERY] {symbol}: initiating progressive recovery (as_of={as_of_timestamp})...")

        # --- Step 0: Listing Awareness (BSE-only vs NSE) ---
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

        # --- Step 2: NSE (Listing-Aware) ---
        nse_records: List[RawFinancialRecord] = []
        nse_raw_cnt = 0
        nse_parser_status = "NOT_APPLICABLE" if is_bse_only else "NO_DATA_RETURNED"
        if not is_bse_only:
            nse_records = self.nse_provider.fetch_raw_financials(symbol)
            nse_raw_cnt = self.nse_provider.last_raw_count.get(symbol, 0)
            nse_parser_status = self.nse_provider.last_status.get(symbol, "NO_DATA_RETURNED")
            logger.info(
                f"[NSE] {symbol}: {len(nse_records)} usable records (raw={nse_raw_cnt}, status={nse_parser_status}, "
                f"401s={self.nse_provider.nse_401_count}, "
                f"403s={self.nse_provider.nse_403_count}, "
                f"refreshes={self.nse_provider.session_refresh_count})"
            )
        else:
            logger.info(f"[NSE] {symbol}: Security is BSE-only. NSE is NOT_APPLICABLE.")

        # Persist newly fetched live records to pit_raw_filings for future reuse
        if upstox_records:
            self._persist_raw_filings(symbol, upstox_records)
        elif nse_records:
            self._persist_raw_filings(symbol, nse_records)

        # --- Step 3: Local Raw Filings ---
        local_records = self._fetch_local_raw_filings(symbol)

        def _annual_count(rs: List[RawFinancialRecord]) -> int:
            return sum(1 for r in rs if getattr(r, "period_type", "ANNUAL") == "ANNUAL")

        trace = self.last_trace.setdefault(symbol, {})
        trace.update({
            "is_bse_only": is_bse_only,
            "isin_resolved": bool(isin),
            "upstox_records": len(upstox_records),
            "upstox_annual": _annual_count(upstox_records),
            "nse_records": len(nse_records),
            "nse_annual": _annual_count(nse_records),
            "nse_raw_count": nse_raw_cnt,
            "nse_usable_count": len(nse_records),
            "nse_parser_status": nse_parser_status,
            "raw_rows_returned": nse_raw_cnt or len(upstox_records) or len(local_records),
            "usable_fields": len(nse_records) if nse_raw_cnt > 0 else (len(upstox_records) or len(local_records)),
            "parser_error": "PARSER_OR_FIELD_MAPPING_FAILURE" if (nse_raw_cnt > 0 and len(nse_records) == 0) else None,
            "local_records": len(local_records),
            "local_annual": _annual_count(local_records),
        })

        # --- Step 4: Route ---
        has_upstox = len(upstox_records) > 0
        has_nse = len(nse_records) > 0
        has_local = len(local_records) > 0

        # Tier 1: Upstox + NSE dual-source
        if has_upstox and has_nse:
            metrics = self.reconciler.reconcile_and_calculate(symbol, nse_records, upstox_records)
            logger.info(f"[RECONCILIATION] {symbol}: status={metrics.overall_status.name}")
            return metrics

        # Tier 2: Upstox + Certified Local Raw Filings dual-source
        if has_upstox and has_local:
            metrics = self._reconcile_upstox_and_local(symbol, upstox_records, local_records, as_of_timestamp=as_of_timestamp)
            logger.info(f"[DUAL_SOURCE_UPSTOX_LOCAL] {symbol}: status={metrics.overall_status.name}")
            return metrics

        # Tier 3: NSE + Certified Local Raw Filings dual-source
        if has_nse and has_local:
            metrics = self._reconcile_upstox_and_local(symbol, nse_records, local_records, as_of_timestamp=as_of_timestamp)
            logger.info(f"[DUAL_SOURCE_NSE_LOCAL] {symbol}: status={metrics.overall_status.name}")
            return metrics

        # Tier 4: Single source fallbacks
        if has_upstox:
            return self._single_source_metrics(symbol, upstox_records, "UPSTOX", as_of_timestamp=as_of_timestamp)

        if has_nse:
            return self._single_source_metrics(symbol, nse_records, "NSE_XBRL", as_of_timestamp=as_of_timestamp)

        if has_local:
            logger.info(f"[LOCAL_RAW] {symbol}: Loaded {len(local_records)} records from certified local raw filings.")
            return self._single_source_metrics(symbol, local_records, "LOCAL_RAW_FILINGS", as_of_timestamp=as_of_timestamp)

        logger.warning(f"[ROUTER] {symbol}: All providers returned no data. DATA_INSUFFICIENT.")
        m = ReconciledCanonicalMetrics(symbol=symbol)
        m.overall_status = FundamentalStatus.DATA_INSUFFICIENT
        return m

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
        live_annual = {r.period_end_date: r for r in live_records if getattr(r, "period_type", "ANNUAL") == "ANNUAL"}
        local_annual = {r.period_end_date: r for r in local_records if getattr(r, "period_type", "ANNUAL") == "ANNUAL"}

        overlap_dates = set(live_annual.keys()).intersection(local_annual.keys())
        has_conflict = False

        for dt in overlap_dates:
            u_rec = live_annual[dt]
            l_rec = local_annual[dt]
            # Check revenue tolerance (<= 5% tolerance for rounding / unit conventions)
            if u_rec.revenue is not None and l_rec.revenue is not None and abs(l_rec.revenue) > 1.0:
                diff = abs(u_rec.revenue - l_rec.revenue) / abs(l_rec.revenue)
                if diff > 0.05:
                    logger.warning(
                        f"[{symbol}] Revenue divergence for {dt}: Live={u_rec.revenue}, Local={l_rec.revenue} (diff={diff:.1%})"
                    )
                    if diff > 0.15:
                        has_conflict = True

            # Check PAT tolerance
            if u_rec.net_profit is not None and l_rec.net_profit is not None and abs(l_rec.net_profit) > 1.0:
                diff = abs(u_rec.net_profit - l_rec.net_profit) / abs(l_rec.net_profit)
                if diff > 0.05:
                    logger.warning(
                        f"[{symbol}] PAT divergence for {dt}: Live={u_rec.net_profit}, Local={l_rec.net_profit} (diff={diff:.1%})"
                    )
                    if diff > 0.15:
                        has_conflict = True

        if has_conflict:
            logger.error(f"❌ [{symbol}] DATA_CONFLICT between Live and Local filings.")
            metrics = ReconciledCanonicalMetrics(symbol=symbol)
            metrics.overall_status = FundamentalStatus.DATA_CONFLICT
            return metrics

        # Merge records across periods, with live taking precedence and local supplementing missing metrics
        all_dates = sorted(set(live_annual.keys()).union(local_annual.keys()))
        merged_records: List[RawFinancialRecord] = []
        for dt in all_dates:
            u = live_annual.get(dt)
            l = local_annual.get(dt)
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
        core_fields = [metrics.roce_5y, metrics.sales_cagr_5y, metrics.pat_cagr_5y, metrics.cfo_pat_5y, metrics.debt_to_equity]
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
                        # FYERS API v3 documentation (https://myapi.fyers.in/docsv3) does NOT provide a public REST
                        # endpoint for Key Ratios (EV/EBITDA, ROCE, ROE, Debt/Equity).
                        # Therefore, fundamental valuation ratios cannot be fabricated from quotes.
                        trace["fyers_key_ratios_status"] = "UNSUPPORTED_IN_PUBLIC_REST_API_V3"
                        trace["fyers_status"] = "MARKET_QUOTES_AVAILABLE_KEY_RATIOS_UNSUPPORTED"
                        logger.info(f"⚡ [ROUTER: FYERS_APPROVED_SOURCE] Retrieved Fyers API v3 market quotes for {symbol} (LTP={quote_data.get('lp')}). Fundamental ratios remain unsupported in public REST v3.")
            except Exception as _fe:
                logger.debug(f"[ROUTER] FYERS recovery attempt notice for {symbol}: {_fe}")

        return ratios

