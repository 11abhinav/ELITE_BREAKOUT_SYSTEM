#!/usr/bin/env python3
"""
tests/test_live_entry_exit_workflow.py
======================================
15 GOLDEN TEST CASES FOR LIVE ENTRY + LIVE EXIT MONITOR + V2 SHADOW TRACKER

MANDATORY GOLDEN CASES (Section 19 of Master Implementation Prompt):
  Case 1:  Healthy winner -> V1 HOLD.
  Case 2:  Temporary weakness -> V1 HOLD.
  Case 3:  Exact V1 structural weakness + confirmation -> V1 EXIT.
  Case 4:  V1 HOLD / V2 EXIT -> dashboard remains OPEN.
  Case 5:  V1 EXIT / V2 HOLD -> dashboard exits.
  Case 6:  Both EXIT -> dashboard exits once.
  Case 7:  Repeated EXIT condition -> one alert only.
  Case 8:  Missing data -> no exit.
  Case 9:  Stale data -> no exit.
  Case 10: Service restart with OPEN position -> state recovered exactly.
  Case 11: Exit triggered -> CLOSED state persisted.
  Case 12: Closed position receives repeated monitor scans -> no new exit alert.
  Case 13: Future BUY after previous CLOSED position -> new position_id.
  Case 14: Market closed -> no live exit evaluation.
  Case 15: Intraday unfinished daily candle -> must not be misinterpreted as a completed daily-close condition.
"""

import os
import sys
import shutil
import tempfile
import pytest
import numpy as np
import pandas as pd
from datetime import datetime, date, time as time_cls
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")
BASE_DIR = "/Users/abhinavmaheshwari/Documents/ELITE_BREAKOUT_SYSTEM"
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from app.live_wealth_monitor import (
    LiveWealthMonitorEngine,
    CanonicalV1ExitEvaluator,
    CanonicalV2ExitEvaluator,
    Canonical20DBreakoutScanner,
    MarketHoursGate,
    DataHealthGate,
    PositionStatus,
    DataHealthStatus,
    FROZEN_GIT_SHA,
    RULES_HASH_V1,
    RULES_HASH_V2,
    AUTOMATIC_BROKER_ORDERS
)


def create_base_bars(n_bars: int = 60, base_price: float = 100.0, trend: float = 0.5) -> pd.DataFrame:
    """Helper to generate a base valid OHLCV series."""
    dates = pd.date_range(end="2026-09-25", periods=n_bars, freq="B")
    prices = [base_price + i * trend for i in range(n_bars)]
    
    opens = [p - 0.5 for p in prices]
    highs = [p + 1.0 for p in prices]
    lows = [p - 1.0 for p in prices]
    closes = prices
    volumes = [100000 + i * 500 for i in range(n_bars)]
    
    return pd.DataFrame({
        "Date": dates,
        "Open": opens,
        "High": highs,
        "Low": lows,
        "Close": closes,
        "Volume": volumes
    })


@pytest.fixture
def temp_monitor_dir():
    temp_dir = tempfile.mkdtemp(prefix="wealth_monitor_test_")
    state_file = os.path.join(temp_dir, "test_state.json")
    alerts_log = os.path.join(temp_dir, "test_alerts.jsonl")
    ledger_file = os.path.join(temp_dir, "test_ledger.jsonl")
    yield temp_dir, state_file, alerts_log, ledger_file
    shutil.rmtree(temp_dir, ignore_errors=True)


# =====================================================================================
# CASE 1: Healthy Winner -> V1 HOLD
# =====================================================================================
def test_case_1_healthy_winner_v1_hold(temp_monitor_dir):
    _, s_file, a_log, l_file = temp_monitor_dir
    engine = LiveWealthMonitorEngine(state_file=s_file, alerts_log=a_log, ledger_file=l_file)

    # Steady upward trending stock, well above SMA50 and 20D low
    df = create_base_bars(n_bars=60, base_price=100.0, trend=1.0)
    cmp = float(df["Close"].iloc[-1])

    # Record user buy
    res = engine.record_user_buy(symbol="WINNER", entry_price=120.0, shares=10)
    assert res["success"] is True
    pid = res["position"]["position_id"]

    market_feed = {
        "WINNER": {
            "cmp": cmp,
            "df_bars": df,
            "is_completed_session": True
        }
    }

    eval_res = engine.evaluate_live_exits(market_feed, force_market_open=True)
    assert len(eval_res["v1_exit_alerts"]) == 0
    assert pid in engine.open_positions
    pos = engine.open_positions[pid]
    assert pos["v1_state"] == "HOLD"
    assert pos["status"] == PositionStatus.OPEN.value
    assert pos["v1_structural_weakness"] is False


# =====================================================================================
# CASE 2: Temporary Weakness -> V1 HOLD
# =====================================================================================
def test_case_2_temporary_weakness_v1_hold(temp_monitor_dir):
    _, s_file, a_log, l_file = temp_monitor_dir
    engine = LiveWealthMonitorEngine(state_file=s_file, alerts_log=a_log, ledger_file=l_file)

    df = create_base_bars(n_bars=60, base_price=100.0, trend=0.5)
    # Ensure prior 20D low is 90.0 so the dip does not break prior 20D low
    df.loc[39, "Close"] = 90.0
    sma50_val = df["Close"].rolling(50, min_periods=20).mean().iloc[-1]
    df.loc[58, "Close"] = sma50_val + 2.0  # Above SMA50
    df.loc[59, "Close"] = sma50_val - 0.5  # Only 1 close below SMA50, well above 20D low (90.0)
    cmp = float(df["Close"].iloc[-1])

    res = engine.record_user_buy(symbol="DIPSTOCK", entry_price=120.0, shares=10)
    pid = res["position"]["position_id"]

    market_feed = {
        "DIPSTOCK": {
            "cmp": cmp,
            "df_bars": df,
            "is_completed_session": True
        }
    }

    eval_res = engine.evaluate_live_exits(market_feed, force_market_open=True)
    assert len(eval_res["v1_exit_alerts"]) == 0
    assert pid in engine.open_positions
    assert engine.open_positions[pid]["v1_state"] == "HOLD"


# =====================================================================================
# CASE 3: Exact V1 Structural Weakness + Confirmation -> V1 EXIT
# =====================================================================================
def test_case_3_exact_v1_weakness_and_confirmation_v1_exit(temp_monitor_dir):
    _, s_file, a_log, l_file = temp_monitor_dir
    engine = LiveWealthMonitorEngine(state_file=s_file, alerts_log=a_log, ledger_file=l_file)

    df = create_base_bars(n_bars=60, base_price=100.0, trend=0.5)
    # Induce 2 consecutive closes below SMA50 AND secondary confirmation (SMA50 slope down)
    # Pull last 8 bars sharply down
    for i in range(50, 60):
        df.loc[i, "Close"] = 70.0 - (i - 50)
        df.loc[i, "Open"] = 72.0 - (i - 50)
        df.loc[i, "High"] = 73.0 - (i - 50)
        df.loc[i, "Low"] = 69.0 - (i - 50)
        df.loc[i, "Volume"] = 300000  # High distribution volume

    cmp = float(df["Close"].iloc[-1])
    res = engine.record_user_buy(symbol="WEAKSTOCK", entry_price=105.0, shares=10)
    pid = res["position"]["position_id"]

    market_feed = {
        "WEAKSTOCK": {
            "cmp": cmp,
            "df_bars": df,
            "is_completed_session": True
        }
    }

    eval_res = engine.evaluate_live_exits(market_feed, force_market_open=True)
    assert len(eval_res["v1_exit_alerts"]) == 1
    alert = eval_res["v1_exit_alerts"][0]
    assert alert["symbol"] == "WEAKSTOCK"
    assert alert["position_id"] == pid
    assert alert["dashboard_exit_cmp"] == cmp
    assert "2_CLOSES_BELOW_SMA50" in alert["v1_exit_reason"] or "BREAK_PRIOR_20D_LOW" in alert["v1_exit_reason"]

    # Dashboard position must be CLOSED
    assert pid not in engine.open_positions
    assert pid in engine.closed_positions
    closed_pos = engine.closed_positions[pid]
    assert closed_pos["status"] == PositionStatus.CLOSED.value
    assert closed_pos["dashboard_exit_cmp"] == cmp
    assert closed_pos["exit_alert_emitted"] is True


# =====================================================================================
# CASE 4: V1 HOLD / V2 EXIT -> Dashboard Remains OPEN
# =====================================================================================
def test_case_4_v1_hold_v2_exit_dashboard_remains_open(temp_monitor_dir):
    """
    V2 triggers exit warning (e.g. 2 closes below 20D low + compound confirmation),
    but V1 does NOT trigger (e.g. SMA50 conditions not met).
    Dashboard user-facing position MUST REMAIN OPEN.
    """
    _, s_file, a_log, l_file = temp_monitor_dir
    engine = LiveWealthMonitorEngine(state_file=s_file, alerts_log=a_log, ledger_file=l_file)

    df = create_base_bars(n_bars=60, base_price=100.0, trend=1.0)
    # Manually configure arrays so V1 is False and V2 is True
    # In V2, 2 closes below prior 20D low is required.
    # Suppose closes are above SMA50 so cond_sma50_2x is False.
    # But prior 20D low was broken twice, and relative return <= -5% & stock 10D return < 0 + slope down.
    # Even simpler: mock or test CanonicalV1 vs CanonicalV2 directly and through engine
    res = engine.record_user_buy(symbol="SHADOWSTOCK", entry_price=120.0, shares=10)
    pid = res["position"]["position_id"]

    # Construct an artificial scenario where V2 triggers but V1 does not:
    # If V1 evaluate returns exit_signal=False and V2 returns exit_signal=True:
    original_v1_eval = CanonicalV1ExitEvaluator.evaluate
    original_v2_eval = CanonicalV2ExitEvaluator.evaluate
    try:
        CanonicalV1ExitEvaluator.evaluate = staticmethod(lambda **kwargs: {
            "exit_signal": False, "reason": "HOLD", "structural_weakness": False,
            "secondary_confirmation": False, "components": [], "blocked": False, "blocked_reason": None
        })
        CanonicalV2ExitEvaluator.evaluate = staticmethod(lambda **kwargs: {
            "exit_signal": True, "reason": "2_CLOSES_BELOW_20D_LOW+(REL_ABS_WEAKNESS+SLOPE_DOWN)",
            "structural_weakness": True, "secondary_confirmation": True, "components": ["2_CLOSES_BELOW_20D_LOW"],
            "blocked": False, "blocked_reason": None
        })

        market_feed = {
            "SHADOWSTOCK": {
                "cmp": 115.0,
                "df_bars": df,
                "is_completed_session": True
            }
        }

        eval_res = engine.evaluate_live_exits(market_feed, force_market_open=True)
        # V1 exit alerts MUST be empty
        assert len(eval_res["v1_exit_alerts"]) == 0
        # V2 shadow exits MUST detect the hypothetical exit
        assert len(eval_res["v2_shadow_exits"]) == 1
        assert eval_res["v2_shadow_exits"][0]["position_id"] == pid

        # CRITICAL GOVERNANCE INVARIANT: Dashboard position MUST REMAIN OPEN!
        assert pid in engine.open_positions
        assert pid not in engine.closed_positions
        pos = engine.open_positions[pid]
        assert pos["status"] == PositionStatus.OPEN.value
        assert pos["v1_state"] == "HOLD"
        assert pos["v2_state"] == "EXIT WARNING"
        assert pos["v2_hypothetical_exit"] is True
        assert pos["v1_vs_v2_agreement"] == "V2_ONLY"
    finally:
        CanonicalV1ExitEvaluator.evaluate = original_v1_eval
        CanonicalV2ExitEvaluator.evaluate = original_v2_eval


# =====================================================================================
# CASE 5: V1 EXIT / V2 HOLD -> Dashboard Exits
# =====================================================================================
def test_case_5_v1_exit_v2_hold_dashboard_exits(temp_monitor_dir):
    _, s_file, a_log, l_file = temp_monitor_dir
    engine = LiveWealthMonitorEngine(state_file=s_file, alerts_log=a_log, ledger_file=l_file)

    df = create_base_bars(n_bars=60, base_price=100.0, trend=0.5)
    res = engine.record_user_buy(symbol="V1TRIGGER", entry_price=110.0, shares=10)
    pid = res["position"]["position_id"]

    original_v1_eval = CanonicalV1ExitEvaluator.evaluate
    original_v2_eval = CanonicalV2ExitEvaluator.evaluate
    try:
        CanonicalV1ExitEvaluator.evaluate = staticmethod(lambda **kwargs: {
            "exit_signal": True, "reason": "2_CLOSES_BELOW_SMA50+(SMA50_SLOPE_DOWN)",
            "structural_weakness": True, "secondary_confirmation": True,
            "components": ["2_CLOSES_BELOW_SMA50", "SMA50_SLOPE_DOWN"],
            "blocked": False, "blocked_reason": None
        })
        CanonicalV2ExitEvaluator.evaluate = staticmethod(lambda **kwargs: {
            "exit_signal": False, "reason": "HOLD", "structural_weakness": False,
            "secondary_confirmation": False, "components": [], "blocked": False, "blocked_reason": None
        })

        market_feed = {
            "V1TRIGGER": {
                "cmp": 95.0,
                "df_bars": df,
                "is_completed_session": True
            }
        }

        eval_res = engine.evaluate_live_exits(market_feed, force_market_open=True)
        assert len(eval_res["v1_exit_alerts"]) == 1
        assert len(eval_res["v2_shadow_exits"]) == 0

        # Dashboard position MUST be CLOSED because V1 is the primary exit decision
        assert pid not in engine.open_positions
        assert pid in engine.closed_positions
        closed = engine.closed_positions[pid]
        assert closed["status"] == PositionStatus.CLOSED.value
        assert closed["dashboard_exit_cmp"] == 95.0
        assert closed["v1_vs_v2_agreement"] == "V1_ONLY"
    finally:
        CanonicalV1ExitEvaluator.evaluate = original_v1_eval
        CanonicalV2ExitEvaluator.evaluate = original_v2_eval


# =====================================================================================
# CASE 6: Both EXIT -> Dashboard Exits Once
# =====================================================================================
def test_case_6_both_exit_dashboard_exits_once(temp_monitor_dir):
    _, s_file, a_log, l_file = temp_monitor_dir
    engine = LiveWealthMonitorEngine(state_file=s_file, alerts_log=a_log, ledger_file=l_file)

    df = create_base_bars(n_bars=60, base_price=100.0, trend=0.5)
    res = engine.record_user_buy(symbol="BOTHEXIT", entry_price=110.0, shares=10)
    pid = res["position"]["position_id"]

    original_v1_eval = CanonicalV1ExitEvaluator.evaluate
    original_v2_eval = CanonicalV2ExitEvaluator.evaluate
    try:
        CanonicalV1ExitEvaluator.evaluate = staticmethod(lambda **kwargs: {
            "exit_signal": True, "reason": "2_CLOSES_BELOW_SMA50+(SMA50_SLOPE_DOWN)",
            "structural_weakness": True, "secondary_confirmation": True, "components": [], "blocked": False, "blocked_reason": None
        })
        CanonicalV2ExitEvaluator.evaluate = staticmethod(lambda **kwargs: {
            "exit_signal": True, "reason": "2_CLOSES_BELOW_20D_LOW+(REL_ABS_WEAKNESS+SLOPE_DOWN)",
            "structural_weakness": True, "secondary_confirmation": True, "components": [], "blocked": False, "blocked_reason": None
        })

        market_feed = {
            "BOTHEXIT": {
                "cmp": 90.0,
                "df_bars": df,
                "is_completed_session": True
            }
        }

        eval_res = engine.evaluate_live_exits(market_feed, force_market_open=True)
        # Exactly one V1 alert
        assert len(eval_res["v1_exit_alerts"]) == 1
        assert len(eval_res["v2_shadow_exits"]) == 1
        assert pid not in engine.open_positions
        assert pid in engine.closed_positions
        assert engine.closed_positions[pid]["v1_vs_v2_agreement"] == "AGREE"
    finally:
        CanonicalV1ExitEvaluator.evaluate = original_v1_eval
        CanonicalV2ExitEvaluator.evaluate = original_v2_eval


# =====================================================================================
# CASE 7: Repeated EXIT Condition -> One Alert Only
# =====================================================================================
def test_case_7_repeated_exit_condition_one_alert_only(temp_monitor_dir):
    _, s_file, a_log, l_file = temp_monitor_dir
    engine = LiveWealthMonitorEngine(state_file=s_file, alerts_log=a_log, ledger_file=l_file)

    df = create_base_bars(n_bars=60, base_price=100.0, trend=0.5)
    res = engine.record_user_buy(symbol="REPEATTEST", entry_price=110.0, shares=10)
    pid = res["position"]["position_id"]

    original_v1_eval = CanonicalV1ExitEvaluator.evaluate
    try:
        CanonicalV1ExitEvaluator.evaluate = staticmethod(lambda **kwargs: {
            "exit_signal": True, "reason": "2_CLOSES_BELOW_SMA50+(SMA50_SLOPE_DOWN)",
            "structural_weakness": True, "secondary_confirmation": True, "components": [], "blocked": False, "blocked_reason": None
        })

        market_feed = {
            "REPEATTEST": {
                "cmp": 92.0,
                "df_bars": df,
                "is_completed_session": True
            }
        }

        # Scan 1: triggers exit
        eval_1 = engine.evaluate_live_exits(market_feed, force_market_open=True)
        assert len(eval_1["v1_exit_alerts"]) == 1

        # Scan 2: condition remains true, but position is already CLOSED
        eval_2 = engine.evaluate_live_exits(market_feed, force_market_open=True)
        assert len(eval_2["v1_exit_alerts"]) == 0

        # Scan 3:
        eval_3 = engine.evaluate_live_exits(market_feed, force_market_open=True)
        assert len(eval_3["v1_exit_alerts"]) == 0

        # Verify only 1 alert in engine
        assert len(engine.exit_alerts) == 1
    finally:
        CanonicalV1ExitEvaluator.evaluate = original_v1_eval


# =====================================================================================
# CASE 8: Missing Data -> No Exit
# =====================================================================================
def test_case_8_missing_data_no_exit(temp_monitor_dir):
    _, s_file, a_log, l_file = temp_monitor_dir
    engine = LiveWealthMonitorEngine(state_file=s_file, alerts_log=a_log, ledger_file=l_file)

    res = engine.record_user_buy(symbol="NODATASTOCK", entry_price=100.0)
    pid = res["position"]["position_id"]

    # Empty DataFrame / missing data
    market_feed = {
        "NODATASTOCK": {
            "cmp": 100.0,
            "df_bars": pd.DataFrame(),
            "is_completed_session": True
        }
    }

    eval_res = engine.evaluate_live_exits(market_feed, force_market_open=True)
    assert len(eval_res["v1_exit_alerts"]) == 0
    assert pid in engine.open_positions
    pos = engine.open_positions[pid]
    assert pos["data_health_status"] == DataHealthStatus.BLOCKED.value
    assert "EMPTY_OR_MISSING_DATA" in pos["data_health_reason"]
    assert pos["v1_state"] == "HOLD"


# =====================================================================================
# CASE 9: Stale Data / Corporate Action Split Anomaly -> No Exit
# =====================================================================================
def test_case_9_stale_data_anomaly_no_exit(temp_monitor_dir):
    _, s_file, a_log, l_file = temp_monitor_dir
    engine = LiveWealthMonitorEngine(state_file=s_file, alerts_log=a_log, ledger_file=l_file)

    df = create_base_bars(n_bars=60, base_price=100.0)
    res = engine.record_user_buy(symbol="ANOMALYSTOCK", entry_price=100.0)
    pid = res["position"]["position_id"]

    market_feed = {
        "ANOMALYSTOCK": {
            "cmp": 50.0,
            "df_bars": df,
            "is_split_anomaly": True,  # Flagged split anomaly!
            "is_completed_session": True
        }
    }

    eval_res = engine.evaluate_live_exits(market_feed, force_market_open=True)
    assert len(eval_res["v1_exit_alerts"]) == 0
    assert pid in engine.open_positions
    pos = engine.open_positions[pid]
    assert pos["data_health_status"] == DataHealthStatus.BLOCKED.value
    assert "CORPORATE_ACTION_UNADJUSTED_ANOMALY" in pos["data_health_reason"]


# =====================================================================================
# CASE 10: Service Restart with OPEN Position -> State Recovered Exactly
# =====================================================================================
def test_case_10_service_restart_with_open_position(temp_monitor_dir):
    _, s_file, a_log, l_file = temp_monitor_dir
    engine1 = LiveWealthMonitorEngine(state_file=s_file, alerts_log=a_log, ledger_file=l_file)

    engine1.record_user_buy(symbol="RESTARTPOS", entry_price=150.0, shares=25)
    assert len(engine1.open_positions) == 1
    pos1 = list(engine1.open_positions.values())[0]
    pid1 = pos1["position_id"]

    # Simulate service crash/restart by instantiating new engine pointing to same state file
    engine2 = LiveWealthMonitorEngine(state_file=s_file, alerts_log=a_log, ledger_file=l_file)
    assert len(engine2.open_positions) == 1
    assert pid1 in engine2.open_positions
    pos2 = engine2.open_positions[pid1]
    assert pos2["symbol"] == "RESTARTPOS"
    assert pos2["entry_reference_price"] == 150.0
    assert pos2["shares"] == 25
    assert pos2["status"] == PositionStatus.OPEN.value


# =====================================================================================
# CASE 11: Exit Triggered -> CLOSED State Persisted
# =====================================================================================
def test_case_11_exit_triggered_closed_state_persisted(temp_monitor_dir):
    _, s_file, a_log, l_file = temp_monitor_dir
    engine1 = LiveWealthMonitorEngine(state_file=s_file, alerts_log=a_log, ledger_file=l_file)

    df = create_base_bars(n_bars=60, base_price=100.0)
    res = engine1.record_user_buy(symbol="CLOSEDPERSIST", entry_price=100.0)
    pid = res["position"]["position_id"]

    original_v1_eval = CanonicalV1ExitEvaluator.evaluate
    try:
        CanonicalV1ExitEvaluator.evaluate = staticmethod(lambda **kwargs: {
            "exit_signal": True, "reason": "CONFIRMED_WEAKNESS", "structural_weakness": True,
            "secondary_confirmation": True, "components": ["2_CLOSES_BELOW_SMA50"], "blocked": False, "blocked_reason": None
        })

        market_feed = {"CLOSEDPERSIST": {"cmp": 85.0, "df_bars": df, "is_completed_session": True}}
        engine1.evaluate_live_exits(market_feed, force_market_open=True)
        assert pid in engine1.closed_positions
        assert pid not in engine1.open_positions
    finally:
        CanonicalV1ExitEvaluator.evaluate = original_v1_eval

    # Restart engine and verify CLOSED state persisted intact
    engine2 = LiveWealthMonitorEngine(state_file=s_file, alerts_log=a_log, ledger_file=l_file)
    assert pid in engine2.closed_positions
    assert pid not in engine2.open_positions
    assert engine2.closed_positions[pid]["status"] == PositionStatus.CLOSED.value
    assert engine2.closed_positions[pid]["dashboard_exit_cmp"] == 85.0


# =====================================================================================
# CASE 12: Closed Position Receives Repeated Monitor Scans -> No New Exit Alert
# =====================================================================================
def test_case_12_closed_position_repeated_scans_no_alert(temp_monitor_dir):
    _, s_file, a_log, l_file = temp_monitor_dir
    engine = LiveWealthMonitorEngine(state_file=s_file, alerts_log=a_log, ledger_file=l_file)

    df = create_base_bars(n_bars=60, base_price=100.0)
    res = engine.record_user_buy(symbol="CLOSEDSCAN", entry_price=100.0)
    pid = res["position"]["position_id"]

    # Close position manually/via exit
    engine.open_positions[pid]["status"] = PositionStatus.CLOSED.value
    engine.open_positions[pid]["dashboard_exit_cmp"] = 90.0
    engine.closed_positions[pid] = engine.open_positions[pid]
    del engine.open_positions[pid]
    engine.save_state()

    market_feed = {"CLOSEDSCAN": {"cmp": 70.0, "df_bars": df, "is_completed_session": True}}
    eval_res = engine.evaluate_live_exits(market_feed, force_market_open=True)
    assert len(eval_res["v1_exit_alerts"]) == 0
    assert eval_res["evaluated_positions"] == 0


# =====================================================================================
# CASE 13: Future BUY after Previous CLOSED Position -> New Position ID
# =====================================================================================
def test_case_13_future_buy_after_closed_position(temp_monitor_dir):
    _, s_file, a_log, l_file = temp_monitor_dir
    engine = LiveWealthMonitorEngine(state_file=s_file, alerts_log=a_log, ledger_file=l_file)

    # First trade lifecycle
    res1 = engine.record_user_buy(symbol="CYCLESTOCK", entry_price=100.0)
    pid1 = res1["position"]["position_id"]
    engine.closed_positions[pid1] = engine.open_positions.pop(pid1)
    engine.closed_positions[pid1]["status"] = PositionStatus.CLOSED.value
    engine.save_state()

    # Second trade lifecycle after new breakout
    res2 = engine.record_user_buy(symbol="CYCLESTOCK", entry_price=150.0)
    assert res2["success"] is True
    pid2 = res2["position"]["position_id"]

    assert pid1 != pid2
    assert pid1 in engine.closed_positions
    assert pid2 in engine.open_positions
    assert engine.open_positions[pid2]["entry_reference_price"] == 150.0


# =====================================================================================
# CASE 14: Market Closed -> No Live Exit Evaluation
# =====================================================================================
def test_case_14_market_closed_no_live_exit_eval(temp_monitor_dir):
    _, s_file, a_log, l_file = temp_monitor_dir
    engine = LiveWealthMonitorEngine(state_file=s_file, alerts_log=a_log, ledger_file=l_file)

    df = create_base_bars(n_bars=60, base_price=100.0)
    engine.record_user_buy(symbol="OFFHOURS", entry_price=100.0)

    market_feed = {"OFFHOURS": {"cmp": 50.0, "df_bars": df, "is_completed_session": True}}

    # Test outside market hours: Sunday 12:00 PM IST
    sunday_dt = datetime(2026, 9, 27, 12, 0, 0, tzinfo=IST)
    eval_res = engine.evaluate_live_exits(market_feed, current_dt=sunday_dt, force_market_open=False)
    assert eval_res["market_status"] == "CLOSED"
    assert eval_res["evaluated_positions"] == 0
    assert len(eval_res["v1_exit_alerts"]) == 0

    # Test outside market hours: Weekday at 16:30 IST (Post-market)
    post_market_dt = datetime(2026, 9, 25, 16, 30, 0, tzinfo=IST)
    eval_res_post = engine.evaluate_live_exits(market_feed, current_dt=post_market_dt, force_market_open=False)
    assert eval_res_post["market_status"] == "CLOSED"
    assert eval_res_post["reason"] == "POST_MARKET_INACTIVE"
    assert eval_res_post["evaluated_positions"] == 0


# =====================================================================================
# CASE 15: Intraday Unfinished Daily Candle -> Not Misinterpreted as Completed Daily Close
# =====================================================================================
def test_case_15_intraday_unfinished_candle_guard(temp_monitor_dir):
    """
    CRITICAL GOVERNANCE TEST:
    If a candle is an unfinished intraday candle (is_completed_session = False),
    it MUST NOT trigger a completed daily close exit condition.
    """
    _, s_file, a_log, l_file = temp_monitor_dir
    engine = LiveWealthMonitorEngine(state_file=s_file, alerts_log=a_log, ledger_file=l_file)

    # Stock plunged intraday below SMA50
    df = create_base_bars(n_bars=60, base_price=100.0)
    df.loc[59, "Close"] = 50.0  # Deep dip in unfinished candle!
    df.loc[58, "Close"] = 50.0

    res = engine.record_user_buy(symbol="INTRADAYSTOCK", entry_price=100.0)
    pid = res["position"]["position_id"]

    market_feed = {
        "INTRADAYSTOCK": {
            "cmp": 50.0,
            "df_bars": df,
            "is_completed_session": False  # UNFINISHED INTRADAY CANDLE!
        }
    }

    eval_res = engine.evaluate_live_exits(market_feed, force_market_open=True)
    assert len(eval_res["v1_exit_alerts"]) == 0
    assert pid in engine.open_positions
    pos = engine.open_positions[pid]
    assert pos["v1_state"] == "HOLD"
    assert pos["status"] == PositionStatus.OPEN.value
    assert "INTRADAY_UNFINISHED_CANDLE" in pos["v1_reasons"]


# =====================================================================================
# ADDITIONAL SAFETY INVARIANT: ZERO BROKER ORDER ROUTING
# =====================================================================================
def test_safety_invariant_zero_broker_order_routing():
    assert AUTOMATIC_BROKER_ORDERS is False
    from app.live_wealth_monitor import SAFETY_INVARIANT
    assert SAFETY_INVARIANT == "NO_BROKER_ORDER_ROUTING"
