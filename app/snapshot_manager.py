import os
import gzip
import json
import math
import time
import logging
import threading
from typing import Dict, Any, List, Optional, Tuple
from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

try:
    import brotli
    HAS_BROTLI = True
except ImportError:
    HAS_BROTLI = False

logger = logging.getLogger(__name__)
IST = ZoneInfo("Asia/Kolkata")


def _sanitize_nan_value(val: Any) -> Any:
    """Helper to clean NaN and Infinity values without changing column data types."""
    if val is None:
        return None
    if isinstance(val, float):
        if math.isnan(val) or math.isinf(val):
            return None
        return val
    if hasattr(val, 'isoformat') and callable(getattr(val, 'isoformat')):
        return val.isoformat()
    return val


def sanitize_records(records: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Clean NaN/Inf in a list of dict records while preserving primitive types."""
    cleaned = []
    for r in records:
        cleaned_row = {}
        for k, v in r.items():
            cleaned_row[k] = _sanitize_nan_value(v)
        cleaned.append(cleaned_row)
    return cleaned


@dataclass(frozen=True)
class Snapshot:
    """Immutable data snapshot representation."""
    snapshot_type: str
    version: int
    generated_at: str
    metadata: Dict[str, Any]
    records: List[Dict[str, Any]]
    summary: Dict[str, Any]
    etag: str
    raw_json_bytes: bytes
    _brotli_bytes: Optional[bytes] = None
    _gzip_bytes: Optional[bytes] = None

    def get_brotli_bytes(self) -> Optional[bytes]:
        """Lazy Brotli compression buffer."""
        if not HAS_BROTLI:
            return None
        if self._brotli_bytes is not None:
            return self._brotli_bytes
        try:
            compressed = brotli.compress(self.raw_json_bytes)
            # Store in object via object.__setattr__ since dataclass is frozen
            object.__setattr__(self, "_brotli_bytes", compressed)
            return compressed
        except Exception as e:
            logger.warning(f"Brotli compression failed for {self.snapshot_type}: {e}")
            return None

    def get_gzip_bytes(self) -> bytes:
        """Lazy Gzip compression buffer."""
        if self._gzip_bytes is not None:
            return self._gzip_bytes
        try:
            compressed = gzip.compress(self.raw_json_bytes)
            object.__setattr__(self, "_gzip_bytes", compressed)
            return compressed
        except Exception as e:
            logger.warning(f"Gzip compression failed for {self.snapshot_type}: {e}")
            return self.raw_json_bytes


from collections import deque


class SnapshotManager:
    """
    Singleton In-Memory Shared Snapshot Manager.
    Scanners write snapshots here; HTTP endpoints serve pre-built memory buffers.
    All state updates perform thread-safe atomic reference swaps.
    Maintains a ring buffer of recent snapshots per type for precise delta generation.
    """
    _instance = None
    _lock = threading.Lock()

    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super(SnapshotManager, cls).__new__(cls)
                    cls._instance._init_manager()
        return cls._instance

    def _init_manager(self):
        self._snapshots: Dict[str, Snapshot] = {}
        self._history: Dict[str, deque] = {}
        self._versions: Dict[str, int] = {}
        self._swap_lock = threading.Lock()
        self._auto_restore_on_init()

    def _auto_restore_on_init(self):
        """Auto-restores initial memory snapshot from Parquet on startup if present."""
        try:
            from config import DATA_DIR
            wealth_path = os.path.join(DATA_DIR, "elite_wealth_system.parquet")
            if os.path.exists(wealth_path):
                import pandas as pd
                df = pd.read_parquet(wealth_path)
                records = df.to_dict(orient="records")
                self.publish_snapshot("wealth", records, metadata={"source": "startup_auto_restore"})
                logger.info("✅ [SnapshotManager] Auto-restored initial wealth snapshot from disk on startup.")
        except Exception as e:
            logger.debug(f"[SnapshotManager] Startup auto-restore skipped/deferred: {e}")

    def get_snapshot(self, snapshot_type: str) -> Optional[Snapshot]:
        """Fetch the current immutable snapshot pointer (0 disk reads, thread-safe)."""
        return self._snapshots.get(snapshot_type)

    def publish_snapshot(
        self,
        snapshot_type: str,
        records: List[Dict[str, Any]],
        metadata: Optional[Dict[str, Any]] = None,
        primary_key: str = "symbol",
    ) -> Snapshot:
        """
        Builds a clean, immutable Snapshot, pre-compresses buffers,
        asserts self-consistency, and performs an atomic pointer swap.
        """
        start_time = time.perf_counter()
        with self._swap_lock:
            current_version = self._versions.get(snapshot_type, 0) + 1
            self._versions[snapshot_type] = current_version

        # 1. Clean NaN/Inf & normalize records
        clean_recs = sanitize_records(records)
        now_ist = datetime.now(IST).isoformat()

        # 2. Materialize summary statistics & assert self-consistency
        total_count = len(clean_recs)
        active_buys = sum(1 for r in clean_recs if "BUY" in str(r.get("Signal", r.get("last_status", ""))).upper())
        active_sells = sum(1 for r in clean_recs if "SELL" in str(r.get("Signal", r.get("last_status", ""))).upper())
        scores = [float(r.get("FM_Score", r.get("health_score", 0))) for r in clean_recs if r.get("FM_Score") is not None or r.get("health_score") is not None]
        avg_score = round(sum(scores) / len(scores), 1) if scores else 0.0

        materialized_summary = {
            "total_count": total_count,
            "active_buys": active_buys,
            "active_sells": active_sells,
            "avg_score": avg_score,
            "generated_at": now_ist,
        }

        # Self-consistency assertion
        assert len(clean_recs) == materialized_summary["total_count"], "Self-consistency assertion failed: record count mismatch"

        etag = f'"{snapshot_type}-{current_version}"'

        # 3. Serialize to raw JSON bytes
        payload_dict = {
            "version": current_version,
            "generated_at": now_ist,
            "summary": materialized_summary,
            "data": clean_recs,
        }
        raw_bytes = json.dumps(payload_dict, ensure_ascii=False).encode("utf-8")
        json_size_bytes = len(raw_bytes)

        # 4. Pre-compress Brotli and Gzip ONCE during publish (not on request path)
        comp_start = time.perf_counter()
        gzip_buf = gzip.compress(raw_bytes)
        brotli_buf = brotli.compress(raw_bytes) if HAS_BROTLI else None
        comp_duration_ms = round((time.perf_counter() - comp_start) * 1000, 2)

        # 5. Build telemetry metadata
        build_duration_ms = round((time.perf_counter() - start_time) * 1000, 2)
        meta = {
            "snapshot_type": snapshot_type,
            "version": current_version,
            "generated_at": now_ist,
            "build_duration_ms": build_duration_ms,
            "compression_duration_ms": comp_duration_ms,
            "stock_count": total_count,
            "raw_json_bytes": json_size_bytes,
            "gzip_bytes": len(gzip_buf),
            "brotli_bytes": len(brotli_buf) if brotli_buf else None,
        }
        if metadata:
            meta.update(metadata)

        payload_dict["metadata"] = meta
        # Re-encode raw_bytes with metadata
        raw_bytes = json.dumps(payload_dict, ensure_ascii=False).encode("utf-8")
        gzip_buf = gzip.compress(raw_bytes)
        brotli_buf = brotli.compress(raw_bytes) if HAS_BROTLI else None

        # 6. Create frozen Snapshot instance
        new_snap = Snapshot(
            snapshot_type=snapshot_type,
            version=current_version,
            generated_at=now_ist,
            metadata=meta,
            records=clean_recs,
            summary=materialized_summary,
            etag=etag,
            raw_json_bytes=raw_bytes,
            brotli_bytes=brotli_buf,
            gzip_bytes=gzip_buf,
        )

        # 7. Atomic Reference Swap & Ring Buffer History Insertion
        with self._swap_lock:
            self._snapshots[snapshot_type] = new_snap
            if snapshot_type not in self._history:
                self._history[snapshot_type] = deque(maxlen=10)
            self._history[snapshot_type].append(new_snap)

        logger.info(f"⚡ [SnapshotManager] Published {snapshot_type} v{current_version} ({total_count} records) in {build_duration_ms}ms (comp: {comp_duration_ms}ms) | ETag: {etag}")
        return new_snap

    def compute_delta(self, snapshot_type: str, since_version: int, primary_key: str = "symbol") -> Optional[Dict[str, Any]]:
        """
        Calculates precise changed/added/removed records between since_version and current version
        using the snapshot ring buffer history.
        """
        current_snap = self.get_snapshot(snapshot_type)
        if not current_snap or current_snap.version == since_version:
            return None

        # Look up old snapshot in ring buffer history
        with self._swap_lock:
            history_queue = list(self._history.get(snapshot_type, []))

        old_snap = next((s for s in history_queue if s.version == since_version), None)

        # If since_version is not in ring buffer (expired or invalid), request full reload
        if old_snap is None:
            return {
                "full_reload": True,
                "version": current_snap.version,
                "etag": current_snap.etag,
                "data": current_snap.records,
            }

        # Calculate exact deltas between old_snap and current_snap
        def get_pk(row):
            val = row.get(primary_key) or row.get("symbol") or row.get("Stock") or ""
            return str(val).strip().upper()

        old_map = {get_pk(r): r for r in old_snap.records if get_pk(r)}
        cur_map = {get_pk(r): r for r in current_snap.records if get_pk(r)}

        added = [r for k, r in cur_map.items() if k not in old_map]
        removed = [k for k in old_map if k not in cur_map]
        
        # Check for changed records (different price, score, or signal)
        updated = []
        for k, cur_r in cur_map.items():
            if k in old_map:
                old_r = old_map[k]
                if cur_r != old_r:
                    updated.append(cur_r)

        return {
            "version": current_snap.version,
            "since_version": since_version,
            "etag": current_snap.etag,
            "generated_at": current_snap.generated_at,
            "updated": updated,
            "added": added,
            "removed": removed,
        }


# Global Singleton Instance Accessor
_global_snapshot_manager = SnapshotManager()

def get_snapshot_manager() -> SnapshotManager:
    """Access global SnapshotManager singleton instance."""
    return _global_snapshot_manager
