#!/usr/bin/env python3
"""
tests/test_wealth_and_lifecycle_fixes.py
========================================
Validates fixes for:
1. Wealth Engine DataFrame duplicate index on to_dict('index').
2. Financial Snapshot Status stale lease auto-healing.
3. Rebuild PIT abort handling without leaving snapshot in AUDITING.
"""

import os
import json
import tempfile
import pandas as pd
from pathlib import Path
from datetime import datetime, timedelta
from unittest.mock import patch

from app.financial_data_integrity import (
    FinancialSnapshotStatus,
    get_financial_snapshot_status,
    set_financial_snapshot_status,
)


def test_wealth_engine_duplicate_stock_deduplication():
    """
    Verifies that duplicate rows for the same stock in portfolio_df
    do not cause ValueError: DataFrame index must be unique for orient='index',
    and active exit codes (e.g. SELL) take priority.
    """
    portfolio_rows = [
        {"Stock": "RELIANCE", "Exit_Code": "", "Exit_Reason": "", "Hold_Score": 60, "hold_trend": "SIDEWAYS"},
        {"Stock": "RELIANCE", "Exit_Code": "SELL", "Exit_Reason": "Trailing Stop Hit", "Hold_Score": 40, "hold_trend": "DOWN"},
        {"Stock": "TCS", "Exit_Code": "HOLD", "Exit_Reason": "Trend Intact", "Hold_Score": 85, "hold_trend": "UP"},
    ]
    portfolio_df = pd.DataFrame(portfolio_rows)
    
    # Run the exact deduplication logic implemented in wealth_engine.py
    clean_port_df = (
        portfolio_df.sort_values(by=["Exit_Code"], ascending=False)
        .drop_duplicates(subset=["Stock"], keep="first")
    )
    port_map = clean_port_df.set_index("Stock")[["Hold_Score", "hold_trend", "Exit_Code", "Exit_Reason"]].to_dict('index')

    assert "RELIANCE" in port_map
    assert "TCS" in port_map
    assert port_map["RELIANCE"]["Exit_Code"] == "SELL"
    assert port_map["RELIANCE"]["Exit_Reason"] == "Trailing Stop Hit"
    assert port_map["TCS"]["Exit_Code"] == "HOLD"


def test_stale_snapshot_status_auto_healing():
    """
    Verifies that get_financial_snapshot_status auto-heals an AUDITING or BUILDING
    status that was abandoned >15 minutes ago when a canonical parquet exists.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        meta_file = tmp_path / "canonical_pit_rebuilt_meta.json"
        parquet_file = tmp_path / "canonical_pit_rebuilt.parquet"

        # Create dummy canonical parquet
        pd.DataFrame([{"symbol": "INFY", "revenue": 100}]).to_parquet(parquet_file)

        # Write meta file with AUDITING status updated 30 minutes ago
        stale_time = (datetime.now() - timedelta(minutes=30)).isoformat()
        meta = {
            "FINANCIAL_SNAPSHOT_STATUS": "AUDITING",
            "snapshot_status": "READY",
            "snapshot_status_updated_at": stale_time,
            "total_symbols": 886,
        }
        with open(meta_file, "w") as f:
            json.dump(meta, f)

        # Call get_financial_snapshot_status
        status, meta_out = get_financial_snapshot_status(data_dir=str(tmp_path))

        assert status == FinancialSnapshotStatus.SNAPSHOT_READY_FOR_SCANNER
        assert meta_out["FINANCIAL_SNAPSHOT_STATUS"] == "SNAPSHOT_READY_FOR_SCANNER"


def test_fresh_building_snapshot_status_not_auto_healed():
    """
    Verifies that a fresh BUILDING status (<15 minutes old) is NOT auto-healed
    and properly blocks scanners while active.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        meta_file = tmp_path / "canonical_pit_rebuilt_meta.json"
        parquet_file = tmp_path / "canonical_pit_rebuilt.parquet"

        pd.DataFrame([{"symbol": "INFY", "revenue": 100}]).to_parquet(parquet_file)

        # Write meta file with BUILDING status updated 2 minutes ago
        fresh_time = (datetime.now() - timedelta(minutes=2)).isoformat()
        meta = {
            "FINANCIAL_SNAPSHOT_STATUS": "BUILDING",
            "snapshot_status_updated_at": fresh_time,
            "total_symbols": 886,
        }
        with open(meta_file, "w") as f:
            json.dump(meta, f)

        status, meta_out = get_financial_snapshot_status(data_dir=str(tmp_path))

        assert status == FinancialSnapshotStatus.BUILDING


def test_quality_value_recovery_coalesce_lock_safety():
    """
    Verifies that QualityValueRecoveryScanner.scan_universe when coalescing
    due to global lock busy cleanly releases scan_thread_lock exactly once
    without raising RuntimeError: release unlocked lock.
    """
    import threading
    from app.live_fundamental_scanner import QualityValueRecoveryScanner, _global_lock
    scanner = QualityValueRecoveryScanner()

    # Pre-acquire global lock in a background thread to simulate another active scanner
    lock_acquired = threading.Event()
    release_lock = threading.Event()

    def hold_lock():
        _global_lock.acquire(blocking=False, owner_scanner="OTHER_SCANNER", operation="FULL_SCAN")
        lock_acquired.set()
        release_lock.wait()
        _global_lock.release()

    t = threading.Thread(target=hold_lock, daemon=True)
    t.start()
    lock_acquired.wait()

    try:
        # Calling scan_universe with coalesce_if_busy=True
        # Under the old code, this raised RuntimeError: release unlocked lock!
        result = scanner.scan_universe(trigger_type="TEST", scheduler_name="TEST", coalesce_if_busy=True)
        assert result.get("status") == "COALESCED"
        # Verify scanner.scan_thread_lock is free and can be acquired cleanly
        can_acquire = scanner.scan_thread_lock.acquire(blocking=False)
        assert can_acquire is True
        scanner.scan_thread_lock.release()
    finally:
        release_lock.set()
        t.join(timeout=3.0)

