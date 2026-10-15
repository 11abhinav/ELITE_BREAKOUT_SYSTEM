"""
tests/test_financial_filing_watcher.py
======================================
Comprehensive unit tests for the decoupled Financial Filing Watcher,
Event Detector, and Dependency-Based Snapshot Invalidator.

Verifies:
  - Event Detection: NEW_FILING, AMENDED_FILING, RESTATED_FILING, UNSEEN_PERIOD, NO_CHANGE
  - SHA256 immutability check
  - Dependency resolution: QUARTERLY vs ANNUAL metric invalidation graphs
  - Status contract: FRESH vs UPDATE_PENDING vs STALE
"""

import json
import os
import tempfile
import pytest
from pathlib import Path

from scripts.financial_filing_watcher import (
    FinancialFilingWatcher,
    FilingEventType,
    SnapshotFreshnessStatus,
    FilingEvent,
)


@pytest.fixture
def temp_watcher():
    with tempfile.TemporaryDirectory() as tmpdir:
        state_file = Path(tmpdir) / "test_watcher_state.json"
        events_log = Path(tmpdir) / "test_events.jsonl"
        watcher = FinancialFilingWatcher(state_file=state_file, events_log=events_log)
        yield watcher, tmpdir


def test_new_filing_event_detection(temp_watcher):
    watcher, tmpdir = temp_watcher
    symbol = "TCS"
    payload = b'{"filing_id": "TCS_20250630_Q1", "revenue": 62000, "net_profit": 12000}'
    filing_meta = {
        "filing_id": "TCS_20250630_Q1",
        "period_end_date": "2025-06-30",
        "statement_type": "QUARTERLY",
        "basis": "CONSOLIDATED",
    }

    event = watcher.detect_filing_changes(symbol, filing_meta, payload)
    assert event.event_type in (FilingEventType.UNSEEN_PERIOD, FilingEventType.NEW_FILING)
    assert event.symbol == "TCS"
    assert "rev_yoy_latest" in event.affected_metrics
    assert "eps_yoy_latest" in event.affected_metrics

    # Verify state was marked UPDATE_PENDING
    status = watcher.get_symbol_freshness_status("TCS")
    assert status == SnapshotFreshnessStatus.UPDATE_PENDING


def test_unchanged_payload_emits_no_change(temp_watcher):
    watcher, tmpdir = temp_watcher
    symbol = "INFY"
    payload = b'{"filing_id": "INFY_20250331_A", "revenue": 150000, "net_profit": 26000}'
    filing_meta = {
        "filing_id": "INFY_20250331_A",
        "period_end_date": "2025-03-31",
        "statement_type": "ANNUAL",
        "basis": "CONSOLIDATED",
    }

    # First event
    ev1 = watcher.detect_filing_changes(symbol, filing_meta, payload)
    assert ev1.event_type in (FilingEventType.UNSEEN_PERIOD, FilingEventType.NEW_FILING)

    # Identical second event -> NO_CHANGE
    ev2 = watcher.detect_filing_changes(symbol, filing_meta, payload)
    assert ev2.event_type == FilingEventType.NO_CHANGE


def test_amended_filing_detected(temp_watcher):
    watcher, tmpdir = temp_watcher
    symbol = "WIPRO"
    payload_v1 = b'{"filing_id": "WIPRO_20250331_A", "revenue": 90000, "cash": 10000}'
    filing_meta_v1 = {
        "filing_id": "WIPRO_20250331_A",
        "period_end_date": "2025-03-31",
        "statement_type": "ANNUAL",
        "basis": "CONSOLIDATED",
    }
    watcher.detect_filing_changes(symbol, filing_meta_v1, payload_v1)

    # Amendment with changed payload
    payload_v2 = b'{"filing_id": "WIPRO_20250331_A", "revenue": 90500, "cash": 10200, "amended": true}'
    filing_meta_v2 = {
        "filing_id": "WIPRO_20250331_A",
        "period_end_date": "2025-03-31",
        "statement_type": "ANNUAL",
        "basis": "CONSOLIDATED",
        "amended": True,
        "revision_number": 2,
    }
    ev_amend = watcher.detect_filing_changes(symbol, filing_meta_v2, payload_v2)
    assert ev_amend.event_type == FilingEventType.AMENDED_FILING
    assert "5y_sales_cagr" in ev_amend.affected_metrics
    assert "current_ev" in ev_amend.affected_metrics


def test_dependency_resolution_quarterly():
    watcher = FinancialFilingWatcher()
    deps = watcher.resolve_dependent_metrics("QUARTERLY", {"facts": {"revenue": 1000, "eps": 20}})
    assert "rev_yoy_latest" in deps
    assert "eps_yoy_latest" in deps
    assert "op_profit_yoy_latest" in deps
    assert "growth_score" in deps


def test_dependency_resolution_annual():
    watcher = FinancialFilingWatcher()
    deps = watcher.resolve_dependent_metrics("ANNUAL", {"facts": {"total_debt": 100, "cash_and_equivalents": 50}})
    assert "5y_sales_cagr" in deps
    assert "5y_pat_cagr" in deps
    assert "roce_5y_avg" in deps
    assert "cfo_pat_5y_ratio" in deps
    assert "current_ev" in deps
    assert "debt_to_equity" in deps


def test_pre_buy_gate_blocks_when_update_pending(monkeypatch):
    from app.financial_data_integrity import pre_buy_data_integrity_gate, FieldProvenance, BUYEvidenceBundle, DataStatus

    # Mock FinancialFilingWatcher to simulate UPDATE_PENDING for TCS
    class MockWatcher:
        def __init__(self, *args, **kwargs):
            pass

        def get_symbol_freshness_status(self, symbol):
            if symbol == "TCS":
                return SnapshotFreshnessStatus.UPDATE_PENDING
            return SnapshotFreshnessStatus.FRESH

    monkeypatch.setattr("scripts.financial_filing_watcher.FinancialFilingWatcher", MockWatcher)
    monkeypatch.setattr("app.financial_data_integrity._mark_update_pending", lambda *args, **kwargs: None)

    bundle = BUYEvidenceBundle(
        scan_run_id="TEST_RUN",
        scanner="QUALITY_COMPOUNDER",
        symbol="TCS",
        cmp=3500.0,
        strategy_score=90.0,
        financial_metrics={
            "roce_5y_avg": FieldProvenance(
                symbol="TCS", scanner="QUALITY_COMPOUNDER", field="roce_5y_avg",
                value_used=35.0, unit="PERCENT", period_end="2025-03-31", basis="CONSOLIDATED",
                source_used="PIT", validation_status="PASSED"
            )
        }
    )
    result = pre_buy_data_integrity_gate(bundle)
    assert not result.ok
    assert any("UPDATE_PENDING" in r for r in bundle.blocking_reasons)


def test_stale_stock_restoration_on_new_filing(monkeypatch):
    import app.database as db
    from app.database import (
        mark_symbol_stale,
        get_active_cooloff_symbols,
        check_and_restore_stale_stock,
        remove_symbol_from_cooloff,
    )

    notifications_sent = []
    def mock_insert_notification(notif_type, title, message, symbol=None, alert_id=None):
        notifications_sent.append({
            "type": notif_type,
            "title": title,
            "message": message,
            "symbol": symbol
        })

    monkeypatch.setattr(db, "insert_notification", mock_insert_notification)

    sym = "TEST_RESTORE_UNIT"
    # Ensure starting clean
    remove_symbol_from_cooloff(sym, "ALL")
    remove_symbol_from_cooloff(sym, "FUNDAMENTAL")

    # 1. Place symbol into cooloff / mark stale -> Should dispatch Stale Data notification
    res = mark_symbol_stale(sym, reason="STALE_DATA_TEST", scanner="FUNDAMENTAL", duration_days=7, notify_admin=True)
    assert res is True
    cooloffs = get_active_cooloff_symbols("FUNDAMENTAL")
    assert sym in cooloffs

    assert len(notifications_sent) == 1
    assert notifications_sent[0]["title"] == f"Stale Data: {sym}"
    assert notifications_sent[0]["symbol"] == sym
    assert "Marking as stale data" in notifications_sent[0]["message"]

    # 2. Call check_and_restore_stale_stock -> Should dispatch Stale Stock Restored notification
    restored = check_and_restore_stale_stock(sym, source="UNIT_TEST", period_end="2025-03-31", notify_admin=True)
    assert restored is True

    assert len(notifications_sent) == 2
    assert notifications_sent[1]["title"] == f"Stale Stock Restored: {sym}"
    assert notifications_sent[1]["symbol"] == sym
    assert "Fresh financial data" in notifications_sent[1]["message"]

    # 3. Verify symbol is evicted from active cooloff
    cooloffs_after = get_active_cooloff_symbols("FUNDAMENTAL")
    assert sym not in cooloffs_after

    # 4. Calling again for the same now-clean symbol should return False (not stale) and send no new notifications
    restored_again = check_and_restore_stale_stock(sym, source="UNIT_TEST")
    assert restored_again is False
    assert len(notifications_sent) == 2


def test_event_logging_date_normalization_not_truncated(temp_watcher):
    watcher, tmpdir = temp_watcher
    symbol = "CEATLTD"

    # Raw exchange filing with dd-MMM-yyyy format in toDate
    filing_meta = {
        "filing_id": "CEATLTD_20150630_v1",
        "toDate": "30-Jun-2015",
        "statement_type": "QUARTERLY",
        "basis": "CONSOLIDATED",
        "facts": {"revenue": 1500, "net_profit": 120, "toDate": "30-Jun-2015"}
    }
    payload = b'{"filing_id": "CEATLTD_20150630_v1", "rev": 1500}'

    event = watcher.detect_filing_changes(symbol, filing_meta, payload)

    # 1. Verify FilingEvent has full normalized ISO date, NOT truncated 9-char '30-Jun-201'
    assert event.period_end_date == "2015-06-30"
    assert len(event.period_end_date) == 10
    assert event.to_dict()["period_end_date"] == "2015-06-30"

    # 2. Verify state file stored normalized date
    state_entry = watcher.state.get("CEATLTD", {})
    assert state_entry.get("latest_filing_date") == "2015-06-30"
    assert state_entry.get("filings", {}).get("CEATLTD_20150630_v1", {}).get("period_end_date") == "2015-06-30"

    # 3. Verify event log file contains normalized ISO date
    with open(watcher.events_log, "r") as f:
        log_line = f.readline()
        logged_event = json.loads(log_line)
        assert logged_event["period_end_date"] == "2015-06-30"
        assert len(logged_event["period_end_date"]) == 10




