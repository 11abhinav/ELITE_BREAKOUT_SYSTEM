import pytest
from app.core.market_structure_engine import run_market_structure_engine

def test_market_structure_strong_trend():
    data = {
        "rs_rating": 85.0,
        "relative_volume_10d": 1.5,
        "pct_from_52w_high": -0.05
    }
    res = run_market_structure_engine("TEST", data)
    assert res.score > 80.0
    assert res.confidence == 100.0

def test_market_structure_weak_trend():
    data = {
        "rs_rating": 20.0,
        "relative_volume_10d": 0.5,
        "pct_from_52w_high": -0.50
    }
    res = run_market_structure_engine("TEST", data)
    assert res.score < 30.0

def test_market_structure_missing_data():
    data = {
        "rs_rating": 85.0
    }
    res = run_market_structure_engine("TEST", data)
    assert res.confidence < 100.0
    assert len(res.missing_metrics) > 0
