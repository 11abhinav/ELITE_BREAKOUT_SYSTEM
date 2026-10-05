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
    "BSE_FEED_ACCESS_REQUIRED": timedelta(minutes=30),
    "BSE_HTTP_ERROR": timedelta(minutes=30),
    "SYMBOL_MAPPING_FAILURE": timedelta(hours=2),
    "PARSER_OR_FIELD_MAPPING_FAILURE": timedelta(minutes=15),  # High priority defect: NEVER 7 days!
    "PARSER_FAILURE": timedelta(minutes=15),
    "PARSER_CIRCUIT_BREAKER_TRIPPED": timedelta(hours=6),     # Repeated parser failures circuit breaker
    "CONFIRMED_NO_DATA_ANYWHERE": timedelta(days=7),
    "CONFIRMED_NO_DATA": timedelta(days=7),
    "DATA_UNAVAILABLE": timedelta(days=7),
    "FIELD_ABSENT": timedelta(days=7),
    "CONFIRMED_SHORT_HISTORY": timedelta(days=7),
    "INSUFFICIENT_HISTORICAL_DEPTH": timedelta(days=7),
    "HISTORICAL_FILING_GAP": timedelta(days=7),
    "CONFIRMED_HISTORICAL_GAP": timedelta(days=7),
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
    "CONFIRMED_SHORT_HISTORY",
    "HISTORICAL_FILING_GAP",
    "CONFIRMED_HISTORICAL_GAP",
    "INSUFFICIENT_HISTORICAL_DEPTH",
}


def make_quarantine_key(symbol: str, field: str = "ALL", scanner_family: str = "FUNDAMENTAL") -> str:
    """Produces canonical key for (symbol, field, scanner_family) dependency quarantine."""
    s = str(symbol or "").strip().upper()
    f = str(field or "ALL").strip().upper()
    fam = str(scanner_family or "FUNDAMENTAL").strip().upper()
    return f"{s}::{f}::{fam}"


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
                        fld = str(r.get("field") or "ALL").strip().upper()
                        fam = str(r.get("scanner_family") or "FUNDAMENTAL").strip().upper()
                        exp = str(r.get("expires_at", ""))
                        if exp and exp > now_str:
                            k = make_quarantine_key(sym, fld, fam)
                            active_cache[k] = r
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

    def get_status(
        self,
        symbol: str,
        field: str = "ALL",
        scanner_family: str = "ALL"
    ) -> Optional[Dict[str, Any]]:
        """
        Returns active, unexpired negative cache entry for (symbol, field, scanner_family).
        Supports wildcard lookups when field or scanner_family is 'ALL'.
        """
        sym = symbol.strip().upper()
        fld = str(field or "ALL").strip().upper()
        fam = str(scanner_family or "ALL").strip().upper()
        now_str = datetime.now(IST).isoformat()
        with self._lock:
            # 1. Exact or wildcard candidate keys
            candidates = []
            if fld != "ALL" and fam != "ALL":
                candidates.append(make_quarantine_key(sym, fld, fam))
            if fld != "ALL":
                candidates.append(make_quarantine_key(sym, fld, "ALL"))
            if fam != "ALL":
                candidates.append(make_quarantine_key(sym, "ALL", fam))
            candidates.append(make_quarantine_key(sym, "ALL", "ALL"))

            for cand in candidates:
                entry = self._cache.get(cand)
                if entry is not None:
                    exp = str(entry.get("expires_at", ""))
                    if exp and exp > now_str:
                        return entry
                    else:
                        del self._cache[cand]

            # 2. Check all entries starting with sym:: if wildcard lookup
            prefix = f"{sym}::"
            matching_keys = [k for k in self._cache if k.startswith(prefix)]
            for k in matching_keys:
                entry = self._cache.get(k)
                if entry is not None:
                    exp = str(entry.get("expires_at", ""))
                    if exp and exp > now_str:
                        if fam != "ALL":
                            e_fam = str(entry.get("scanner_family", "FUNDAMENTAL")).strip().upper()
                            if e_fam != "ALL" and e_fam != fam:
                                continue
                        if fld != "ALL":
                            e_fld = str(entry.get("field", "ALL")).strip().upper()
                            if e_fld != "ALL" and e_fld != fld:
                                continue
                        return entry
                    else:
                        del self._cache[k]
        return None

    def is_negatively_cached(
        self,
        symbol: str,
        field: str = "ALL",
        scanner_family: str = "ALL"
    ) -> Tuple[bool, Optional[str]]:
        """
        Returns (True, reason) if (symbol, field, scanner_family) is actively negatively cached.
        Returns (False, None) if eligible for lookup/recovery.
        """
        entry = self.get_status(symbol, field=field, scanner_family=scanner_family)
        if entry is not None:
            return True, str(entry.get("reason", entry.get("status", "DATA_UNAVAILABLE")))
        return False, None

    def is_quarantined_for_scanner(
        self,
        symbol: str,
        scanner_family: str = "FUNDAMENTAL",
        field: Optional[str] = None
    ) -> bool:
        """
        [RULE 67 CHANGE-RATIONALE: Dependency-Scoped Scanner Quarantine Invariant]
        Checks if (symbol, field, scanner_family) is currently under an active 7-day quarantine.
        Quarantine applies strictly to confirmed data absence (CONFIRMED_NO_DATA_ANYWHERE,
        CONFIRMED_SHORT_HISTORY, HISTORICAL_FILING_GAP, DATA_UNAVAILABLE_VERIFIED).
        Returns False for internal code defects (PARSER_OR_FIELD_MAPPING_FAILURE) or
        transient provider outages (PROVIDER_FAILURE).
        """
        sym = symbol.strip().upper()
        fam = str(scanner_family or "FUNDAMENTAL").strip().upper()
        now_str = datetime.now(IST).isoformat()
        with self._lock:
            prefix = f"{sym}::"
            keys = [k for k in self._cache if k.startswith(prefix)]
            for k in keys:
                entry = self._cache.get(k)
                if entry is None:
                    continue
                exp = str(entry.get("expires_at", ""))
                if not exp or exp <= now_str:
                    del self._cache[k]
                    continue

                # Check scanner family match
                e_fam = str(entry.get("scanner_family", "FUNDAMENTAL")).strip().upper()
                if e_fam != "ALL" and fam != "ALL" and e_fam != fam:
                    continue

                # Check field match if specific field requested
                if field is not None and str(field).strip().upper() not in ("", "ALL"):
                    target_fld = str(field).strip().upper()
                    e_fld = str(entry.get("field", "ALL")).strip().upper()
                    if e_fld != "ALL" and e_fld != target_fld:
                        continue

                # Check quarantine eligibility reasons
                reason_u = str(entry.get("reason", entry.get("classification", entry.get("status", "")))).upper()
                if any(q_r in reason_u for q_r in _QUARANTINE_REASONS):
                    return True
        return False

    def get_quarantined_symbols(self, scanner_family: str = "ALL") -> Set[str]:
        """
        [RULE 67 CHANGE-RATIONALE: Universe-Level Pre-Filter Quarantine Invariant]
        Returns set of symbol strings currently under an active 7-day quarantine cooldown.
        Used at universe loading time to exclude quarantined stocks upfront before fetching candles.
        """
        quarantined = set()
        fam = str(scanner_family or "ALL").strip().upper()
        now_str = datetime.now(IST).isoformat()
        with self._lock:
            for k, entry in list(self._cache.items()):
                if entry is None:
                    continue
                exp = str(entry.get("expires_at", ""))
                if not exp or exp <= now_str:
                    del self._cache[k]
                    continue
                e_fam = str(entry.get("scanner_family", "FUNDAMENTAL")).strip().upper()
                if e_fam != "ALL" and fam != "ALL" and e_fam != fam:
                    continue
                reason_u = str(entry.get("reason", entry.get("classification", entry.get("status", "")))).upper()
                if any(q_r in reason_u for q_r in _QUARANTINE_REASONS):
                    sym = str(entry.get("symbol") or k.split("::")[0]).strip().upper()
                    if sym:
                        quarantined.add(sym)
        return quarantined

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
        [RULE 67 CHANGE-RATIONALE: Evidence Fingerprint Invalidation Invariant]
        Breaks the 7-day quarantine early if any component of upstream exchange
        evidence changes:
          - latest_filing_date
          - latest_period_end
          - latest_broadcast_timestamp
          - raw_record_count
          - raw_content_hash
          - provider_snapshot_hash
        Ensuring revised filings or updated periods immediately release all scoped entries for the stock.
        """
        sym = symbol.strip().upper()
        with self._lock:
            prefix = f"{sym}::"
            matching_keys = [k for k in self._cache if k.startswith(prefix)]
            if not matching_keys:
                return False

            invalidated = False
            for k in matching_keys:
                entry = self._cache[k]
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
                    break
                elif latest_period_end and cached_period_end and str(latest_period_end)[:10] > cached_period_end[:10]:
                    logger.info(
                        f"⚡ [NEGATIVE_CACHE_INVALIDATED] Newer period end detected for {sym}: "
                        f"exchange={latest_period_end[:10]} > cached={cached_period_end[:10]}. Invalidating cooldown immediately."
                    )
                    invalidated = True
                    break
                elif latest_broadcast_timestamp and cached_broadcast and str(latest_broadcast_timestamp) > cached_broadcast:
                    logger.info(
                        f"⚡ [NEGATIVE_CACHE_INVALIDATED] Newer broadcast timestamp detected for {sym}: "
                        f"exchange={latest_broadcast_timestamp} > cached={cached_broadcast}. Invalidating cooldown immediately."
                    )
                    invalidated = True
                    break
                elif raw_record_count is not None and raw_record_count > cached_count and cached_count > 0:
                    logger.info(
                        f"⚡ [NEGATIVE_CACHE_INVALIDATED] Additional raw records detected for {sym}: "
                        f"raw_records={raw_record_count} > cached={cached_count}. Invalidating cooldown immediately."
                    )
                    invalidated = True
                    break
                elif raw_content_hash and cached_content_hash and raw_content_hash != cached_content_hash:
                    logger.info(
                        f"⚡ [NEGATIVE_CACHE_INVALIDATED] Raw content hash changed for {sym}. Invalidating cooldown immediately."
                    )
                    invalidated = True
                    break
                elif provider_snapshot_hash and cached_snapshot_hash and provider_snapshot_hash != cached_snapshot_hash:
                    logger.info(
                        f"⚡ [NEGATIVE_CACHE_INVALIDATED] Provider snapshot hash changed for {sym}. Invalidating cooldown immediately."
                    )
                    invalidated = True
                    break

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
                    break

            if invalidated:
                for k in matching_keys:
                    del self._cache[k]
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

        k = make_quarantine_key(sym, field_name, scanner_family)

        with self._lock:
            existing = self._cache.get(k)
            if existing is None:
                existing = self._cache.get(make_quarantine_key(sym, "ALL", scanner_family)) or self._cache.get(make_quarantine_key(sym, "ALL", "ALL"))
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
            ttl = REASON_SPECIFIC_TTLS.get(reason_u, REASON_SPECIFIC_TTLS.get(status_u, timedelta(minutes=30)))

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
            self._cache[k] = record
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
                sym = str(r["symbol"]).strip().upper()
                fld = str(r.get("field") or "ALL").strip().upper()
                fam = str(r.get("scanner_family") or "FUNDAMENTAL").strip().upper()
                k = make_quarantine_key(sym, fld, fam)
                self._cache[k] = r
            self._atomic_persist()

    def clear_quarantine(self, symbol: str, field: Optional[str] = None, scanner_family: Optional[str] = None) -> None:
        """Clears quarantine entries matching symbol and optional field/scanner_family."""
        sym = symbol.strip().upper()
        with self._lock:
            prefix = f"{sym}::"
            to_del = []
            for k in self._cache:
                if k.startswith(prefix):
                    entry = self._cache[k]
                    if field is not None and str(field).strip().upper() not in ("", "ALL"):
                        if str(entry.get("field", "ALL")).strip().upper() != str(field).strip().upper():
                            continue
                    if scanner_family is not None and str(scanner_family).strip().upper() not in ("", "ALL"):
                        if str(entry.get("scanner_family", "FUNDAMENTAL")).strip().upper() != str(scanner_family).strip().upper():
                            continue
                    to_del.append(k)
            for k in to_del:
                del self._cache[k]
            if to_del:
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


# Backward-compatible alias for live scanner pre-filter
get_pit_recovery_cache = get_pit_recovery_store


DEFAULT_VALIDATED_CACHE_DIR = os.path.join(DATA_DIR, "pit_recovery_cache", "validated")


try:
    import fcntl
except ImportError:
    fcntl = None


def _sync_dir(dir_path: str) -> None:
    """Invokes fsync on directory file descriptor for strict crash-persistence."""
    try:
        if hasattr(os, "O_RDONLY") and hasattr(os, "fsync"):
            dir_fd = os.open(dir_path, os.O_RDONLY)
            try:
                os.fsync(dir_fd)
            finally:
                os.close(dir_fd)
    except Exception as _e:
        logger.debug(f"Directory fsync notice for {dir_path}: {_e}")


class InterProcessFileLock:
    """Inter-process OS-level file lock using fcntl.flock on POSIX / macOS systems."""

    def __init__(self, lock_file_path: str):
        self.lock_file_path = lock_file_path
        self._fd = None

    def __enter__(self):
        if fcntl is not None:
            try:
                os.makedirs(os.path.dirname(self.lock_file_path), exist_ok=True)
                self._fd = open(self.lock_file_path, "w")
                fcntl.flock(self._fd.fileno(), fcntl.LOCK_EX)
            except Exception as _e:
                logger.debug(f"Inter-process lock notice for {self.lock_file_path}: {_e}")
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if fcntl is not None and self._fd is not None:
            try:
                fcntl.flock(self._fd.fileno(), fcntl.LOCK_UN)
                self._fd.close()
            except Exception:
                pass


class ValidatedRecoveryDiskCache:
    """
    Layer B — Durable Validated Recovery Disk Cache for Point-in-Time (PIT) metrics.

    Stores validated production-eligible fields in data/pit_recovery_cache/validated/{SYMBOL}.json.
    Ensures thread-safe and multi-process-safe concurrent read-modify-write merging via
    per-symbol threading locks, OS fcntl.flock inter-process locks, and atomic file writes with directory fsync.
    Enables cross-scanner reuse and process-restart persistence with 0 network calls.
    """

    def __init__(self, cache_dir: Optional[str] = None):
        self.cache_dir = cache_dir or DEFAULT_VALIDATED_CACHE_DIR
        os.makedirs(self.cache_dir, exist_ok=True)
        self._lock = threading.RLock()
        self._symbol_locks: Dict[str, threading.Lock] = {}

    def _get_symbol_path(self, symbol: str) -> str:
        sym = str(symbol or "").strip().upper()
        return os.path.join(self.cache_dir, f"{sym}.json")

    def _get_lock_file_path(self, symbol: str) -> str:
        sym = str(symbol or "").strip().upper()
        return os.path.join(self.cache_dir, f"{sym}.lock")

    def _get_symbol_lock(self, symbol: str) -> threading.Lock:
        sym = str(symbol or "").strip().upper()
        with self._lock:
            if sym not in self._symbol_locks:
                self._symbol_locks[sym] = threading.Lock()
            return self._symbol_locks[sym]

    def _read_disk_file(self, target_path: str) -> Optional[Dict[str, Any]]:
        if not os.path.exists(target_path):
            return None
        try:
            with open(target_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                return data
        except Exception as e:
            logger.warning(f"⚠️ [VALIDATED_CACHE] Read error for {target_path}: {e}")
        return None

    def get_validated_record(
        self,
        symbol: str,
        required_calculation_version: Optional[str] = None,
        required_snapshot_fingerprint: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """Reads validated metrics for a symbol if cache exists and schema/version matches."""
        sym = str(symbol or "").strip().upper()
        target_path = self._get_symbol_path(sym)
        sym_lock = self._get_symbol_lock(sym)
        proc_lock_path = self._get_lock_file_path(sym)

        with sym_lock:
            with InterProcessFileLock(proc_lock_path):
                data = self._read_disk_file(target_path)
                if not data or data.get("symbol") != sym:
                    return None

                # Validate calculation version if specified
                if required_calculation_version is not None:
                    if data.get("calculation_version") != required_calculation_version:
                        logger.info(f"🔄 [VALIDATED_CACHE] Version mismatch for {sym}: {data.get('calculation_version')} != {required_calculation_version}")
                        return None

                # Validate PIT snapshot fingerprint if specified
                if required_snapshot_fingerprint is not None:
                    if data.get("pit_snapshot_fingerprint") and data.get("pit_snapshot_fingerprint") != required_snapshot_fingerprint:
                        logger.info(f"🔄 [VALIDATED_CACHE] Snapshot fingerprint mismatch for {sym}")
                        return None

                return data

    def get_all_validated_records(self) -> Dict[str, Dict[str, Any]]:
        """Reads all validated symbol records in the cache directory."""
        records = {}
        with self._lock:
            if not os.path.exists(self.cache_dir):
                return records
            for fname in os.listdir(self.cache_dir):
                if fname.endswith(".json") and not fname.endswith(".tmp"):
                    sym = fname[:-5].upper()
                    rec = self.get_validated_record(sym)
                    if rec:
                        records[sym] = rec
        return records

    def save_validated_record(
        self,
        symbol: str,
        fields: Dict[str, Any],
        evidence_map: Optional[Dict[str, Any]] = None,
        evidence_fingerprint: str = "",
        pit_snapshot_fingerprint: str = "",
        calculation_version: str = "v2.1",
    ) -> str:
        """
        Thread-safe & Multi-process safe saving of validated metrics to
        data/pit_recovery_cache/validated/{SYMBOL}.json.
        Sequence: per-symbol thread lock -> OS inter-process lock -> read disk -> field merge -> write .tmp -> fsync file -> os.replace -> fsync dir
        """
        sym = str(symbol or "").strip().upper()
        target_path = self._get_symbol_path(sym)
        tmp_path = target_path + ".tmp"
        sym_lock = self._get_symbol_lock(sym)
        proc_lock_path = self._get_lock_file_path(sym)

        with sym_lock:
            with InterProcessFileLock(proc_lock_path):
                existing = self._read_disk_file(target_path) or {"symbol": sym, "fields": {}, "evidence_fingerprint": ""}
                existing_fields = existing.get("fields", {})

                now_iso = datetime.now(IST).isoformat()
                today_str = datetime.now(IST).strftime("%Y-%m-%d")

                for k, v in fields.items():
                    if v is not None:
                        ev = (evidence_map or {}).get(k, {})
                        is_valuation_metric = k in ("current_ev_ebitda", "current_pe")
                        existing_fields[k] = {
                            "value": v,
                            "status": "VERIFIED",
                            "source": ev.get("provider", "NSE+UPSTOX+LOCAL") if isinstance(ev, dict) else getattr(ev, "provider", "NSE+UPSTOX+LOCAL"),
                            "basis": ev.get("basis", "CONSOLIDATED") if isinstance(ev, dict) else getattr(ev, "basis", "CONSOLIDATED"),
                            "period_start": ev.get("period_start") if isinstance(ev, dict) else getattr(ev, "period_start", None),
                            "period_end": ev.get("period_end") if isinstance(ev, dict) else getattr(ev, "period_end", None),
                            "filing_date": ev.get("filing_date") if isinstance(ev, dict) else getattr(ev, "filing_date", None),
                            "available_at": ev.get("available_at", now_iso) if isinstance(ev, dict) else getattr(ev, "available_at", now_iso),
                            "as_of_date": today_str if is_valuation_metric else ev.get("available_at", now_iso),
                            "source_hash": ev.get("raw_hash") if isinstance(ev, dict) else getattr(ev, "raw_hash", None),
                            "calculation_version": calculation_version,
                            "updated_at": now_iso,
                        }

                record_data = {
                    "symbol": sym,
                    "fields": existing_fields,
                    "evidence_fingerprint": evidence_fingerprint or existing.get("evidence_fingerprint", ""),
                    "pit_snapshot_fingerprint": pit_snapshot_fingerprint or existing.get("pit_snapshot_fingerprint", ""),
                    "schema_version": "1",
                    "validation_version": "1",
                    "calculation_version": calculation_version,
                    "updated_at": now_iso,
                }

                try:
                    os.makedirs(os.path.dirname(target_path), exist_ok=True)
                    with open(tmp_path, "w", encoding="utf-8") as f:
                        json.dump(record_data, f, indent=2)
                        f.flush()
                        os.fsync(f.fileno())
                    os.replace(tmp_path, target_path)
                    _sync_dir(self.cache_dir)
                    logger.info(f"💾 [VALIDATED_CACHE] Persisted {len(fields)} verified fields for {sym} -> {target_path}")
                    return target_path
                except Exception as e:
                    logger.error(f"❌ [VALIDATED_CACHE] Atomic write failed for {sym}: {e}")
                    if os.path.exists(tmp_path):
                        try:
                            os.remove(tmp_path)
                        except Exception:
                            pass
                    raise

    def invalidate_metric(self, symbol: str, metric: str) -> None:
        """Invalidates a specific metric entry for a symbol under symbol lock."""
        sym = str(symbol or "").strip().upper()
        target_path = self._get_symbol_path(sym)
        sym_lock = self._get_symbol_lock(sym)

        with sym_lock:
            existing = self._read_disk_file(target_path)
            if existing and "fields" in existing and metric in existing["fields"]:
                del existing["fields"][metric]
                existing["updated_at"] = datetime.now(IST).isoformat()
                tmp_path = target_path + ".tmp"
                with open(tmp_path, "w", encoding="utf-8") as f:
                    json.dump(existing, f, indent=2)
                    f.flush()
                    os.fsync(f.fileno())
                os.replace(tmp_path, target_path)
                logger.info(f"🧹 [VALIDATED_CACHE] Invalidated {metric} for {sym}")


_GLOBAL_VALIDATED_CACHE: Optional[ValidatedRecoveryDiskCache] = None
_VALIDATED_LOCK = threading.Lock()


def get_validated_recovery_cache(cache_dir: Optional[str] = None) -> ValidatedRecoveryDiskCache:
    global _GLOBAL_VALIDATED_CACHE
    with _VALIDATED_LOCK:
        if _GLOBAL_VALIDATED_CACHE is None or cache_dir is not None:
            _GLOBAL_VALIDATED_CACHE = ValidatedRecoveryDiskCache(cache_dir)
        return _GLOBAL_VALIDATED_CACHE


