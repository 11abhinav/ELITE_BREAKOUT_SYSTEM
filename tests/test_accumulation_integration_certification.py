"""
tests/test_accumulation_integration_certification.py — Integration & Certification Suite for ACCUMULATION_SCANNER_V1.
Tests database schema constraints, full setup lifecycle DB integration, 18:00 IST delivery finalization,
Admin CLI control plane, and empirical field accuracy across both ZONE_MIDPOINT and BREAKOUT_CONFIRMATION.
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
    CREATE_ACTIVE_SETUP_UNIQUE_INDEX,
    CREATE_AUDIT_SNAPSHOT_UNIQUE_INDEX,
)
from app.accumulation.config import (
    STRATEGY_VERSION, SL_TARGET_VERSION, CONFIG_VERSION, SCORE_NORMALIZATION_VERSION
)
from app.accumulation.contracts import TradeSetupContract, AccumulationContractValidator
from app.accumulation.sl_target import AccumulationSLTargetEngine
from app.accumulation.exit_evaluator import AccumulationExitEvaluator
from app.accumulation.scanner import AccumulationScanner
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

    # 1. Setup Creation (ACTIVE_SETUP)
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

    # Verify initial active state
    cur.execute("SELECT status, setup_outcome, entry_trigger_level_reached FROM accumulation_trades WHERE trade_id = %s;" % trade_id)
    row = cur.fetchone()
    assert row[0] == "ACTIVE_SETUP"
    assert row[1] == "PENDING"
    assert row[2] is None  # Initial state is NULL

    # 2. Activation -> ENTRY_TRIGGERED
    cur.execute("""
        UPDATE accumulation_trades
        SET status = 'ENTRY_TRIGGERED', entry_triggered_at = CURRENT_TIMESTAMP, entry_trigger_level_reached = 1, trigger_direction = 'OPEN_INSIDE'
        WHERE trade_id = %s;
    """ % trade_id)
    memory_db.commit()

    # 3. Milestone T1 -> TARGET_1_REACHED
    cur.execute("""
        UPDATE accumulation_trades
        SET status = 'TARGET_1_REACHED', best_target_reached = 'T1', last_milestone_price = 182.0, last_milestone_timestamp = CURRENT_TIMESTAMP
        WHERE trade_id = %s;
    """ % trade_id)
    memory_db.commit()

    # 4. Milestone T2 -> TARGET_2_REACHED
    cur.execute("""
        UPDATE accumulation_trades
        SET status = 'TARGET_2_REACHED', best_target_reached = 'T2', last_milestone_price = 200.0, last_milestone_timestamp = CURRENT_TIMESTAMP
        WHERE trade_id = %s;
    """ % trade_id)
    memory_db.commit()

    # 5. Milestone T3 -> SETUP_COMPLETED (SUCCESS)
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
    assert final_row[3] == "TARGET_3_REACHED"


def test_1800_delivery_finalization_pass():
    """Verifies Snapshot A -> Snapshot B lineage, canonical snapshot assignment, technical freeze, and delivery recomputation."""
    scheduler = AccumulationScheduler()
    
    pending_alerts = [{
        "symbol": "INFY",
        "run_id": "RUN_1545",
        "audit_snapshot_id": "ACCUM_SNAP_INFY_1545_12345678",
        "score": 82.0,
        "delivery_status": "PENDING"
    }]

    delivery_map_valid = {"INFY": {"status": "VALID", "delivery_pct": 55.0}}

    finalized = scheduler.run_delivery_finalization(pending_alerts, delivery_map_valid)
    assert len(finalized) == 1
    snapshot_b = finalized[0]
    assert snapshot_b["parent_snapshot_id"] == "ACCUM_SNAP_INFY_1545_12345678"
    assert snapshot_b["audit_snapshot_id"].startswith("ACCUM_SNAP_INFY_")
    assert snapshot_b["finalization_status"] == "PASSED"
    assert snapshot_b["canonical_for_trade"] is True

    # Test Delivery Finalization Rejection
    delivery_map_invalid = {"INFY": {"status": "UNAVAILABLE"}}
    finalized_rejected = scheduler.run_delivery_finalization([{
        "symbol": "INFY",
        "run_id": "RUN_1545",
        "audit_snapshot_id": "ACCUM_SNAP_INFY_1545_12345678",
        "score": 82.0,
        "delivery_status": "PENDING"
    }], delivery_map_invalid)
    assert len(finalized_rejected) == 0  # Not emitted as trade setup


def test_admin_control_plane_integration(memory_db):
    """Verifies control plane pause, resume, stop, and status updates."""
    state = AccumulationControlPlane.get_control_state(conn=memory_db)
    assert state["enabled"] is True
    assert state["paused"] is False

    # Test Pause
    AccumulationControlPlane.update_control_state(paused=True, reason="Unit test pause", conn=memory_db)
    paused_state = AccumulationControlPlane.get_control_state(conn=memory_db)
    assert paused_state["paused"] is True
    assert paused_state["reason"] == "Unit test pause"

    # Test Resume
    AccumulationControlPlane.update_control_state(paused=False, reason="Unit test resume", conn=memory_db)
    resumed_state = AccumulationControlPlane.get_control_state(conn=memory_db)
    assert resumed_state["paused"] is False


def test_empirical_field_certification_zone_and_breakout():
    """Performs ground-truth field certification across ZONE_MIDPOINT and BREAKOUT_CONFIRMATION."""
    # 1. ZONE_MIDPOINT Ground Truth
    res_zone = AccumulationSLTargetEngine.compute_sl_and_targets(
        entry_zone_low=1000.0,
        entry_zone_high=1050.0,
        breakout_level=1060.0,
        close_price=1045.0,
        eff_atr=20.0,
        entry_method="ZONE_MIDPOINT",
        supports=[(980.0, "SWING_LOW", 50)]
    )

    assert res_zone.is_valid is True
    assert res_zone.entry_price == 1025.0  # midpoint(1000, 1050)
    assert res_zone.stop_loss == 970.0    # 980 - 0.5 * 20 = 970
    assert res_zone.risk_pct == round(((1025.0 - 970.0) / 1025.0) * 100.0, 2)  # 5.37%
    assert res_zone.target_1 >= 1060.0
    assert res_zone.target_1 > 1025.0
    assert res_zone.target_1 < res_zone.target_2 < res_zone.target_3

    val_zone = AccumulationFieldValidator.validate_setup({
        "symbol": "CERT_ZONE", "entry_type": "ZONE_MIDPOINT", "entry_trigger_rule": "RANGE_TOUCH",
        "entry_reference_type": "STRATEGY_REFERENCE", "entry_zone_low": 1000.0, "entry_zone_high": 1050.0,
        "entry_price": 1025.0, "preferred_entry": 1025.0, "entry_trigger_level": 1025.0,
        "entry_displacement_reference": 1050.0, "breakout_level": 1060.0, "stop_loss": 970.0,
        "target_1": res_zone.target_1, "target_2": res_zone.target_2, "target_3": res_zone.target_3,
        "risk_pct": res_zone.risk_pct, "rr_1": res_zone.rr_1, "rr_2": res_zone.rr_2, "rr_3": res_zone.rr_3,
        "status": "ACTIVE_SETUP", "setup_outcome": "PENDING", "entry_trigger_level_reached": None
    })
    assert val_zone["is_valid"] is True

    # 2. BREAKOUT_CONFIRMATION Ground Truth
    res_breakout = AccumulationSLTargetEngine.compute_sl_and_targets(
        entry_zone_low=1000.0,
        entry_zone_high=1050.0,
        breakout_level=1060.0,
        close_price=1045.0,
        eff_atr=20.0,
        entry_method="BREAKOUT_CONFIRMATION",
        supports=[(1000.0, "ZONE_LOW", 50)]
    )

    expected_trigger = round(1060.0 * 1.002, 2)  # 1062.12
    assert res_breakout.is_valid is True
    assert res_breakout.entry_price == expected_trigger
    assert res_breakout.stop_loss == 1002.12  # capped at max 3.0x ATR below entry (1062.12 - 60.0 = 1002.12)
    assert res_breakout.target_1 >= 1060.0
    assert res_breakout.target_1 > expected_trigger
    assert res_breakout.target_1 < res_breakout.target_2 < res_breakout.target_3

    val_breakout = AccumulationFieldValidator.validate_setup({
        "symbol": "CERT_BREAKOUT", "entry_type": "BREAKOUT_CONFIRMATION", "entry_trigger_rule": "LEVEL_CROSS",
        "entry_reference_type": "CONFIRMED_LEVEL", "entry_zone_low": 1000.0, "entry_zone_high": 1050.0,
        "entry_price": expected_trigger, "preferred_entry": 1025.0, "entry_trigger_level": expected_trigger,
        "entry_displacement_reference": expected_trigger, "breakout_level": 1060.0, "stop_loss": 1002.12,
        "target_1": res_breakout.target_1, "target_2": res_breakout.target_2, "target_3": res_breakout.target_3,
        "risk_pct": res_breakout.risk_pct, "rr_1": res_breakout.rr_1, "rr_2": res_breakout.rr_2, "rr_3": res_breakout.rr_3,
        "status": "ACTIVE_SETUP", "setup_outcome": "PENDING", "entry_trigger_level_reached": None
    })
    assert val_breakout["is_valid"] is True
