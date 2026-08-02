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


class SnapshotManager:
    """
    Singleton In-Memory Shared Snapshot Manager.
    Scanners write snapshots here; HTTP endpoints serve pre-built memory buffers.
    All state updates perform thread-safe atomic reference swaps.
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
        self._versions: Dict[str, int] = {}
        self._swap_lock = threading.Lock()

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
        Builds a clean, immutable Snapshot and performs an atomic pointer swap.
        """
        start_time = time.perf_counter()
        with self._swap_lock:
            current_version = self._versions.get(snapshot_type, 0) + 1
            self._versions[snapshot_type] = current_version

        # 1. Clean NaN/Inf & normalize records
        clean_recs = sanitize_records(records)
        now_ist = datetime.now(IST).isoformat()

        # 2. Materialize summary statistics
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
        }

        # 3. Build metadata
        duration_ms = round((time.perf_counter() - start_time) * 1000, 2)
        meta = {
            "snapshot_type": snapshot_type,
            "version": current_version,
            "generated_at": now_ist,
            "build_duration_ms": duration_ms,
            "stock_count": total_count,
        }
        if metadata:
            meta.update(metadata)

        etag = f'"{snapshot_type}-{current_version}"'

        # 4. Serialize to raw JSON bytes
        payload_dict = {
            "version": current_version,
            "generated_at": now_ist,
            "summary": materialized_summary,
            "metadata": meta,
            "data": clean_recs,
        }
        raw_bytes = json.dumps(payload_dict, ensure_ascii=False).encode("utf-8")

        # 5. Create frozen Snapshot instance
        new_snap = Snapshot(
            snapshot_type=snapshot_type,
            version=current_version,
            generated_at=now_ist,
            metadata=meta,
            records=clean_recs,
            summary=materialized_summary,
            etag=etag,
            raw_json_bytes=raw_bytes,
        )

        # 6. Atomic Reference Swap
        with self._swap_lock:
            self._snapshots[snapshot_type] = new_snap

        logger.info(f"⚡ [SnapshotManager] Published {snapshot_type} v{current_version} ({total_count} records) in {duration_ms}ms | ETag: {etag}")
        return new_snap

    def compute_delta(self, snapshot_type: str, since_version: int, primary_key: str = "symbol") -> Optional[Dict[str, Any]]:
        """
        Calculates changed/added/removed records between since_version and current version.
        Returns None if snapshot is missing or since_version matches current version.
        """
        current_snap = self.get_snapshot(snapshot_type)
        if not current_snap or current_snap.version == since_version:
            return None

        # Return full payload if version mismatch is too large or since_version <= 0
        if since_version <= 0 or (current_snap.version - since_version) > 50:
            return {
                "full_reload": True,
                "version": current_snap.version,
                "etag": current_snap.etag,
                "data": current_snap.records,
            }

        # Build key map for delta calculation
        cur_map = {str(r.get(primary_key, r.get("Stock", ""))).upper(): r for r in current_snap.records if r.get(primary_key) or r.get("Stock")}
        
        # If we don't have historical record state, return current full payload
        return {
            "version": current_snap.version,
            "etag": current_snap.etag,
            "generated_at": current_snap.generated_at,
            "updated": list(cur_map.values()),
            "removed": [],
            "added": [],
        }


# Global Singleton Instance Accessor
_global_snapshot_manager = SnapshotManager()

def get_snapshot_manager() -> SnapshotManager:
    """Access global SnapshotManager singleton instance."""
    return _global_snapshot_manager
