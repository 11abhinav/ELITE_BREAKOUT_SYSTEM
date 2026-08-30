"""
Unit tests for engine/analytics/stats_contract.py.
Verifies Wilson CI boundary conditions, Cluster Bootstrap reproducibility & fallback,
sample size eligibility, three-tier R metrics, and 2x2 confusion matrix calculations.
"""

import pytest
import numpy as np
import pandas as pd
from engine.analytics.stats_contract import (
    wilson_score_ci,
    cluster_bootstrap_ci,
    calculate_confusion_matrix,
    evaluate_sample_eligibility,
    compute_three_tier_r
)


def test_wilson_score_ci_boundaries():
    # 0/14 wins
    res_0 = wilson_score_ci(0, 14, confidence=0.95)
    assert res_0.point_estimate == 0.0
    assert res_0.ci_lower == 0.0
    assert res_0.ci_upper > 0.0
    assert res_0.ci_method == "WILSON_SCORE"

    # 14/14 wins
    res_14 = wilson_score_ci(14, 14, confidence=0.95)
    assert res_14.point_estimate == 1.0
    assert res_14.ci_lower < 1.0
    assert res_14.ci_upper == 1.0
    assert 0.75 < res_14.ci_lower < 0.85  # ~78.5%

    # Empty / zero trials
    res_empty = wilson_score_ci(0, 0)
    assert res_empty.point_estimate == 0.0
    assert res_empty.n_observations == 0


def test_cluster_bootstrap_reproducibility():
    # Generate clustered synthetic data
    np.random.seed(123)
    clusters = [f"SYM{i}" for i in range(10)]
    data = []
    for c in clusters:
        n_rows = np.random.randint(1, 4)
        for _ in range(n_rows):
            r = np.random.normal(1.0, 0.5)
            data.append({"cluster": c, "realized_r": r})

    df = pd.DataFrame(data)

    def mean_r(d):
        return float(d["realized_r"].mean())

    # Run cluster bootstrap twice with identical seed
    res1 = cluster_bootstrap_ci(df, "cluster", mean_r, n_resamples=50, random_seed=42)
    res2 = cluster_bootstrap_ci(df, "cluster", mean_r, n_resamples=50, random_seed=42)

    assert res1.point_estimate == res2.point_estimate
    assert res1.ci_lower == res2.ci_lower
    assert res1.ci_upper == res2.ci_upper
    assert res1.ci_method in ["BCa", "PERCENTILE_FALLBACK"]
    assert res1.n_clusters == len(df["cluster"].unique())


def test_cluster_bootstrap_fallback():
    # Degenerate case with zero variance
    df = pd.DataFrame([{"cluster": f"C_{i}", "val": 2.0} for i in range(10)])

    res = cluster_bootstrap_ci(df, "cluster", lambda d: float(d["val"].mean()), n_resamples=100)
    assert res.ci_degraded is True
    assert res.point_estimate == 2.0
    assert res.ci_lower == 2.0
    assert res.ci_upper == 2.0


def test_sample_eligibility():
    res_eligible = evaluate_sample_eligibility(70, threshold=50)
    assert res_eligible["status"] == "ELIGIBLE"
    assert res_eligible["is_inferable"] is True

    res_insufficient = evaluate_sample_eligibility(14, threshold=50)
    assert res_insufficient["status"] == "INSUFFICIENT_SAMPLE"
    assert res_insufficient["is_inferable"] is False


def test_three_tier_r():
    # Entry: 100, Exit: 110, SL: 95 -> Risk: 5 -> Gross R: +2.0R
    r_dict = compute_three_tier_r(
        entry_price=100.0,
        exit_price=110.0,
        sl_price=95.0,
        friction_r=0.05,
        position_size_multiplier=0.5
    )
    assert r_dict["gross_trade_R"] == 2.0
    assert r_dict["net_trade_R"] == 1.95
    assert r_dict["portfolio_weighted_R"] == 0.975


def test_confusion_matrix():
    is_winner = pd.Series([True, True, True, False, False, False])
    pass_filter = pd.Series([True, True, False, True, False, False])
    realized_r = pd.Series([2.0, 1.5, 1.0, -1.0, -1.0, -1.0])

    cm = calculate_confusion_matrix(is_winner, pass_filter, realized_r)
    assert cm.true_positives == 2
    assert cm.false_positives == 1
    assert cm.false_negatives == 1
    assert cm.true_negatives == 2
    assert cm.winner_retention_pct == pytest.approx(66.67, rel=1e-2)
    assert cm.loser_recall_pct == pytest.approx(66.67, rel=1e-2)
    assert cm.trade_retention_pct == pytest.approx(50.0, rel=1e-2)
    assert cm.delta_expected_r > 0
