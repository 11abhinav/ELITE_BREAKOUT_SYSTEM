"""
tests/test_score_calibration_and_attribution.py
Unit tests for Block-Bootstrap Score Calibration & Matched-Sample Filter Attribution
"""

import pytest
import numpy as np
import pandas as pd

from score_calibration_engine import ScoreCalibrationEngine


def test_score_calibration_distribution_metrics():
    """Verify distribution metrics and Block-Bootstrap confidence intervals."""
    # Synthetic dataset with 100 trades
    # 60 wins (+2.0R), 40 losses (-1.0R) -> Expected E[R] = (60*2 - 40*1)/100 = +0.8R
    r_data = ([2.0] * 60) + ([-1.0] * 40)
    df = pd.DataFrame({
        "realized_rr": r_data,
        "score": ([85.0] * 50) + ([75.0] * 50),
        "r1_hit_before_sl": [True] * 70 + [False] * 30,
        "r1_5_hit_before_sl": [True] * 65 + [False] * 35,
        "r2_hit_before_sl": [True] * 60 + [False] * 40,
        "max_favorable_excursion_r": [2.2] * 60 + [0.3] * 40,
        "max_adverse_excursion_r": [0.4] * 60 + [1.2] * 40
    })

    metrics = ScoreCalibrationEngine.compute_distribution_metrics(df)
    assert metrics["sample_size"] == 100
    assert metrics["win_rate_pct"] == 60.0
    assert metrics["expectancy_r"] == pytest.approx(0.8)
    assert metrics["ci_95_lower_r"] <= metrics["expectancy_r"] <= metrics["ci_95_upper_r"]
    assert metrics["r1_hit_rate_pct"] == 70.0
    assert metrics["r2_hit_rate_pct"] == 60.0


def test_matched_sample_attribution_delta():
    """Verify matched-sample attribution correctly calculates ΔE[R]."""
    # Cohort with filter: 40 wins (+2R), 10 losses (-1R) -> E[R] = (80 - 10)/50 = +1.4R
    # Cohort without filter: 20 wins (+2R), 30 losses (-1R) -> E[R] = (40 - 30)/50 = +0.2R
    r_with = ([2.0] * 40) + ([-1.0] * 10)
    r_without = ([2.0] * 20) + ([-1.0] * 30)

    df = pd.DataFrame({
        "realized_rr": r_with + r_without,
        "volume_filter_passed": ([True] * 50) + ([False] * 50)
    })

    res = ScoreCalibrationEngine.compute_matched_sample_attribution(df, "volume_filter_passed")
    assert res["expectancy_with"] == pytest.approx(1.4)
    assert res["expectancy_without"] == pytest.approx(0.2)
    assert res["delta_expectancy_r"] == pytest.approx(1.2)  # 1.4 - 0.2


def test_multiple_testing_correction():
    """Verify Benjamini-Hochberg FDR control adjusts significance threshold."""
    hyps = [
        {"hypothesis": "H1", "p_value": 0.005},
        {"hypothesis": "H2", "p_value": 0.045},
        {"hypothesis": "H3", "p_value": 0.15},
    ]

    adjusted = ScoreCalibrationEngine.apply_multiple_testing_correction(hyps, alpha=0.05)
    assert adjusted[0]["fdr_significant"] is True   # p=0.005 <= (1/3)*0.05 = 0.0167
    assert adjusted[1]["fdr_significant"] is False  # p=0.045 > (2/3)*0.05 = 0.0333
    assert adjusted[2]["fdr_significant"] is False  # p=0.15 > (3/3)*0.05 = 0.05
