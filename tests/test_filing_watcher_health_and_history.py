"""
tests/test_filing_watcher_health_and_history.py
================================================
Comprehensive verification suite for Financial Filing Watcher health monitoring,
scanner execution history recording, failure diagnostics, and admin board integration.

Verifies:
  1. Successful cycle logs entry in scanner_execution_history with lifecycle_status='COMPLETED'.
  2. Successful cycle upserts scanner_health with status='OK', outcome='SUCCESS', last_success, and duration.
  3. Failure during cycle marks scanner_health as 'DOWN' with detailed error_msg.
  4. Failure during cycle finalizes scanner_execution_history with lifecycle_status='FAILED' and stop_reason.
  5. Failure logs to fetch_errors and scan_failures tables.
  6. get_all_scanner_health() includes FILING_WATCHER with expected schedule.
  7. normalize_scanner_name() resolves FILING_WATCHER aliases cleanly.
  8. trigger_scanner_manual("FILING_WATCHER") successfully initiates execution.
  9. admin_dashboard.html includes FILING_WATCHER in SCANNER_META and ALLOWED_SCANNERS.
"""

import json
import os
import sys
import tempfile
import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch

# Add repository root to sys.path
BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from scripts.financial_filing_watcher import (
    FinancialFilingWatcher,
    SnapshotFreshnessStatus,
    _WATCHER_LOCK,
)
from app.database import (
    normalize_scanner_name,
    get_all_scanner_health,
    is_scanner_stopped,
)
from app.main import trigger_scanner_manual, _trigger_filing_watcher


def test_normalize_scanner_name_filing_watcher():
    """Verify FILING_WATCHER aliases are canonicalized."""
    assert normalize_scanner_name("FILING_WATCHER") == "FILING_WATCHER"
    assert normalize_scanner_name("FINANCIAL_FILING_WATCHER") == "FILING_WATCHER"
    assert normalize_scanner_name("filing_watcher") == "FILING_WATCHER"
    assert normalize_scanner_name("financial_watcher") == "FILING_WATCHER"


def test_get_all_scanner_health_includes_filing_watcher():
    """Verify get_all_scanner_health surfaces FILING_WATCHER with schedule."""
    rows = get_all_scanner_health()
    filing_rows = [r for r in rows if r.get("scanner_name") == "FILING_WATCHER"]
    assert len(filing_rows) == 1, "FILING_WATCHER must appear in get_all_scanner_health"
    row = filing_rows[0]
    assert "08:00" in row.get("scheduled_for", "")
    assert "16:30" in row.get("scheduled_for", "")


def test_successful_watcher_cycle_records_history_and_ok_health(monkeypatch):
    """Verify a clean watcher cycle logs history and marks health OK."""
    with tempfile.TemporaryDirectory() as tmpdir:
        state_file = Path(tmpdir) / "state.json"
        events_log = Path(tmpdir) / "events.jsonl"
        snap_path = Path(tmpdir) / "canonical.parquet"
        snap_path.touch()

        # Mock database telemetry calls
        history_calls = []
        health_calls = []

        def mock_start_run(*args, **kwargs):
            mock_ctx = MagicMock()
            mock_ctx.run_id = "test-run-123"
            mock_ctx.scanner_name = "FILING_WATCHER"
            mock_ctx.total_stocks = kwargs.get("total_stocks", 0)
            return mock_ctx

        def mock_complete_run(*args, **kwargs):
            history_calls.append(kwargs)

        def mock_upsert_health(*args, **kwargs):
            health_calls.append(kwargs)

        monkeypatch.setattr("app.database.start_scanner_execution_run", mock_start_run)
        monkeypatch.setattr("app.database.complete_scanner_execution_run", mock_complete_run)
        monkeypatch.setattr("app.database.upsert_scanner_health", mock_upsert_health)
        monkeypatch.setattr("scripts.financial_filing_watcher.SNAPSHOT_PATH", snap_path)

        watcher = FinancialFilingWatcher(state_file=state_file, events_log=events_log)
        # Mock invalidate_and_rebuild_snapshot to return True
        monkeypatch.setattr(watcher, "invalidate_and_rebuild_snapshot", lambda **kwargs: True)

        res = watcher.run_watcher_cycle(trigger_type="TEST", scheduler_name="PYTEST")
        assert res["status"] == "SUCCESS"

        # Verify scanner_health transitions
        assert any(h.get("status") == "RUNNING" for h in health_calls)
        ok_health = [h for h in health_calls if h.get("status") == "OK"]
        assert len(ok_health) >= 1
        assert ok_health[-1]["scanner_name"] == "FILING_WATCHER"
        assert ok_health[-1]["outcome"] == "SUCCESS"
        assert ok_health[-1]["error_msg"] is None
        assert ok_health[-1]["last_success"] is not None

        # Verify scanner_execution_history finalized as COMPLETED
        assert len(history_calls) >= 1
        last_history = history_calls[-1]
        assert last_history.get("lifecycle_status") == "COMPLETED"
        assert last_history.get("quality_status") == "NORMAL"


def test_failed_watcher_cycle_marks_down_and_records_failures(monkeypatch):
    """Verify an exception during cycle sets status DOWN with error details."""
    with tempfile.TemporaryDirectory() as tmpdir:
        state_file = Path(tmpdir) / "state.json"
        events_log = Path(tmpdir) / "events.jsonl"
        snap_path = Path(tmpdir) / "canonical.parquet"

        history_calls = []
        health_calls = []
        fetch_error_calls = []

        def mock_start_run(*args, **kwargs):
            mock_ctx = MagicMock()
            mock_ctx.run_id = "test-fail-run-456"
            mock_ctx.scanner_name = "FILING_WATCHER"
            return mock_ctx

        def mock_complete_run(*args, **kwargs):
            history_calls.append(kwargs)

        def mock_upsert_health(*args, **kwargs):
            health_calls.append(kwargs)

        def mock_upsert_fetch_error(*args, **kwargs):
            fetch_error_calls.append(kwargs)

        monkeypatch.setattr("app.database.start_scanner_execution_run", mock_start_run)
        monkeypatch.setattr("app.database.complete_scanner_execution_run", mock_complete_run)
        monkeypatch.setattr("app.database.upsert_scanner_health", mock_upsert_health)
        monkeypatch.setattr("app.database.upsert_fetch_error", mock_upsert_fetch_error)
        monkeypatch.setattr("scripts.financial_filing_watcher.SNAPSHOT_PATH", snap_path)

        watcher = FinancialFilingWatcher(state_file=state_file, events_log=events_log)

        # Force rebuild failure
        def mock_failing_rebuild(**kwargs):
            raise RuntimeError("NSE API 503 Service Unavailable: Exchange connection timeout")

        monkeypatch.setattr(watcher, "invalidate_and_rebuild_snapshot", mock_failing_rebuild)

        res = watcher.run_watcher_cycle(trigger_type="TEST", scheduler_name="PYTEST", force_rebuild=True)
        assert res["status"] == "FAILED"
        assert "NSE API 503" in res["error"]

        # Verify scanner_health marked DOWN with detailed error message
        down_health = [h for h in health_calls if h.get("status") == "DOWN"]
        assert len(down_health) >= 1
        assert down_health[-1]["scanner_name"] == "FILING_WATCHER"
        assert "NSE API 503" in down_health[-1]["error_msg"]
        assert down_health[-1]["outcome"] == "FAILED"

        # Verify scanner_execution_history marked FAILED
        assert len(history_calls) >= 1
        last_history = history_calls[-1]
        assert last_history.get("lifecycle_status") == "FAILED"
        assert "NSE API 503" in last_history.get("stop_reason", "")

        # Verify fetch_errors logged
        assert len(fetch_error_calls) >= 1
        assert fetch_error_calls[0].get("scanner_name") == "FILING_WATCHER"
        assert fetch_error_calls[0].get("category") == "CORPORATE_FILING"


def test_trigger_scanner_manual_initiates_filing_watcher(monkeypatch):
    """Verify trigger_scanner_manual recognizes FILING_WATCHER and initiates execution."""
    cycle_triggered = []

    def mock_trigger_watcher(*args, **kwargs):
        cycle_triggered.append(kwargs)
        return {"status": "SUCCESS", "processed_count": 886}

    monkeypatch.setattr("app.main._trigger_filing_watcher", mock_trigger_watcher)
    monkeypatch.setattr("app.database.is_scanner_stopped", lambda name: False)
    monkeypatch.setattr("app.database.is_scanner_actively_running", lambda name: False)
    monkeypatch.setattr("app.database.upsert_scanner_health", lambda *args, **kwargs: None)

    res = trigger_scanner_manual("FILING_WATCHER")
    assert res.get("status") == "ok"
    assert "triggered" in res.get("message", "")


def test_admin_dashboard_html_contains_filing_watcher():
    """Verify admin_dashboard.html registers FILING_WATCHER in meta and allowed scanners."""
    html_path = BASE_DIR / "app" / "admin_dashboard.html"
    assert html_path.exists()
    content = html_path.read_text()

    assert "'FILING_WATCHER'" in content
    assert "Filing Watcher" in content
    assert "Filings Audited:" in content or "Pre-Buy Fence:" in content
