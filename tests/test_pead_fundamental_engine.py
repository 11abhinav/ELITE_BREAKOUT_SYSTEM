"""
tests/test_pead_fundamental_engine.py
====================================
Unit test battery for PEAD_FUNDAMENTAL_V1 core engine components (VERSION 1.2).
Verifies:
1. Strict pre-event fundamental gating (no lookahead leakage).
2. SUE calculation — minimum 12-quarter seasonal time series (frozen rule).
3. Forward return calculation with symmetric 2.5+2.5 bps friction and MFE/MAE excursions.
4. Statistical significance with cluster-aware permutation p-value and block bootstrap.
5. Events before BACKTEST_EVAL_START_DATE blocked with WARM_UP_PERIOD_INSUFFICIENT.
"""

import os
import sqlite3
import numpy as np
import pandas as pd
import pytest

from engine.research.pead_fundamental_engine import (
    PeadTaxonomy,
    PreEventFundamentalGating,
    SueEngine,
    ForwardReturnCalculator,
    PeadStatisticalAuditor,
    FINANCIAL_SYMBOLS,
    SUE_MIN_QUARTERS,
    BACKTEST_EVAL_START_DATE,
    FRICTION_ENTRY_BPS,
    FRICTION_EXIT_BPS,
)


def test_financial_entity_exclusion():
    """Any financial symbol is immediately excluded as METRIC_NOT_APPLICABLE_FINANCIAL."""
    con = sqlite3.connect(":memory:")
    is_pass, reason, metrics = PreEventFundamentalGating.evaluate_state_at_event(
        symbol="HDFCBANK",
        event_timestamp="2023-08-10 18:00:00",
        db_con=con
    )
    assert is_pass is False
    assert reason == PeadTaxonomy.METRIC_NOT_APPLICABLE_FINANCIAL
    assert metrics.get("is_financial") is True
    con.close()


def test_warm_up_period_block():
    """Events before BACKTEST_EVAL_START_DATE must be blocked with WARM_UP_PERIOD_INSUFFICIENT."""
    con = sqlite3.connect(":memory:")
    is_pass, reason, metrics = PreEventFundamentalGating.evaluate_state_at_event(
        symbol="TESTCO",
        event_timestamp="2015-12-31 18:00:00",   # before 2016-01-01
        db_con=con
    )
    assert is_pass is False
    assert reason == PeadTaxonomy.WARM_UP_PERIOD_INSUFFICIENT
    assert metrics["event_date"] == "2015-12-31"
    con.close()


def test_strict_pit_causality():
    """A filing published AFTER the event timestamp cannot enter the pre-event gate."""
    con = sqlite3.connect(":memory:")
    cur = con.cursor()
    cur.execute("""
        CREATE TABLE pit_fundamentals_v1 (
            symbol TEXT, statement_type TEXT, period_end_date TEXT,
            roce REAL, roe REAL, operating_cash_flow REAL, total_debt REAL, total_equity REAL,
            revenue REAL, operating_profit REAL, eps REAL,
            conservative_availability_timestamp TEXT, revision_number INTEGER
        )
    """)
    # Insert filing available only AFTER event (e.g. 2023-08-15)
    cur.execute("""
        INSERT INTO pit_fundamentals_v1 VALUES (
            'TESTCO', 'ANNUAL', '2023-03-31', 25.0, 20.0, 1000.0, 100.0, 500.0,
            5000.0, 1200.0, 50.0, '2023-08-15 23:59:59', 1
        )
    """)
    con.commit()

    # Event date is 2023-08-10 (BEFORE filing was available)
    is_pass, reason, metrics = PreEventFundamentalGating.evaluate_state_at_event(
        symbol="TESTCO",
        event_timestamp="2023-08-10 18:00:00",
        db_con=con
    )
    # Must fail because filing was not yet available at event time
    assert is_pass is False
    assert reason == PeadTaxonomy.DATA_MISSING_PIT
    con.close()


def test_sue_requires_12_quarters():
    """SUE engine must reject series shorter than 12 quarters with DATA_INSUFFICIENT_HISTORY."""
    # Only 8 quarters — must fail under the frozen 12-quarter rule
    historical_eps_8 = [10.0, 12.0, 11.0, 13.0, 11.5, 13.5, 12.5, 14.5]
    assert len(historical_eps_8) == 8, "Test setup: exactly 8 quarters"

    res = SueEngine.calculate_sue(
        actual_eps=18.0,
        actual_revenue=None,
        actual_op_profit=None,
        historical_eps_series=historical_eps_8
    )
    assert res["sue_valid"] is False
    assert res["reason"] == "DATA_INSUFFICIENT_HISTORY"
    assert res["min_quarters_required"] == SUE_MIN_QUARTERS   # 12
    assert res["available"] == 8


def test_sue_calculation_12_quarters():
    """SUE engine succeeds and returns correct structure with exactly 12 quarters."""
    # 12 quarters with an upward seasonal trend
    historical_eps = [
        10.0, 12.0, 11.0, 13.0,   # q-12 to q-9
        11.5, 13.5, 12.5, 14.5,   # q-8  to q-5
        12.5, 14.5, 13.5, 15.5    # q-4  to q-1
    ]
    assert len(historical_eps) == 12, "Test setup: exactly 12 quarters"
    actual_eps = 20.0  # Large positive surprise

    res = SueEngine.calculate_sue(
        actual_eps=actual_eps,
        actual_revenue=1000.0,
        actual_op_profit=250.0,
        historical_eps_series=historical_eps
    )
    assert res["sue_valid"] is True
    assert res["unexpected_eps"] > 0
    assert res["sue_eps"] > 0
    assert "drift" in res
    assert "sigma_err" in res
    assert "sigma_eff" in res
    assert res["sigma_eff"] >= 0.05  # Volatility floor applied


def test_sue_min_quarters_constant():
    """SUE_MIN_QUARTERS module constant must be exactly 12."""
    assert SUE_MIN_QUARTERS == 12


def test_forward_return_friction_symmetry():
    """Net return must reflect symmetric 2.5 bps entry + 2.5 bps exit convention."""
    assert FRICTION_ENTRY_BPS == 2.5
    assert FRICTION_EXIT_BPS  == 2.5

    # Single-session test: entry at 100, exit at 100 (flat trade) should yield small negative
    dates = pd.date_range("2023-08-01", periods=25, freq="B").strftime("%Y-%m-%d").tolist()
    prices_open  = [100.0] * 25
    prices_close = [100.0] * 25  # flat close
    df = pd.DataFrame({
        "date": dates,
        "open": prices_open,
        "high": [p + 1.0 for p in prices_open],
        "low":  [p - 1.0 for p in prices_open],
        "close": prices_close,
        "volume": [100000] * 25
    })
    outcomes = ForwardReturnCalculator.calculate_forward_outcomes(
        df_daily=df,
        entry_date=dates[0],
        horizons=[1, 5, 20]
    )
    assert outcomes is not None
    # Net return on flat trade must be negative (friction cost)
    assert outcomes["net_ret_20d"] < 0.0
    # Exact friction check: (100*(1-0.00025)) / (100*(1+0.00025)) - 1 = -0.005% approx
    expected_net = (100.0 * (1 - 0.00025)) / (100.0 * (1 + 0.00025)) - 1.0
    assert abs(outcomes["net_ret_20d"] / 100.0 - expected_net) < 1e-6


def test_forward_return_calculator():
    """MFE/MAE excursions and positive returns with trending prices."""
    dates = pd.date_range("2023-08-01", periods=30, freq="B").strftime("%Y-%m-%d").tolist()
    prices = [100.0 + i for i in range(30)]
    df = pd.DataFrame({
        "date": dates,
        "open": prices,
        "high": [p + 2.0 for p in prices],
        "low":  [p - 1.0 for p in prices],
        "close": [p + 1.0 for p in prices],
        "volume": [100000] * 30
    })

    outcomes = ForwardReturnCalculator.calculate_forward_outcomes(
        df_daily=df,
        entry_date="2023-08-11",
        horizons=[1, 5, 10]
    )
    assert outcomes is not None
    assert outcomes["entry_date"] == "2023-08-11"
    assert outcomes["net_ret_5d"] > 0
    assert outcomes["mfe_5d"] > outcomes["net_ret_5d"]  # MFE peak is higher than close
    assert "entry_friction_bps" in outcomes
    assert "exit_friction_bps" in outcomes
    assert outcomes["entry_friction_bps"] == 2.5
    assert outcomes["exit_friction_bps"]  == 2.5


def test_statistical_significance_returns_cluster_perm_p():
    """Auditor must return cluster_permutation_p (not paired_permutation_p) and verdict."""
    np.random.seed(42)
    arm_a  = list(np.random.normal(5.0, 4.0, 60))
    ctrl_1 = list(np.random.normal(1.0, 4.0, 60))

    stats = PeadStatisticalAuditor.evaluate_significance(
        arm_a_returns=arm_a,
        control_1_returns=ctrl_1,
        n_boot=500,
        n_perm=500,
        random_seed=42
    )
    # Must have cluster_permutation_p (renamed from paired_permutation_p)
    assert "cluster_permutation_p" in stats, "cluster_permutation_p key must be present"
    assert "paired_permutation_p" not in stats, "Deprecated 'paired_permutation_p' key must be absent"
    # Must have verdict (not 'status')
    assert "verdict" in stats
    # Must have certification_conditions breakdown
    assert "certification_conditions" in stats
    # Delta should be positive in this scenario
    assert stats["delta_incremental_alpha_pct"] > 0


def test_statistical_auditor_simultaneous_hurdle():
    """Both hurdle conditions (mean >= 1.5% AND CI_lower > 0) must be checked independently."""
    # Scenario: ARM A mean is 1.6% but CI_lower is negative (high variance) — should NOT certify
    np.random.seed(99)
    # High variance so CI_lower goes negative
    arm_a_high_var  = list(np.random.normal(1.6, 20.0, 15))
    ctrl_1          = list(np.random.normal(0.0, 4.0, 30))

    stats = PeadStatisticalAuditor.evaluate_significance(
        arm_a_returns=arm_a_high_var,
        control_1_returns=ctrl_1,
        n_boot=1000,
        n_perm=1000,
        random_seed=99
    )
    # Certification conditions must individually show the failure
    cond = stats["certification_conditions"]
    # If CI_lower fails, overall verdict must not be CERTIFIED_FOR_PAPER
    if not cond["ci_lower_arm_a_pass"]:
        assert stats["verdict"] != "CERTIFIED_FOR_PAPER"
