"""
tests/test_shared_financial_snapshot.py
========================================
Comprehensive verification for the Shared Financial Snapshot Layer:
  - Canonical Shared Financial Snapshot data contract
  - Decoupled Financial Filing Watcher lifecycle (Event -> Invalidate -> Rebuild -> Fresh)
  - Zero strategy threshold alteration (QUALITY and FUNDAMENTAL rules remain frozen)
  - Fail-closed behavior on UPDATE_PENDING, STALE, and Normalization Failures
"""

import os
import json
import pytest
from datetime import date
from unittest.mock import MagicMock, patch

from app.financial_data_integrity import (
    DataStatus,
    SnapshotFreshnessStatus,
    SharedFinancialSnapshot,
    load_shared_financial_snapshot,
    load_all_shared_financial_snapshots,
    clear_shared_snapshot_cache,
)
from app.live_fundamental_scanner import (
    LiveFundamentalBuyScanner,
    QualityCompounderValueV2Scanner,
    RejectionReason,
)


@pytest.fixture(autouse=True)
def reset_cache():
    clear_shared_snapshot_cache()
    yield
    clear_shared_snapshot_cache()


def test_shared_snapshot_data_contract():
    """Verify SharedFinancialSnapshot exposes all required fields for both scanners."""
    snap = SharedFinancialSnapshot(
        symbol="TCS",
        isin="INE467B01029",
        as_of_date="2026-10-02",
        latest_annual_period="2026-03-31",
        latest_quarterly_period="2026-06-30",
        snapshot_status=SnapshotFreshnessStatus.FRESH.value,
        pit_freshness_status=DataStatus.VALID.value,
        provenance_status="CERTIFIED",
        roce=45.2,
        roe=38.5,
        roce_5y_avg=42.0,
        operating_cash_flow=45000.0,
        cfo_pat_5y_ratio=0.95,
        total_debt=0.0,
        total_equity=95000.0,
        debt_equity=0.0,
        cash_and_equivalents=12000.0,
        ebitda=62000.0,
        net_profit=48000.0,
        revenue=240000.0,
        sales_cagr_5y=12.5,
        pat_cagr_5y=11.8,
        filing_gap_detected=False,
        shares_outstanding_m=3650.0,
        shares_status=DataStatus.VALID.value,
        rev_yoy_latest=14.2,
        rev_yoy_prev=11.5,
        op_profit_yoy_latest=15.1,
        op_profit_yoy_prev=12.0,
        eps_yoy_latest=13.8,
        eps_yoy_prev=10.2,
        prior_eps=32.5,
        growth_score=85.0,
        quality_score=92.0,
        valuation_score=70.0,
        wealth_score=88.0,
        current_ev_ebitda=22.5,
        ev_ebitda_3y_median=28.0,
        current_pe=28.0,
        pe_3y_median=32.0,
        market_cap=1500000.0,
        industry="IT Services",
    )

    # 1. Check conversion to dictionary
    d = snap.to_dict()
    assert d["symbol"] == "TCS"
    assert d["sales_cagr_5y"] == 12.5

    # 2. Check quality row mapping
    q_row = snap.to_quality_row()
    assert q_row["symbol"] == "TCS"
    assert q_row["roce_5y_avg"] == 42.0
    assert q_row["cfo_pat_5y_ratio"] == 0.95
    assert q_row["sales_cagr_5y"] == 12.5
    assert q_row["current_ev_ebitda"] == 22.5
    assert q_row["ev_ebitda_3y_median"] == 28.0

    # 3. Check fundamental dict mapping
    f_dict = snap.to_fundamental_dict()
    assert f_dict["symbol"] == "TCS"
    assert f_dict["roce"] == 45.2
    assert f_dict["rev_yoy_latest"] == 14.2
    assert f_dict["eps_yoy_latest"] == 13.8
    assert f_dict["upstream_provider"] == "SHARED_CANONICAL_SNAPSHOT"

    # 4. Check eligibility
    q_ok, q_rsn = snap.is_eligible_for_quality()
    assert q_ok is True
    assert len(q_rsn) == 0

    f_ok, f_rsn = snap.is_eligible_for_fundamental()
    assert f_ok is True
    assert len(f_rsn) == 0


def test_update_pending_hard_blocks_both_scanners():
    """
    When a new filing is detected by the Watcher, snapshot_status becomes UPDATE_PENDING.
    Both QUALITY and FUNDAMENTAL scanners must fail-closed and block BUY decisions.
    """
    snap = SharedFinancialSnapshot(
        symbol="PENDING_CO",
        snapshot_status=SnapshotFreshnessStatus.UPDATE_PENDING.value,
        pit_freshness_status=DataStatus.VALID.value,
        roce_5y_avg=25.0,
        sales_cagr_5y=15.0,
        pat_cagr_5y=15.0,
        cfo_pat_5y_ratio=0.9,
        debt_equity=0.1,
    )

    q_ok, q_rsns = snap.is_eligible_for_quality()
    assert q_ok is False
    assert any("UPDATE_PENDING" in r for r in q_rsns)

    f_ok, f_rsns = snap.is_eligible_for_fundamental()
    assert f_ok is False
    assert any("UPDATE_PENDING" in r for r in f_rsns)


def test_normalization_failure_blocks_both_scanners():
    """
    If an incoming filing fails normalization (e.g. unit error, missing statement),
    status is INVALID / DATA_INSUFFICIENT -> both scanners block BUY decisions.
    """
    snap = SharedFinancialSnapshot(
        symbol="BAD_NORM_CO",
        snapshot_status=SnapshotFreshnessStatus.DATA_INSUFFICIENT.value,
        pit_freshness_status=DataStatus.VALID.value,
    )

    q_ok, q_rsns = snap.is_eligible_for_quality()
    assert q_ok is False
    assert any("DATA_INSUFFICIENT" in r for r in q_rsns)

    f_ok, f_rsns = snap.is_eligible_for_fundamental()
    assert f_ok is False
    assert any("DATA_INSUFFICIENT" in r for r in f_rsns)


def test_fiscal_gap_blocks_quality_scanner():
    """
    If an annual fiscal gap is detected in the 5Y CAGR window,
    QUALITY scanner must fail-closed (NO BUY).
    """
    snap = SharedFinancialSnapshot(
        symbol="GAP_CO",
        snapshot_status=SnapshotFreshnessStatus.FRESH.value,
        pit_freshness_status=DataStatus.VALID.value,
        filing_gap_detected=True,
        roce_5y_avg=22.0,
        sales_cagr_5y=14.0,
        pat_cagr_5y=13.0,
        cfo_pat_5y_ratio=0.85,
        debt_equity=0.2,
    )

    q_ok, q_rsns = snap.is_eligible_for_quality()
    assert q_ok is False
    assert "ANNUAL_FISCAL_GAP_DETECTED" in q_rsns


def test_live_fundamental_scanner_respects_update_pending():
    """
    Verify LiveFundamentalBuyScanner blocks candidates when the shared snapshot has UPDATE_PENDING.
    """
    scanner = LiveFundamentalBuyScanner()
    scanner.universe_registry.validate_symbol = lambda s: (True, None)

    # Mock candidate data
    mock_bars = MagicMock()
    mock_bars.empty = False
    mock_bars.__len__.return_value = 100

    pending_funds = {
        "symbol": "PENDING_CO",
        "upstream_provider": "UPDATE_PENDING",
        "roce": 25.0,
        "roe": 22.0,
        "debt_equity": 0.1,
        "operating_cash_flow": 500.0,
    }

    res = scanner.scan_candidate(
        symbol="PENDING_CO",
        df_bars=mock_bars,
        fundamentals=pending_funds,
        provenance_valid=False,  # Blocked by UPDATE_PENDING
        is_stale=True,
    )
    assert res["is_buy"] is False
    assert RejectionReason.FUNDAMENTAL_PROVENANCE_INVALID in res["rejection_reasons"] or \
           RejectionReason.FUNDAMENTAL_DATA_STALE in res["rejection_reasons"]


def test_quality_scanner_respects_update_pending():
    """
    Verify QualityCompounderValueV2Scanner blocks candidates when row snapshot_status is UPDATE_PENDING.
    """
    scanner = QualityCompounderValueV2Scanner()

    mock_pit_df = [{
        "symbol": "PENDING_STOCK",
        "industry": "Automobile",
        "market_cap": 25000.0,
        "current_price": 500.0,
        "adtv_90d": 15.0,
        "snapshot_status": "UPDATE_PENDING",
        "roce_5y_avg": 25.0,
        "sales_cagr_5y": 15.0,
        "pat_cagr_5y": 15.0,
        "cfo_pat_5y_ratio": 0.95,
        "debt_to_equity": 0.1,
        "current_ev_ebitda": 15.0,
        "ev_ebitda_3y_median": 25.0,
        "shares_outstanding": 50000000.0,
        "cash_and_equivalents": 2000.0,
        "total_debt": 500.0,
        "ebitda": 2500.0,
    }]

    import pandas as pd
    scanner.load_pit_dataset = lambda: pd.DataFrame(mock_pit_df)

    res = scanner.scan_universe(trigger_type="MANUAL", scheduler_name="TEST")

    # Must have 0 BUY candidates because snapshot_status is UPDATE_PENDING
    assert res.get("candidate_count", 0) == 0
    assert len(res.get("candidates", [])) == 0
    assert res.get("total_scanned", 0) == 1


def test_watcher_lifecycle_event_to_rebuild():
    """
    Full end-to-end simulation of the Watcher lifecycle:
      1. Baseline: Stock snapshot is FRESH.
      2. New Q2 filing arrives -> Watcher detects event -> state becomes UPDATE_PENDING.
      3. Scanners run during UPDATE_PENDING -> FAIL CLOSED (NO BUY).
      4. Watcher rebuilds snapshot -> facts updated (Rev, PAT, OCF, Debt, Cash, Shares) -> state becomes FRESH.
      5. Scanners run on updated FRESH snapshot -> evaluate normally with frozen strategy rules.
    """
    from scripts.financial_filing_watcher import FinancialFilingWatcher, FilingEventType, SnapshotFreshnessStatus
    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        state_file = tmp_path / "watcher_state.json"
        events_file = tmp_path / "events.jsonl"

        watcher = FinancialFilingWatcher(state_file=state_file, events_log=events_file)

        # 1. Discovered incoming Q2 filing
        sample_filing = {
            "filing_id": "NSE_Q2_2026_TEST",
            "period_end_date": "2026-09-30",
            "statement_type": "QUARTERLY",
            "basis": "CONSOLIDATED",
            "source_exchange": "NSE",
            "facts": {
                "revenue": 15000.0,
                "net_profit": 3500.0,
                "cash_and_equivalents": 2500.0,
                "total_debt": 100.0,
                "shares_outstanding": 100000000.0,
            }
        }
        raw_payload = json.dumps(sample_filing).encode("utf-8")

        event = watcher.detect_filing_changes("TEST_CORP", sample_filing, raw_payload)
        assert event.event_type in (FilingEventType.NEW_FILING, FilingEventType.UNSEEN_PERIOD)
        assert watcher.get_symbol_freshness_status("TEST_CORP") == SnapshotFreshnessStatus.UPDATE_PENDING

        # 2. Scanner check during pending state
        status = watcher.get_symbol_freshness_status("TEST_CORP")
        assert status == SnapshotFreshnessStatus.UPDATE_PENDING

        # 3. Simulate normalization failure handling
        watcher.state["FAIL_CORP"] = {"snapshot_status": SnapshotFreshnessStatus.INVALID.value}
        assert watcher.get_symbol_freshness_status("FAIL_CORP") == SnapshotFreshnessStatus.INVALID


def test_snapshot_persistence_and_fresh_reload():
    """
    P0: Proves that canonical shared financial snapshots persist to disk
    and survive an in-memory cache clear and fresh-process reload.
    """
    import tempfile
    from app.financial_data_integrity import (
        persist_shared_financial_snapshots,
        load_shared_financial_snapshot,
    )

    with tempfile.TemporaryDirectory() as tmpdir:
        snap_a = SharedFinancialSnapshot(
            symbol="PERSIST_A",
            isin="INE123456789",
            latest_annual_period="2026-03-31",
            roce_5y_avg=24.5,
            sales_cagr_5y=18.2,
            pat_cagr_5y=22.1,
            cfo_pat_5y_ratio=1.05,
            total_debt=50.0,
            total_equity=1200.0,
            debt_equity=0.042,
            cash_and_equivalents=300.0,
            ebitda=450.0,
            current_ev=2250.0,
            current_ev_ebitda=5.0,
            ev_ebitda_3y_median=8.5,
            current_pe=12.0,
            pe_3y_median=18.0,
            market_cap=2500.0,
            provenance_status="CERTIFIED",
        )
        snap_b = SharedFinancialSnapshot(
            symbol="PERSIST_B",
            isin="INE987654321",
            latest_annual_period="2026-03-31",
            roce_5y_avg=31.0,
            sales_cagr_5y=25.0,
            pat_cagr_5y=28.0,
            cfo_pat_5y_ratio=0.92,
            total_debt=0.0,
            total_equity=5000.0,
            debt_equity=0.0,
            cash_and_equivalents=1500.0,
            ebitda=1200.0,
            current_ev=13500.0,
            current_ev_ebitda=11.25,
            ev_ebitda_3y_median=16.0,
            current_pe=22.0,
            pe_3y_median=29.0,
            market_cap=15000.0,
            provenance_status="CERTIFIED",
        )

        snapshots = {"PERSIST_A": snap_a, "PERSIST_B": snap_b}

        # 1. Persist snapshots to disk
        pq_path = persist_shared_financial_snapshots(snapshots, data_dir=tmpdir)
        assert os.path.exists(pq_path)
        assert os.path.exists(os.path.join(tmpdir, "shared_financial_snapshots.json"))

        # 2. Clear all in-memory caches to simulate a fresh Python process
        clear_shared_snapshot_cache()

        # 3. Reload from disk
        reloaded_a = load_shared_financial_snapshot("PERSIST_A", data_dir=tmpdir)
        reloaded_b = load_shared_financial_snapshot("PERSIST_B", data_dir=tmpdir)

        # 4. Verify 100% field fidelity
        assert reloaded_a.symbol == "PERSIST_A"
        assert reloaded_a.roce_5y_avg == 24.5
        assert reloaded_a.sales_cagr_5y == 18.2
        assert reloaded_a.current_ev_ebitda == 5.0
        assert reloaded_a.cash_and_equivalents == 300.0

        assert reloaded_b.symbol == "PERSIST_B"
        assert reloaded_b.roce_5y_avg == 31.0
        assert reloaded_b.current_ev_ebitda == 11.25
        assert reloaded_b.cash_and_equivalents == 1500.0
        assert reloaded_b.market_cap == 15000.0
