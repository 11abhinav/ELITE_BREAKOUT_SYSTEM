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
    "ROCE":             ["roce_5y_avg", "ROCE", "roce"],
    "sales_cagr_5y":     ["sales_cagr_5y", "sales_cagr"],
    "pat_cagr_5y":       ["pat_cagr_5y", "pat_cagr"],
    "cfo_pat_5y":        ["cfo_pat_5y_ratio", "cfo_pat_5y", "cfo_pat"],
    "debt":              ["debt_to_equity", "debt", "total_debt"],
    "current_ev_ebitda": ["current_ev_ebitda"],
}
REQUIRED_FIELDS: List[str] = list(REQUIRED_FIELD_ALIASES.keys())

CALCULATION_VERSION = "v2.1_dual_source_reconciliation"


def _generate_deterministic_key(symbol: str, metrics: ReconciledCanonicalMetrics) -> str:
    """SHA256 hash of the canonical metrics — excludes retrieval timestamp."""
    payload = (
        f"{symbol}|{CALCULATION_VERSION}|"
        f"roce={metrics.roce_5y}|"
        f"sales_cagr={metrics.sales_cagr_5y}|"
        f"pat_cagr={metrics.pat_cagr_5y}|"
        f"cfo_pat={metrics.cfo_pat_5y}|"
        f"debt={metrics.debt_to_equity}"
    )
    return hashlib.sha256(payload.encode()).hexdigest()


class FundamentalPreRecoveryEngine:
    """
    Pre-scan sweep engine: identifies missing fields at field level and recovers
    them via the Dual-Source Router (Upstox + NSE). Writes to a staging candidate;
    does NOT directly overwrite the canonical PIT dataset.
    """

    def __init__(self, pit_parquet_path: Optional[str] = None, scanner_name: str = "QUALITY_VALUE_RECOVERY"):
        self.scanner_name = scanner_name
        if pit_parquet_path is not None:
            self.pit_parquet_path = pit_parquet_path
        else:
            canonical_path = "data/canonical_pit_rebuilt.parquet"
            if os.path.exists(canonical_path):
                self.pit_parquet_path = canonical_path
            else:
                self.pit_parquet_path = "data/daily_builder_master_v2.parquet"
        self.router = FundamentalSourceRouter()
        # 0 or negative means uncapped (processes every incomplete symbol in universe)
        self.global_daily_recovery_limit = int(os.getenv("PRE_RECOVERY_LIMIT", "0"))

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
                # Column missing entirely → all symbols affected
                for sym in eval_df.get("symbol", pd.Series([])).unique():
                    field_map.setdefault(str(sym), []).append(field_name)
            else:
                missing_mask = eval_df[matched_col].isna()
                for sym in eval_df[missing_mask]["symbol"].unique():
                    field_map.setdefault(str(sym), []).append(field_name)

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
        return self.router.execute_progressive_recovery(symbol)

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
                df.loc[idx, col] = metrics.roce_5y
                break
        for col in ["sales_cagr_5y", "sales_cagr"]:
            if col in df.columns:
                df.loc[idx, col] = metrics.sales_cagr_5y
                break
        for col in ["pat_cagr_5y", "pat_cagr"]:
            if col in df.columns:
                df.loc[idx, col] = metrics.pat_cagr_5y
                break
        for col in ["cfo_pat_5y_ratio", "cfo_pat_5y", "cfo_pat"]:
            if col in df.columns:
                df.loc[idx, col] = metrics.cfo_pat_5y
                break
        for col in ["debt_to_equity", "debt"]:
            if col in df.columns:
                df.loc[idx, col] = metrics.debt_to_equity
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
        df.loc[idx, "provenance_hash"] = _generate_deterministic_key(symbol, metrics)

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
        recovered_count = 0

        for symbol, missing_fields in recovery_queue:
            logger.info(f"📥 [SCANNER: {self.scanner_name}] [FETCH_DATA] Fetching Upstox/NSE filings for {symbol} (missing: {missing_fields})...")
            metrics = self.recover_symbol(symbol)
            self.publish_recovery_status(symbol, missing_fields, metrics)

            # ONLY promote to in-memory dataset if reconciliation succeeded
            if metrics.overall_status in (
                FundamentalStatus.VERIFIED,
                FundamentalStatus.VERIFIED_SINGLE_SOURCE,
            ):
                self.persist_verified_record(df, symbol, metrics)
                recovered_count += 1
                logger.info(f"✅ [PRE_RECOVERY] {symbol}: recovery_status=SUCCESS | promotion_status=PROMOTED | fields={list(metrics.recovered_fields.keys())}")
            else:
                logger.info(
                    f"⚠️ [PRE_RECOVERY] {symbol}: recovery_status=FETCH_COMPLETED | promotion_status=BLOCKED | "
                    f"reason={metrics.rejection_reason or metrics.overall_status.value} | "
                    f"unverified_fields={list(metrics.recovered_fields.keys())}"
                )

        published = False
        if recovered_count > 0:
            candidate_path = self.pit_parquet_path.replace(
                ".parquet", "_pre_recovery_candidate.parquet"
            )
            logger.info(
                f"💾 [PRE_RECOVERY] Writing staging candidate: "
                f"{recovered_count} symbols recovered → {candidate_path}"
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
                        f"{pub_result.get('gate_reasons', pub_result.get('reason', ''))}"
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
