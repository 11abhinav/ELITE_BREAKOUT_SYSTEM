import logging
import pandas as pd
import os
import hashlib
from typing import List, Set
from datetime import datetime
from data_providers.fundamental_source_router import FundamentalSourceRouter
from data_providers.fundamental_models import FundamentalStatus, ReconciledCanonicalMetrics

logger = logging.getLogger(__name__)

class FundamentalPreRecoveryEngine:
    """
    Sweeps the canonical PIT dataset before scanner execution.
    Identifies symbols with missing/insufficient data and executes the Progressive Dual-Source Recovery.
    Completely eliminates scanner-side JIT dependencies and scan-order bias.
    Maintains strict idempotency and provenance versioning.
    """
    
    CALCULATION_VERSION = "v2.0_dual_source_reconciliation"
    
    def __init__(self, pit_parquet_path: str = "data/daily_builder_master_v2.parquet"):
        self.pit_parquet_path = pit_parquet_path
        self.router = FundamentalSourceRouter()
        
        # JIT limits
        self.global_daily_jit_limit = 50
        self._current_daily_fetches = 0
        
    def _generate_deterministic_key(self, symbol: str, metrics: ReconciledCanonicalMetrics) -> str:
        """Generates a deterministic hash for forensic traceability, strictly omitting retrieval timestamp."""
        # A true idempotent hash based on raw canonical values and versions
        payload = (
            f"{symbol}|{self.CALCULATION_VERSION}|"
            f"roce={metrics.roce_5y}|"
            f"sales_cagr={metrics.sales_cagr_5y}|"
            f"pat_cagr={metrics.pat_cagr_5y}|"
            f"cfo_pat={metrics.cfo_pat_5y}|"
            f"debt={metrics.debt_to_equity}"
        )
        return hashlib.sha256(payload.encode()).hexdigest()

    def identify_incomplete_symbols(self, df: pd.DataFrame) -> List[str]:
        """Scans the PIT DataFrame for any missing fundamental required fields."""
        required_cols = ["ROCE", "sales_cagr_5y", "pat_cagr_5y", "cfo_pat_5y", "debt"]
        
        missing_mask = pd.Series(False, index=df.index)
        for col in required_cols:
            if col in df.columns:
                missing_mask |= df[col].isna()
            else:
                missing_mask = pd.Series(True, index=df.index)
                break
                
        if "symbol" in df.columns:
            return df[missing_mask]["symbol"].unique().tolist()
        return []

    def build_recovery_queue(self, incomplete_symbols: List[str]) -> List[str]:
        """Sorts or prioritizes the queue. Can drop symbols already attempted today."""
        # Stub: just return up to the global limit to prevent runaway fetches
        return incomplete_symbols[:self.global_daily_jit_limit]

    def recover_symbol(self, symbol: str) -> ReconciledCanonicalMetrics:
        """Invokes the dual-source router to progressively fetch and reconcile."""
        self._current_daily_fetches += 1
        return self.router.execute_progressive_recovery(symbol)
        
    def persist_verified_record(self, df: pd.DataFrame, symbol: str, metrics: ReconciledCanonicalMetrics):
        """Idempotently updates the DataFrame with verified canonical data."""
        idx = df["symbol"] == symbol
        
        if "ROCE" in df.columns: df.loc[idx, "ROCE"] = metrics.roce_5y
        if "sales_cagr_5y" in df.columns: df.loc[idx, "sales_cagr_5y"] = metrics.sales_cagr_5y
        if "pat_cagr_5y" in df.columns: df.loc[idx, "pat_cagr_5y"] = metrics.pat_cagr_5y
        if "cfo_pat_5y" in df.columns: df.loc[idx, "cfo_pat_5y"] = metrics.cfo_pat_5y
        if "debt" in df.columns: df.loc[idx, "debt"] = metrics.debt_to_equity
        
        # Add provenance tracking columns if they don't exist
        for col in ["calculation_version", "recovery_status", "provenance_hash"]:
            if col not in df.columns:
                df[col] = None
                
        retrieved = datetime.now().isoformat()
        df.loc[idx, "calculation_version"] = self.CALCULATION_VERSION
        df.loc[idx, "recovery_status"] = metrics.overall_status.name
        df.loc[idx, "provenance_hash"] = self._generate_deterministic_key(symbol, metrics)
        df.loc[idx, "retrieved_at"] = retrieved

    def publish_recovery_status(self, symbol: str, status: FundamentalStatus):
        """Logs the final exact outcome to the audit trail."""
        if status in (FundamentalStatus.VERIFIED, FundamentalStatus.VERIFIED_SINGLE_SOURCE):
            logger.info(f"✅ [PRE_RECOVERY] Successfully recovered and VERIFIED {symbol}.")
        elif status in (FundamentalStatus.INSUFFICIENT, FundamentalStatus.DATA_INSUFFICIENT):
            logger.warning(f"⚠️ [PRE_RECOVERY] {symbol} data is genuinely INSUFFICIENT across all sources.")
        elif status == FundamentalStatus.DATA_CONFLICT:
            logger.error(f"❌ [PRE_RECOVERY] {symbol} data CONFLICT between Upstox and NSE. Blocking.")

    def execute_pre_scan_sweep(self) -> None:
        """Main orchestrator for the Pre-Recovery Backfill Queue."""
        logger.info("🧹 [PRE_RECOVERY] Starting pre-scan fundamental sweep.")
        
        if not os.path.exists(self.pit_parquet_path):
            logger.warning(f"PIT Database not found at {self.pit_parquet_path}. Skipping pre-recovery.")
            return
            
        try:
            df = pd.read_parquet(self.pit_parquet_path)
        except Exception as e:
            logger.error(f"Failed to load PIT Parquet: {e}")
            return
            
        incomplete_symbols = self.identify_incomplete_symbols(df)
        logger.info(f"🔍 [PRE_RECOVERY] Found {len(incomplete_symbols)} symbols requiring fundamental recovery.")
        
        recovery_queue = self.build_recovery_queue(incomplete_symbols)
        
        recovered_count = 0
        for symbol in recovery_queue:
            metrics = self.recover_symbol(symbol)
            self.publish_recovery_status(symbol, metrics.overall_status)
            
            # ONLY promote to canonical PIT if reconciliation succeeded
            if metrics.overall_status in (FundamentalStatus.VERIFIED, FundamentalStatus.VERIFIED_SINGLE_SOURCE):
                self.persist_verified_record(df, symbol, metrics)
                recovered_count += 1
                
        if recovered_count > 0:
            candidate_path = self.pit_parquet_path.replace(".parquet", "_pre_recovery_candidate.parquet")
            logger.info(f"💾 [PRE_RECOVERY] Saving candidate PIT dataset with {recovered_count} newly verified symbols to {candidate_path}")
            df.to_parquet(candidate_path, index=False)
        else:
            logger.info("✅ [PRE_RECOVERY] No new verified data recovered. PIT unchanged.")

