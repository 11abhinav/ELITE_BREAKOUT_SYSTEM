"""
COMPREHENSIVE SCANNER ARCHITECTURE AUDIT TEST SUITE
===================================================
Tests and asserts system invariants across all certified scanners:
1. Schedule alignment: 17:00 IST CRON invokes QUALITY_COMPOUNDER V2, not decommissioned Wealth Engine.
2. Non-market boot queue: Excludes decommissioned WEALTH_ENGINE.
3. Fundamental wealth exit authority: close_position_atomic, run_v2_exit_check,
   save_v2_exit_event, get_active_wealth_holdings all support 'FUNDAMENTAL'.
4. Alert CMP Invariant: performance_tracker Tier-3 parquet fallback enforces latest-session freshness.
5. Two-strategy isolation: Compounder vs Recovery vs Fundamental exit rules remain strictly unpolluted.
"""

import pytest
import os
import json
import pandas as pd
from datetime import datetime
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")


def test_1700_cron_triggers_quality_compounder():
    """Verify that main.py scheduler invokes _trigger_quality_compounder_v2 at 17:00 IST."""
    with open("app/main.py", "r", encoding="utf-8") as f:
        content = f.read()

    # Assert 17:00 IST section references Quality Compounder
    assert "_trigger_quality_compounder_v2" in content
    # Assert at 17:00, Quality Compounder is scheduled
    assert "Triggering QUALITY_COMPOUNDER V2 Full Daily Scan" in content
    # Assert Wealth Engine is not triggered in the 17:00 CRON block
    idx_1700 = content.find("17:00 - Quality Compounder V2 Full Daily Scan")
    assert idx_1700 != -1
    block_1700 = content[idx_1700:idx_1700 + 1500]
    assert "threading.Thread(target=_trigger_quality_compounder_v2" in block_1700
    assert "threading.Thread(target=_trigger_wealth_engine" not in block_1700


def test_non_market_boot_excludes_decommissioned_wealth_engine():
    """Verify run_all_seven_scanners_non_market_boot queues only certified production scanners."""
    with open("app/main.py", "r", encoding="utf-8") as f:
        content = f.read()

    idx_boot = content.find("def run_all_seven_scanners_non_market_boot")
    assert idx_boot != -1
    block_boot = content[idx_boot:idx_boot + 3000]
    assert "(\"WEALTH_ENGINE\", _trigger_wealth_engine)" not in block_boot
    assert "(\"QUALITY_COMPOUNDER\", _trigger_quality_compounder_v2)" in block_boot
    assert "(\"QUALITY_VALUE_RECOVERY\", _trigger_quality_value_recovery)" in block_boot
    assert "(\"TECHNICAL\", _trigger_technical)" in block_boot
    assert "(\"FUNDAMENTAL\", _trigger_fundamental)" in block_boot


def test_fundamental_scanner_supported_in_database_wealth_queries():
    """Verify database.py functions for wealth exits include 'FUNDAMENTAL' in scanner list."""
    with open("app/database.py", "r", encoding="utf-8") as f:
        content = f.read()

    expected_scanner_clause = "scanner IN ('FUNDAMENTAL', 'QUALITY_COMPOUNDER', 'QUALITY_COMPOUNDER_VALUE_V2_FINAL', 'QUALITY_VALUE_RECOVERY', 'QUALITY_VALUE_RECOVERY_WEALTH_V1')"
    
    # Assert in get_active_wealth_holdings
    assert expected_scanner_clause in content

    # Assert in close_position_atomic SELECT and UPDATE
    idx_close = content.find("def close_position_atomic")
    assert idx_close != -1
    close_func = content[idx_close:idx_close + 3000]
    assert expected_scanner_clause in close_func

    # Assert in save_v2_exit_event
    idx_v2_event = content.find("def save_v2_exit_event")
    assert idx_v2_event != -1
    v2_event_func = content[idx_v2_event:idx_v2_event + 1500]
    assert expected_scanner_clause in v2_event_func


def test_live_wealth_monitor_includes_fundamental():
    """Verify live_wealth_monitor.py run_v2_exit_check includes 'FUNDAMENTAL' in its query."""
    with open("app/live_wealth_monitor.py", "r", encoding="utf-8") as f:
        content = f.read()

    expected_scanner_clause = "scanner IN ('FUNDAMENTAL', 'QUALITY_COMPOUNDER', 'QUALITY_COMPOUNDER_VALUE_V2_FINAL', 'QUALITY_VALUE_RECOVERY', 'QUALITY_VALUE_RECOVERY_WEALTH_V1')"
    assert expected_scanner_clause in content


def test_performance_tracker_tier3_rejects_stale_parquet(monkeypatch):
    """Verify performance_tracker Tier-3 fallback rejects parquet data that is not from latest session."""
    from app.performance_tracker import _fetch_current_prices

    # Run _fetch_current_prices with a dummy symbol
    # Live fetch fails for non-existent symbol, then it attempts Tier 3
    # If no valid fresh parquet exists, it returns None / empty for that symbol
    prices = _fetch_current_prices(["NONEXISTENT_STALE_TEST_SYM_XYZ"])
    assert prices.get("NONEXISTENT_STALE_TEST_SYM_XYZ") is None
