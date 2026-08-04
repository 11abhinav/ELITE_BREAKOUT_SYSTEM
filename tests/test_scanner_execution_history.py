# =====================================================================================
# tests/test_scanner_execution_history.py
# Unit tests for Scanner Execution History & Telemetry system.
# =====================================================================================

import pytest
import time
from unittest.mock import MagicMock, patch
from scanner_run_context import ScannerRunContext, STALE_THRESHOLDS

def test_scanner_run_context_metrics():
    ctx = ScannerRunContext(scanner_name="EOD", total_stocks=100)
    assert ctx.scanner_name == "EOD"
    assert ctx.total_stocks == 100
    assert ctx.run_id is not None
    
    ctx.mark_fresh(75)
    ctx.mark_stale(25)
    ctx.add_alert(5)
    ctx.record_api_call(10)
    ctx.record_cache_hit(8)
    ctx.record_cache_miss(2)
    
    assert ctx.fresh_count == 75
    assert ctx.stale_count == 25
    assert ctx.alerts_generated == 5
    assert ctx.api_calls == 10
    assert ctx.cache_hits == 8
    assert ctx.cache_misses == 2
    assert ctx.compute_stale_ratio() == 0.25

def test_quality_status_degradation_thresholds():
    # EOD threshold is 0.25 (25%)
    ctx_normal = ScannerRunContext(scanner_name="EOD", total_stocks=100)
    ctx_normal.mark_stale(20) # 20% <= 25%
    assert ctx_normal.evaluate_quality_status() == "NORMAL"
    
    ctx_degraded = ScannerRunContext(scanner_name="EOD", total_stocks=100)
    ctx_degraded.mark_stale(30) # 30% > 25%
    assert ctx_degraded.evaluate_quality_status() == "DEGRADED"

    # REVERSAL threshold is 0.20 (20%)
    ctx_rev_normal = ScannerRunContext(scanner_name="REVERSAL", total_stocks=100)
    ctx_rev_normal.mark_stale(15) # 15% <= 20%
    assert ctx_rev_normal.evaluate_quality_status() == "NORMAL"

    ctx_rev_deg = ScannerRunContext(scanner_name="REVERSAL", total_stocks=100)
    ctx_rev_deg.mark_stale(25) # 25% > 20%
    assert ctx_rev_deg.evaluate_quality_status() == "DEGRADED"

    # PARTIAL quality status when incomplete count > 0
    ctx_partial = ScannerRunContext(scanner_name="EOD", total_stocks=100)
    ctx_partial.mark_fresh(90)
    ctx_partial.mark_incomplete(10)
    assert ctx_partial.evaluate_quality_status() == "PARTIAL"

def test_db_start_and_complete_execution_run():
    from database import start_scanner_execution_run, complete_scanner_execution_run
    from contextlib import contextmanager
    
    mock_conn = MagicMock()
    mock_cur = MagicMock()
    mock_cur.rowcount = 1
    mock_conn.cursor.return_value.__enter__.return_value = mock_cur
    
    @contextmanager
    def mock_get_conn():
        yield mock_conn

    with patch("database.get_connection", side_effect=mock_get_conn):
        ctx = start_scanner_execution_run("EOD", trigger_type="SCHEDULED", total_stocks=100)
        assert ctx.scanner_name == "EOD"
        assert mock_cur.execute.called
        
        ctx.mark_fresh(80)
        ctx.mark_stale(20)
        ctx.add_alert(3)
        complete_scanner_execution_run(ctx)
        
        assert mock_cur.execute.call_count >= 2

def test_boot_cleanup_orphaned_runs():
    from database import cleanup_orphaned_scanner_runs_on_boot
    from contextlib import contextmanager
    
    mock_conn = MagicMock()
    mock_cur = MagicMock()
    mock_cur.rowcount = 2
    mock_conn.cursor.return_value.__enter__.return_value = mock_cur
    
    @contextmanager
    def mock_get_conn():
        yield mock_conn

    with patch("database.get_connection", side_effect=mock_get_conn):
        cleanup_orphaned_scanner_runs_on_boot()
        assert mock_cur.execute.called
        sql = mock_cur.execute.call_args[0][0]
        assert "SERVER_RESTARTED" in sql
        assert "TIMED_OUT" in sql
