#!/usr/bin/env python3
"""
UNIT & INTEGRATION TEST: TEMPORAL REPLICATION & REGIME ROBUSTNESS GATE
======================================================================
Validates multi-year cells, quarterly partitions, replication consistency checks,
concentration flagging, and governance promotion verdict.
"""

import os
import sys
import pytest
import numpy as np
import pandas as pd

# Add repo root to path
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from engine.production.temporal_replication_gate import (
    TemporalReplicationGate,
    DEFAULT_TEMPORAL_CELLS
)


@pytest.fixture
def synthetic_multi_year_trades():
    """Generates synthetic trades spanning 2017 to 2026 across multiple cells."""
    rng = np.random.default_rng(42)
    dates = []
    # 2017 (Cell 1), 2020 (Cell 2), 2023 (Cell 3), 2025 (Cell 4)
    years = [2017, 2018, 2020, 2021, 2023, 2024, 2025, 2026]
    trade_list = []

    for y in years:
        for m in [2, 5, 8, 11]:  # Q1, Q2, Q3, Q4
            for _ in range(25):
                d_str = f"{y}-{m:02d}-15"
                net_a = float(rng.normal(0.05, 0.5))
                net_b = float(net_a + rng.normal(0.06, 0.2))  # Consistent positive delta
                trade_list.append({
                    "symbol": rng.choice(["TCS", "INFY", "RELIANCE", "SBIN", "LT"]),
                    "signal_date": d_str,
                    "net_r_arm_a": net_a,
                    "net_r_arm_b": net_b
                })
    return pd.DataFrame(trade_list)


def test_temporal_cell_and_quarter_partitioning(synthetic_multi_year_trades):
    """Verify partitioning into pre-registered temporal cells and quarterly buckets."""
    # 1. Multi-Year Cells
    cells = TemporalReplicationGate.partition_into_temporal_cells(synthetic_multi_year_trades)
    assert len(cells) == len(DEFAULT_TEMPORAL_CELLS)
    assert "Cell_1_2016_2018" in cells
    assert "Cell_2_2019_2021" in cells
    assert "Cell_3_2022_2024" in cells
    assert "Cell_4_2025_2026" in cells
    assert len(cells["Cell_1_2016_2018"]) > 0

    # 2. Quarterly Buckets
    quarters = TemporalReplicationGate.partition_into_quarters(synthetic_multi_year_trades)
    assert set(quarters.keys()) == {"Q1", "Q2", "Q3", "Q4"}
    for q in ["Q1", "Q2", "Q3", "Q4"]:
        assert len(quarters[q]) > 0


def test_cell_metrics_and_replication_consistency(synthetic_multi_year_trades):
    """Verify cell-level statistics calculation and multi-cell replication consistency."""
    cells = TemporalReplicationGate.partition_into_temporal_cells(synthetic_multi_year_trades)
    cell_metrics = {}
    for cid, cdf in cells.items():
        cell_metrics[cid] = TemporalReplicationGate.calculate_cell_metrics(cdf)

    consistency = TemporalReplicationGate.evaluate_replication_consistency(cell_metrics)
    assert consistency["valid_cells_count"] == 4
    assert consistency["positive_cells_count"] >= 3
    assert consistency["is_robust"] is True
    assert consistency["status"] == "PASS"


def test_detection_of_concentrated_edge():
    """Verify that if one cell contributes >60% of total PnL, CONCENTRATED_EDGE is flagged."""
    # Construct results where Cell 1 has +1000R and Cells 2-4 have near 0
    skewed_results = {
        "Cell_1": {"trade_count": 100, "mean_net_r_b": 10.0, "total_pnl_r": 1000.0, "cell_passed": True},
        "Cell_2": {"trade_count": 100, "mean_net_r_b": 0.5, "total_pnl_r": 50.0, "cell_passed": False},
        "Cell_3": {"trade_count": 100, "mean_net_r_b": 0.2, "total_pnl_r": 20.0, "cell_passed": False},
        "Cell_4": {"trade_count": 100, "mean_net_r_b": -0.1, "total_pnl_r": -10.0, "cell_passed": False},
    }
    consistency = TemporalReplicationGate.evaluate_replication_consistency(skewed_results)
    assert "CONCENTRATED_EDGE" in consistency["flags"]
    assert consistency["is_robust"] is False


def test_detection_of_single_episode_edge():
    """Verify that if only 1 cell is positive, SINGLE_EPISODE_EDGE is flagged."""
    single_win_results = {
        "Cell_1": {"trade_count": 50, "mean_net_r_b": 0.5, "total_pnl_r": 25.0, "cell_passed": True},
        "Cell_2": {"trade_count": 50, "mean_net_r_b": -0.2, "total_pnl_r": -10.0, "cell_passed": False},
        "Cell_3": {"trade_count": 50, "mean_net_r_b": -0.3, "total_pnl_r": -15.0, "cell_passed": False},
        "Cell_4": {"trade_count": 50, "mean_net_r_b": -0.1, "total_pnl_r": -5.0, "cell_passed": False},
    }
    consistency = TemporalReplicationGate.evaluate_replication_consistency(single_win_results)
    assert "SINGLE_EPISODE_EDGE" in consistency["flags"]
    assert consistency["is_robust"] is False


def test_full_scanner_regime_temporal_gate(synthetic_multi_year_trades):
    """Test full workflow of evaluate_scanner_regime_temporal_gate and report generation."""
    # 1. Successful Case (Holdout passed + Temporal passed)
    res_pass = TemporalReplicationGate.evaluate_scanner_regime_temporal_gate(
        scanner_name="TECHNICAL",
        regime_name="BULL",
        trade_df=synthetic_multi_year_trades,
        holdout_arm_b_ci_low=0.10,
        holdout_delta_ci_low=0.03,
        holdout_perm_p=0.001
    )
    assert res_pass["governance_verdict"] == "CERTIFIED_FOR_PRODUCTION"

    # 2. Failed Holdout Case
    res_fail_holdout = TemporalReplicationGate.evaluate_scanner_regime_temporal_gate(
        scanner_name="TECHNICAL",
        regime_name="SIDEWAYS",
        trade_df=synthetic_multi_year_trades,
        holdout_arm_b_ci_low=-0.05,  # Holdout crosses zero!
        holdout_delta_ci_low=-0.02,
        holdout_perm_p=0.20
    )
    assert res_fail_holdout["governance_verdict"] == "NOT_CERTIFIED"

    # 3. Test Markdown Matrix Generation
    matrix_md = TemporalReplicationGate.generate_replication_matrix_markdown([res_pass, res_fail_holdout])
    assert "### TEMPORAL REPLICATION & REGIME ROBUSTNESS MATRIX" in matrix_md
    assert "TECHNICAL | BULL" in matrix_md
    assert "CERTIFIED_FOR_PRODUCTION" in matrix_md
    assert "NOT_CERTIFIED" in matrix_md


if __name__ == "__main__":
    pytest.main(["-v", __file__])
