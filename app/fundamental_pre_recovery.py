"""
app/fundamental_pre_recovery.py
================================
Pre-Recovery Engine: sweeps the canonical PIT dataset ONCE per scanner run,
identifies symbols with genuinely missing fields (field-level granularity),
invokes the dual-source router to recover them, and writes a staging candidate.

Ownership:
  Called EXACTLY ONCE at QUALITY_COMPOUNDER orchestration level.
  NOT called inside _get_pit_filings() or any data-loading helper.
  NOT called during stock-by-stock evaluation.

Output:
  Staging candidate parquet → canonical_pit_publisher.publish_canonical_pit()
  The publisher enforces the 18-dimension Never-Downgrade Gate before atomic publish.

Architecture:
  QUALITY_COMPOUNDER
        ↓
  execute_pre_scan_sweep()    ← exactly once here
        ↓
  identify_incomplete_symbols()  (field-level granularity)
        ↓
  recover_symbol()            ← Upstox + NSE dual-source
        ↓
  persist_verified_record()   → staging candidate
        ↓
  canonical_pit_publisher.publish_canonical_pit()
        ↓
  Never-Downgrade Gate (18-dim)
        ↓
  Atomic canonical publish
        ↓
  load_pit_dataset()          ← pure read
        ↓
  scan
"""

from __future__ import annotations

import hashlib
import logging
import os
from datetime import datetime
from typing import Dict, List, Optional, Tuple

import pandas as pd

try:
    from app.data_providers.fundamental_source_router import FundamentalSourceRouter
    from app.data_providers.fundamental_models import FundamentalStatus, ReconciledCanonicalMetrics
    from app.data_providers.data_availability_auditor import DataAvailabilityAuditor
except ImportError:
    from data_providers.fundamental_source_router import FundamentalSourceRouter
    from data_providers.fundamental_models import FundamentalStatus, ReconciledCanonicalMetrics
    from data_providers.data_availability_auditor import DataAvailabilityAuditor

logger = logging.getLogger(__name__)

REQUIRED_FIELD_ALIASES: Dict[str, List[str]] = {
    "ROCE":              ["roce_5y_avg", "ROCE", "roce"],
    "sales_cagr_5y":      ["sales_cagr_5y", "sales_cagr"],
    "pat_cagr_5y":        ["pat_cagr_5y", "pat_cagr"],
    "cfo_pat_5y":         ["cfo_pat_5y_ratio", "cfo_pat_5y", "cfo_pat"],
    "debt":               ["debt_to_equity", "debt", "total_debt"],
    "share_dilution_3y":  ["share_dilution_3y_pct", "share_dilution_3y"],
    "current_ev_ebitda":  ["current_ev_ebitda"],
}
REQUIRED_FIELDS: List[str] = list(REQUIRED_FIELD_ALIASES.keys())

CALCULATION_VERSION = "v2.1_dual_source_reconciliation"


def _generate_deterministic_key(symbol: str, metrics: ReconciledCanonicalMetrics, as_of_timestamp: Optional[str] = None) -> str:
    """SHA256 hash of the canonical metrics and explicit PIT cutoff."""
    payload = (
        f"{symbol}|{CALCULATION_VERSION}|as_of={as_of_timestamp}|"
        f"roce={metrics.roce_5y}|"
        f"sales_cagr={metrics.sales_cagr_5y}|"
        f"pat_cagr={metrics.pat_cagr_5y}|"
        f"cfo_pat={metrics.cfo_pat_5y}|"
        f"debt={metrics.debt_to_equity}|"
        f"dilution={metrics.share_dilution_3y}"
    )
    return hashlib.sha256(payload.encode()).hexdigest()


class FundamentalPreRecoveryEngine:
    """
    Pre-scan sweep engine: identifies missing fields at field level and recovers
    them via the Dual-Source Router (Upstox + NSE). Writes to a staging candidate;
    does NOT directly overwrite the canonical PIT dataset.
    """

    def __init__(
        self,
        pit_parquet_path: Optional[str] = None,
        scanner_name: str = "QUALITY_VALUE_RECOVERY",
        validated_cache: Optional[Any] = None,
        as_of_timestamp: Optional[str] = None,
    ):
        self.scanner_name = scanner_name
        if not as_of_timestamp:
            from datetime import datetime, timezone
            as_of_timestamp = datetime.now(timezone.utc).isoformat()
        self.as_of_timestamp = as_of_timestamp
        if pit_parquet_path is not None:
            self.pit_parquet_path = pit_parquet_path
        else:
            canonical_path = "data/canonical_pit_rebuilt.parquet"
            if os.path.exists(canonical_path):
                self.pit_parquet_path = canonical_path
            else:
                self.pit_parquet_path = "data/daily_builder_master_v2.parquet"
        self.router = FundamentalSourceRouter()
        self.auditor = DataAvailabilityAuditor()
        if validated_cache is not None:
            self.validated_cache = validated_cache
        else:
            try:
                from app.pit_recovery_cache import get_validated_recovery_cache
            except ImportError:
                from pit_recovery_cache import get_validated_recovery_cache
            self.validated_cache = get_validated_recovery_cache()
        # 0 or negative means uncapped (processes every incomplete symbol in universe)
        self.global_daily_recovery_limit = int(os.getenv("PRE_RECOVERY_LIMIT", "0"))

    _generate_deterministic_key = staticmethod(_generate_deterministic_key)

    # ------------------------------------------------------------------
    # ------------------------------------------------------------------
    # Pre-Recovery Cache Schema Validation Gate
    # ------------------------------------------------------------------

    def validate_pre_recovery_cache_schema(self, df: pd.DataFrame) -> Dict[str, Any]:
        """
        [P0 SCHEMA VALIDATION GATE]
        Validates the pre-recovery cache schema before initiating any network recovery:
          - required_field
          - expected_aliases
          - schema hash
          - completeness %
        Prevents column alias regressions (e.g. roce_5y_avg vs ROCE) from falsely declaring
        100% of universe incomplete and triggering a massive recovery network storm.
        """
        cols_sorted = sorted(str(c) for c in df.columns)
        schema_hash = hashlib.sha256(",".join(cols_sorted).encode("utf-8")).hexdigest()[:16]

        field_reports: Dict[str, Dict[str, Any]] = {}
        is_valid = True
        critical_issues = []

        total_rows = len(df)
        for field_name, aliases in REQUIRED_FIELD_ALIASES.items():
            matched_col = None
            for alias in aliases:
                if alias in df.columns:
                    matched_col = alias
                    break

            if matched_col is not None:
                non_null = int(df[matched_col].notna().sum())
                pct = round((non_null / total_rows * 100.0), 1) if total_rows > 0 else 0.0
                field_reports[field_name] = {
                    "status": "MATCHED",
                    "matched_column": matched_col,
                    "expected_aliases": aliases,
                    "non_null_count": non_null,
                    "completeness_pct": pct,
                }
            else:
                is_valid = False
                issue = f"Missing column alias for required field '{field_name}' (expected one of {aliases})"
                critical_issues.append(issue)
                field_reports[field_name] = {
                    "status": "UNMATCHED_ALIAS",
                    "matched_column": None,
                    "expected_aliases": aliases,
                    "non_null_count": 0,
                    "completeness_pct": 0.0,
                }

        audit_result = {
            "is_valid": is_valid,
            "schema_hash": schema_hash,
            "total_columns": len(df.columns),
            "total_rows": total_rows,
            "field_reports": field_reports,
            "critical_issues": critical_issues,
        }

        if not is_valid:
            logger.error(
                f"🚨 [PRE_RECOVERY_SCHEMA_GATE] Schema validation failed (schema_hash={schema_hash}): "
                f"{critical_issues}. Available columns: {list(df.columns[:15])}..."
            )
        else:
            logger.info(
                f"✅ [PRE_RECOVERY_SCHEMA_GATE] Schema validation passed (schema_hash={schema_hash}). "
                f"Completeness: " + ", ".join(f"{k}={v['completeness_pct']}%" for k, v in field_reports.items())
            )

        return audit_result

    # ------------------------------------------------------------------
    # Field-level incomplete detection
    # ------------------------------------------------------------------

    def identify_incomplete_symbols(
        self, df: pd.DataFrame
    ) -> Tuple[List[str], Dict[str, List[str]]]:
        """
        Scans PIT DataFrame for any missing required fields using alias matching.
        Ignores structurally ineligible (e.g. BFSI) symbols for debt/operating metrics.
        Returns:
          - List of incomplete symbols
          - Dict mapping symbol → list of missing field names
        """
        field_map: Dict[str, List[str]] = {}

        # Isolate eligible non-BFSI symbols for debt/operating metrics check
        eval_df = df
        if "is_bfsi" in df.columns:
            eval_df = df[~df["is_bfsi"].fillna(False)]
        elif "is_structural_ineligible" in df.columns:
            eval_df = df[~df["is_structural_ineligible"].fillna(False)]

        for field_name, aliases in REQUIRED_FIELD_ALIASES.items():
            matched_col = None
            for alias in aliases:
                if alias in eval_df.columns:
                    matched_col = alias
                    break

            if matched_col is None:
                # Column missing entirely → Log schema warning and avoid false universal incompleteness storm
                logger.warning(
                    f"⚠️ [PRE_RECOVERY] Column missing entirely for field '{field_name}' (expected aliases: {aliases}). "
                    f"Skipping universal missing marking to protect provider quotas."
                )
            else:
                missing_mask = eval_df[matched_col].isna()
                for sym in eval_df[missing_mask]["symbol"].unique():
                    field_map.setdefault(str(sym), []).append(field_name)

        # Check PIT filing staleness (> 2.0Y old) or missing latest_annual_period
        period_col = None
        for col_candidate in ["latest_annual_period", "period_end_date", "financial_period_end", "period_end"]:
            if col_candidate in eval_df.columns:
                period_col = col_candidate
                break

        if period_col is not None:
            try:
                from datetime import datetime
                import pytz
                IST = pytz.timezone("Asia/Kolkata")
                now_date = datetime.now(IST).date()

                for _, r in eval_df.iterrows():
                    sym = str(r.get("symbol", "") or "").strip()
                    if not sym:
                        continue
                    p_val = r.get(period_col)
                    if pd.isna(p_val) or not p_val:
                        field_map.setdefault(sym, []).append("latest_annual_period")
                    else:
                        try:
                            p_str = str(p_val)[:10]
                            dt = datetime.strptime(p_str, "%Y-%m-%d").date()
                            staleness_years = (now_date - dt).days / 365.25
                            if staleness_years > 2.0:
                                field_map.setdefault(sym, []).append("pit_staleness")
                                try:
                                    try:
                                        from database import add_symbol_to_cooloff
                                    except ImportError:
                                        from app.database import add_symbol_to_cooloff
                                    add_symbol_to_cooloff(
                                        symbol=sym,
                                        reason=f"PIT_STALENESS_{round(staleness_years, 1)}Y_EXCEEDS_2.0Y",
                                        scanner="ALL",
                                        duration_days=7
                                    )
                                except Exception:
                                    pass
                        except Exception:
                            pass
            except Exception as st_err:
                logger.debug(f"[PRE_RECOVERY] Staleness check notice: {st_err}")

        return list(field_map.keys()), field_map

    def build_recovery_queue(
        self, field_map: Dict[str, List[str]]
    ) -> List[Tuple[str, List[str]]]:
        """
        Builds the recovery queue.
        Prioritizes symbols missing the most fields (worst first).
        If global_daily_recovery_limit > 0, caps the queue; otherwise uncapped (all symbols).
        Returns: list of (symbol, [missing_fields])
        """
        ordered = sorted(field_map.items(), key=lambda x: len(x[1]), reverse=True)
        if self.global_daily_recovery_limit > 0:
            return ordered[: self.global_daily_recovery_limit]
        return ordered

    # ------------------------------------------------------------------
    # Recovery
    # ------------------------------------------------------------------

    def recover_symbol(self, symbol: str) -> ReconciledCanonicalMetrics:
        """Invokes the dual-source router to progressively fetch and reconcile."""
        return self.router.execute_progressive_recovery(symbol, as_of_timestamp=self.as_of_timestamp)

    def persist_verified_record(
        self,
        df: pd.DataFrame,
        symbol: str,
        metrics: ReconciledCanonicalMetrics,
    ) -> None:
        """Idempotently updates the in-memory DataFrame with verified canonical data."""
        idx = df["symbol"] == symbol

        for col in ["roce_5y_avg", "ROCE", "roce"]:
            if col in df.columns:
                if metrics.roce_5y is not None:
                    df.loc[idx, col] = metrics.roce_5y
                break
        for col in ["sales_cagr_5y", "sales_cagr"]:
            if col in df.columns:
                if metrics.sales_cagr_5y is not None:
                    df.loc[idx, col] = metrics.sales_cagr_5y
                break
        for col in ["pat_cagr_5y", "pat_cagr"]:
            if col in df.columns:
                if metrics.pat_cagr_5y is not None:
                    df.loc[idx, col] = metrics.pat_cagr_5y
                break
        for col in ["cfo_pat_5y_ratio", "cfo_pat_5y", "cfo_pat"]:
            if col in df.columns:
                if metrics.cfo_pat_5y is not None:
                    df.loc[idx, col] = metrics.cfo_pat_5y
                break
        for col in ["debt_to_equity", "debt"]:
            if col in df.columns:
                if metrics.debt_to_equity is not None:
                    df.loc[idx, col] = metrics.debt_to_equity
                break
        for col in ["share_dilution_3y_pct", "share_dilution_3y"]:
            if col in df.columns:
                if metrics.share_dilution_3y is not None:
                    df.loc[idx, col] = metrics.share_dilution_3y
                break

        # [RULE 67 CHANGE-RATIONALE: Progressive recovery of current_ev_ebitda & current_pe from Upstox Key Ratios]
        if "current_ev_ebitda" in df.columns and (df.loc[idx, "current_ev_ebitda"].isna().any() or df.loc[idx, "current_ev_ebitda"].iloc[0] is None):
            try:
                val_ratios = self.router.recover_valuation_ratios(symbol)
                if val_ratios and val_ratios.get("current_ev_ebitda") is not None:
                    df.loc[idx, "current_ev_ebitda"] = val_ratios["current_ev_ebitda"]
                    if "current_pe" in df.columns and val_ratios.get("current_pe") is not None:
                        df.loc[idx, "current_pe"] = val_ratios["current_pe"]
                    logger.info(f"⚡ [PRE_RECOVERY] Recovered valuation for {symbol}: EV/EBITDA={val_ratios['current_ev_ebitda']}, PE={val_ratios.get('current_pe')}")
            except Exception as _ve:
                logger.debug(f"[PRE_RECOVERY] Valuation recovery notice for {symbol}: {_ve}")

        for col in ["calculation_version", "recovery_status", "provenance_hash"]:
            if col not in df.columns:
                df[col] = None

        df.loc[idx, "calculation_version"] = CALCULATION_VERSION
        df.loc[idx, "recovery_status"] = metrics.overall_status.name
        df.loc[idx, "provenance_hash"] = _generate_deterministic_key(symbol, metrics, as_of_timestamp=self.as_of_timestamp)

        # Layer B — Persist to Validated Recovery Disk Cache for cross-scanner and process restart reuse
        try:
            val_cache = getattr(self, "validated_cache", None)
            if val_cache is None:
                try:
                    from app.pit_recovery_cache import get_validated_recovery_cache
                except ImportError:
                    from pit_recovery_cache import get_validated_recovery_cache
                val_cache = get_validated_recovery_cache()
            rec_fields = metrics.recovered_fields
            if rec_fields:
                val_cache.save_validated_record(
                    symbol=symbol,
                    fields=rec_fields,
                    evidence_fingerprint=_generate_deterministic_key(symbol, metrics, as_of_timestamp=self.as_of_timestamp),
                    calculation_version=CALCULATION_VERSION,
                )
                for f_name, f_val in rec_fields.items():
                    logger.info(
                        f"RECOVERY_TRACE scanner={self.scanner_name} symbol={symbol} field={f_name} "
                        f"cache_status=MISS provider={metrics.overall_status.name} provider_status=SUCCESS "
                        f"validation_status=VERIFIED persist_status=PERSISTED final_status=AVAILABLE_TO_SCANNER"
                    )
                # Clear UPDATE_PENDING in filing_watcher_state once verified
                try:
                    state_file = "data/filing_watcher_state.json"
                    if os.path.exists(state_file):
                        with open(state_file, "r") as sf:
                            st_data = json.load(sf)
                        if symbol.upper() in st_data and st_data[symbol.upper()].get("snapshot_status") == "UPDATE_PENDING":
                            st_data[symbol.upper()]["snapshot_status"] = "FRESH"
                            tmp = f"{state_file}.tmp.{os.getpid()}"
                            with open(tmp, "w") as tf:
                                json.dump(st_data, tf, indent=2)
                            os.replace(tmp, state_file)
                except Exception:
                    pass
        except Exception as _vc_err:
            logger.error(f"❌ [PRE_RECOVERY] Validated disk cache write error for {symbol}: {_vc_err}")

    def publish_recovery_status(
        self, symbol: str, missing_fields: List[str], metrics: ReconciledCanonicalMetrics
    ) -> None:
        """
        # [RULE 67 CHANGE-RATIONALE: VERIFIED with NULL field = 0.
        # Only log VERIFIED if the recovered metrics actually populated the missing fields.
        # If missing fields are still None, log as PARTIAL_RECOVERY or DATA_INSUFFICIENT.]
        """
        status = metrics.overall_status
        field_vals = {
            "ROCE": metrics.roce_5y,
            "sales_cagr_5y": metrics.sales_cagr_5y,
            "pat_cagr_5y": metrics.pat_cagr_5y,
            "cfo_pat_5y": metrics.cfo_pat_5y,
            "debt": metrics.debt_to_equity,
            "share_dilution_3y": metrics.share_dilution_3y,
        }
        still_missing = [f for f in missing_fields if field_vals.get(f) is None and f != "current_ev_ebitda"]
        recovered = [f for f in missing_fields if field_vals.get(f) is not None or f == "current_ev_ebitda"]

        if status in (FundamentalStatus.VERIFIED, FundamentalStatus.VERIFIED_SINGLE_SOURCE) and not still_missing:
            logger.info(
                f"✅ [PRE_RECOVERY] {status.name} {symbol} | all fields recovered: {recovered}"
            )
        elif recovered:
            logger.info(
                f"⚡ [PRE_RECOVERY] PARTIAL_RECOVERY {symbol} | "
                f"recovered: {recovered} | still missing: {still_missing}"
            )
        elif status == FundamentalStatus.DATA_CONFLICT:
            logger.error(
                f"❌ [PRE_RECOVERY] DATA_CONFLICT {symbol} | "
                f"fields: {missing_fields} | blocking"
            )
        else:
            logger.warning(
                f"⚠️ [PRE_RECOVERY] {status.name} {symbol} | "
                f"failed to recover missing fields: {missing_fields}"
            )

    # ------------------------------------------------------------------
    # Main sweep
    # ------------------------------------------------------------------

    def execute_pre_scan_sweep(self) -> None:
        """
        Orchestrator for the Pre-Recovery Backfill sweep.
        Called EXACTLY ONCE per scanner run at orchestration level.
        """
        logger.info(f"🧹 [SCANNER: {self.scanner_name}] [FETCH_DATA] [PRE_RECOVERY] START — pre-scan fundamental sweep.")

        if not os.path.exists(self.pit_parquet_path):
            logger.warning(
                f"⚠️ [SCANNER: {self.scanner_name}] [FETCH_DATA] [PRE_RECOVERY] PIT dataset not found at {self.pit_parquet_path}. Skipping."
            )
            return

        try:
            df = pd.read_parquet(self.pit_parquet_path)
        except Exception as e:
            logger.error(f"❌ [SCANNER: {self.scanner_name}] [FETCH_DATA] [PRE_RECOVERY] Failed to load PIT parquet: {e}")
            return

        total_universe = len(df)
        schema_audit = self.validate_pre_recovery_cache_schema(df)
        if not schema_audit["is_valid"] and total_universe > 50:
            logger.warning(
                f"⚠️ [SCANNER: {self.scanner_name}] Pre-recovery schema validation flagged issues: {schema_audit['critical_issues']}."
            )

        try:
            try:
                from database import get_active_cooloff_symbols
            except ImportError:
                from app.database import get_active_cooloff_symbols
            active_cooloff = get_active_cooloff_symbols(self.scanner_name) | get_active_cooloff_symbols("ALL")
        except Exception:
            active_cooloff = set()

        if active_cooloff and "symbol" in df.columns:
            orig_total = total_universe
            df = df[~df["symbol"].astype(str).str.strip().str.upper().isin(active_cooloff)].copy()
            total_universe = len(df)
            logger.info(
                f"🛡️ [PRE_RECOVERY_COOLOFF] Excluded {orig_total - total_universe} symbols under active 7-day cool-off from pre-recovery sweep. Active universe reduced from {orig_total} to {total_universe}."
            )

        incomplete_symbols, field_map = self.identify_incomplete_symbols(df)
        complete_count = total_universe - len(incomplete_symbols)

        # Field-level diagnostic log
        # [RULE 67 CHANGE-RATIONALE: Clarify Recovery queue cap = UNCAPPED (0) logging]
        cap_str = "UNCAPPED (0)" if self.global_daily_recovery_limit <= 0 else str(self.global_daily_recovery_limit)
        logger.info(
            f"🔍 [SCANNER: {self.scanner_name}] [FETCH_DATA] [PRE_RECOVERY] Universe={total_universe} | "
            f"Complete={complete_count} | "
            f"Incomplete={len(incomplete_symbols)} | "
            f"Recovery queue cap = {cap_str}"
        )
        # Per-symbol field breakdown (first 20 for readability)
        for sym, missing in list(field_map.items())[:20]:
            logger.info(f"   [SCANNER: {self.scanner_name}] [FETCH_DATA] [PRE_RECOVERY] {sym} → missing: {missing}")
        if len(field_map) > 20:
            logger.info(f"   [SCANNER: {self.scanner_name}] [FETCH_DATA] [PRE_RECOVERY] ... and {len(field_map) - 20} more symbols")

        recovery_queue = self.build_recovery_queue(field_map)
        if not recovery_queue:
            logger.info(f"✅ [SCANNER: {self.scanner_name}] [FETCH_DATA] [PRE_RECOVERY] All symbols complete. No recovery needed.")
            return

        logger.info(f"🚀 [SCANNER: {self.scanner_name}] [FETCH_DATA] [PRE_RECOVERY] Starting data recovery for {len(recovery_queue)} symbols.")
        recovery_success_count = 0
        promoted_count = 0

        for symbol, missing_fields in recovery_queue:
            logger.info(f"📥 [SCANNER: {self.scanner_name}] [FETCH_DATA] Fetching Upstox/NSE filings for {symbol} (missing: {missing_fields})...")
            metrics = self.recover_symbol(symbol)
            self.publish_recovery_status(symbol, missing_fields, metrics)

            has_recovered = bool(metrics.recovered_fields) or metrics.overall_status in (
                FundamentalStatus.VERIFIED,
                FundamentalStatus.VERIFIED_SINGLE_SOURCE,
                FundamentalStatus.PARTIAL_RECOVERY,
                FundamentalStatus.DATA_RECOVERED,
            )
            if has_recovered:
                recovery_success_count += 1

            # ONLY promote to in-memory dataset if reconciliation succeeded
            if metrics.overall_status in (
                FundamentalStatus.VERIFIED,
                FundamentalStatus.VERIFIED_SINGLE_SOURCE,
            ):
                self.persist_verified_record(df, symbol, metrics)
                promoted_count += 1
                logger.info(f"✅ [PRE_RECOVERY] {symbol}: recovery_status=SUCCESS | promotion_status=PROMOTED | fields={list(metrics.recovered_fields.keys())}")
            elif metrics.overall_status == FundamentalStatus.PARTIAL_RECOVERY or has_recovered:
                logger.info(
                    f"⚠️ [PRE_RECOVERY] {symbol}: recovery_status=PARTIAL_SUCCESS | promotion_status=BLOCKED | "
                    f"promotion_reason=VALIDATION_INCOMPLETE | recovered_fields={list(metrics.recovered_fields.keys())}"
                )
            else:
                audit_reason = metrics.rejection_reason or metrics.overall_status.value
                logger.info(
                    f"⚠️ [PRE_RECOVERY] {symbol}: recovery_status=UNAVAILABLE | promotion_status=BLOCKED | "
                    f"reason={audit_reason}"
                )
                try:
                    from app.pit_recovery_cache import get_pit_recovery_store
                    store = get_pit_recovery_store()
                    trace = getattr(self.router, "last_trace", {}) or {}
                    attempts_map = {}
                    if trace.get("nse_status"): attempts_map["NSE_XBRL"] = trace["nse_status"]
                    if trace.get("bse_status"): attempts_map["BSE_CORPORATE"] = trace["bse_status"]
                    if trace.get("upstox_status"): attempts_map["UPSTOX"] = trace["upstox_status"]
                    if trace.get("fyers_status"): attempts_map["FYERS"] = trace["fyers_status"]
                    if trace.get("screener_status"): attempts_map["SCREENER"] = trace["screener_status"]

                    fld = missing_fields[0] if missing_fields else "sales_cagr_5y"
                    aud = self.auditor.audit_stock(symbol, trace, target_field=fld)

                    store.record_unavailability(
                        symbol=symbol,
                        provider=trace.get("resolved_source", "ROUTER"),
                        status=aud.get("availability_status", "DATA_UNAVAILABLE"),
                        reason=aud.get("block_reason") or aud.get("final_classification", "DATA_UNAVAILABLE"),
                        scanner_family="QUALITY_COMPOUNDER",
                        field=fld,
                        missing_fields=missing_fields,
                        source_attempts=attempts_map,
                        raw_record_count=trace.get("annual_record_count", 0),
                        latest_filing_date=trace.get("latest_filing_date"),
                        latest_period_end=trace.get("latest_period_end"),
                    )
                except Exception as _rec_err:
                    logger.debug(f"[PRE_RECOVERY] Store recording notice for {symbol}: {_rec_err}")

                try:
                    try:
                        from database import add_symbol_to_cooloff
                    except ImportError:
                        from app.database import add_symbol_to_cooloff
                    reason_desc = (
                        f"DATA_CONFLICT_ACROSS_PROVIDERS: {missing_fields}"
                        if metrics.overall_status == FundamentalStatus.DATA_CONFLICT
                        else f"RECOVERY_UNAVAILABLE: {missing_fields}"
                    )
                    add_symbol_to_cooloff(
                        symbol=symbol,
                        reason=reason_desc,
                        scanner="ALL",
                        duration_days=7
                    )
                except Exception:
                    pass

        published = False
        if promoted_count > 0:
            candidate_path = self.pit_parquet_path.replace(
                ".parquet", "_pre_recovery_candidate.parquet"
            )
            logger.info(
                f"💾 [PRE_RECOVERY] Writing staging candidate: "
                f"{promoted_count} symbols promoted → {candidate_path}"
            )
            df.to_parquet(candidate_path, index=False)

            # Route through canonical publisher (Never-Downgrade Gate)
            publish_canonical_pit = None
            try:
                from scripts.canonical_pit_publisher import publish_canonical_pit
            except ImportError:
                try:
                    from canonical_pit_publisher import publish_canonical_pit
                except ImportError:
                    logger.error(
                        "[PRE_RECOVERY] canonical_pit_publisher not importable. "
                        "Candidate written but NOT promoted to canonical."
                    )

            if publish_canonical_pit is not None:
                pub_result = publish_canonical_pit(
                    candidate_path=candidate_path,
                    reason=f"PRE_RECOVERY_SWEEP_{datetime.now().strftime('%Y%m%d_%H%M%S')}",
                    publisher_version="v3.0",
                )
                decision = pub_result.get("publication_decision", "UNKNOWN")
                if decision == "PUBLISHED":
                    published = True
                    logger.info(
                        f"✅ [PRE_RECOVERY] Canonical PIT updated via publisher. "
                        f"SHA256: {pub_result.get('dataset_sha256', 'N/A')[:16]}..."
                    )
                else:
                    logger.warning(
                        f"⚠️ [PRE_RECOVERY] Publisher blocked candidate ({decision}): "
                        f"recovery_status=SUCCESS | promotion_status=BLOCKED | "
                        f"promotion_reason=NEVER_DOWNGRADE/VALIDATION | gate_reasons={pub_result.get('gate_reasons', pub_result.get('reason', ''))}"
                    )
        else:
            if recovery_success_count > 0:
                logger.info(
                    f"ℹ️ [PRE_RECOVERY] Upstream recovery succeeded for {recovery_success_count} symbols, but 0 promoted to candidate: "
                    f"promotion_status=BLOCKED | promotion_reason=STRICT_VERIFICATION. Canonical PIT unchanged."
                )
            else:
                logger.info(
                    "✅ [PRE_RECOVERY] No symbols successfully recovered. Canonical PIT unchanged."
                )

        self._run_availability_audit(df, recovery_queue, published)
        logger.info("🏁 [FUNDAMENTAL_PRE_RECOVERY] END.")

    # ------------------------------------------------------------------
    # Recovery-diagnostics (availability audit)
    # ------------------------------------------------------------------

    def _run_availability_audit(
        self,
        df: pd.DataFrame,
        recovery_queue: List[Tuple[str, List[str]]],
        published: bool,
    ) -> None:
        """
        [RULE 67 CHANGE-RATIONALE: DATA_INSUFFICIENT must distinguish "nobody has the data" from
        "our Tier-1 pipeline failed to obtain data that exists". Every field still missing after
        Tier-1 recovery is classified by DataAvailabilityAuditor (diagnostic only — never writes PIT,
        never feeds the scanner, never creates alerts). If the publisher did not publish, the
        canonical PIT is unchanged, so every originally-missing field is still missing.]
        """
        try:
            unresolved: Dict[str, List[str]] = {}
            for symbol, missing_fields in recovery_queue:
                row = df.loc[df["symbol"] == symbol]
                remaining: List[str] = []
                for f in missing_fields:
                    if not published or row.empty:
                        remaining.append(f)
                        continue
                    col = next((a for a in REQUIRED_FIELD_ALIASES.get(f, [f]) if a in df.columns), None)
                    if col is None or pd.isna(row[col].iloc[0]):
                        remaining.append(f)
                if remaining:
                    unresolved[symbol] = remaining

            if not unresolved:
                logger.info(f"✅ [SCANNER: {self.scanner_name}] [DATA_AVAILABILITY_AUDIT] No unresolved fields.")
                return

            auditor = DataAvailabilityAuditor(scanner_name=self.scanner_name)
            auditor.audit(unresolved, getattr(self.router, "last_trace", {}) or {})
        except Exception as e:
            logger.error(
                f"❌ [SCANNER: {self.scanner_name}] [DATA_AVAILABILITY_AUDIT] failed (diagnostic only, "
                f"scan unaffected): {e}"
            )
