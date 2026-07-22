import pytest
from unittest.mock import patch, MagicMock
from app.outcome_tracker import compute_advanced_outcome_analytics, _metric_confidence, _compute_metrics_block

def test_metric_confidence_thresholds():
    assert _metric_confidence(10) == "LOW"
    assert _metric_confidence(19) == "LOW"
    assert _metric_confidence(20) == "MEDIUM"
    assert _metric_confidence(49) == "MEDIUM"
    assert _metric_confidence(50) == "HIGH"
    assert _metric_confidence(100) == "HIGH"

def test_compute_metrics_block_empty():
    res = _compute_metrics_block([])
    assert res["trades"] == 0
    assert res["win_rate_pct"] == 0.0
    assert res["avg_realized_r"] == 0.0
    assert res["expectancy_r"] == 0.0
    assert res["capture_efficiency_pct"] == 0.0
    assert res["confidence"] == "LOW"

def test_compute_metrics_block_populated():
    rows = [
        {"realized_rr": 2.0, "max_favorable_excursion_r": 3.0, "max_adverse_excursion_r": 0.5, "exit_reason": "T1_HIT"},
        {"realized_rr": 1.5, "max_favorable_excursion_r": 2.0, "max_adverse_excursion_r": 0.2, "exit_reason": "T1_HIT"},
        {"realized_rr": -1.0, "max_favorable_excursion_r": 0.2, "max_adverse_excursion_r": 1.0, "exit_reason": "SL_HIT"},
    ]
    res = _compute_metrics_block(rows)
    assert res["trades"] == 3
    assert res["win_rate_pct"] == 66.7
    assert res["avg_realized_r"] == round((2.0 + 1.5 - 1.0) / 3, 2)
    assert res["avg_mfe_r"] == round((3.0 + 2.0 + 0.2) / 3, 2)
    assert res["confidence"] == "LOW"

@patch("app.outcome_tracker.get_connection")
def test_compute_advanced_outcome_analytics_mock_db(mock_conn):
    mock_cursor = MagicMock()
    mock_conn.return_value.__enter__.return_value.cursor.return_value.__enter__.return_value = mock_cursor
    
    # 3 mock closed trades
    mock_cursor.fetchall.return_value = [
        (1, "RELIANCE.NS", "EOD", "BULL", 85.0, 75, 10, 8, 92.0, "BANK", 2.0, "T1_HIT", 2.0, 2.5, 0.3, "2026-07-22", "2026-07-20", 93),
        (2, "TCS.NS", "EOD", "BULL", 80.0, 70, 0, 0, 45.0, "IT", 1.8, "SL_HIT", -1.0, 0.4, 1.0, "2026-07-22", "2026-07-20", 70),
        (3, "INFY.NS", "MULTI_TF", "BULL", 88.0, 80, 10, 8, 88.0, "IT", 2.5, "T1_HIT", 1.5, 2.0, 0.1, "2026-07-22", "2026-07-21", 98),
    ]

    analytics = compute_advanced_outcome_analytics()

    assert analytics["total_completed_trades"] == 3
    assert analytics["overall_confidence"] == "LOW"
    assert analytics["is_preview_mode"] is True
    assert "snapshot_coverage_pct" in analytics
    assert "overall_metrics" in analytics
    assert "feature_attribution" in analytics
    assert "score_bands" in analytics
    assert "rolling_validation" in analytics

    # Test RS Attribution
    rs_ge_80 = analytics["feature_attribution"]["relative_strength"]["rs_ge_80"]
    assert rs_ge_80["trades"] == 2
    assert rs_ge_80["win_rate_pct"] == 100.0
