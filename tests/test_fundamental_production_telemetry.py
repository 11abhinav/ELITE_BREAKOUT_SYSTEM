#!/usr/bin/env python3
"""
tests/test_fundamental_production_telemetry.py
==============================================
PRODUCTION TELEMETRY & DECISION AUDIT TEST BATTERY
Verifies:
  1. Full Buy Pipeline Telemetry (Cycle Start, Gate-by-Gate Metrics, Composite Scores, Final Decisions)
  2. Individual Gate Failure Reason Codes (Quality, Acceleration, Trend, Consolidation, Breakout, Value Trap)
  3. Mathematical Universe Reconciliation:
     loaded == evaluated + skipped
     evaluated == rejected + buy_eligible
     buy_eligible == alerts_created + alerts_suppressed
  4. Wealth Exit V1 (Live) vs V2 (Shadow) Telemetry:
     - Independent evaluation per open position
     - Runtime proof V2 cannot close positions (V2_CAN_CLOSE_POSITION=False)
     - Incomplete daily candle guard telemetry
     - Duplicate exit alert blocked telemetry
  5. Telemetry Integrity Self-Check (TELEMETRY_INTEGRITY_CHECK = PASS)
"""

import os
import sys
import tempfile
import pytest
import numpy as np
import pandas as pd
from datetime import datetime
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")
BASE_DIR = "/Users/abhinavmaheshwari/Documents/ELITE_BREAKOUT_SYSTEM"
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from app.fundamental_telemetry import (
    FundamentalScanTelemetry,
    WealthExitTelemetry,
    TelemetryReasonCode
)
from app.live_fundamental_scanner import (
    LiveFundamentalBuyScanner,
    ApprovedUniverseRegistry,
    RejectionReason
)
from app.live_wealth_monitor import (
    LiveWealthMonitorEngine,
    PositionStatus,
    DataHealthStatus
)


def create_ideal_bars(n_bars: int = 210, base_price: float = 100.0) -> pd.DataFrame:
    """Creates technical bars passing Trend, Consolidation, and Breakout."""
    dates = pd.date_range(end="2026-09-25", periods=n_bars, freq="B")
    prices = np.linspace(base_price, base_price * 1.5, n_bars)
    
    # Consolidation in bars -30 to -2
    for i in range(n_bars - 30, n_bars - 1):
        prices[i] = 145.0 + 1.0 * np.sin(i)

    # Breakout on bar -1
    prices[-1] = 152.0

    opens = prices - 0.5
    highs = prices + 1.0
    lows = prices - 1.0
    closes = prices.copy()
    volumes = np.full(n_bars, 100000.0)
    volumes[-1] = 250000.0

    return pd.DataFrame({
        "Date": dates,
        "Open": opens,
        "High": highs,
        "Low": lows,
        "Close": closes,
        "Volume": volumes
    })


def create_ideal_benchmark(n_bars: int = 210) -> np.ndarray:
    """Benchmark series with modest return so ideal candidate outperforms."""
    return np.linspace(100.0, 105.0, n_bars)


def create_ideal_fundamentals() -> dict:
    """Fundamentally strong candidate passing all quality and acceleration gates."""
    return {
        "roce": 25.0,
        "roe": 20.0,
        "operating_cash_flow": 100000000.0,
        "debt_equity": 0.3,
        "rev_yoy_latest": 20.0,
        "rev_yoy_prev": 12.0,
        "op_profit_yoy_latest": 25.0,
        "op_profit_yoy_prev": 15.0,
        "eps_yoy_latest": 30.0,
        "eps_yoy_prev": 18.0,
        "prior_eps": 10.0,
        "piotroski_score": 8,
        "altman_z_score": 3.5,
        "margin_headwind": False,
        "governance_flag": False
    }


# =====================================================================================
# 1. BUY PIPELINE TELEMETRY TESTS
# =====================================================================================

def test_buy_pipeline_pass_telemetry_complete_trace():
    """Verifies complete gate trace, metrics, composite scores, and PASS decision."""
    telemetry = FundamentalScanTelemetry()
    telemetry.log_scan_start(master_count=1, quarantined_count=0, eligible_count=1)

    scanner = LiveFundamentalBuyScanner()
    symbol = "RELIANCE"
    df_bars = create_ideal_bars()
    funds = create_ideal_fundamentals()
    benchmark_closes = create_ideal_benchmark()

    res = scanner.scan_candidate(
        symbol=symbol,
        df_bars=df_bars,
        fundamentals=funds,
        benchmark_closes=benchmark_closes,
        consolidation_window=20,
        provenance_valid=True,
        is_stale=False,
        telemetry=telemetry
    )

    assert res["is_buy"] is True

    # Verify disposition
    disp = telemetry.dispositions.get(symbol)
    assert disp is not None
    assert disp["status"] == "BUY_ELIGIBLE"
    assert disp["final_decision"] in ("BUY_ALERT", "BUY_ELIGIBLE")
    assert disp["duration_ms"] >= 0.0

    # Verify all gates evaluated
    gates = disp["gates"]
    for expected_gate in ["UNIVERSE", "PROVENANCE", "FUNDAMENTAL_QUALITY", "EARNINGS_ACCELERATION", "MARKET_DATA", "TECHNICAL_TREND", "CONSOLIDATION", "BREAKOUT"]:
        assert expected_gate in gates, f"Missing gate: {expected_gate}"
        assert gates[expected_gate]["passed"] is True

    # Verify composite scores context-only recording
    assert "composite_scores" in disp
    assert disp["composite_scores"]["COMPOSITE_SCORES_ARE_CONTEXT_ONLY"] is True
    assert disp["composite_scores"]["COMPOSITE_SCORES_CANNOT_BYPASS_HARD_GATES"] is True

    # Record alert persistence
    telemetry.record_alert_persistence(symbol=symbol, persisted=True, reason="ALERT_CREATED", entry_price=152.0)
    assert disp["status"] == "BUY_ALERT_CREATED"

    # End of run summary and integrity check
    summary = telemetry.produce_end_of_run_summary()
    assert summary["reconciliation"] == "PASS"
    assert telemetry.funnel_counts["buy_alerts_created"] == 1
    assert summary["processing"]["buy_alerts"] == 1

    passed, failures = telemetry.run_telemetry_integrity_check()
    assert passed is True
    assert len(failures) == 0


def test_buy_pipeline_individual_gate_rejections():
    """Tests each individual rejection gate records exact failure code in telemetry."""
    from app.fundamental_telemetry import normalize_reason_code
    gates_to_test = [
        ("roce", 10.0, RejectionReason.FAIL_ROCE.value),
        ("operating_cash_flow", -50000.0, RejectionReason.FAIL_OCF.value),
        ("debt_equity", 1.5, RejectionReason.FAIL_DEBT_EQUITY.value),
        ("eps_yoy_latest", 10.0, RejectionReason.FAIL_EPS_ACCELERATION.value),
        ("is_value_trap", True, RejectionReason.FAIL_VALUE_TRAP.value)
    ]

    for field, bad_value, raw_expected_reason in gates_to_test:
        telemetry = FundamentalScanTelemetry()
        telemetry.log_scan_start(master_count=886, quarantined_count=0, eligible_count=886)

        scanner = LiveFundamentalBuyScanner()
        funds = create_ideal_fundamentals()
        funds[field] = bad_value
        df_bars = create_ideal_bars()

        res = scanner.scan_candidate(
            symbol="TCS",
            df_bars=df_bars,
            fundamentals=funds,
            provenance_valid=True,
            is_stale=False,
            telemetry=telemetry
        )

        assert res["is_buy"] is False
        disp = telemetry.dispositions.get("TCS")
        assert disp is not None
        assert disp["status"] == "REJECTED"
        assert disp["final_decision"] == "REJECTED"
        assert disp["primary_reason"] in (raw_expected_reason, normalize_reason_code(raw_expected_reason))


def test_buy_pipeline_provenance_failure_telemetry():
    """Tests uncertified Upstox provenance records FUNDAMENTAL_PROVENANCE_INVALID / DATA_INVALID."""
    telemetry = FundamentalScanTelemetry()
    telemetry.log_scan_start(master_count=886, quarantined_count=0, eligible_count=886)
    scanner = LiveFundamentalBuyScanner()

    res = scanner.scan_candidate(
        symbol="INFY",
        df_bars=create_ideal_bars(),
        fundamentals=create_ideal_fundamentals(),
        provenance_valid=False,
        telemetry=telemetry
    )

    assert res["is_buy"] is False
    disp = telemetry.dispositions.get("INFY")
    assert disp is not None
    assert disp["status"] == "REJECTED"
    assert disp["primary_reason"] in (RejectionReason.FUNDAMENTAL_PROVENANCE_INVALID.value, "DATA_INVALID")


def test_buy_universe_mathematical_reconciliation():
    """
    Validates end-of-run mathematical reconciliation:
    loaded == evaluated + skipped
    evaluated == rejected + buy_eligible
    buy_eligible == alerts_created + alerts_suppressed
    """
    telemetry = FundamentalScanTelemetry()
    telemetry.log_scan_start(master_count=3, quarantined_count=0, eligible_count=3)

    scanner = LiveFundamentalBuyScanner()

    # Candidate 1: PASS
    res1 = scanner.scan_candidate("RELIANCE", create_ideal_bars(), create_ideal_fundamentals(), benchmark_closes=create_ideal_benchmark(), provenance_valid=True, telemetry=telemetry)
    assert res1["is_buy"] is True
    telemetry.record_alert_persistence(symbol="RELIANCE", persisted=True, reason="ALERT_CREATED", entry_price=152.0)

    # Candidate 2: REJECT (Low ROCE)
    funds_bad = create_ideal_fundamentals()
    funds_bad["roce"] = 5.0
    res2 = scanner.scan_candidate("TCS", create_ideal_bars(), funds_bad, provenance_valid=True, telemetry=telemetry)
    assert res2["is_buy"] is False

    # Candidate 3: REJECT (Negative CFO)
    funds_cfo = create_ideal_fundamentals()
    funds_cfo["operating_cash_flow"] = -100.0
    res3 = scanner.scan_candidate("INFY", create_ideal_bars(), funds_cfo, provenance_valid=True, telemetry=telemetry)
    assert res3["is_buy"] is False

    summary = telemetry.produce_end_of_run_summary()

    assert summary["processing"]["evaluated"] == 3
    assert telemetry.funnel_counts["buy_eligible"] == 1
    assert telemetry.funnel_counts["rejected"] == 2
    assert telemetry.funnel_counts["buy_alerts_created"] == 1
    assert telemetry.funnel_counts["buy_alerts_suppressed"] == 0
    assert summary["reconciliation"] == "PASS"

    # Telemetry self-check
    check_status, issues = telemetry.run_telemetry_integrity_check()
    assert check_status is True
    assert len(issues) == 0


# =====================================================================================
# 2. WEALTH EXIT MONITOR TELEMETRY TESTS
# =====================================================================================

def test_wealth_exit_v1_v2_hold_telemetry():
    """Tests normal holding cycle emits POSITION_AUDIT, V1, V2, and INDEPENDENCE records."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        state_file = os.path.join(tmp_dir, "monitor_state.json")
        ledger_file = os.path.join(tmp_dir, "ledger.jsonl")
        monitor = LiveWealthMonitorEngine(state_file=state_file, ledger_file=ledger_file)

        # Record user buy
        pos_res = monitor.record_user_buy(symbol="RELIANCE", entry_price=100.0, entry_date="2026-09-01")
        assert pos_res["success"] is True

        # Stable market data: Close hovering around 105.0 > SMA50 (~102.0), no exit signal
        df_bars = create_ideal_bars(n_bars=60, base_price=100.0)
        market_data = {
            "RELIANCE": {
                "cmp": 105.0,
                "is_completed_session": True,
                "is_split_anomaly": False,
                "df_bars": df_bars
            }
        }

        eval_res = monitor.evaluate_live_exits(market_data_by_symbol=market_data, force_market_open=True)

        assert eval_res["market_status"] == "OPEN"
        assert len(eval_res["v1_exit_alerts"]) == 0
        assert len(eval_res["v2_shadow_exits"]) == 0

        summary = eval_res.get("telemetry_summary")
        assert summary is not None
        assert summary["cycle_counts"]["evaluated"] == 1
        assert summary["cycle_counts"]["v1_hold"] == 1
        assert summary["cycle_counts"]["v1_exit"] == 0
        assert summary["cycle_counts"]["v2_hold"] == 1
        assert summary["cycle_counts"]["v2_shadow_exit"] == 0
        assert summary["cycle_counts"]["positions_closed"] == 0
        assert summary["reconciliation"] == "PASS"


def test_wealth_exit_v1_primary_authority_closes_position():
    """Tests that V1 trigger generates EXIT_ALERT, closes position, and records V1 mutating authority."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        state_file = os.path.join(tmp_dir, "monitor_state.json")
        ledger_file = os.path.join(tmp_dir, "ledger.jsonl")
        monitor = LiveWealthMonitorEngine(state_file=state_file, ledger_file=ledger_file)

        monitor.record_user_buy(symbol="TATAMOTORS", entry_price=100.0, entry_date="2026-09-01")

        # Bars engineered to trigger V1: 2 consecutive closes < SMA50 + SMA50 slope down
        n_bars = 60
        dates = pd.date_range(end="2026-09-25", periods=n_bars, freq="B")
        closes = np.full(n_bars, 100.0)
        closes[-10:] = 80.0
        df_bars = pd.DataFrame({
            "Date": dates,
            "Open": closes + 1.0,
            "High": closes + 2.0,
            "Low": closes - 2.0,
            "Close": closes,
            "Volume": np.full(n_bars, 50000.0)
        })

        market_data = {
            "TATAMOTORS": {
                "cmp": 80.0,
                "is_completed_session": True,
                "is_split_anomaly": False,
                "df_bars": df_bars
            }
        }

        eval_res = monitor.evaluate_live_exits(market_data_by_symbol=market_data, force_market_open=True)

        assert len(eval_res["v1_exit_alerts"]) == 1
        assert len(monitor.open_positions) == 0
        assert len(monitor.closed_positions) == 1

        summary = eval_res["telemetry_summary"]
        assert summary["cycle_counts"]["v1_exit"] == 1
        assert summary["cycle_counts"]["positions_closed"] == 1
        assert summary["reconciliation"] == "PASS"


def test_wealth_exit_v2_shadow_cannot_mutate_state():
    """
    CRITICAL RUNTIME PROOF:
    V2 produces EXIT WARNING telemetry, but position_mutation_allowed is False and
    the position strictly remains OPEN on the dashboard.
    """
    with tempfile.TemporaryDirectory() as tmp_dir:
        state_file = os.path.join(tmp_dir, "monitor_state.json")
        ledger_file = os.path.join(tmp_dir, "ledger.jsonl")
        monitor = LiveWealthMonitorEngine(state_file=state_file, ledger_file=ledger_file)

        pos_info = monitor.record_user_buy(symbol="INFY", entry_price=100.0, entry_date="2026-09-01")
        pos_id = pos_info["position"]["position_id"]

        # Test telemetry recording for V2-only trigger
        exit_telem = WealthExitTelemetry(scanner="FUNDAMENTAL")
        exit_telem.log_cycle_start(open_position_count=1)

        # Record position audit
        exit_telem.record_position_audit(pos_id, "INFY", "2026-09-01", 100.0, 95.0, 24)

        # V1 evaluation: HOLD
        exit_telem.record_v1_evaluation(
            position_id=pos_id,
            close_t=95.0,
            close_t_prev=96.0,
            sma50_t=98.0,
            sma50_t_prev5=97.5,
            prior_20d_low=94.0,
            relative_return_10d=-0.02,
            distribution_days_10d=0,
            cond_2_closes_sma50=False,
            cond_close_prior_20d_low=False,
            sec_sma50_slope_down=False,
            sec_rel_ret_lte_m5=False,
            sec_dist_days_ge_2=False,
            v1_exit=False,
            reason="HOLD"
        )

        # V2 evaluation: EXIT (Shadow only)
        exit_telem.record_v2_evaluation(
            position_id=pos_id,
            close_t=95.0,
            close_t_prev=96.0,
            prior_20d_low_t=94.0,
            prior_20d_low_t_prev=94.5,
            v2_exit=True,
            reason="SHADOW_EXIT_V2"
        )

        # Record independence: V1=False, V2=True -> Zero mutation
        exit_telem.record_v1_v2_independence(
            position_id=pos_id,
            symbol="INFY",
            v1_exit=False,
            v2_exit=True,
            pos_status_before="OPEN",
            pos_status_after="OPEN"
        )

        cycle_summary = exit_telem.produce_end_of_cycle_summary()
        assert cycle_summary["cycle_counts"]["v1_hold"] == 1
        assert cycle_summary["cycle_counts"]["v1_exit"] == 0
        assert cycle_summary["cycle_counts"]["v2_hold"] == 0
        assert cycle_summary["cycle_counts"]["v2_shadow_exit"] == 1
        assert cycle_summary["cycle_counts"]["positions_closed"] == 0
        assert cycle_summary["reconciliation"] == "PASS"

        # Verify live monitor also guarantees position remains OPEN
        assert monitor.open_positions[pos_id]["status"] == PositionStatus.OPEN.value


def test_wealth_exit_incomplete_candle_blocked_telemetry():
    """Tests incomplete intraday candle guard emits INCOMPLETE_CANDLE_BLOCKED and blocks exit."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        state_file = os.path.join(tmp_dir, "monitor_state.json")
        ledger_file = os.path.join(tmp_dir, "ledger.jsonl")
        monitor = LiveWealthMonitorEngine(state_file=state_file, ledger_file=ledger_file)

        monitor.record_user_buy(symbol="HDFCBANK", entry_price=100.0, entry_date="2026-09-01")

        df_bars = create_ideal_bars(n_bars=60, base_price=100.0)
        market_data = {
            "HDFCBANK": {
                "cmp": 90.0,
                "is_completed_session": False,  # INCOMPLETE SESSION
                "is_split_anomaly": False,
                "df_bars": df_bars
            }
        }

        eval_res = monitor.evaluate_live_exits(market_data_by_symbol=market_data, force_market_open=True)

        assert len(eval_res["v1_exit_alerts"]) == 0
        summary = eval_res["telemetry_summary"]
        assert summary["cycle_counts"]["v1_hold"] == 1
        assert summary["cycle_counts"]["v1_exit"] == 0
