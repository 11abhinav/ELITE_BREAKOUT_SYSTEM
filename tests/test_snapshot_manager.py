import math
import pytest
from app.snapshot_manager import SnapshotManager, Snapshot, get_snapshot_manager, sanitize_records

def test_sanitize_records_nan_and_inf():
    raw_data = [
        {"symbol": "TCS", "price": 4000.0, "score": float('nan'), "notes": None},
        {"symbol": "INFY", "price": float('inf'), "score": 85.5, "neg_inf": float('-inf')},
    ]
    cleaned = sanitize_records(raw_data)
    assert cleaned[0]["score"] is None
    assert cleaned[0]["price"] == 4000.0
    assert cleaned[1]["price"] == None
    assert cleaned[1]["neg_inf"] == None
    assert cleaned[1]["score"] == 85.5

def test_snapshot_immutability():
    snap = Snapshot(
        snapshot_type="test",
        version=1,
        generated_at="2026-08-02T18:00:00IST",
        metadata={"count": 1},
        records=[{"symbol": "TCS", "price": 4000}],
        summary={"total_count": 1},
        etag='"test-1"',
        raw_json_bytes=b'{"test":1}',
    )
    with pytest.raises(Exception):
        snap.version = 2

def test_snapshot_manager_atomic_swap():
    mgr = SnapshotManager()
    
    # 1. Initial Publish
    data1 = [{"symbol": "TCS", "FM_Score": 75.0, "Signal": "BUY"}]
    snap1 = mgr.publish_snapshot("wealth", data1, metadata={"scanner": "wealth_test"})
    
    assert snap1.version >= 1
    assert snap1.etag == f'"wealth-{snap1.version}"'
    assert snap1.summary["total_count"] == 1
    assert snap1.summary["active_buys"] == 1
    
    fetched1 = mgr.get_snapshot("wealth")
    assert fetched1 is snap1

    # 2. Atomic Pointer Swap (Publish v2)
    data2 = [
        {"symbol": "TCS", "FM_Score": 80.0, "Signal": "BUY"},
        {"symbol": "INFY", "FM_Score": 60.0, "Signal": "HOLD"},
    ]
    snap2 = mgr.publish_snapshot("wealth", data2)
    assert snap2.version == snap1.version + 1
    assert snap2.summary["total_count"] == 2

    # Verify atomic swap point update
    fetched2 = mgr.get_snapshot("wealth")
    assert fetched2 is snap2
    assert fetched2 is not snap1

def test_snapshot_compression_buffers():
    mgr = SnapshotManager()
    snap = mgr.publish_snapshot("summary", [{"symbol": "RELIANCE", "close": 2900}])
    
    gzip_bytes = snap.get_gzip_bytes()
    assert isinstance(gzip_bytes, bytes)
    assert len(gzip_bytes) > 0

    # Brotli byte test if library installed
    brotli_bytes = snap.get_brotli_bytes()
    if brotli_bytes is not None:
        assert isinstance(brotli_bytes, bytes)
        assert len(brotli_bytes) > 0

def test_snapshot_delta_computation():
    mgr = SnapshotManager()
    mgr.publish_snapshot("delta_test", [{"symbol": "TCS", "price": 4000}])
    v1_snap = mgr.get_snapshot("delta_test")
    
    # Same version returns None
    delta_same = mgr.compute_delta("delta_test", v1_snap.version)
    assert delta_same is None

    # Version mismatch produces delta payload
    mgr.publish_snapshot("delta_test", [{"symbol": "TCS", "price": 4050}, {"symbol": "INFY", "price": 1600}])
    v2_snap = mgr.get_snapshot("delta_test")
    
    delta_diff = mgr.compute_delta("delta_test", v1_snap.version)
    assert delta_diff is not None
    assert delta_diff["version"] == v2_snap.version
    assert len(delta_diff["updated"]) == 2
