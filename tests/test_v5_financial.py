import pytest
from app.core.financial_strength_engine import run_financial_strength_engine

def test_financial_engine_standard_company():
    data = {
        "is_financial": False,
        "debt_equity": 0.5,
        "interest_coverage_ratio": 10.0,
        "debt_yoy_growth": -0.05,
        "altman_z": 3.5,
        "current_ratio": 2.0
    }
    res = run_financial_strength_engine("TEST", data)
    assert res.score > 80.0
    assert res.confidence == 100.0

def test_financial_engine_financial_company():
    # Banks carry huge debt_equity normally. The engine should not penalize them
    data = {
        "is_financial": True,
        "capital_adequacy_ratio": 0.20, # Excellent CAR
        "gross_npa": 0.01, # Excellent GNPA
        "net_npa": 0.002, # Excellent NNPA
        "liquidity_coverage_ratio": 150.0,
        "altman_z": None # Skipped
    }
    res = run_financial_strength_engine("TEST", data)
    assert res.score == 100.0 # Excellent bank metrics now get full score

def test_financial_engine_poor_standard_company():
    data = {
        "is_financial": False,
        "debt_equity": 2.5,
        "interest_coverage_ratio": 1.0,
        "debt_yoy_growth": 0.50,
        "altman_z": 1.0,
        "current_ratio": 0.5
    }
    res = run_financial_strength_engine("TEST", data)
    assert res.score < 30.0

def test_financial_engine_missing_data():
    data = {
        "is_financial": False,
        "debt_equity": 0.5
    }
    res = run_financial_strength_engine("TEST", data)
    assert res.confidence < 100.0
    assert len(res.missing_metrics) > 0
