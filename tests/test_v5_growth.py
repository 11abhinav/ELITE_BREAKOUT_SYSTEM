import pytest
from app.core.growth_engine import run_growth_engine

def test_growth_engine_high_growth():
    data = {
        "revenue_cagr_3y": 0.25,
        "eps_cagr_3y": 0.30,
        "fcf_cagr_3y": 0.20,
        "reinvestment_rate": 0.60,
        "roce": 0.30
    }
    res = run_growth_engine("TEST", data)
    assert res.score > 90.0
    assert res.confidence == 100.0

def test_growth_engine_negative_growth():
    data = {
        "revenue_cagr_3y": -0.05,
        "eps_cagr_3y": -0.10,
        "fcf_cagr_3y": -0.15,
        "reinvestment_rate": 0.10,
        "roce": -0.05
    }
    res = run_growth_engine("TEST", data)
    assert res.score <= 30.0
    assert res.confidence == 100.0

def test_growth_engine_missing_data():
    data = {
        "revenue_cagr_3y": 0.15
    }
    res = run_growth_engine("TEST", data)
    # Since EPS growth and others are missing, score should be partial and confidence dropped
    assert res.confidence < 100.0
    assert len(res.missing_metrics) > 0
