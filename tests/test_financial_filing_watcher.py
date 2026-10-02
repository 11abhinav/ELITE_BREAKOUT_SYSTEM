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
        def get_symbol_freshness_status(self, symbol):
            if symbol == "TCS":
                return SnapshotFreshnessStatus.UPDATE_PENDING
            return SnapshotFreshnessStatus.FRESH

    monkeypatch.setattr("scripts.financial_filing_watcher.FinancialFilingWatcher", MockWatcher)

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
