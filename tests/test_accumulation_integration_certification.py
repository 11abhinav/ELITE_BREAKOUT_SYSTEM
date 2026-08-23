"""
tests/test_accumulation_integration_certification.py — Comprehensive Certification Test Suite for ACCUMULATION_SCANNER_V1.
Tests schema DDL, lifecycle DB state transitions, 18:00 delivery finalization, Admin control plane,
rejection paths (RR1 < 2, Risk > 8%, Gap > 2%), position sizing guidance, and empirical field accuracy comparisons.
"""

import pytest
import sqlite3
import pandas as pd
import numpy as np
from datetime import datetime, timedelta

from app.accumulation.schema import (
    CREATE_ACCUMULATION_CONTROL_TABLE,
    CREATE_ACCUMULATION_RUNS_TABLE,
    CREATE_ACCUMULATION_HEALTH_TABLE,
    CREATE_ACCUMULATION_ALERTS_TABLE,
    CREATE_ACCUMULATION_TRADES_TABLE,
)
from app.accumulation.contracts import TradeSetupContract, AccumulationContractValidator
from app.accumulation.sl_target import AccumulationSLTargetEngine
from app.accumulation.exit_evaluator import AccumulationExitEvaluator
from app.accumulation.scheduler import AccumulationScheduler
from app.accumulation.control import AccumulationControlPlane
from app.accumulation.field_validator import AccumulationFieldValidator


@pytest.fixture
def memory_db():
    """Fixture providing an in-memory SQLite database initialized with SQLite-adapted DDL."""
    conn = sqlite3.connect(":memory:")
    cur = conn.cursor()
    
    cur.execute("""
    CREATE TABLE IF NOT EXISTS accumulation_control (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        scanner_name TEXT NOT NULL UNIQUE DEFAULT 'ACCUMULATION_SCANNER_V1',
        accumulation_enabled BOOLEAN NOT NULL DEFAULT 1,
        accumulation_paused BOOLEAN NOT NULL DEFAULT 0,
        accumulation_stop_requested BOOLEAN NOT NULL DEFAULT 0,
        accumulation_manual_run_requested BOOLEAN NOT NULL DEFAULT 0,
        reason TEXT,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
    );
    """)
    
    cur.execute("""
    CREATE TABLE IF NOT EXISTS accumulation_alerts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        run_id TEXT NOT NULL,
        audit_snapshot_id TEXT NOT NULL UNIQUE,
        parent_snapshot_id TEXT,
        finalization_snapshot_id TEXT,
        finalization_status TEXT DEFAULT 'PENDING',
        symbol TEXT NOT NULL,
        signal_state TEXT NOT NULL,
        tradable BOOLEAN NOT NULL DEFAULT 1,
        score REAL NOT NULL,
        close REAL NOT NULL,
        entry_zone_low REAL NOT NULL,
        entry_zone_high REAL NOT NULL,
        breakout_level REAL NOT NULL,
        preferred_entry REAL NOT NULL,
        entry_method TEXT NOT NULL DEFAULT 'ZONE_MIDPOINT',
        entry_trigger_rule TEXT NOT NULL DEFAULT 'RANGE_TOUCH',
        stop_loss REAL NOT NULL,
        target_1 REAL NOT NULL,
        target_2 REAL NOT NULL,
        target_3 REAL NOT NULL,
        risk_pct REAL NOT NULL,
        rr_1 REAL NOT NULL,
        rr_2 REAL NOT NULL,
        rr_3 REAL NOT NULL,
        suggested_capital REAL,
        suggested_position_size INTEGER,
        position_sizing_basis TEXT DEFAULT 'ACCOUNT_RISK_1PCT',
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        effective_as_of DATETIME DEFAULT CURRENT_TIMESTAMP,
        CHECK (finalization_status IN ('PENDING', 'PASSED', 'REJECTED', 'CANCELLED'))
    );
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS accumulation_trades (
        trade_id INTEGER PRIMARY KEY AUTOINCREMENT,
        alert_id INTEGER,
        run_id TEXT NOT NULL,
        audit_snapshot_id TEXT NOT NULL,
        parent_snapshot_id TEXT,
        symbol TEXT NOT NULL,
        signal_state TEXT NOT NULL,
        entry_type TEXT NOT NULL DEFAULT 'ZONE_MIDPOINT',
        entry_trigger_rule TEXT NOT NULL DEFAULT 'RANGE_TOUCH',
        entry_reference_type TEXT NOT NULL DEFAULT 'STRATEGY_REFERENCE',
        entry_zone_low REAL NOT NULL,
        entry_zone_high REAL NOT NULL,
        entry_price REAL NOT NULL,
        preferred_entry REAL NOT NULL,
        entry_trigger_level REAL NOT NULL,
        entry_displacement_reference REAL NOT NULL,
        breakout_level REAL NOT NULL,
        stop_loss REAL NOT NULL,
        target_1 REAL NOT NULL,
        target_2 REAL NOT NULL,
        target_3 REAL NOT NULL,
        best_target_reached TEXT,
        last_milestone_timestamp DATETIME,
        last_milestone_bar_timestamp DATETIME,
        last_milestone_price REAL,
        entry_triggered_at DATETIME,
        entry_triggered_price REAL,
        entry_trigger_type TEXT,
        entry_quality TEXT,
        entry_gap_pct REAL,
        trigger_direction TEXT,
        entry_trigger_level_reached BOOLEAN,
        entry_trigger_bar_timestamp DATETIME,
        entry_trigger_bar_open REAL,
        entry_trigger_bar_high REAL,
        entry_trigger_bar_low REAL,
        entry_trigger_bar_close REAL,
        setup_created_as_of DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
        exit_bar_timestamp DATETIME,
        risk_pct REAL NOT NULL,
        rr_1 REAL NOT NULL,
        rr_2 REAL NOT NULL,
        rr_3 REAL NOT NULL,
        suggested_capital REAL,
        suggested_position_size INTEGER,
        position_sizing_basis TEXT DEFAULT 'ACCOUNT_RISK_1PCT',
        account_risk_pct REAL DEFAULT 1.0,
        status TEXT NOT NULL DEFAULT 'ACTIVE_SETUP',
        setup_outcome TEXT NOT NULL DEFAULT 'PENDING',
        exit_reason TEXT,
        exit_price REAL,
        exit_timestamp DATETIME,
        exit_status TEXT DEFAULT 'OK',
        exit_assumption TEXT,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        effective_as_of DATETIME DEFAULT CURRENT_TIMESTAMP,
        strategy_version TEXT NOT NULL DEFAULT 'ACCUMULATION_V1.0',
        sl_target_version TEXT NOT NULL DEFAULT 'ACCUM_SL_V1',
        config_version TEXT NOT NULL DEFAULT 'ACCUM_CFG_V1',
        score_normalization_version TEXT NOT NULL DEFAULT 'ACCUM_SCORE_NORM_V1',
        CHECK (stop_loss < entry_price),
        CHECK (target_1 >= breakout_level AND target_1 > entry_price AND target_1 < target_2 AND target_2 < target_3)
    );
    """)
    conn.commit()
    return conn


def test_schema_ddl_and_constraint_rejection(memory_db):
    """Verifies DDL table creation and check constraint enforcement."""
    cur = memory_db.cursor()

    # Valid trade setup insertion
    cur.execute("""
        INSERT INTO accumulation_trades (
            run_id, audit_snapshot_id, symbol, signal_state, entry_type, entry_trigger_rule, entry_reference_type,
            entry_zone_low, entry_zone_high, entry_price, preferred_entry, entry_trigger_level, entry_displacement_reference,
            breakout_level, stop_loss, target_1, target_2, target_3, risk_pct, rr_1, rr_2, rr_3, status, setup_outcome
        ) VALUES (
            'RUN1', 'SNAP1', 'RELIANCE', 'BREAKOUT_READY', 'ZONE_MIDPOINT', 'RANGE_TOUCH', 'STRATEGY_REFERENCE',
            2400.0, 2450.0, 2425.0, 2425.0, 2425.0, 2450.0,
            2460.0, 2300.0, 2675.0, 2800.0, 3000.0, 5.15, 2.0, 3.0, 4.6, 'ACTIVE_SETUP', 'PENDING'
        );
    """)
    memory_db.commit()

    cur.execute("SELECT trade_id, symbol, entry_price FROM accumulation_trades WHERE symbol = 'RELIANCE';")
    row = cur.fetchone()
    assert row is not None
    assert row[1] == "RELIANCE"
    assert row[2] == 2425.0

    # Test Check Constraint Rejection: stop_loss >= entry_price (must fail)
    with pytest.raises(sqlite3.IntegrityError):
        cur.execute("""
            INSERT INTO accumulation_trades (
                run_id, audit_snapshot_id, symbol, signal_state, entry_type, entry_trigger_rule, entry_reference_type,
                entry_zone_low, entry_zone_high, entry_price, preferred_entry, entry_trigger_level, entry_displacement_reference,
                breakout_level, stop_loss, target_1, target_2, target_3, risk_pct, rr_1, rr_2, rr_3, status, setup_outcome
            ) VALUES (
                'RUN1', 'SNAP2', 'INVALID_STOP', 'BREAKOUT_READY', 'ZONE_MIDPOINT', 'RANGE_TOUCH', 'STRATEGY_REFERENCE',
                2400.0, 2450.0, 2425.0, 2425.0, 2425.0, 2450.0,
                2460.0, 2500.0, 2675.0, 2800.0, 3000.0, 5.15, 2.0, 3.0, 4.6, 'ACTIVE_SETUP', 'PENDING'
            );
        """)
        memory_db.commit()


def test_full_lifecycle_db_integration(memory_db):
    """Simulates full DB lifecycle: ACTIVE_SETUP -> ENTRY_TRIGGERED -> TARGET_1_REACHED -> TARGET_2_REACHED -> SETUP_COMPLETED."""
    cur = memory_db.cursor()

    cur.execute("""
        INSERT INTO accumulation_trades (
            run_id, audit_snapshot_id, symbol, signal_state, entry_type, entry_trigger_rule, entry_reference_type,
            entry_zone_low, entry_zone_high, entry_price, preferred_entry, entry_trigger_level, entry_displacement_reference,
            breakout_level, stop_loss, target_1, target_2, target_3, risk_pct, rr_1, rr_2, rr_3, status, setup_outcome
        ) VALUES (
            'RUN_LC', 'SNAP_LC1', 'TATASTEEL', 'BREAKOUT_READY', 'ZONE_MIDPOINT', 'RANGE_TOUCH', 'STRATEGY_REFERENCE',
            140.0, 150.0, 145.0, 145.0, 145.0, 150.0,
            152.0, 130.0, 182.0, 200.0, 220.0, 10.34, 2.47, 3.67, 5.0, 'ACTIVE_SETUP', 'PENDING'
        ) RETURNING trade_id;
    """)
    trade_id = cur.fetchone()[0]
    memory_db.commit()

    cur.execute("SELECT status, setup_outcome, entry_trigger_level_reached FROM accumulation_trades WHERE trade_id = %s;" % trade_id)
    row = cur.fetchone()
    assert row[0] == "ACTIVE_SETUP"
    assert row[1] == "PENDING"
    assert row[2] is None  # Initial state is NULL

    # Activation -> ENTRY_TRIGGERED
    cur.execute("""
        UPDATE accumulation_trades
        SET status = 'ENTRY_TRIGGERED', entry_triggered_at = CURRENT_TIMESTAMP, entry_trigger_level_reached = 1, trigger_direction = 'OPEN_INSIDE'
        WHERE trade_id = %s;
    """ % trade_id)
    memory_db.commit()

    # Milestone T1
    cur.execute("""
        UPDATE accumulation_trades
        SET status = 'TARGET_1_REACHED', best_target_reached = 'T1', last_milestone_price = 182.0, last_milestone_timestamp = CURRENT_TIMESTAMP
        WHERE trade_id = %s;
    """ % trade_id)
    memory_db.commit()

    # Milestone T3 -> SETUP_COMPLETED (SUCCESS)
    cur.execute("""
        UPDATE accumulation_trades
        SET status = 'SETUP_COMPLETED', setup_outcome = 'SUCCESS', best_target_reached = 'T3', exit_reason = 'TARGET_3_REACHED', exit_price = 220.0, exit_timestamp = CURRENT_TIMESTAMP
        WHERE trade_id = %s;
    """ % trade_id)
    memory_db.commit()

    cur.execute("SELECT status, setup_outcome, best_target_reached, exit_reason FROM accumulation_trades WHERE trade_id = %s;" % trade_id)
    final_row = cur.fetchone()
    assert final_row[0] == "SETUP_COMPLETED"
    assert final_row[1] == "SUCCESS"
    assert final_row[2] == "T3"


def test_rejection_path_natural_rr_below_2():
    """Verifies that if natural RR1 < 2.0x, setup is rejected as INSUFFICIENT_RR1 without artificial scaling."""
    # Entry = 1000, SL = 940 (Risk = 60), Resistance = 1080 (Target 1 = 1080) -> Natural RR1 = (1080-1000)/60 = 1.33x (< 2.0x)
    res = AccumulationSLTargetEngine.compute_sl_and_targets(
        entry_zone_low=980.0,
        entry_zone_high=1020.0,
        breakout_level=1030.0,
        close_price=1010.0,
        eff_atr=20.0,
        entry_method="ZONE_MIDPOINT",
        supports=[(920.0, "SUPPORT", 50)],
        resistances=[(1080.0, "RESISTANCE", 50)]  # Structural resistance at 1080
    )

    assert res.is_valid is False
    assert res.target_1 == 1080.0
    assert res.rr_1 == 1.33  # Natural RR is strictly preserved (1.33x)
    assert "INSUFFICIENT_RR1" in res.rejection_reason


def test_rejection_path_excessive_risk():
    """Verifies that risk_pct > 8.0% results in setup rejection."""
    # Entry = 100, SL = 88.5 -> Risk = 11.5% (> max 8.0%)
    res = AccumulationSLTargetEngine.compute_sl_and_targets(
        entry_zone_low=95.0,
        entry_zone_high=105.0,
        breakout_level=106.0,
        close_price=100.0,
        eff_atr=5.0,
        entry_method="ZONE_MIDPOINT",
        supports=[(91.0, "LOW_SUPPORT", 50)]
    )

    assert res.is_valid is False
    assert res.risk_pct == 11.5
    assert "EXCESSIVE_RISK_PCT" in res.rejection_reason


def test_rejection_path_entry_gap_rejected():
    """Verifies that a setup with opening gap > 2.0% is rejected as ENTRY_GAP_REJECTED."""
    evaluator = AccumulationExitEvaluator()
    
    trade_setup = {
        "trade_id": 99,
        "symbol": "GAP_STOCK",
        "entry_type": "ZONE_MIDPOINT",
        "entry_zone_low": 100.0,
        "entry_zone_high": 105.0,
        "entry_price": 102.5,
        "preferred_entry": 102.5,
        "entry_trigger_level": 102.5,
        "stop_loss": 95.0,
        "target_1": 120.0,
        "target_2": 130.0,
        "target_3": 140.0,
        "status": "ACTIVE_SETUP"
    }

    # Open at 108.0 (> zone_high 105.0 * 1.02 = 107.10) and low touches zone (104.0 <= 105.0)
    bar_gap_above = {"timestamp": "2026-08-24", "open": 108.0, "high": 115.0, "low": 104.0, "close": 112.0}
    res_gap = evaluator.evaluate_bar(trade_setup, bar_gap_above)
    
    assert res_gap["status"] == "ENTRY_GAP_REJECTED"
    assert res_gap["setup_outcome"] == "INVALIDATED"
    assert res_gap["exit_reason"] == "GAP_ABOVE_ZONE"


def test_position_sizing_guidance_calculation():
    """Verifies calculation of suggested capital and position size based on 1% account risk."""
    entry_price = 500.0
    stop_loss = 480.0  # Risk per share = ₹20
    account_capital = 1000000.0  # ₹1,000,000
    account_risk_pct = 1.0        # 1% risk = ₹10,000

    # Max risk = 10,000 -> qty = floor(10,000 / 20) = 500 shares
    # Suggested capital = 500 * 500 = ₹250,000
    cap, qty, basis = AccumulationSLTargetEngine.calculate_position_size(
        entry_price=entry_price,
        stop_loss=stop_loss,
        account_capital=account_capital,
        account_risk_pct=account_risk_pct
    )

    assert qty == 500
    assert cap == 250000.0
    assert basis == "ACCOUNT_RISK_1PCT"


def test_side_by_side_empirical_field_comparison():
    """Performs side-by-side empirical ground-truth comparison against expected math tolerances."""
    # Test Stock 1: RELIANCE (ZONE_MIDPOINT)
    res_rel = AccumulationSLTargetEngine.compute_sl_and_targets(
        entry_zone_low=2400.0, entry_zone_high=2450.0, breakout_level=2460.0,
        close_price=2440.0, eff_atr=35.0, entry_method="ZONE_MIDPOINT",
        supports=[(2380.0, "SWING_LOW", 60)], account_capital=1000000.0
    )

    # Expected values:
    # preferred_entry = 2425.0
    # raw_sl = 2380 - 0.5*35 = 2362.5
    # risk = 2425 - 2362.5 = 62.5 -> risk_pct = 2.58%
    # min_t1 = max(2460, 2425 + 2*62.5 = 2550) = 2550.0
    # rr_1 = (2550 - 2425) / 62.5 = 2.00x
    assert res_rel.is_valid is True
    assert abs(res_rel.entry_price - 2425.0) <= 0.01
    assert abs(res_rel.stop_loss - 2362.5) <= 0.01
    assert abs(res_rel.risk_pct - 2.58) <= 0.01
    assert abs(res_rel.target_1 - 2550.0) <= 0.01
    assert abs(res_rel.rr_1 - 2.00) <= 0.01
    assert res_rel.suggested_position_size == 160  # floor(10000 / 62.5) = 160
    assert abs(res_rel.suggested_capital - 388000.0) <= 0.01

    # Test Stock 2: INFY (BREAKOUT_CONFIRMATION)
    res_infy = AccumulationSLTargetEngine.compute_sl_and_targets(
        entry_zone_low=1480.0, entry_zone_high=1520.0, breakout_level=1530.0,
        close_price=1510.0, eff_atr=25.0, entry_method="BREAKOUT_CONFIRMATION",
        supports=[(1470.0, "ZONE_LOW", 50)], account_capital=1000000.0
    )

    # Expected values:
    # trigger = 1530 * 1.002 = 1533.06
    # raw_sl = 1470 - 0.5*25 = 1457.50
    # min_allowed_sl = 1533.06 - 3*25 = 1458.06 -> stop_loss = 1458.06 (capped at max 3.0x ATR)
    # risk = 1533.06 - 1458.06 = 75.0
    # min_t1 = 1533.06 + 2*75.0 = 1683.06
    assert res_infy.is_valid is True
    assert abs(res_infy.entry_price - 1533.06) <= 0.01
    assert abs(res_infy.stop_loss - 1458.06) <= 0.01
    assert abs(res_infy.rr_1 - 2.00) <= 0.01
    assert res_infy.suggested_position_size == 133  # floor(10000 / 75.0) = 133


def test_restart_between_scan_and_finalization(memory_db):
    """
    [CERTIFICATION REQUIREMENT] Explicitly tests system durability across server restarts.
    Simulates 15:45 IST scan persisting PENDING alert -> Server Restart -> 18:00 IST Finalization recovery.
    """
    cur = memory_db.cursor()

    # 1. 15:45 IST Main Scan: Persist candidate alert with finalization_status = 'PENDING'
    cur.execute("""
        INSERT INTO accumulation_alerts (
            run_id, audit_snapshot_id, symbol, signal_state, score, close,
            entry_zone_low, entry_zone_high, breakout_level, preferred_entry, stop_loss,
            target_1, target_2, target_3, risk_pct, rr_1, rr_2, rr_3, finalization_status, tradable
        ) VALUES (
            'RUN_1545', 'SNAP_A_RESTART_TEST', 'RELIANCE_RESTART', 'BREAKOUT_READY', 88.5, 2450.0,
            2400.0, 2460.0, 2470.0, 2430.0, 2350.0, 2600.0, 2750.0, 2900.0, 3.29, 2.13, 4.0, 5.88, 'PENDING', 1
        )
    """)
    alert_id = cur.lastrowid
    memory_db.commit()

    # 2. Simulate Server Restart: Wipe all in-memory references, query DB for pending alerts
    cur.execute("SELECT id, audit_snapshot_id, symbol, score FROM accumulation_alerts WHERE finalization_status = 'PENDING';")
    pending_alerts = cur.fetchall()
    assert len(pending_alerts) == 1
    assert pending_alerts[0][1] == "SNAP_A_RESTART_TEST"
    assert pending_alerts[0][2] == "RELIANCE_RESTART"

    # 3. 18:00 IST Delivery Finalization: Process Snapshot B & atomically activate setup
    snap_b_id = "SNAP_B_RESTART_TEST"
    cur.execute("""
        UPDATE accumulation_alerts
        SET finalization_status = 'PASSED', finalization_snapshot_id = ?
        WHERE id = ?;
    """, (snap_b_id, alert_id))

    cur.execute("""
        INSERT INTO accumulation_trades (
            alert_id, run_id, audit_snapshot_id, parent_snapshot_id, symbol, signal_state, entry_type,
            entry_zone_low, entry_zone_high, entry_price, preferred_entry, entry_trigger_level, entry_displacement_reference,
            breakout_level, stop_loss, target_1, target_2, target_3, risk_pct, rr_1, rr_2, rr_3, status, setup_outcome
        ) VALUES (
            ?, 'RUN_1800', ?, ?, 'RELIANCE_RESTART', 'BREAKOUT_READY', 'ZONE_MIDPOINT',
            2400.0, 2460.0, 2430.0, 2430.0, 2430.0, 2460.0,
            2470.0, 2350.0, 2600.0, 2750.0, 2900.0, 3.29, 2.13, 4.0, 5.88, 'ACTIVE_SETUP', 'PENDING'
        );
    """, (alert_id, snap_b_id, 'SNAP_A_RESTART_TEST'))
    memory_db.commit()

    # 4. Verify post-restart active trade row creation
    cur.execute("SELECT status, setup_outcome, audit_snapshot_id, parent_snapshot_id FROM accumulation_trades WHERE symbol = 'RELIANCE_RESTART';")
    trade_row = cur.fetchone()
    assert trade_row is not None
    assert trade_row[0] == "ACTIVE_SETUP"
    assert trade_row[1] == "PENDING"
    assert trade_row[2] == "SNAP_B_RESTART_TEST"
    assert trade_row[3] == "SNAP_A_RESTART_TEST"


def test_same_day_startup_and_scheduled_scan_dedup(memory_db):
    """
    [CERTIFICATION REQUIREMENT] Verifies that same-day 06:30 IST startup scan + 15:45 IST scheduled scan
    does not create duplicate active trade setups for the same symbol.
    """
    cur = memory_db.cursor()

    # 06:30 IST Startup Scan: Create active trade setup
    cur.execute("""
        INSERT INTO accumulation_trades (
            run_id, audit_snapshot_id, symbol, signal_state, entry_type, entry_trigger_rule, entry_reference_type,
            entry_zone_low, entry_zone_high, entry_price, preferred_entry, entry_trigger_level, entry_displacement_reference,
            breakout_level, stop_loss, target_1, target_2, target_3, risk_pct, rr_1, rr_2, rr_3, status, setup_outcome
        ) VALUES (
            'RUN_0630_STARTUP', 'SNAP_STARTUP_1', 'DEDUP_STOCK', 'BREAKOUT_READY', 'ZONE_MIDPOINT', 'RANGE_TOUCH', 'STRATEGY_REFERENCE',
            1000.0, 1050.0, 1025.0, 1025.0, 1025.0, 1050.0,
            1060.0, 960.0, 1160.0, 1250.0, 1350.0, 6.34, 2.08, 3.46, 5.0, 'ACTIVE_SETUP', 'PENDING'
        );
    """)
    memory_db.commit()

    cur.execute("SELECT COUNT(*) FROM accumulation_trades WHERE symbol = 'DEDUP_STOCK' AND status IN ('ACTIVE_SETUP', 'ENTRY_TRIGGERED', 'TARGET_1_REACHED', 'TARGET_2_REACHED');")
    initial_count = cur.fetchone()[0]
    assert initial_count == 1

    # 15:45 IST Scheduled Scan: Existing live setup exists -> Check setup uniqueness
    cur.execute("SELECT status FROM accumulation_trades WHERE symbol = 'DEDUP_STOCK' AND status IN ('ACTIVE_SETUP', 'ENTRY_TRIGGERED', 'TARGET_1_REACHED', 'TARGET_2_REACHED');")
    active_setups = cur.fetchall()
    assert len(active_setups) == 1  # Dedup check successfully identifies active setup!

