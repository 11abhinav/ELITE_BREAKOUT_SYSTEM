import pytest
from app.quality_trajectory import compute_trajectory_score

def test_quality_trajectory_improving():
    data = {
        "roce_history": [10.0, 14.0, 18.0, 22.0],
        "roe_history": [12.0, 15.0, 18.0, 22.0],
        "opm_history": [10.0, 12.0, 14.0, 16.0],
        "de_history": [0.8, 0.6, 0.4, 0.2],
        "icr_history": [3.0, 5.0, 8.0, 12.0],
        "cfo_pat": 1.10
    }
    result = compute_trajectory_score(data)
    assert result["trajectory_score"] >= 18
    assert result["trajectory_grade"] == "A"
    assert "roce" in result["trajectory_details"]

def test_quality_trajectory_deteriorating():
    data = {
        "roce_history": [22.0, 18.0, 14.0, 10.0],
        "roe_history": [22.0, 18.0, 14.0, 10.0],
        "opm_history": [16.0, 14.0, 12.0, 8.0],
        "de_history": [0.2, 0.5, 0.8, 1.2],
        "icr_history": [12.0, 8.0, 4.0, 1.5],
        "cfo_pat": 0.40
    }
    result = compute_trajectory_score(data)
    assert result["trajectory_score"] < 10
    assert result["trajectory_grade"] == "D"

def test_quality_trajectory_missing_data():
    result = compute_trajectory_score({})
    assert result["trajectory_grade"] == "UNKNOWN"
    assert result["trajectory_details"]["status"] == "MISSING_DATA"

def test_quality_trajectory_with_none_values():
    data = {
        "roce_history": [None, 14.0, None, 22.0],
        "roe_history": [12.0, None, 18.0, None],
        "opm_history": [None],
        "de_history": [None],
        "icr_history": [None],
        "cfo_pat": None
    }
    result = compute_trajectory_score(data)
    assert "trajectory_grade" in result
    assert result["trajectory_grade"] in ("A", "B", "C", "D", "UNKNOWN")

def test_stable_elite_company_receives_grade_a():
    """Verify that a company with consistently elite flat fundamentals (slope=0) earns Grade A via level scoring."""
    data = {
        "roce_history": [35.0, 35.0, 35.0, 35.0],
        "roe_history": [30.0, 30.0, 30.0, 30.0],
        "opm_history": [25.0, 25.0, 25.0, 25.0],
        "de_history": [0.05, 0.05, 0.05, 0.05],
        "icr_history": [20.0, 20.0, 20.0, 20.0],
        "cfo_pat": 1.20
    }
    result = compute_trajectory_score(data)
    assert result["trajectory_score"] >= 18
    assert result["trajectory_grade"] == "A"

