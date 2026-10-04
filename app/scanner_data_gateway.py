"""
app/scanner_data_gateway.py
===========================
Universal Data Gateway for all Breakout System Scanners.

Architecture:
  ANY SCANNER
        │
        v
  ScannerDataGateway
        │
        ├───────────────────────┬────────────────────────┐
        v                       v                        v
  Canonical Base PIT      Validated Disk         Recovery Status Store
  (rebuilt.parquet)          Overlay                  (Quarantine)
        │                       │
        └───────────┬───────────┘
                    │
            Working Dataset
                    │
         (If field still missing)
                    │
                    v
         Validated Provider Router (NSE -> BSE -> Upstox -> FYERS)
                    │
          Field-Level Validation & Reconciliation
                    │
            Write Layer B Validated Cache
                    │
            Update Working Overlay
                    │
                    v
               ANY SCANNER
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional, Set, Tuple

import pandas as pd

try:
    from app.data_providers.fundamental_models import (
        FieldEvidence,
        FundamentalStatus,
        ProviderAttempt,
        RecoveryResult,
    )
    from app.pit_recovery_cache import (
        ValidatedRecoveryDiskCache,
        get_pit_recovery_store,
        get_validated_recovery_cache,
    )
except ImportError:
    from data_providers.fundamental_models import (
        FieldEvidence,
        FundamentalStatus,
        ProviderAttempt,
        RecoveryResult,
    )
    from pit_recovery_cache import (
        ValidatedRecoveryDiskCache,
        get_pit_recovery_store,
        get_validated_recovery_cache,
    )

logger = logging.getLogger(__name__)

# Authoritative Fundamental Recovery Scanner Registry
FUNDAMENTAL_RECOVERY_SCANNER_REGISTRY: Dict[str, Dict[str, Any]] = {
    "QUALITY_COMPOUNDER": {
        "file": "app/live_fundamental_scanner.py",
        "class": "QualityCompounderValueV2Scanner",
        "entrypoint": "load_pit_dataset",
        "lifecycle_state": "ACTIVE_PRODUCTION",
    },
    "QUALITY_VALUE_RECOVERY": {
        "file": "app/live_fundamental_scanner.py",
        "class": "QualityValueRecoveryScanner",
        "entrypoint": "load_pit_dataset",
        "lifecycle_state": "ACTIVE_PRODUCTION",
    },
    "LIVE_FUNDAMENTAL_BUY_SCANNER": {
        "file": "app/live_fundamental_scanner.py",
        "class": "LiveFundamentalBuyScanner",
        "entrypoint": "load_pit_dataset",
        "lifecycle_state": "ACTIVE_PRODUCTION",
    },
    "BEAR_QUALITY_RECOVERY_V1": {
        "file": "scripts/run_bear_quality_recovery_v1_master_program.py",
        "class": "BearQualityRecoveryV1Scanner",
        "entrypoint": "load_pit_dataset",
        "lifecycle_state": "RESEARCH_ONLY_CLOSED",
    },
}

# Backward-compatible alias
ACTIVE_SCANNER_REGISTRY = FUNDAMENTAL_RECOVERY_SCANNER_REGISTRY

# Field name mappings / aliases
FIELD_ALIAS_MAP: Dict[str, List[str]] = {
    "roce_5y": ["roce_5y_avg", "ROCE", "roce", "roce_5y"],
    "sales_cagr_5y": ["sales_cagr_5y", "sales_cagr"],
    "pat_cagr_5y": ["pat_cagr_5y", "pat_cagr"],
    "cfo_pat_5y": ["cfo_pat_5y_ratio", "cfo_pat_5y", "cfo_pat"],
    "debt_to_equity": ["debt_to_equity", "debt", "total_debt"],
    "share_dilution_3y": ["share_dilution_3y_pct", "share_dilution_3y"],
    "current_ev_ebitda": ["current_ev_ebitda"],
}


@dataclass(frozen=True)
class DataBundle:
    symbol: str
    fields: Dict[str, Any]
    verified_fields: Tuple[str, ...]
    missing_fields: Tuple[str, ...]
    blocked_fields: Tuple[str, ...]
    provenance: Dict[str, FieldEvidence] = field(default_factory=dict)
    recovery_result: Optional[RecoveryResult] = None
    source: str = "CANONICAL_BASE+VALIDATED_OVERLAY"
    evidence_fingerprint: str = ""


class ScannerDataGateway:
    """
    Central Data Access Gateway. Ensures every active scanner reads from the exact same
    working dataset (Canonical Base PIT + Validated Layer B Recovery Overlay).
    """

    def __init__(
        self,
        pit_parquet_path: Optional[str] = None,
        validated_cache: Optional[ValidatedRecoveryDiskCache] = None,
    ):
        self.pit_parquet_path = pit_parquet_path or "data/canonical_pit_rebuilt.parquet"
        self.validated_cache = validated_cache or get_validated_recovery_cache()
        self.recovery_store = get_pit_recovery_store()

    def get_working_dataset(self, pit_parquet_path: Optional[str] = None) -> pd.DataFrame:
        """
        Loads the Canonical PIT base dataset and applies the Layer B Validated Recovery Disk Cache overlay.
        Returns the unified working dataset.
        """
        path = pit_parquet_path or self.pit_parquet_path
        if not os.path.exists(path):
            logger.warning(f"⚠️ [GATEWAY] Base PIT parquet not found at {path}")
            return pd.DataFrame()

        df = pd.read_parquet(path)
        val_records = self.validated_cache.get_all_validated_records()
        if not val_records:
            return df

        overlay_count = 0
        for sym, rec in val_records.items():
            fields = rec.get("fields", {})
            idx = df["symbol"] == sym
            if not idx.any():
                continue
            for f_name, f_info in fields.items():
                val = f_info.get("value")
                if val is not None:
                    aliases = FIELD_ALIAS_MAP.get(f_name, [f_name])
                    target_col = next((a for a in aliases if a in df.columns), f_name)
                    if target_col not in df.columns:
                        df[target_col] = None
                    df.loc[idx, target_col] = val
                    overlay_count += 1

        logger.info(f"⚡ [GATEWAY] Applied {overlay_count} validated overlay metrics for {len(val_records)} symbols.")
        return df

    def get_data(
        self,
        symbol: str,
        required_fields: Set[str],
        as_of: Optional[datetime] = None,
        df_row: Optional[pd.Series] = None,
        trigger_recovery_if_missing: bool = True,
    ) -> DataBundle:
        """
        Universal data fetch method for any scanner.
        """
        sym = str(symbol or "").strip().upper()
        fields_val: Dict[str, Any] = {}
        verified: List[str] = []
        missing: List[str] = []
        blocked: List[str] = []
        provenance_map: Dict[str, FieldEvidence] = {}

        # 1. Check validated disk cache first (Layer B Cache HIT check)
        val_rec = self.validated_cache.get_validated_record(sym)
        val_fields = val_rec.get("fields", {}) if val_rec else {}

        for req_field in required_fields:
            val = None

            # A. Check Layer B disk cache
            if req_field in val_fields and val_fields[req_field].get("status") in ("VERIFIED", "VERIFIED_SINGLE_SOURCE"):
                val = val_fields[req_field].get("value")

            # B. Check df_row if present
            if val is None and df_row is not None:
                aliases = FIELD_ALIAS_MAP.get(req_field, [req_field])
                for alias in aliases:
                    if alias in df_row and df_row[alias] is not None and not pd.isna(df_row[alias]):
                        val = df_row[alias]
                        break

            if val is not None:
                fields_val[req_field] = val
                verified.append(req_field)
            else:
                missing.append(req_field)

        # 2. If fields are missing and recovery enabled, trigger universal pre-recovery
        rec_result: Optional[RecoveryResult] = None
        if missing and trigger_recovery_if_missing:
            logger.info(f"🔄 [GATEWAY] {sym}: missing fields {missing} -> invoking recovery engine.")
            try:
                from app.fundamental_pre_recovery import FundamentalPreRecoveryEngine
                engine = FundamentalPreRecoveryEngine(scanner_name="GATEWAY")
                metrics = engine.recover_symbol(sym)

                rec_dict = metrics.recovered_fields
                evidence_dict = {}

                if rec_dict:
                    for mf in list(missing):
                        aliases = FIELD_ALIAS_MAP.get(mf, [mf])
                        matched_val = None
                        for a in aliases:
                            if a in rec_dict:
                                matched_val = rec_dict[a]
                                break
                        if matched_val is None and mf in rec_dict:
                            matched_val = rec_dict[mf]

                        if matched_val is not None:
                            fields_val[mf] = matched_val
                            verified.append(mf)
                            missing.remove(mf)
                            ev = FieldEvidence(
                                symbol=sym,
                                field=mf,
                                value=matched_val,
                                provider=metrics.overall_status.name,
                                provider_status="VERIFIED",
                            )
                            evidence_dict[mf] = ev
                            provenance_map[mf] = ev

                    if verified:
                        self.validated_cache.save_validated_record(
                            symbol=sym,
                            fields={k: fields_val[k] for k in verified if k in fields_val},
                            evidence_map=evidence_dict,
                            calculation_version="v2.1",
                        )

                rec_result = RecoveryResult(
                    symbol=sym,
                    requested_fields=tuple(required_fields),
                    recovered_fields=tuple(verified),
                    missing_fields=tuple(missing),
                    status=metrics.overall_status,
                    rejection_reason=getattr(metrics, "rejection_reason", None),
                    field_evidence=provenance_map,
                    persisted=bool(verified),
                )
            except Exception as _rec_err:
                logger.error(f"❌ [GATEWAY] Recovery exception for {sym}: {_rec_err}")
                rec_result = RecoveryResult(
                    symbol=sym,
                    requested_fields=tuple(required_fields),
                    recovered_fields=tuple(verified),
                    missing_fields=tuple(missing),
                    status=FundamentalStatus.CALCULATION_ERROR,
                    rejection_reason=str(_rec_err),
                    persisted=False,
                )

        blocked = list(missing)

        return DataBundle(
            symbol=sym,
            fields=fields_val,
            verified_fields=tuple(verified),
            missing_fields=tuple(missing),
            blocked_fields=tuple(blocked),
            provenance=provenance_map,
            recovery_result=rec_result,
            source="CACHE_HIT" if (not missing and val_fields) else "GATEWAY_MERGED",
        )
