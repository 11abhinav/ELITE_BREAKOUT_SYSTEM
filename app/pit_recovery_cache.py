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
import hashlib
import json
import logging
import os
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
PRODUCTION_QUARANTINE_PATH = DEFAULT_RECOVERY_STATUS_PATH
QUARANTINE_DB_SYNC_ENV = "QUARANTINE_DB_SYNC_ENABLED"


def is_quarantine_db_sync_allowed(parquet_path: str) -> bool:
    """
    [RULE 67 CHANGE-RATIONALE: P0 TEST->PRODUCTION DB ISOLATION]
    PostgreSQL sync of the quarantine table is permitted ONLY when BOTH hold:
      1. The store path resolves exactly to PRODUCTION_QUARANTINE_PATH.
      2. QUARANTINE_DB_SYNC_ENABLED is not explicitly disabled ("false"/"0"/"no").
    A non-production path can NEVER enable sync, regardless of the env flag.
    """
    try:
        if os.path.realpath(parquet_path) != os.path.realpath(PRODUCTION_QUARANTINE_PATH):
            return False
    except Exception:
        return False
    flag = os.environ.get(QUARANTINE_DB_SYNC_ENV, "true").strip().lower()
    return flag not in ("false", "0", "no", "off")

# [RULE 67 CHANGE-RATIONALE: Strict reason-specific TTLs.
# PARSER_OR_FIELD_MAPPING_FAILURE is an internal code/mapping bug requiring short retries (15m).
# If repeated >= 3 times with identical fingerprint, PARSER_CIRCUIT_BREAKER_TRIPPED applies 6h cooldown.
# 7-day quarantine applies ONLY to confirmed, deterministic data absences.]
REASON_SPECIFIC_TTLS: Dict[str, timedelta] = {
    "PROVIDER_FAILURE": timedelta(minutes=30),     # Transient broker/upstream network outage
    "PROVIDER_TIMEOUT": timedelta(minutes=30),
    "PROVIDER_5XX": timedelta(minutes=30),
    "AUTH_FAILURE": timedelta(minutes=30),
    "SYMBOL_MAPPING_FAILURE": timedelta(hours=2),
    "PARSER_OR_FIELD_MAPPING_FAILURE": timedelta(minutes=15),  # High priority defect: NEVER 7 days!
    "PARSER_FAILURE": timedelta(minutes=15),
    "PARSER_CIRCUIT_BREAKER_TRIPPED": timedelta(hours=6),     # Repeated parser failures circuit breaker
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
    "evidence_fingerprint",
    "latest_filing_date",
    "latest_period",
    "latest_period_end",
    "latest_broadcast_timestamp",
    "raw_record_count",
    "raw_content_hash",
    "provider_snapshot_hash",
    "attempt_count",
    "consecutive_failures",
    "circuit_breaker_tripped",
    "resolution_status",
    "source_attempts",
]


def compute_evidence_fingerprint(
    latest_filing_date: Optional[str] = None,
    latest_period_end: Optional[str] = None,
    latest_broadcast_timestamp: Optional[str] = None,
    raw_record_count: Optional[int] = None,
    raw_content_hash: Optional[str] = None,
    provider_snapshot_hash: Optional[str] = None,
) -> str:
    """Computes a canonical SHA256 digest over all upstream exchange filing properties."""
    components = [
        str(latest_filing_date or ""),
        str(latest_period_end or ""),
        str(latest_broadcast_timestamp or ""),
        str(raw_record_count or 0),
        str(raw_content_hash or ""),
        str(provider_snapshot_hash or ""),
    ]
    raw_str = "|".join(components)
    return hashlib.sha256(raw_str.encode("utf-8")).hexdigest()

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
        self.db_sync_enabled = is_quarantine_db_sync_allowed(self.parquet_path)
        self.db_sync_attempts = 0  # Telemetry for isolation regression tests
        self._lock = threading.RLock()
        self._cache: Dict[str, Dict[str, Any]] = {}
        self._loaded = False
        self._load()

    def _load(self) -> None:
        with self._lock:
            # [RULE 67 CHANGE-RATIONALE: P1_DURABLE_NEGATIVE_CACHE]
            # Download negative cache from PostgreSQL if absent on local disk after restart.
            if not os.path.exists(self.parquet_path) and self.db_sync_enabled:
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
                # (production path + env flag only; see is_quarantine_db_sync_allowed)
                if not self.db_sync_enabled:
                    return
                try:
                    from app.database import upload_parquet_to_db, submit_background_upload
                    self.db_sync_attempts += 1
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
        raw_record_count: Optional[int] = None,
        latest_period_end: Optional[str] = None,
        latest_broadcast_timestamp: Optional[str] = None,
        raw_content_hash: Optional[str] = None,
        provider_snapshot_hash: Optional[str] = None,
    ) -> bool:
        """
        [RULE 67 CHANGE-RATIONALE: Evidence Fingerprint Invalidation Invariant.
        Breaks the 7-day quarantine early if any component of upstream exchange
        evidence changes:
          - latest_filing_date
          - latest_period_end
          - latest_broadcast_timestamp
          - raw_record_count
          - raw_content_hash
          - provider_snapshot_hash
        Ensuring revised filings or updated periods immediately release the stock.]
        """
        sym = symbol.strip().upper()
        with self._lock:
            entry = self._cache.get(sym)
            if entry is None:
                return False

            invalidated = False
            cached_date = str(entry.get("latest_filing_date") or "")
            cached_period_end = str(entry.get("latest_period_end") or "")
            cached_broadcast = str(entry.get("latest_broadcast_timestamp") or "")
            cached_count = int(entry.get("raw_record_count") or 0)
            cached_content_hash = str(entry.get("raw_content_hash") or "")
            cached_snapshot_hash = str(entry.get("provider_snapshot_hash") or "")
            cached_evidence_hash = str(entry.get("evidence_fingerprint") or "")

            if latest_filing_date and cached_date and str(latest_filing_date)[:10] > cached_date[:10]:
                logger.info(
                    f"⚡ [NEGATIVE_CACHE_INVALIDATED] Newer filing date detected for {sym}: "
                    f"exchange={latest_filing_date[:10]} > cached={cached_date[:10]}. Invalidating cooldown immediately."
                )
                invalidated = True
            elif latest_period_end and cached_period_end and str(latest_period_end)[:10] > cached_period_end[:10]:
                logger.info(
                    f"⚡ [NEGATIVE_CACHE_INVALIDATED] Newer period end detected for {sym}: "
                    f"exchange={latest_period_end[:10]} > cached={cached_period_end[:10]}. Invalidating cooldown immediately."
                )
                invalidated = True
            elif latest_broadcast_timestamp and cached_broadcast and str(latest_broadcast_timestamp) > cached_broadcast:
                logger.info(
                    f"⚡ [NEGATIVE_CACHE_INVALIDATED] Newer broadcast timestamp detected for {sym}: "
                    f"exchange={latest_broadcast_timestamp} > cached={cached_broadcast}. Invalidating cooldown immediately."
                )
                invalidated = True
            elif raw_record_count is not None and raw_record_count > cached_count and cached_count > 0:
                logger.info(
                    f"⚡ [NEGATIVE_CACHE_INVALIDATED] Additional raw records detected for {sym}: "
                    f"raw_records={raw_record_count} > cached={cached_count}. Invalidating cooldown immediately."
                )
                invalidated = True
            elif raw_content_hash and cached_content_hash and raw_content_hash != cached_content_hash:
                logger.info(
                    f"⚡ [NEGATIVE_CACHE_INVALIDATED] Raw content hash changed for {sym}. Invalidating cooldown immediately."
                )
                invalidated = True
            elif provider_snapshot_hash and cached_snapshot_hash and provider_snapshot_hash != cached_snapshot_hash:
                logger.info(
                    f"⚡ [NEGATIVE_CACHE_INVALIDATED] Provider snapshot hash changed for {sym}. Invalidating cooldown immediately."
                )
                invalidated = True

            new_fingerprint = compute_evidence_fingerprint(
                latest_filing_date=latest_filing_date or cached_date or None,
                latest_period_end=latest_period_end or cached_period_end or None,
                latest_broadcast_timestamp=latest_broadcast_timestamp or cached_broadcast or None,
                raw_record_count=raw_record_count if raw_record_count is not None else cached_count,
                raw_content_hash=raw_content_hash or cached_content_hash or None,
                provider_snapshot_hash=provider_snapshot_hash or cached_snapshot_hash or None,
            )
            if cached_evidence_hash and new_fingerprint != cached_evidence_hash:
                logger.info(
                    f"⚡ [NEGATIVE_CACHE_INVALIDATED] Composite evidence fingerprint changed for {sym}: "
                    f"{new_fingerprint[:8]} != {cached_evidence_hash[:8]}. Invalidating cooldown immediately."
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
        latest_period_end: Optional[str] = None,
        latest_broadcast_timestamp: Optional[str] = None,
        raw_record_count: Optional[int] = None,
        raw_content_hash: Optional[str] = None,
        provider_snapshot_hash: Optional[str] = None,
        approved_provider_status: Optional[str] = None,
        reference_provider_status: Optional[str] = None,
        persist: bool = True
    ) -> Dict[str, Any]:
        """
        Records a negative cache entry with reason-specific TTL, circuit breaker for
        repeated identical parser defects, and atomically persists.
        """
        sym = symbol.strip().upper()
        now_dt = datetime.now(IST)
        status_u = str(status).upper()
        reason_u = str(reason).upper()

        fail_fp = f"{sym}:{reason_u}:{latest_filing_date or 'NONE'}:{raw_record_count or 0}"
        consecutive_failures = 1
        circuit_breaker_tripped = False

        with self._lock:
            existing = self._cache.get(sym)
            if existing is not None:
                attempt_cnt = int(existing.get("attempt_count", 0)) + 1
                prev_fp = str(existing.get("failure_fingerprint", ""))
                prev_cons = int(existing.get("consecutive_failures", 1))
                if prev_fp == fail_fp:
                    consecutive_failures = prev_cons + 1
                else:
                    consecutive_failures = 1
            else:
                attempt_cnt = 1

        # Circuit Breaker: Repeated parser failures (>2 times) trip extended 6-hour cooldown
        if ("PARSER" in reason_u or "PARSER" in status_u) and consecutive_failures >= 3:
            circuit_breaker_tripped = True
            ttl = timedelta(hours=6)
            reason_u = "PARSER_CIRCUIT_BREAKER_TRIPPED"
            logger.error(
                f"🛑 [PARSER_CIRCUIT_BREAKER] {sym}: Repeated identical parser failure ({consecutive_failures}x). "
                f"Tripping circuit breaker! Extended 6h cooldown applied to prevent quota exhaustion."
            )
            try:
                from database import insert_notification
                insert_notification(
                    "P0_PARSER_CIRCUIT_BREAKER_TRIPPED",
                    f"🛑 Parser failure circuit breaker tripped for {sym}. Provider: {provider}. Fingerprint: {fail_fp}. Cooldown: 6 hours.",
                    priority="HIGH",
                )
            except Exception:
                pass
        elif ttl is None:
            ttl = REASON_SPECIFIC_TTLS.get(reason_u, REASON_SPECIFIC_TTLS.get(status_u, timedelta(days=7)))

        expires_dt = now_dt + ttl
        evidence_fp = compute_evidence_fingerprint(
            latest_filing_date=latest_filing_date,
            latest_period_end=latest_period_end or latest_period,
            latest_broadcast_timestamp=latest_broadcast_timestamp,
            raw_record_count=raw_record_count,
            raw_content_hash=raw_content_hash,
            provider_snapshot_hash=provider_snapshot_hash,
        )

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
            "failure_fingerprint": fail_fp,
            "provider_fingerprint": f"{provider}:{raw_record_count or 0}",
            "evidence_fingerprint": evidence_fp,
            "latest_filing_date": str(latest_filing_date) if latest_filing_date else None,
            "latest_period": str(latest_period) if latest_period else None,
            "latest_period_end": str(latest_period_end) if latest_period_end else None,
            "latest_broadcast_timestamp": str(latest_broadcast_timestamp) if latest_broadcast_timestamp else None,
            "raw_record_count": int(raw_record_count or 0),
            "raw_content_hash": str(raw_content_hash) if raw_content_hash else None,
            "provider_snapshot_hash": str(provider_snapshot_hash) if provider_snapshot_hash else None,
            "attempt_count": attempt_cnt,
            "consecutive_failures": consecutive_failures,
            "circuit_breaker_tripped": circuit_breaker_tripped,
            "resolution_status": "BLOCKED",
            "source_attempts": json.dumps(source_attempts) if source_attempts else "{}",
        }

        with self._lock:
            self._cache[sym] = record
            if persist:
                self._atomic_persist()

        return record

    def export_quarantine_evidence_report(self, output_dir: Optional[str] = None) -> Tuple[str, str]:
        """
        Exports structured quarantine evidence table to data/reports/quarantine_evidence_report.json and .csv.
        Columns: Symbol, Field, Scanner_Family, Approved_Providers, Screener, Classification,
                 Quarantine_Start, Retry_After, Evidence_Fingerprint, Circuit_Breaker_Status.
        """
        out_dir = output_dir or os.path.join(DATA_DIR, "reports")
        os.makedirs(out_dir, exist_ok=True)
        json_path = os.path.join(out_dir, "quarantine_evidence_report.json")
        csv_path = os.path.join(out_dir, "quarantine_evidence_report.csv")

        with self._lock:
            entries = list(self._cache.values())

        report_rows = []
        for e in entries:
            sym = e.get("symbol")
            field = e.get("field", "ALL")
            scanner = e.get("scanner_family", "FUNDAMENTAL")
            approved = e.get("approved_provider_status", e.get("provider", "UNKNOWN"))
            screener = e.get("reference_provider_status", "NOT_CHECKED")
            cls = e.get("classification", e.get("reason", "UNKNOWN"))
            q_start = e.get("checked_at", "")
            retry_after = e.get("retry_after", e.get("expires_at", ""))
            fp = e.get("evidence_fingerprint") or e.get("failure_fingerprint", "")
            cb = "TRIPPED" if e.get("circuit_breaker_tripped") else "NORMAL"

            report_rows.append({
                "Symbol": sym,
                "Field": field,
                "Scanner_Family": scanner,
                "Approved_Providers": approved,
                "Screener": screener,
                "Classification": cls,
                "Quarantine_Start": q_start,
                "Retry_After": retry_after,
                "Evidence_Fingerprint": fp,
                "Circuit_Breaker_Status": cb,
            })

        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(report_rows, f, indent=2)

        if report_rows:
            import csv
            with open(csv_path, "w", encoding="utf-8", newline="") as f:
                writer = csv.DictWriter(f, fieldnames=list(report_rows[0].keys()))
                writer.writeheader()
                writer.writerows(report_rows)
        else:
            with open(csv_path, "w", encoding="utf-8") as f:
                f.write("Symbol,Field,Scanner_Family,Approved_Providers,Screener,Classification,Quarantine_Start,Retry_After,Evidence_Fingerprint,Circuit_Breaker_Status\n")

        logger.info(f"📊 [QUARANTINE_REPORT] Exported {len(report_rows)} quarantine evidence records to {json_path} and {csv_path}")
        return json_path, csv_path

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
