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
from typing import Dict, List, Optional, Set, Tuple

import pandas as pd

try:
    from app.data_providers.fundamental_source_router import FundamentalSourceRouter
    from app.data_providers.fundamental_models import FundamentalStatus, ReconciledCanonicalMetrics
except ImportError:
    from data_providers.fundamental_source_router import FundamentalSourceRouter
    from data_providers.fundamental_models import FundamentalStatus, ReconciledCanonicalMetrics

logger = logging.getLogger(__name__)

# Required fields and aliases for a symbol to be considered "complete" for scanner use
REQUIRED_FIELD_ALIASES: Dict[str, List[str]] = {
    "ROCE":         ["roce_5y_avg", "ROCE", "roce"],
    "sales_cagr_5y": ["sales_cagr_5y", "sales_cagr"],
    "pat_cagr_5y":   ["pat_cagr_5y", "pat_cagr"],
    "cfo_pat_5y":    ["cfo_pat_5y_ratio", "cfo_pat_5y", "cfo_pat"],
    "debt":          ["debt_to_equity", "debt", "total_debt"],
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

    def __init__(self, pit_parquet_path: Optional[str] = None):
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

        for col in ["calculation_version", "recovery_status", "provenance_hash"]:
            if col not in df.columns:
                df[col] = None

        df.loc[idx, "calculation_version"] = CALCULATION_VERSION
        df.loc[idx, "recovery_status"] = metrics.overall_status.name
        df.loc[idx, "provenance_hash"] = _generate_deterministic_key(symbol, metrics)

    def publish_recovery_status(
        self, symbol: str, missing_fields: List[str], status: FundamentalStatus
    ) -> None:
        """Logs the final outcome to the audit trail."""
        if status == FundamentalStatus.VERIFIED:
            logger.info(
                f"✅ [PRE_RECOVERY] VERIFIED {symbol} | fields recovered: {missing_fields}"
            )
        elif status == FundamentalStatus.VERIFIED_SINGLE_SOURCE:
            logger.info(
                f"⚡ [PRE_RECOVERY] VERIFIED_SINGLE_SOURCE {symbol} | "
                f"fields: {missing_fields} | second provider unavailable"
            )
        elif status in (FundamentalStatus.INSUFFICIENT, FundamentalStatus.DATA_INSUFFICIENT):
            logger.warning(
                f"⚠️ [PRE_RECOVERY] DATA_INSUFFICIENT {symbol} | "
                f"missing: {missing_fields} | blocking"
            )
        elif status == FundamentalStatus.DATA_CONFLICT:
            logger.error(
                f"❌ [PRE_RECOVERY] DATA_CONFLICT {symbol} | "
                f"fields: {missing_fields} | blocking"
            )
        else:
            logger.warning(
                f"⚠️ [PRE_RECOVERY] {status.name} {symbol} | fields: {missing_fields}"
            )

    # ------------------------------------------------------------------
    # Main sweep
    # ------------------------------------------------------------------

    def execute_pre_scan_sweep(self) -> None:
        """
        Orchestrator for the Pre-Recovery Backfill sweep.
        Called EXACTLY ONCE per scanner run at orchestration level.
        """
        logger.info("🧹 [FUNDAMENTAL_PRE_RECOVERY] START — pre-scan fundamental sweep.")

        if not os.path.exists(self.pit_parquet_path):
            logger.warning(
                f"[PRE_RECOVERY] PIT dataset not found at {self.pit_parquet_path}. Skipping."
            )
            return

        try:
            df = pd.read_parquet(self.pit_parquet_path)
        except Exception as e:
            logger.error(f"[PRE_RECOVERY] Failed to load PIT parquet: {e}")
            return

        total_universe = len(df)
        incomplete_symbols, field_map = self.identify_incomplete_symbols(df)
        complete_count = total_universe - len(incomplete_symbols)

        # Field-level diagnostic log
        logger.info(
            f"🔍 [PRE_RECOVERY] Universe={total_universe} | "
            f"Complete={complete_count} | "
            f"Incomplete={len(incomplete_symbols)} | "
            f"Recovery queue cap={self.global_daily_recovery_limit}"
        )
        # Per-symbol field breakdown (first 20 for readability)
        for sym, missing in list(field_map.items())[:20]:
            logger.info(f"   [PRE_RECOVERY] {sym} → missing: {missing}")
        if len(field_map) > 20:
            logger.info(f"   [PRE_RECOVERY] ... and {len(field_map) - 20} more symbols")

        recovery_queue = self.build_recovery_queue(field_map)
        if not recovery_queue:
            logger.info("✅ [PRE_RECOVERY] All symbols complete. No recovery needed.")
            return

        logger.info(f"🚀 [PRE_RECOVERY] Starting recovery for {len(recovery_queue)} symbols.")
        recovered_count = 0

        for symbol, missing_fields in recovery_queue:
            metrics = self.recover_symbol(symbol)
            self.publish_recovery_status(symbol, missing_fields, metrics.overall_status)

            # ONLY promote to in-memory dataset if reconciliation succeeded
            if metrics.overall_status in (
                FundamentalStatus.VERIFIED,
                FundamentalStatus.VERIFIED_SINGLE_SOURCE,
            ):
                self.persist_verified_record(df, symbol, metrics)
                recovered_count += 1

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
                    return

            pub_result = publish_canonical_pit(
                candidate_path=candidate_path,
                reason=f"PRE_RECOVERY_SWEEP_{datetime.now().strftime('%Y%m%d_%H%M%S')}",
                publisher_version="v3.0",
            )
            decision = pub_result.get("publication_decision", "UNKNOWN")
            if decision == "PUBLISHED":
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

        logger.info("🏁 [FUNDAMENTAL_PRE_RECOVERY] END.")
