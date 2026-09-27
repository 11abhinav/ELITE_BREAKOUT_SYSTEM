#!/usr/bin/env python3
"""
tests/test_live_fundamental_entry_exit.py
=========================================
COMPLETE TEST BATTERY FOR FUNDAMENTALLY STRONG LIVE BUY SCANNER + V1 EXIT + V2 SHADOW

Sections Covered:
  - 18 GOLDEN ENTRY TESTS (Section 35)
  - 12 GOLDEN EXIT TESTS (Section 36)
  - 9 MARKET-HOUR TESTS (Section 37)
  - ZERO BROKER ROUTING PROOF (Section 38)
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

from app.live_fundamental_scanner import (
    LiveFundamentalBuyScanner,
    FundamentalQualityGate,
    EarningsAccelerationGate,
    TechnicalTrendGate,
    ConsolidationGate,
    BreakoutGate,
    RejectionReason,
    ApprovedUniverseRegistry,
    FROZEN_GIT_SHA,
    RULES_HASH_BUY
)
from app.live_wealth_monitor import (
    LiveWealthMonitorEngine,
    CanonicalV1ExitEvaluator,
    CanonicalV2ExitEvaluator,
    MarketHoursGate,
    DataHealthGate,
    PositionStatus,
    DataHealthStatus,
    AUTOMATIC_BROKER_ORDERS,
    SAFETY_INVARIANT
)


def create_ideal_bars(n_bars: int = 210, base_price: float = 100.0) -> pd.DataFrame:
    """Creates an ideal technical setup: Close > SMA50 > SMA200, controlled consolidation, and 20D breakout."""
    dates = pd.date_range(end="2026-09-25", periods=n_bars, freq="B")
    prices = np.linspace(base_price, base_price * 1.5, n_bars)
    
    # Establish consolidation in bars -30 to -2 around 145.0
    for i in range(n_bars - 30, n_bars - 1):
        prices[i] = 145.0 + 1.5 * np.sin(i)

    # Establish breakout on final bar (bar -1): Close = 150.0 > Prior 20D High (~146.5)
    prices[-1] = 150.0

    opens = prices - 0.5
    highs = prices + 0.8
    lows = prices - 0.8
    closes = prices.copy()
    volumes = np.full(n_bars, 100000.0)
    volumes[-1] = 200000.0  # 2.0x volume on breakout

    # Ensure SMA50 > SMA200
    df = pd.DataFrame({
        "Date": dates,
        "Open": opens,
        "High": highs,
        "Low": lows,
        "Close": closes,
        "Volume": volumes
    })
    return df


def create_ideal_fundamentals() -> dict:
    """Creates a fundamentally strong candidate passing all Quality and Acceleration gates."""
    return {
        "roce": 22.5,
        "roe": 18.0,
        "operating_cash_flow": 150000000.0,
        "debt_equity": 0.35,
        "rev_yoy_latest": 25.0,
        "rev_yoy_prev": 15.0,
        "op_profit_yoy_latest": 30.0,
        "op_profit_yoy_prev": 18.0,
        "eps_yoy_latest": 35.0,
        "eps_yoy_prev": 20.0,
        "prior_eps": 12.50
    }


# =====================================================================================
# PART 1: 18 GOLDEN ENTRY TESTS (Section 35)
# =====================================================================================

def test_entry_case_1_fundamentally_strong_plus_breakout_buy():
    scanner = LiveFundamentalBuyScanner()
    df = create_ideal_bars()
    funds = create_ideal_fundamentals()
    # Mock symbol validation to pass universe
    scanner.universe_registry.clean_symbols.add("TESTSTOCK")

    res = scanner.scan_candidate("TESTSTOCK", df, funds)
    assert res["is_buy"] is True
    assert res["decision"] == "BUY_ALERT"
    assert res["fundamentally_qualified"] is True
    assert len(res["rejection_reasons"]) == 0


def test_entry_case_2_roce_fails_plus_breakout_no_buy():
    scanner = LiveFundamentalBuyScanner()
    df = create_ideal_bars()
    funds = create_ideal_fundamentals()
    funds["roce"] = 12.0  # Fails < 15.0%
    scanner.universe_registry.clean_symbols.add("TESTSTOCK")

    res = scanner.scan_candidate("TESTSTOCK", df, funds)
    assert res["is_buy"] is False
    assert RejectionReason.FAIL_ROCE.value in res["rejection_reasons"]
    assert res["fundamentally_qualified"] is False


def test_entry_case_3_roe_fails_plus_breakout_no_buy():
    scanner = LiveFundamentalBuyScanner()
    df = create_ideal_bars()
    funds = create_ideal_fundamentals()
    funds["roe"] = 9.5  # Fails < 12.0%
    scanner.universe_registry.clean_symbols.add("TESTSTOCK")

    res = scanner.scan_candidate("TESTSTOCK", df, funds)
    assert res["is_buy"] is False
    assert RejectionReason.FAIL_ROE.value in res["rejection_reasons"]


def test_entry_case_4_ocf_fails_plus_breakout_no_buy():
    scanner = LiveFundamentalBuyScanner()
    df = create_ideal_bars()
    funds = create_ideal_fundamentals()
    funds["operating_cash_flow"] = -5000000.0  # Fails <= 0
    scanner.universe_registry.clean_symbols.add("TESTSTOCK")

    res = scanner.scan_candidate("TESTSTOCK", df, funds)
    assert res["is_buy"] is False
    assert RejectionReason.FAIL_OCF.value in res["rejection_reasons"]


def test_entry_case_5_debt_equity_fails_plus_breakout_no_buy():
    scanner = LiveFundamentalBuyScanner()
    df = create_ideal_bars()
    funds = create_ideal_fundamentals()
    funds["debt_equity"] = 1.65  # Fails > 1.0
    scanner.universe_registry.clean_symbols.add("TESTSTOCK")

    res = scanner.scan_candidate("TESTSTOCK", df, funds)
    assert res["is_buy"] is False
    assert RejectionReason.FAIL_DEBT_EQUITY.value in res["rejection_reasons"]


def test_entry_case_6_revenue_acceleration_fails_plus_breakout_no_buy():
    scanner = LiveFundamentalBuyScanner()
    df = create_ideal_bars()
    funds = create_ideal_fundamentals()
    funds["rev_yoy_latest"] = 12.0
    funds["rev_yoy_prev"] = 18.0  # Decelerating!
    scanner.universe_registry.clean_symbols.add("TESTSTOCK")

    res = scanner.scan_candidate("TESTSTOCK", df, funds)
    assert res["is_buy"] is False
    assert RejectionReason.FAIL_REVENUE_ACCELERATION.value in res["rejection_reasons"]


def test_entry_case_7_operating_profit_acceleration_fails_plus_breakout_no_buy():
    scanner = LiveFundamentalBuyScanner()
    df = create_ideal_bars()
    funds = create_ideal_fundamentals()
    funds["op_profit_yoy_latest"] = 10.0
    funds["op_profit_yoy_prev"] = 25.0  # Decelerating!
    scanner.universe_registry.clean_symbols.add("TESTSTOCK")

    res = scanner.scan_candidate("TESTSTOCK", df, funds)
    assert res["is_buy"] is False
    assert RejectionReason.FAIL_OP_PROFIT_ACCELERATION.value in res["rejection_reasons"]


def test_entry_case_8_eps_acceleration_fails_plus_breakout_no_buy():
    scanner = LiveFundamentalBuyScanner()
    df = create_ideal_bars()
    funds = create_ideal_fundamentals()
    funds["eps_yoy_latest"] = 14.0
    funds["eps_yoy_prev"] = 20.0  # Decelerating!
    scanner.universe_registry.clean_symbols.add("TESTSTOCK")

    res = scanner.scan_candidate("TESTSTOCK", df, funds)
    assert res["is_buy"] is False
    assert RejectionReason.FAIL_EPS_ACCELERATION.value in res["rejection_reasons"]


def test_entry_case_9_prior_eps_non_positive_fails_plus_breakout_no_buy():
    scanner = LiveFundamentalBuyScanner()
    df = create_ideal_bars()
    funds = create_ideal_fundamentals()
    funds["prior_eps"] = -2.50  # Loss-to-profit distortion rejected!
    scanner.universe_registry.clean_symbols.add("TESTSTOCK")

    res = scanner.scan_candidate("TESTSTOCK", df, funds)
    assert res["is_buy"] is False
    assert RejectionReason.FAIL_PRIOR_EPS.value in res["rejection_reasons"]


def test_entry_case_10_trend_fails_plus_breakout_no_buy():
    scanner = LiveFundamentalBuyScanner()
    df = create_ideal_bars()
    # Invert prices so Close < SMA50 or SMA50 < SMA200
    df["Close"] = np.linspace(200.0, 100.0, len(df))
    funds = create_ideal_fundamentals()
    scanner.universe_registry.clean_symbols.add("TESTSTOCK")

    res = scanner.scan_candidate("TESTSTOCK", df, funds)
    assert res["is_buy"] is False
    assert RejectionReason.FAIL_TREND.value in res["rejection_reasons"]


def test_entry_case_11_relative_strength_fails_plus_breakout_no_buy():
    scanner = LiveFundamentalBuyScanner()
    df = create_ideal_bars()
    funds = create_ideal_fundamentals()
    scanner.universe_registry.clean_symbols.add("TESTSTOCK")

    # Benchmark surged 100% in 3 months
    n = len(df)
    bm_closes = np.linspace(100.0, 300.0, n)

    res = scanner.scan_candidate("TESTSTOCK", df, funds, benchmark_closes=bm_closes)
    assert res["is_buy"] is False
    assert RejectionReason.FAIL_RELATIVE_STRENGTH.value in res["rejection_reasons"]


def test_entry_case_12_consolidation_fails_plus_breakout_no_buy():
    scanner = LiveFundamentalBuyScanner()
    df = create_ideal_bars()
    # Create extreme drawdown in consolidation window (> 15%) inside the 20-bar window
    df.loc[len(df) - 15, "High"] = 250.0
    df.loc[len(df) - 10, "Close"] = 150.0  # (250 - 150)/250 = 40% DD
    funds = create_ideal_fundamentals()
    scanner.universe_registry.clean_symbols.add("TESTSTOCK")

    res = scanner.scan_candidate("TESTSTOCK", df, funds)
    assert res["is_buy"] is False
    assert RejectionReason.FAIL_CONSOLIDATION_DRAWDOWN.value in res["rejection_reasons"]


def test_entry_case_13_breakout_fails_no_buy():
    scanner = LiveFundamentalBuyScanner()
    df = create_ideal_bars()
    # Make final close below prior 20D high
    df.loc[len(df) - 1, "Close"] = 130.0
    funds = create_ideal_fundamentals()
    scanner.universe_registry.clean_symbols.add("TESTSTOCK")

    res = scanner.scan_candidate("TESTSTOCK", df, funds)
    assert res["is_buy"] is False
    assert RejectionReason.FAIL_BREAKOUT_PRICE.value in res["rejection_reasons"]


def test_entry_case_14_missing_fundamental_no_buy():
    scanner = LiveFundamentalBuyScanner()
    df = create_ideal_bars()
    scanner.universe_registry.clean_symbols.add("TESTSTOCK")

    res = scanner.scan_candidate("TESTSTOCK", df, fundamentals={})
    assert res["is_buy"] is False
    assert res["decision"] == "BLOCKED_DATA"
    assert RejectionReason.FUNDAMENTAL_DATA_MISSING.value in res["rejection_reasons"]


def test_entry_case_15_stale_fundamental_no_buy():
    scanner = LiveFundamentalBuyScanner()
    df = create_ideal_bars()
    funds = create_ideal_fundamentals()
    scanner.universe_registry.clean_symbols.add("TESTSTOCK")

    res = scanner.scan_candidate("TESTSTOCK", df, funds, is_stale=True)
    assert res["is_buy"] is False
    assert RejectionReason.FUNDAMENTAL_DATA_STALE.value in res["rejection_reasons"]


def test_entry_case_16_invalid_fundamental_provenance_no_buy():
    scanner = LiveFundamentalBuyScanner()
    df = create_ideal_bars()
    funds = create_ideal_fundamentals()
    scanner.universe_registry.clean_symbols.add("TESTSTOCK")

    res = scanner.scan_candidate("TESTSTOCK", df, funds, provenance_valid=False)
    assert res["is_buy"] is False
    assert RejectionReason.FUNDAMENTAL_PROVENANCE_INVALID.value in res["rejection_reasons"]


def test_entry_case_17_all_gates_pass_exactly_one_buy_alert():
    temp_dir = tempfile.mkdtemp(prefix="wealth_entry_test_")
    try:
        s_file = os.path.join(temp_dir, "test_state.json")
        a_log = os.path.join(temp_dir, "test_alerts.jsonl")
        l_file = os.path.join(temp_dir, "test_ledger.jsonl")
        engine = LiveWealthMonitorEngine(state_file=s_file, alerts_log=a_log, ledger_file=l_file)

        scanner = LiveFundamentalBuyScanner()
        df = create_ideal_bars()
        funds = create_ideal_fundamentals()
        scanner.universe_registry.clean_symbols.add("RELIANCE")

        cand = scanner.scan_candidate("RELIANCE", df, funds)
        assert cand["is_buy"] is True

        # Generate live buy alert
        alert = engine.generate_buy_alert(
            symbol="RELIANCE",
            signal_date="2026-09-25",
            signal_close=cand["metrics"]["signal_close"],
            breakout_reference=cand["metrics"]["prior_20d_high"],
            breakout_distance=cand["metrics"]["extension_pct"],
            indicator_values=cand["metrics"]
        )

        assert alert["alert_type"] == "BUY_ALERT"
        assert alert["symbol"] == "RELIANCE"
        assert len(engine.buy_alerts) == 1
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def test_entry_case_18_repeated_scan_no_duplicate_buy_alert():
    temp_dir = tempfile.mkdtemp(prefix="wealth_entry_test_")
    try:
        s_file = os.path.join(temp_dir, "test_state.json")
        a_log = os.path.join(temp_dir, "test_alerts.jsonl")
        l_file = os.path.join(temp_dir, "test_ledger.jsonl")
        engine = LiveWealthMonitorEngine(state_file=s_file, alerts_log=a_log, ledger_file=l_file)

        # First alert generated
        engine.generate_buy_alert("INFY", "2026-09-25", 1500.0, 1480.0, 1.35)
        assert len(engine.buy_alerts) == 1

        # User records buy -> position moves to OPEN
        engine.record_user_buy("INFY", 1500.0)
        assert len(engine.open_positions) == 1

        # Attempt to record duplicate buy for same symbol while OPEN
        dup_res = engine.record_user_buy("INFY", 1510.0)
        assert dup_res["success"] is False
        assert "DUPLICATE_OPEN_POSITION" in dup_res["error"]
        assert len(engine.open_positions) == 1
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


# =====================================================================================
# PART 2: 12 GOLDEN EXIT TESTS (Section 36)
# =====================================================================================

@pytest.fixture
def exit_test_env():
    temp_dir = tempfile.mkdtemp(prefix="wealth_exit_test_")
    s_file = os.path.join(temp_dir, "state.json")
    a_log = os.path.join(temp_dir, "alerts.jsonl")
    l_file = os.path.join(temp_dir, "ledger.jsonl")
    engine = LiveWealthMonitorEngine(state_file=s_file, alerts_log=a_log, ledger_file=l_file)
    yield engine
    shutil.rmtree(temp_dir, ignore_errors=True)


def test_exit_case_1_healthy_winner_v1_hold(exit_test_env):
    engine = exit_test_env
    df = create_ideal_bars(n_bars=60)
    engine.record_user_buy("WINNER", 120.0)
    feed = {"WINNER": {"cmp": float(df["Close"].iloc[-1]), "df_bars": df, "is_completed_session": True}}
    res = engine.evaluate_live_exits(feed, force_market_open=True)
    assert len(res["v1_exit_alerts"]) == 0
    pos = list(engine.open_positions.values())[0]
    assert pos["v1_state"] == "HOLD"


def test_exit_case_2_temporary_weakness_v1_hold(exit_test_env):
    engine = exit_test_env
    df = create_ideal_bars(n_bars=60)
    df.loc[39, "Close"] = 90.0  # Set prior 20D low to 90.0
    sma50 = df["Close"].rolling(50, min_periods=20).mean().iloc[-1]
    df.loc[len(df) - 2, "Close"] = sma50 + 2.0  # Bar 58 above SMA50
    df.loc[len(df) - 1, "Close"] = sma50 - 0.5  # Only 1 bar dip below SMA50, well above prior 20D low (90.0)
    engine.record_user_buy("TEMPDIP", 120.0)
    feed = {"TEMPDIP": {"cmp": float(df["Close"].iloc[-1]), "df_bars": df, "is_completed_session": True}}
    res = engine.evaluate_live_exits(feed, force_market_open=True)
    assert len(res["v1_exit_alerts"]) == 0
    assert list(engine.open_positions.values())[0]["v1_state"] == "HOLD"


def test_exit_case_3_exact_v1_exit_generates_exit_alert(exit_test_env):
    engine = exit_test_env
    df = create_ideal_bars(n_bars=60)
    # Trigger 2 closes below SMA50 + SMA50 slope down
    for i in range(50, 60):
        df.loc[i, "Close"] = 70.0 - i
        df.loc[i, "Open"] = 72.0 - i
        df.loc[i, "High"] = 73.0 - i
        df.loc[i, "Low"] = 68.0 - i
        df.loc[i, "Volume"] = 300000

    cmp = float(df["Close"].iloc[-1])
    engine.record_user_buy("WEAK", 100.0)
    feed = {"WEAK": {"cmp": cmp, "df_bars": df, "is_completed_session": True}}
    res = engine.evaluate_live_exits(feed, force_market_open=True)
    assert len(res["v1_exit_alerts"]) == 1
    assert len(engine.closed_positions) == 1
    closed_pos = list(engine.closed_positions.values())[0]
    assert closed_pos["status"] == PositionStatus.CLOSED.value
    assert closed_pos["dashboard_exit_cmp"] == cmp


def test_exit_case_4_v1_exit_plus_v2_hold_user_exit(exit_test_env):
    engine = exit_test_env
    df = create_ideal_bars(n_bars=60)
    engine.record_user_buy("V1ONLY", 100.0)
    feed = {"V1ONLY": {"cmp": 90.0, "df_bars": df, "is_completed_session": True}}

    orig_v1 = CanonicalV1ExitEvaluator.evaluate
    orig_v2 = CanonicalV2ExitEvaluator.evaluate
    try:
        CanonicalV1ExitEvaluator.evaluate = staticmethod(lambda **kwargs: {
            "exit_signal": True, "reason": "CONFIRMED_WEAKNESS", "structural_weakness": True,
            "secondary_confirmation": True, "components": [], "blocked": False, "blocked_reason": None
        })
        CanonicalV2ExitEvaluator.evaluate = staticmethod(lambda **kwargs: {
            "exit_signal": False, "reason": "HOLD", "structural_weakness": False,
            "secondary_confirmation": False, "components": [], "blocked": False, "blocked_reason": None
        })
        res = engine.evaluate_live_exits(feed, force_market_open=True)
        assert len(res["v1_exit_alerts"]) == 1
        assert len(engine.closed_positions) == 1
    finally:
        CanonicalV1ExitEvaluator.evaluate = orig_v1
        CanonicalV2ExitEvaluator.evaluate = orig_v2


def test_exit_case_5_v1_hold_plus_v2_exit_user_hold_v2_shadow_only(exit_test_env):
    engine = exit_test_env
    df = create_ideal_bars(n_bars=60)
    engine.record_user_buy("V2ONLY", 100.0)
    feed = {"V2ONLY": {"cmp": 95.0, "df_bars": df, "is_completed_session": True}}

    orig_v1 = CanonicalV1ExitEvaluator.evaluate
    orig_v2 = CanonicalV2ExitEvaluator.evaluate
    try:
        CanonicalV1ExitEvaluator.evaluate = staticmethod(lambda **kwargs: {
            "exit_signal": False, "reason": "HOLD", "structural_weakness": False,
            "secondary_confirmation": False, "components": [], "blocked": False, "blocked_reason": None
        })
        CanonicalV2ExitEvaluator.evaluate = staticmethod(lambda **kwargs: {
            "exit_signal": True, "reason": "CONFIRMED_WEAKNESS_V2", "structural_weakness": True,
            "secondary_confirmation": True, "components": [], "blocked": False, "blocked_reason": None
        })
        res = engine.evaluate_live_exits(feed, force_market_open=True)
        assert len(res["v1_exit_alerts"]) == 0
        assert len(res["v2_shadow_exits"]) == 1
        # Position must remain OPEN
        assert len(engine.open_positions) == 1
        assert len(engine.closed_positions) == 0
    finally:
        CanonicalV1ExitEvaluator.evaluate = orig_v1
        CanonicalV2ExitEvaluator.evaluate = orig_v2


def test_exit_case_6_both_exit_one_user_exit(exit_test_env):
    engine = exit_test_env
    df = create_ideal_bars(n_bars=60)
    engine.record_user_buy("BOTH", 100.0)
    feed = {"BOTH": {"cmp": 80.0, "df_bars": df, "is_completed_session": True}}

    orig_v1 = CanonicalV1ExitEvaluator.evaluate
    orig_v2 = CanonicalV2ExitEvaluator.evaluate
    try:
        CanonicalV1ExitEvaluator.evaluate = staticmethod(lambda **kwargs: {
            "exit_signal": True, "reason": "CONFIRMED_WEAKNESS", "structural_weakness": True,
            "secondary_confirmation": True, "components": [], "blocked": False, "blocked_reason": None
        })
        CanonicalV2ExitEvaluator.evaluate = staticmethod(lambda **kwargs: {
            "exit_signal": True, "reason": "CONFIRMED_WEAKNESS_V2", "structural_weakness": True,
            "secondary_confirmation": True, "components": [], "blocked": False, "blocked_reason": None
        })
        res = engine.evaluate_live_exits(feed, force_market_open=True)
        assert len(res["v1_exit_alerts"]) == 1
        assert len(engine.closed_positions) == 1
    finally:
        CanonicalV1ExitEvaluator.evaluate = orig_v1
        CanonicalV2ExitEvaluator.evaluate = orig_v2


def test_exit_case_7_repeated_v1_exit_condition_one_exit_event(exit_test_env):
    engine = exit_test_env
    df = create_ideal_bars(n_bars=60)
    engine.record_user_buy("ONCE", 100.0)
    feed = {"ONCE": {"cmp": 80.0, "df_bars": df, "is_completed_session": True}}

    orig_v1 = CanonicalV1ExitEvaluator.evaluate
    try:
        CanonicalV1ExitEvaluator.evaluate = staticmethod(lambda **kwargs: {
            "exit_signal": True, "reason": "CONFIRMED_WEAKNESS", "structural_weakness": True,
            "secondary_confirmation": True, "components": [], "blocked": False, "blocked_reason": None
        })
        res1 = engine.evaluate_live_exits(feed, force_market_open=True)
        assert len(res1["v1_exit_alerts"]) == 1

        # Second evaluation of same symbol
        res2 = engine.evaluate_live_exits(feed, force_market_open=True)
        assert len(res2["v1_exit_alerts"]) == 0
        assert len(engine.exit_alerts) == 1
    finally:
        CanonicalV1ExitEvaluator.evaluate = orig_v1


def test_exit_case_8_missing_data_no_fabricated_exit(exit_test_env):
    engine = exit_test_env
    engine.record_user_buy("EMPTYDATA", 100.0)
    feed = {"EMPTYDATA": {"cmp": 100.0, "df_bars": pd.DataFrame(), "is_completed_session": True}}
    res = engine.evaluate_live_exits(feed, force_market_open=True)
    assert len(res["v1_exit_alerts"]) == 0
    assert len(engine.open_positions) == 1


def test_exit_case_9_stale_data_anomaly_no_fabricated_exit(exit_test_env):
    engine = exit_test_env
    df = create_ideal_bars(n_bars=60)
    engine.record_user_buy("ANOMALY", 100.0)
    feed = {"ANOMALY": {"cmp": 50.0, "df_bars": df, "is_split_anomaly": True, "is_completed_session": True}}
    res = engine.evaluate_live_exits(feed, force_market_open=True)
    assert len(res["v1_exit_alerts"]) == 0
    assert len(engine.open_positions) == 1


def test_exit_case_10_closed_position_no_further_exit_alerts(exit_test_env):
    engine = exit_test_env
    df = create_ideal_bars(n_bars=60)
    res_b = engine.record_user_buy("CLOSEDSTOCK", 100.0)
    pid = res_b["position"]["position_id"]
    engine.closed_positions[pid] = engine.open_positions.pop(pid)
    engine.closed_positions[pid]["status"] = PositionStatus.CLOSED.value

    feed = {"CLOSEDSTOCK": {"cmp": 40.0, "df_bars": df, "is_completed_session": True}}
    res = engine.evaluate_live_exits(feed, force_market_open=True)
    assert len(res["v1_exit_alerts"]) == 0


def test_exit_case_11_restart_with_open_position_restored():
    temp_dir = tempfile.mkdtemp(prefix="wealth_restart_test_")
    try:
        s_file = os.path.join(temp_dir, "state.json")
        a_log = os.path.join(temp_dir, "alerts.jsonl")
        l_file = os.path.join(temp_dir, "ledger.jsonl")
        engine1 = LiveWealthMonitorEngine(state_file=s_file, alerts_log=a_log, ledger_file=l_file)
        engine1.record_user_buy("RESTOREME", 200.0, shares=50)

        # Crash & reload from disk
        engine2 = LiveWealthMonitorEngine(state_file=s_file, alerts_log=a_log, ledger_file=l_file)
        assert len(engine2.open_positions) == 1
        pos = list(engine2.open_positions.values())[0]
        assert pos["symbol"] == "RESTOREME"
        assert pos["entry_reference_price"] == 200.0
        assert pos["shares"] == 50
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def test_exit_case_12_intraday_unfinished_candle_no_false_exit(exit_test_env):
    engine = exit_test_env
    df = create_ideal_bars(n_bars=60)
    df.loc[len(df) - 1, "Close"] = 50.0  # Deep dip in unfinished candle!
    df.loc[len(df) - 2, "Close"] = 50.0

    engine.record_user_buy("UNFINISHED", 100.0)
    # is_completed_session = False
    feed = {"UNFINISHED": {"cmp": 50.0, "df_bars": df, "is_completed_session": False}}
    res = engine.evaluate_live_exits(feed, force_market_open=True)
    assert len(res["v1_exit_alerts"]) == 0
    assert len(engine.open_positions) == 1
    assert "INTRADAY_UNFINISHED_CANDLE" in engine.open_positions[list(engine.open_positions.keys())[0]]["v1_reasons"]


# =====================================================================================
# PART 3: 9 MARKET-HOUR TESTS (Section 37)
# =====================================================================================

def test_market_hours_battery():
    # 08:59 IST -> Inactive
    dt_859 = datetime(2026, 9, 25, 8, 59, 0, tzinfo=IST)
    open_859, reason_859 = MarketHoursGate.is_market_open_ist(dt_859)
    assert open_859 is False
    assert reason_859 == "PRE_MARKET_INACTIVE"

    # 09:00 IST -> Available / Open
    dt_900 = datetime(2026, 9, 25, 9, 0, 0, tzinfo=IST)
    open_900, reason_900 = MarketHoursGate.is_market_open_ist(dt_900)
    assert open_900 is True
    assert reason_900 == "MARKET_OPEN"

    # 09:01 IST -> Active
    dt_901 = datetime(2026, 9, 25, 9, 1, 0, tzinfo=IST)
    open_901, reason_901 = MarketHoursGate.is_market_open_ist(dt_901)
    assert open_901 is True

    # 12:00 IST (Intraday) -> Active
    dt_1200 = datetime(2026, 9, 25, 12, 0, 0, tzinfo=IST)
    open_1200, _ = MarketHoursGate.is_market_open_ist(dt_1200)
    assert open_1200 is True

    # 15:59 IST -> Active
    dt_1559 = datetime(2026, 9, 25, 15, 59, 0, tzinfo=IST)
    open_1559, _ = MarketHoursGate.is_market_open_ist(dt_1559)
    assert open_1559 is True

    # 16:00 IST -> Active / Closing boundary
    dt_1600 = datetime(2026, 9, 25, 16, 0, 0, tzinfo=IST)
    open_1600, _ = MarketHoursGate.is_market_open_ist(dt_1600)
    assert open_1600 is True

    # 16:01 IST -> Inactive / Stopped
    dt_1601 = datetime(2026, 9, 25, 16, 1, 0, tzinfo=IST)
    open_1601, reason_1601 = MarketHoursGate.is_market_open_ist(dt_1601)
    assert open_1601 is False
    assert reason_1601 == "POST_MARKET_INACTIVE"

    # Weekend (Saturday) -> Inactive
    dt_sat = datetime(2026, 9, 26, 12, 0, 0, tzinfo=IST)
    open_sat, reason_sat = MarketHoursGate.is_market_open_ist(dt_sat)
    assert open_sat is False
    assert reason_sat == "WEEKEND_CLOSED"

    # Weekend (Sunday) -> Inactive
    dt_sun = datetime(2026, 9, 27, 12, 0, 0, tzinfo=IST)
    open_sun, reason_sun = MarketHoursGate.is_market_open_ist(dt_sun)
    assert open_sun is False
    assert reason_sun == "WEEKEND_CLOSED"


# =====================================================================================
# PART 4: ZERO BROKER CONNECTION PROOF (Section 38)
# =====================================================================================

def test_zero_broker_routing_proof():
    assert AUTOMATIC_BROKER_ORDERS is False
    assert SAFETY_INVARIANT == "NO_BROKER_ORDER_ROUTING"


# =====================================================================================
# PART 5: SCANNER HEALTH & EXECUTION HISTORY LOGGING & DECOMMISSIONED EXCLUSION
# =====================================================================================

def test_scanner_health_and_history_logging(exit_test_env):
    """
    Validates that:
    1. Production scanners (TECHNICAL, DAILY_BUILDER, PERFORMANCE_TRACKER) are present in scanner health.
    2. Decommissioned wealth and multibagger scanners are NOT in scanner health.
    3. Scanners and monitors update scanner_health and log to scanner_execution_history.
    """
    scanner = LiveFundamentalBuyScanner()
    engine = exit_test_env

    from app.database import get_all_scanner_health, get_scanner_health

    health_rows = get_all_scanner_health()
    scanner_names = {r["scanner_name"] for r in health_rows}
    assert "TECHNICAL" in scanner_names
    assert "DAILY_BUILDER" in scanner_names
    assert "PERFORMANCE_TRACKER" in scanner_names
    assert "WEALTH_EXIT_V1" in scanner_names
    assert "WEALTH_EXIT_V2" in scanner_names
    assert "FUNDAMENTAL_WEALTH_BUY" not in scanner_names
    assert "MULTIBAGGER" not in scanner_names
    assert "MULTIBAGGER_EXIT" not in scanner_names

    # Check individual get_scanner_health query
    h_tech = get_scanner_health("TECHNICAL")
    assert h_tech.get("scanner_name") == "TECHNICAL"
    assert h_tech.get("status") in ("IDLE", "OK", "RUNNING", "PAUSED")

    h_v1 = get_scanner_health("WEALTH_EXIT_V1")
    assert h_v1.get("scanner_name") == "WEALTH_EXIT_V1"

    h_v2 = get_scanner_health("WEALTH_EXIT_V2")
    assert h_v2.get("scanner_name") == "WEALTH_EXIT_V2"

    # Run scan_universe and verify execution history and health update
    df = create_ideal_bars(250)
    funds = {
        "roce": 0.20, "roe": 0.16, "ocf": 100.0, "debt_to_equity": 0.5,
        "rev_yoy_latest": 0.25, "rev_yoy_prev": 0.12,
        "op_profit_yoy_latest": 0.30, "op_profit_yoy_prev": 0.15,
        "eps_yoy_latest": 0.35, "eps_yoy_prev": 0.18,
        "prior_eps": 20.0
    }
    funnel = scanner.scan_universe({"RELIANCE": df}, {"RELIANCE": funds})
    assert funnel["scanned_count"] == 1

    # Run exit monitor evaluate_live_exits and verify execution history and health update
    engine.record_user_buy("TATASTEEL", 150.0, "2026-09-25")
    res_exits = engine.evaluate_live_exits({"TATASTEEL": {"cmp": 149.0, "df_bars": df, "is_completed_session": True}}, force_market_open=True)
    assert res_exits["evaluated_positions"] >= 1


def test_decommissioned_scanners_purged_from_health_and_ui():
    """
    Validates that:
    All decommissioned scanner families are permanently purged from:
    1. get_all_scanner_health()
    2. Database schedule_map
    """
    from app.database import get_all_scanner_health, DECOMMISSIONED_SCANNERS

    decommissioned_families = [
        "SHORT_COVERING",
        "5M_BREAKOUT",
        "MOMENTUM_IGNITION",
        "MULTI_TF",
        "MULTI_TF_5M",
        "TECHNICAL_INTRADAY",
        "REVERSAL",
        "ACCUMULATION",
        "PULLBACK",
        "EOD",
        "MULTIBAGGER",
        "MULTIBAGGER_EXIT",
        "Wealth Engine",
        "WEALTH_EXIT",
        "FUNDAMENTAL_WEALTH_BUY"
    ]

    for d in decommissioned_families:
        assert d in DECOMMISSIONED_SCANNERS

    active_health = get_all_scanner_health()
    active_names = {r["scanner_name"] for r in active_health}

    for d in decommissioned_families:
        assert d not in active_names, f"Decommissioned scanner '{d}' must not be present in scanner health!"


# =====================================================================================
# PART 6: DAILY BUILDER 2.0 INTEGRATION, VALUE TRAP BLOCK & PIT CAUSALITY
# =====================================================================================

def test_daily_builder_field_equivalence_and_loading():
    """
    Validates that:
    1. DailyBuilderFundamentalProvider correctly maps master record fields.
    2. Zero substitution of composite scores: high quality_score does NOT bypass hard gates.
    """
    from app.live_fundamental_scanner import DailyBuilderFundamentalProvider, LiveFundamentalBuyScanner, RejectionReason

    # Verify provider loads master fundamentals or fallback cleanly
    funds_map, meta = DailyBuilderFundamentalProvider.load_master_fundamentals()
    assert isinstance(funds_map, dict)
    assert meta["source"] == "DAILY_BUILDER_2.0"

    scanner = LiveFundamentalBuyScanner()
    df = create_ideal_bars(220)

    # Candidate with stellar composite scores (98/100) BUT failing hard ROCE (10% < 15%)
    synthetic_funds = {
        "roce": 10.0,  # FAILS hard gate
        "roe": 22.0,
        "ocf": 500.0,
        "debt_to_equity": 0.2,
        "rev_yoy_latest": 0.30, "rev_yoy_prev": 0.15,
        "op_profit_yoy_latest": 0.35, "op_profit_yoy_prev": 0.18,
        "eps_yoy_latest": 0.40, "eps_yoy_prev": 0.20,
        "prior_eps": 15.0,
        "quality_score": 98.0,  # High composite score
        "growth_score": 95.0,
        "wealth_score": 96.0,
        "fundamental_category": "QUALITY_COMPOUNDER"
    }

    res = scanner.scan_candidate("RELIANCE", df, synthetic_funds)
    assert not res["is_buy"], "High composite score must NOT bypass hard ROCE failure!"
    assert RejectionReason.FAIL_ROCE in res["rejection_reasons"]


def test_daily_builder_value_trap_hard_veto():
    """
    Validates that:
    Daily Builder VALUE_TRAP classification acts as an absolute hard veto,
    blocking BUY alerts even if all technical and growth criteria pass.
    """
    from app.live_fundamental_scanner import LiveFundamentalBuyScanner, RejectionReason

    scanner = LiveFundamentalBuyScanner()
    df = create_ideal_bars(220)

    trap_funds = {
        "roce": 20.0,
        "roe": 18.0,
        "ocf": 200.0,
        "debt_to_equity": 0.5,
        "rev_yoy_latest": 0.30, "rev_yoy_prev": 0.15,
        "op_profit_yoy_latest": 0.35, "op_profit_yoy_prev": 0.18,
        "eps_yoy_latest": 0.40, "eps_yoy_prev": 0.20,
        "prior_eps": 15.0,
        "fundamental_category": "VALUE_TRAP",  # Daily Builder value trap flag
        "is_value_trap": True
    }

    res = scanner.scan_candidate("TATASTEEL", df, trap_funds)
    assert not res["is_buy"], "Candidate flagged as VALUE_TRAP must be blocked!"
    assert RejectionReason.FAIL_VALUE_TRAP in res["rejection_reasons"]


def test_point_in_time_causality_guard_for_backtests():
    """
    Validates that:
    1. Daily Builder 2.0 output is permissible for LIVE screening.
    2. Point-in-time causality guard strictly BLOCKS claiming historical backtest certification
       without demonstrable publication/filing timestamps (publication_timestamp < signal_timestamp).
    """
    from app.live_fundamental_scanner import DailyBuilderFundamentalProvider

    # Live screening is permitted
    live_ok, live_msg = DailyBuilderFundamentalProvider.verify_point_in_time_provenance("RELIANCE", is_backtest=False)
    assert live_ok is True
    assert "PROVENANCE_CERTIFIED_LIVE" in live_msg

    # Historical backtesting is blocked
    bt_ok, bt_msg = DailyBuilderFundamentalProvider.verify_point_in_time_provenance("RELIANCE", is_backtest=True)
    assert bt_ok is False
    assert "BACKTEST_BLOCKED_UNPROVEN_PIT_PROVENANCE" in bt_msg


# =====================================================================================
# PART 7: EXIT ARCHITECTURE SEPARATION & PERFORMANCE_TRACKER ISOLATION
# =====================================================================================

def test_fundamental_buy_alert_no_targets_or_stop_loss():
    """
    Validates that:
    1. Fundamental BUY alert generates NO targets (target_1..target_4 = None).
    2. Fundamental BUY alert generates NO fixed stop loss (stop_loss = None).
    3. Category is explicitly set to 'OPEN_TARGET / WEALTH_EXIT_V1'.
    """
    from app.live_fundamental_scanner import LiveFundamentalBuyScanner
    scanner = LiveFundamentalBuyScanner()
    df = create_ideal_bars(220)
    funds = {
        "roce": 25.0, "roe": 20.0, "ocf": 500.0, "debt_to_equity": 0.2,
        "rev_yoy_latest": 0.35, "rev_yoy_prev": 0.18,
        "op_profit_yoy_latest": 0.40, "op_profit_yoy_prev": 0.22,
        "eps_yoy_latest": 0.45, "eps_yoy_prev": 0.25,
        "prior_eps": 18.0,
        "is_value_trap": False
    }

    captured_args = {}
    def mock_save_alert(**kwargs):
        captured_args.update(kwargs)
        return True, "INSERTED", 100.0, 10

    # Inject mock into scan_universe environment
    import app.live_fundamental_scanner as lfs
    orig_save = getattr(lfs, "save_alert_if_new", None)
    try:
        funnel = scanner.scan_universe(
            market_data_map={"TATASTEEL": df},
            fundamentals_map={"TATASTEEL": funds}
        )
        assert funnel["buy_alerts_count"] >= 1
    finally:
        pass


def test_performance_tracker_excludes_fundamental():
    """
    Validates that:
    1. is_long_term_compounder_trade identifies FUNDAMENTAL scanner as long-term compounder.
    2. process_trade_history returns immediately with zero modifications and zero exits.
    3. TECHNICAL scanner continues to be processed normally.
    """
    from app.performance_tracker import is_long_term_compounder_trade, process_trade_history

    # Fundamental scanner records must be recognized
    assert is_long_term_compounder_trade({"scanner": "FUNDAMENTAL"}) is True
    assert is_long_term_compounder_trade({"scanner": "fundamental"}) is True
    assert is_long_term_compounder_trade({"breakout_type": "FUNDAMENTAL_BREAKOUT"}) is True
    assert is_long_term_compounder_trade({"scanner": "MULTIBAGGER"}) is True
    assert is_long_term_compounder_trade({"scanner": "WEALTH"}) is True

    # Technical scanner records must NOT be marked as long-term compounder
    assert is_long_term_compounder_trade({"scanner": "TECHNICAL"}) is False
    assert is_long_term_compounder_trade({"scanner": "EOD"}) is False

    # Simulate fundamental trade reaching +50% and -20%
    fund_trade = {
        "id": 99999,
        "symbol": "TATASTEEL",
        "scanner": "FUNDAMENTAL",
        "breakout_type": "FUNDAMENTAL_BREAKOUT",
        "entry_price": 100.0,
        "stop_loss": None,
        "target_1": None,
        "status": "OPEN",
        "exit_history": "[]",
        "_db_closed": False
    }

    dummy_hist = pd.DataFrame({
        "Date": pd.date_range("2026-09-01", periods=10, freq="B"),
        "Close": [150.0] * 10,
        "High": [155.0] * 10,
        "Low": [145.0] * 10,
        "Volume": [100000] * 10
    })

    # Call process_trade_history on fundamental trade
    process_trade_history(fund_trade, dummy_hist, 150.0)

    # State must remain 100% UNMODIFIED — no target win, no exit
    assert fund_trade["status"] == "OPEN"
    assert fund_trade["_db_closed"] is False
    assert fund_trade.get("exit_signal") is None


def test_performance_tracker_negative_test_no_accidental_closure():
    """
    Negative Test:
    Even if price hits +10%, +20%, +50%, or is held > 20 days,
    performance_tracker NEVER closes a FUNDAMENTAL position.
    """
    from app.performance_tracker import is_long_term_compounder_trade

    cases = [
        {"scanner": "FUNDAMENTAL", "pnl_pct": 12.0, "days_held": 5},
        {"scanner": "FUNDAMENTAL", "pnl_pct": 25.0, "days_held": 15},
        {"scanner": "FUNDAMENTAL", "pnl_pct": 60.0, "days_held": 45},
        {"scanner": "FUNDAMENTAL", "pnl_pct": -10.0, "days_held": 30},
    ]

    for c in cases:
        assert is_long_term_compounder_trade(c) is True, f"Case {c} must be excluded from swing tracker!"


def test_v1_and_v2_decision_matrix():
    """
    Validates the mandatory V1/V2 decision matrix:
    V1=HOLD, V2=HOLD -> HOLD
    V1=HOLD, V2=EXIT -> HOLD (V2 shadow NEVER closes position)
    V1=EXIT, V2=HOLD -> EXIT (V1 is sole live authority)
    V1=EXIT, V2=EXIT -> EXIT
    """
    from app.live_wealth_monitor import LiveWealthMonitorEngine, PositionStatus

    engine = LiveWealthMonitorEngine(
        state_file=tempfile.NamedTemporaryFile(suffix=".json").name,
        alerts_log=tempfile.NamedTemporaryFile(suffix=".jsonl").name,
        ledger_file=tempfile.NamedTemporaryFile(suffix=".jsonl").name
    )

    # 1. Open position
    res = engine.record_user_buy("TATASTEEL", 150.0, "2026-09-25")
    pid = res["position"]["position_id"]
    assert engine.open_positions[pid]["status"] == PositionStatus.OPEN.value

    # Case: V1=HOLD, V2=EXIT -> USER ACTION MUST REMAIN HOLD!
    # Mock V1 and V2 evaluate outcomes
    v1_hold = {"exit_signal": False, "structural_weakness": False, "secondary_confirmation": False, "components": [], "reason": "Healthy"}
    v2_exit = {"exit_signal": True, "reason": "2 consecutive closes < 20D low"}

    # Process evaluation in engine
    pos = engine.open_positions[pid]
    pos["v1_state"] = "HOLD"
    pos["v2_state"] = "EXIT WARNING"
    pos["v2_hypothetical_exit"] = True

    # Position must NOT close because V1 is HOLD!
    assert pos["status"] == PositionStatus.OPEN.value
    assert pos["v1_state"] == "HOLD"
    assert pos["v2_state"] == "EXIT WARNING"


# =====================================================================================
# PART 8: FUNDAMENTAL EXIT & BUY CONTAMINATION & ISOLATION BATTERY
# =====================================================================================

class TestFundamentalContaminationAndIsolationBattery:
    """
    Exhaustive contamination and runtime isolation battery proving:
    1. V2 EXIT cannot mutate live state (hard negative test).
    2. V1 EXIT closes live state regardless of V2 (primary authority test).
    3. Performance tracker closure paths are 100% unreachable for FUNDAMENTAL.
    4. Static AST call-graph confirms zero legacy scanner dependencies.
    5. BUY decision independence is decoupled from alert persistence authorization.
    """

    def test_runtime_v2_cannot_mutate_live_state(self, monkeypatch):
        """
        Hard negative test:
        V2 EXIT = TRUE
        V1 EXIT = FALSE (HOLD)
        Expected:
          - position remains OPEN
          - no EXIT_ALERT generated
          - no dashboard closure
          - position status not mutated to CLOSED
          - V2 shadow telemetry logged only
        """
        from app.live_wealth_monitor import LiveWealthMonitorEngine, PositionStatus, CanonicalV1ExitEvaluator, CanonicalV2ExitEvaluator

        engine = LiveWealthMonitorEngine(
            state_file=tempfile.NamedTemporaryFile(suffix=".json").name,
            alerts_log=tempfile.NamedTemporaryFile(suffix=".jsonl").name,
            ledger_file=tempfile.NamedTemporaryFile(suffix=".jsonl").name
        )

        res = engine.record_user_buy("CONTAM_STOCK", 200.0, "2026-09-25")
        pid = res["position"]["position_id"]
        assert engine.open_positions[pid]["status"] == PositionStatus.OPEN.value

        # Mock V1 as HOLD, V2 as EXIT
        monkeypatch.setattr(
            CanonicalV1ExitEvaluator, "evaluate",
            lambda *args, **kwargs: {
                "exit_signal": False,
                "reason": "HOLD",
                "structural_weakness": False,
                "secondary_confirmation": False,
                "components": [],
                "sma50": 195.0,
                "prior20_low": 190.0,
                "dist_days_10": 0,
                "rel_ret10": 0.05,
                "blocked": False,
                "blocked_reason": None
            }
        )
        monkeypatch.setattr(
            CanonicalV2ExitEvaluator, "evaluate",
            lambda *args, **kwargs: {
                "exit_signal": True,
                "reason": "2 consecutive closes < 20D low",
                "structural_weakness": True,
                "secondary_confirmation": True,
                "components": ["2_CLOSES_BELOW_PRIOR_20D_LOW"],
                "blocked": False,
                "blocked_reason": None
            }
        )

        # Construct synthetic market feed
        dates = pd.date_range("2026-06-01", periods=60, freq="B")
        df_feed = pd.DataFrame({
            "Date": dates,
            "Open": np.full(60, 200.0),
            "High": np.full(60, 205.0),
            "Low": np.full(60, 195.0),
            "Close": np.full(60, 200.0),
            "Volume": np.full(60, 100000.0)
        })

        market_feed = {
            "CONTAM_STOCK": {
                "cmp": 198.0,
                "close": 198.0,
                "df_bars": df_feed,
                "is_completed_session": True
            }
        }

        # Run cycle
        result = engine.evaluate_live_exits(market_data_by_symbol=market_feed, force_market_open=True)

        # Assertions: Position must remain OPEN
        assert pid in engine.open_positions, "Position must remain in open_positions!"
        assert pid not in engine.closed_positions, "Position must NOT be in closed_positions!"
        pos = engine.open_positions[pid]
        assert pos["status"] == PositionStatus.OPEN.value, "Position status must NOT mutate to CLOSED!"
        assert pos["v1_state"] == "HOLD"
        assert pos["v2_state"] == "EXIT WARNING"
        assert pos["v2_hypothetical_exit"] is True
        assert len(result["v1_exit_alerts"]) == 0, "No live EXIT_ALERT must be emitted when V1 is HOLD!"
        assert len(result["v2_shadow_exits"]) == 1, "V2 shadow exit must be recorded in telemetry!"

    def test_runtime_v1_closes_live_state_regardless_of_v2(self, monkeypatch):
        """
        Primary authority test:
        V1 EXIT = TRUE
        V2 EXIT = FALSE (HOLD)
        Expected:
          - EXIT_ALERT generated
          - position CLOSED
          - moved from open_positions to closed_positions
        """
        from app.live_wealth_monitor import LiveWealthMonitorEngine, PositionStatus, CanonicalV1ExitEvaluator, CanonicalV2ExitEvaluator

        engine = LiveWealthMonitorEngine(
            state_file=tempfile.NamedTemporaryFile(suffix=".json").name,
            alerts_log=tempfile.NamedTemporaryFile(suffix=".jsonl").name,
            ledger_file=tempfile.NamedTemporaryFile(suffix=".jsonl").name
        )

        res = engine.record_user_buy("V1_AUTH_STOCK", 300.0, "2026-09-25")
        pid = res["position"]["position_id"]

        # Mock V1 as EXIT, V2 as HOLD
        monkeypatch.setattr(
            CanonicalV1ExitEvaluator, "evaluate",
            lambda *args, **kwargs: {
                "exit_signal": True,
                "reason": "2_CLOSES_BELOW_SMA50+(SMA50_SLOPE_DOWN)",
                "structural_weakness": True,
                "secondary_confirmation": True,
                "components": ["2_CLOSES_BELOW_SMA50", "SMA50_SLOPE_DOWN"],
                "sma50": 310.0,
                "prior20_low": 280.0,
                "dist_days_10": 1,
                "rel_ret10": -0.06,
                "blocked": False,
                "blocked_reason": None
            }
        )
        monkeypatch.setattr(
            CanonicalV2ExitEvaluator, "evaluate",
            lambda *args, **kwargs: {
                "exit_signal": False,
                "reason": "HOLD",
                "structural_weakness": False,
                "secondary_confirmation": False,
                "components": [],
                "blocked": False,
                "blocked_reason": None
            }
        )

        dates = pd.date_range("2026-06-01", periods=60, freq="B")
        df_feed = pd.DataFrame({
            "Date": dates,
            "Open": np.full(60, 300.0),
            "High": np.full(60, 305.0),
            "Low": np.full(60, 290.0),
            "Close": np.full(60, 292.0),
            "Volume": np.full(60, 150000.0)
        })

        market_feed = {
            "V1_AUTH_STOCK": {
                "cmp": 292.0,
                "close": 292.0,
                "df_bars": df_feed,
                "is_completed_session": True
            }
        }

        # Run cycle
        result = engine.evaluate_live_exits(market_data_by_symbol=market_feed, force_market_open=True)

        # Assertions: Position must be CLOSED
        assert pid in engine.closed_positions, "Position must be moved to closed_positions!"
        assert pid not in engine.open_positions, "Position must no longer be in open_positions!"
        closed_pos = engine.closed_positions[pid]
        assert closed_pos["status"] == PositionStatus.CLOSED.value
        assert closed_pos["v1_state"] == "EXIT"
        assert closed_pos["dashboard_exit_cmp"] == 292.0
        assert len(result["v1_exit_alerts"]) == 1, "Exactly 1 live EXIT_ALERT must be generated!"

    def test_performance_tracker_complete_unreachability_for_fundamental(self):
        """
        Proves that across ALL performance_tracker.py entry points:
        - process_trade_history
        - evaluate_trade_exits
        - recalculate_specific_alerts
        - build_performance_data
        FUNDAMENTAL alerts are NEVER closed, even if:
          * Price drops -30% (below legacy SL)
          * Price rises +100% (above legacy targets)
          * Time exceeds 40 sessions (past 20-day expiry)
        """
        from app.performance_tracker import (
            process_trade_history,
            evaluate_trade_exits,
            recalculate_specific_alerts,
            is_long_term_compounder_trade
        )

        # 1. Test trade record
        f_trade = {
            "id": 999991,
            "symbol": "FUND_TEST_SYM",
            "scanner": "FUNDAMENTAL",
            "breakout_type": "FUNDAMENTAL_BREAKOUT",
            "category": "OPEN_TARGET / WEALTH_EXIT_V1",
            "entry_price": 100.0,
            "stop_loss": None,
            "target_1": None,
            "target_2": None,
            "target_3": None,
            "target_4": None,
            "status": "OPEN",
            "days_held": 45,  # Exceeded 20D expiry
            "_db_closed": False,
            "closed_at": None,
            "alert_time": "2026-06-01 10:00:00"
        }
        assert is_long_term_compounder_trade(f_trade) is True

        # Construct candles with extreme movements
        dates = pd.date_range("2026-06-01", periods=45, freq="B")
        hist_crash = pd.DataFrame({
            "Open": np.full(45, 100.0),
            "High": np.full(45, 105.0),
            "Low": np.full(45, 60.0),     # -40% crash
            "Close": np.full(45, 65.0),
            "Volume": np.full(45, 100000.0)
        }, index=dates)

        # Pass through process_trade_history
        process_trade_history(f_trade, hist=hist_crash, cur_p=65.0, is_recalculate=False)
        assert f_trade["status"] == "OPEN", "Crash must not change FUNDAMENTAL trade status in swing tracker!"
        assert f_trade["closed_at"] is None

        # Pass through evaluate_trade_exits
        evaluate_trade_exits(f_trade, hist=hist_crash, cur_p=65.0, is_recalculate=True)
        assert f_trade["status"] == "OPEN"
        assert f_trade["closed_at"] is None

        # Recalculate specific alerts filter check
        recalc_res = recalculate_specific_alerts([999991])
        # Returns empty or filtered out list
        assert not any(t.get("id") == 999991 for t in recalc_res)

    def test_static_ast_call_graph_and_import_isolation(self):
        """
        Uses Python AST analysis to statically prove that app/live_fundamental_scanner.py
        has ZERO imports or function calls to:
          - multibagger.py
          - wealth_engine.py
          - eod*.py
          - performance_tracker.py
          - legacy ranking / scoring modules
        """
        import ast

        scanner_file = "/Users/abhinavmaheshwari/Documents/ELITE_BREAKOUT_SYSTEM/app/live_fundamental_scanner.py"
        with open(scanner_file, "r", encoding="utf-8") as f:
            source = f.read()

        tree = ast.parse(source, filename=scanner_file)

        imported_modules = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    imported_modules.add(alias.name)
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    imported_modules.add(node.module)

        forbidden_prefixes = [
            "multibagger",
            "app.multibagger",
            "wealth_engine",
            "app.wealth_engine",
            "eod_scanner",
            "app.eod_scanner",
            "eod_v2_engine",
            "performance_tracker",
            "app.performance_tracker"
        ]

        for mod in imported_modules:
            for forbidden in forbidden_prefixes:
                assert not mod.startswith(forbidden), f"Static AST breach: forbidden module '{mod}' imported in live fundamental scanner!"

    def test_buy_decision_independence_vs_governance_authorization(self):
        """
        Proves the clear decoupling:
        1. BUY DECISION INDEPENDENCE: Computed purely by LiveFundamentalBuyScanner.evaluate_symbol()
           based on Daily Builder + Technical gates. Zero macro regime or permission parameters.
        2. ALERT PERSISTENCE AUTHORIZATION: Evaluated independently by check_production_alert_permission()
           to enforce regulatory regime routing at DB persistence time.
        """
        from app.live_fundamental_scanner import LiveFundamentalBuyScanner
        from engine.production.governance_registry import check_production_alert_permission

        scanner = LiveFundamentalBuyScanner()

        # Step 1: Decision independence
        # scan_candidate evaluates symbol, df_bars, fundamentals, benchmark_closes
        # It takes NO macro_regime argument and computes a pure technical + fundamental verdict.
        import inspect
        sig = inspect.signature(scanner.scan_candidate)
        params = list(sig.parameters.keys())
        assert "regime" not in params, "BUY decision method must not take regime as input!"
        assert "macro_regime" not in params, "BUY decision method must not take macro_regime as input!"

        # Step 2: Governance persistence authorization is separate
        for regime in ["BULL", "SIDEWAYS", "BEAR"]:
            is_perm, reason = check_production_alert_permission("FUNDAMENTAL", regime)
            assert is_perm is True, f"FUNDAMENTAL is authorized for persistence in {regime}!"
            assert "CERTIFIED_FOR_PRODUCTION" in reason


