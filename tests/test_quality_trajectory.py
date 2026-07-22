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
