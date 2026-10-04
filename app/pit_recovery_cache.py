"""
app/pit_recovery_cache.py
==========================
Durable Negative Availability Cache for Point-in-Time (PIT) fundamentals.

Implements P1 Governance Directive:
  - Keeps canonical pit_fundamentals_v1.parquet purely for verified financial statement facts.
  - Stores negative availability states (DATA_UNAVAILABLE, CONFIRMED_NO_DATA_ANYWHERE, etc.)
    in a dedicated durable store: data/pit_recovery_status.parquet + PostgreSQL table.
  - Reason-specific TTL prevents repeated HTTP network calls for known-missing data while
    ensuring code/parser defects (PARSER_OR_FIELD_MAPPING_FAILURE) get short 15m retry cycles,
    transient provider errors (PROVIDER_FAILURE) recover on 30m cadences, and confirmed gaps
    receive the strict 7-day quarantine.
  - Invalidation Fingerprint: Breaks 7-day quarantine immediately if a newer filing date
    or higher raw record count appears on the exchange.
  - Scanner Pre-Filter: Provides is_quarantined_for_scanner(symbol, scanner_family) so the
    scanner removes quarantined stocks from the evaluation list completely for 7 days.
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

# [RULE 67 CHANGE-RATIONALE: Strict reason-specific TTLs.
# PARSER_OR_FIELD_MAPPING_FAILURE is an internal code/mapping bug requiring short retries (15m).
# 7-day quarantine applies ONLY to confirmed, deterministic data absences.]
REASON_SPECIFIC_TTLS: Dict[str, timedelta] = {
    "PROVIDER_FAILURE": timedelta(minutes=30),     # Transient broker/upstream network outage
    "PROVIDER_TIMEOUT": timedelta(minutes=30),
    "PROVIDER_5XX": timedelta(minutes=30),
    "AUTH_FAILURE": timedelta(minutes=30),
    "SYMBOL_MAPPING_FAILURE": timedelta(hours=2),
    "PARSER_OR_FIELD_MAPPING_FAILURE": timedelta(minutes=15),  # High priority defect: NEVER 7 days!
    "PARSER_FAILURE": timedelta(minutes=15),
    "CONFIRMED_NO_DATA": timedelta(days=7),
    "CONFIRMED_SHORT_HISTORY": timedelta(days=7),
    "CONFIRMED_HISTORICAL_GAP": timedelta(days=7),
    "CONFIRMED_NO_DATA_ANYWHERE": timedelta(days=7),
    "NO_DATA_ANYWHERE": timedelta(days=7),
    "DATA_UNAVAILABLE": timedelta(days=7),
    "FIELD_ABSENT": timedelta(days=7),             # Specific ratio not reported in published statements
    "NOT_REPORTED": timedelta(days=21),            # Filing not yet published by exchange (tied to LODR deadline)
}

COLUMNS = [
    "symbol",
    "scanner_family",
    "field",
    "provider",
    "status",
    "classification",
    "reason",
    "approved_provider_status",
    "reference_provider_status",
    "missing_fields",
    "checked_at",
    "expires_at",
    "retry_after",
    "failure_fingerprint",
    "provider_fingerprint",
    "latest_filing_date",
    "latest_period",
    "raw_record_count",
    "attempt_count",
    "resolution_status",
    "source_attempts",
]

_QUARANTINE_REASONS = {
    "CONFIRMED_NO_DATA_ANYWHERE",
    "NO_DATA_ANYWHERE",
    "CONFIRMED_SHORT_HISTORY",
    "CONFIRMED_HISTORICAL_GAP",
    "CONFIRMED_NO_DATA",
    "DATA_UNAVAILABLE",
    "FIELD_ABSENT",
}


class PitRecoveryStatusStore:
    """Thread-safe, durable negative availability store backed by pit_recovery_status.parquet + PostgreSQL."""

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
        """Atomically persists active cache to parquet via tempfile replace and syncs to PostgreSQL."""
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
                # Sync negative availability cache to PostgreSQL asynchronously
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

    def is_quarantined_for_scanner(self, symbol: str, scanner_family: str = "FUNDAMENTAL") -> bool:
        """
        [RULE 67 CHANGE-RATIONALE: Scanner Dependency Quarantine Invariant.
        Checks if symbol is currently under an active 7-day quarantine specifically for
        scanner_family (e.g. FUNDAMENTAL / QUALITY_COMPOUNDER).
        Quarantine applies strictly to confirmed data absence (CONFIRMED_NO_DATA_ANYWHERE,
        CONFIRMED_SHORT_HISTORY, HISTORICAL_GAP).
        Returns False for internal code defects (PARSER_OR_FIELD_MAPPING_FAILURE) or
        transient provider outages (PROVIDER_FAILURE).]
        """
        entry = self.get_status(symbol)
        if entry is None:
            return False

        entry_family = str(entry.get("scanner_family", "FUNDAMENTAL")).upper()
        if entry_family != "ALL" and entry_family != str(scanner_family).upper():
            return False

        reason_u = str(entry.get("reason", entry.get("classification", entry.get("status", "")))).upper()
        return any(q_r in reason_u for q_r in _QUARANTINE_REASONS)

    def check_and_invalidate_on_new_filing(
        self,
        symbol: str,
        latest_filing_date: Optional[str] = None,
        raw_record_count: Optional[int] = None
    ) -> bool:
        """
        [RULE 67 CHANGE-RATIONALE: Evidence Fingerprint Invalidation Invariant.
        Breaks the 7-day quarantine early if a newer filing date or increased raw record
        count is observed upstream on the exchange, ensuring no symbol remains blocked
        after publishing new financial statements.]
        """
        sym = symbol.strip().upper()
        with self._lock:
            entry = self._cache.get(sym)
            if entry is None:
                return False

            invalidated = False
            cached_date = str(entry.get("latest_filing_date") or "")
            cached_count = int(entry.get("raw_record_count") or 0)

            if latest_filing_date and cached_date and str(latest_filing_date)[:10] > cached_date[:10]:
                logger.info(
                    f"⚡ [NEGATIVE_CACHE_INVALIDATED] Newer filing detected for {sym}: "
                    f"exchange={latest_filing_date[:10]} > cached={cached_date[:10]}. Invalidating cooldown immediately."
                )
                invalidated = True
            elif raw_record_count is not None and raw_record_count > cached_count and cached_count > 0:
                logger.info(
                    f"⚡ [NEGATIVE_CACHE_INVALIDATED] Additional raw records detected for {sym}: "
                    f"raw_records={raw_record_count} > cached={cached_count}. Invalidating cooldown immediately."
                )
                invalidated = True

            if invalidated:
                del self._cache[sym]
                self._atomic_persist()
                return True
            return False

    def record_unavailability(
        self,
        symbol: str,
        provider: str,
        status: str,
        reason: str,
        missing_fields: Optional[List[str]] = None,
        source_attempts: Optional[Dict[str, Any]] = None,
        ttl: Optional[timedelta] = None,
        scanner_family: str = "FUNDAMENTAL",
        field_name: str = "ALL",
        latest_filing_date: Optional[str] = None,
        latest_period: Optional[str] = None,
        raw_record_count: Optional[int] = None,
        approved_provider_status: Optional[str] = None,
        reference_provider_status: Optional[str] = None,
        persist: bool = True
    ) -> Dict[str, Any]:
        """
        Records a negative cache entry with reason-specific TTL and atomically persists.
        """
        sym = symbol.strip().upper()
        now_dt = datetime.now(IST)
        status_u = str(status).upper()
        reason_u = str(reason).upper()

        if ttl is None:
            # Check exact reason match first, then status
            ttl = REASON_SPECIFIC_TTLS.get(reason_u, REASON_SPECIFIC_TTLS.get(status_u, timedelta(days=7)))

        expires_dt = now_dt + ttl
        record = {
            "symbol": sym,
            "scanner_family": str(scanner_family).upper(),
            "field": str(field_name),
            "provider": str(provider),
            "status": status_u,
            "classification": reason_u,
            "reason": reason_u,
            "approved_provider_status": str(approved_provider_status or provider),
            "reference_provider_status": str(reference_provider_status or "NOT_CHECKED"),
            "missing_fields": ",".join(missing_fields) if missing_fields else "",
            "checked_at": now_dt.isoformat(),
            "expires_at": expires_dt.isoformat(),
            "retry_after": expires_dt.isoformat(),
            "failure_fingerprint": f"{sym}:{reason_u}:{latest_filing_date or 'NONE'}",
            "provider_fingerprint": f"{provider}:{raw_record_count or 0}",
            "latest_filing_date": str(latest_filing_date) if latest_filing_date else None,
            "latest_period": str(latest_period) if latest_period else None,
            "raw_record_count": int(raw_record_count or 0),
            "attempt_count": 1,
            "resolution_status": "BLOCKED",
            "source_attempts": json.dumps(source_attempts) if source_attempts else "{}",
        }

        with self._lock:
            existing = self._cache.get(sym)
            if existing is not None:
                record["attempt_count"] = int(existing.get("attempt_count", 0)) + 1
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
