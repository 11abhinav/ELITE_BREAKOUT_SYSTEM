"""
app/pit_recovery_cache.py
==========================
Durable Negative Availability Cache for Point-in-Time (PIT) fundamentals.

Implements P1 Governance Directive:
  - Keeps canonical pit_fundamentals_v1.parquet purely for verified financial statement facts.
  - Stores negative availability states (DATA_UNAVAILABLE, FIELD_ABSENT, NOT_REPORTED, PROVIDER_FAILURE)
    in a dedicated durable store: data/pit_recovery_status.parquet.
  - Reason-specific TTL prevents repeated HTTP network calls for known-missing data while
    allowing transient provider errors (PROVIDER_FAILURE) to recover on a shorter cadence.
"""

from __future__ import annotations
import os
import json
import logging
import threading
from datetime import datetime, timedelta
from typing import Dict, Any, Optional, Tuple, List
from zoneinfo import ZoneInfo
import pandas as pd

logger = logging.getLogger(__name__)
IST = ZoneInfo("Asia/Kolkata")

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(BASE_DIR, "data")
DEFAULT_RECOVERY_STATUS_PATH = os.path.join(DATA_DIR, "pit_recovery_status.parquet")

REASON_SPECIFIC_TTLS: Dict[str, timedelta] = {
    "PROVIDER_FAILURE": timedelta(minutes=30),     # Transient broker/upstream network outage
    "FIELD_ABSENT": timedelta(days=7),             # Specific ratio not reported in published statements
    "DATA_UNAVAILABLE": timedelta(days=7),         # Company statements missing upstream across Tier-1 sources
    "NOT_REPORTED": timedelta(days=21),            # Filing not yet published by exchange (tied to LODR deadline)
}

COLUMNS = [
    "symbol",
    "provider",
    "status",
    "reason",
    "missing_fields",
    "checked_at",
    "expires_at",
    "source_attempts",
]


class PitRecoveryStatusStore:
    """Thread-safe, durable negative availability store backed by pit_recovery_status.parquet."""

    def __init__(self, parquet_path: Optional[str] = None):
        self.parquet_path = parquet_path or DEFAULT_RECOVERY_STATUS_PATH
        self._lock = threading.RLock()
        self._cache: Dict[str, Dict[str, Any]] = {}
        self._loaded = False
        self._load()

    def _load(self) -> None:
        with self._lock:
            # [RULE 67 CHANGE-RATIONALE: P1_DURABLE_NEGATIVE_CACHE]
            # Download negative cache from PostgreSQL if absent on local disk after restart.
            if not os.path.exists(self.parquet_path):
                try:
                    from app.database import download_parquet_from_db
                    download_parquet_from_db("pit_recovery_status", self.parquet_path)
                except Exception:
                    pass

            if not os.path.exists(self.parquet_path):
                self._cache = {}
                self._loaded = True
                return
            try:
                df = pd.read_parquet(self.parquet_path)
                if not df.empty and "symbol" in df.columns:
                    records = df.to_dict("records")
                    now_str = datetime.now(IST).isoformat()
                    active_cache = {}
                    for r in records:
                        sym = str(r["symbol"]).strip().upper()
                        exp = str(r.get("expires_at", ""))
                        if exp and exp > now_str:
                            active_cache[sym] = r
                    self._cache = active_cache
                else:
                    self._cache = {}
            except Exception as e:
                logger.warning(f"Failed to load pit_recovery_status.parquet: {e}")
                self._cache = {}
            self._loaded = True

    def _atomic_persist(self) -> None:
        """Atomically persists active cache to parquet via tempfile replace."""
        with self._lock:
            if not self._cache:
                if os.path.exists(self.parquet_path):
                    try:
                        df_empty = pd.DataFrame(columns=COLUMNS)
                        tmp_path = f"{self.parquet_path}.tmp.{os.getpid()}"
                        df_empty.to_parquet(tmp_path, index=False)
                        os.replace(tmp_path, self.parquet_path)
                    except Exception as e:
                        logger.warning(f"Failed to persist empty pit_recovery_status: {e}")
                return

            rows = list(self._cache.values())
            df = pd.DataFrame(rows)
            for col in COLUMNS:
                if col not in df.columns:
                    df[col] = None
            df = df[COLUMNS]

            os.makedirs(os.path.dirname(self.parquet_path), exist_ok=True)
            tmp_path = f"{self.parquet_path}.tmp.{os.getpid()}"
            try:
                df.to_parquet(tmp_path, index=False)
                os.replace(tmp_path, self.parquet_path)
                # [RULE 67 CHANGE-RATIONALE: P1_DURABLE_NEGATIVE_CACHE]
                # Sync negative availability cache to PostgreSQL parquet_cache asynchronously.
                try:
                    from app.database import upload_parquet_to_db, submit_background_upload
                    submit_background_upload(lambda: upload_parquet_to_db("pit_recovery_status", self.parquet_path))
                except Exception as _sync_err:
                    logger.debug(f"DB sync notice for pit_recovery_status: {_sync_err}")
            except Exception as e:
                logger.error(f"Failed atomic write to {self.parquet_path}: {e}")
                if os.path.exists(tmp_path):
                    try:
                        os.remove(tmp_path)
                    except OSError:
                        pass

    def get_status(self, symbol: str) -> Optional[Dict[str, Any]]:
        """Returns active, unexpired negative cache entry if present."""
        sym = symbol.strip().upper()
        now_str = datetime.now(IST).isoformat()
        with self._lock:
            entry = self._cache.get(sym)
            if entry is not None:
                exp = str(entry.get("expires_at", ""))
                if exp and exp > now_str:
                    return entry
                # Expired
                del self._cache[sym]
        return None

    def is_negatively_cached(self, symbol: str) -> Tuple[bool, Optional[str]]:
        """
        Returns (True, reason) if symbol is actively negatively cached and unexpired.
        Returns (False, None) if symbol is eligible for lookup/recovery.
        """
        entry = self.get_status(symbol)
        if entry is not None:
            return True, str(entry.get("reason", entry.get("status", "DATA_UNAVAILABLE")))
        return False, None

    def record_unavailability(
        self,
        symbol: str,
        provider: str,
        status: str,
        reason: str,
        missing_fields: Optional[List[str]] = None,
        source_attempts: Optional[Dict[str, Any]] = None,
        ttl: Optional[timedelta] = None,
        persist: bool = True
    ) -> Dict[str, Any]:
        """
        Records a negative cache entry with reason-specific TTL and atomically persists.
        """
        sym = symbol.strip().upper()
        now_dt = datetime.now(IST)
        status_u = str(status).upper()

        if ttl is None:
            ttl = REASON_SPECIFIC_TTLS.get(status_u, timedelta(days=7))

        expires_dt = now_dt + ttl
        record = {
            "symbol": sym,
            "provider": str(provider),
            "status": status_u,
            "reason": str(reason),
            "missing_fields": ",".join(missing_fields) if missing_fields else "",
            "checked_at": now_dt.isoformat(),
            "expires_at": expires_dt.isoformat(),
            "source_attempts": json.dumps(source_attempts) if source_attempts else "{}",
        }

        with self._lock:
            self._cache[sym] = record
            if persist:
                self._atomic_persist()

        return record

    def bulk_record_unavailability(self, records: List[Dict[str, Any]]) -> None:
        """Batch-records multiple negative availability entries with a single atomic disk write."""
        with self._lock:
            for r in records:
                sym = r["symbol"].strip().upper()
                self._cache[sym] = r
            self._atomic_persist()

    def clear(self) -> None:
        with self._lock:
            self._cache = {}
            self._atomic_persist()


# Global Singleton Store
_GLOBAL_RECOVERY_STORE: Optional[PitRecoveryStatusStore] = None
_STORE_LOCK = threading.Lock()

def get_pit_recovery_store(path: Optional[str] = None) -> PitRecoveryStatusStore:
    global _GLOBAL_RECOVERY_STORE
    with _STORE_LOCK:
        if _GLOBAL_RECOVERY_STORE is None or path is not None:
            _GLOBAL_RECOVERY_STORE = PitRecoveryStatusStore(path)
        return _GLOBAL_RECOVERY_STORE
