import pytest
from app.core.quality_engine import run_quality_engine

def test_quality_engine_perfect_score():
    data = {
        "operating_margin_ttm": 0.25,
        "gross_margin_stability": 0.01,
        "roce": 0.30,
        "cfo_pat_ratio": 1.2,
        "fcf_margin": 0.20,
        "revenue_cagr_3y": 0.20,
        "eps_cagr_3y": 0.25,
        "fcf_cagr_3y": 0.15,
        "reinvestment_rate": 0.50,
        "debt_equity": 0.1,
        "interest_coverage_ratio": 20.0,
        "debt_yoy_growth": -0.05,
        "altman_z": 4.0,
        "current_ratio": 2.5
    }
    weights = {
        "profitability": 0.30,
        "moat": 0.20,
        "capital_efficiency": 0.20,
        "cash_conversion": 0.15,
        "earnings_quality": 0.15
    }
    res = run_quality_engine("TEST", data, weights)
    assert res.score > 90.0
    assert res.confidence == 100.0

def test_quality_engine_negative_margins():
    data = {
        "operating_margin_ttm": -0.10,
        "gross_margin_stability": 0.15,
        "roce": -0.05,
        "cfo_pat_ratio": 0.5,
        "fcf_margin": -0.05,
        "revenue_cagr_3y": 0.05,
        "eps_cagr_3y": -0.10,
        "fcf_cagr_3y": -0.05,
        "reinvestment_rate": 0.10,
        "debt_equity": 1.5,
        "interest_coverage_ratio": 1.0,
        "debt_yoy_growth": 0.20,
        "altman_z": 1.0,
        "current_ratio": 0.8
    }
    weights = {
        "profitability": 0.30,
        "moat": 0.20,
        "capital_efficiency": 0.20,
        "cash_conversion": 0.15,
        "earnings_quality": 0.15
    }
    res = run_quality_engine("TEST", data, weights)
    assert res.score < 30.0

def test_quality_engine_missing_critical_data():
    # Only partial data
    data = {
        "operating_margin_ttm": 0.20,
        "roce": 0.15
    }
    weights = {
        "profitability": 0.30,
        "moat": 0.20,
        "capital_efficiency": 0.20,
        "cash_conversion": 0.15,
        "earnings_quality": 0.15
    }
    res = run_quality_engine("TEST", data, weights)
    assert res.confidence < 100.0
    assert len(res.missing_metrics) > 0
