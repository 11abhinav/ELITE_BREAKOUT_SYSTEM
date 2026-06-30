import pytest
from app.core.valuation_engine import run_valuation_engine

def test_valuation_engine_fair_value():
    data = {
        "eps": 10.0,
        "book_value_per_share": 50.0,
        "free_cash_flow": 1000000.0,
        "shares_outstanding": 100000.0, # FCF/share = 10.0
        "price": 100.0,
        "tt_indpe": 20.0,
        "eps_cagr_3y": 0.15,
        "ebit": 1500000.0
    }
    weights = {
        "dcf": 0.35,
        "peer_relative": 0.25,
        "graham": 0.15,
        "earnings_power": 0.15,
        "asset_value": 0.10
    }
    res = run_valuation_engine("TEST", data, weights)
    
    assert res.fair_value > 0
    assert res.margin_of_safety is not None
    # DCF will be roughly 10 * something, Graham will be around 100, etc.
    assert res.confidence == 100.0

def test_valuation_engine_negative_earnings():
    data = {
        "eps": -5.0,
        "book_value_per_share": 20.0,
        "free_cash_flow": -100000.0,
        "shares_outstanding": 100000.0,
        "price": 50.0,
        "tt_indpe": 15.0,
        "eps_cagr_3y": -0.10,
        "ebit": -50000.0
    }
    weights = {
        "dcf": 0.35,
        "peer_relative": 0.25,
        "graham": 0.15,
        "earnings_power": 0.15,
        "asset_value": 0.10
    }
    res = run_valuation_engine("TEST", data, weights)
    # Valuation models typically return 0 for negative inputs
    assert res.fair_value <= 50.0 # Some fallback might hit
    assert res.margin_of_safety < 0 or res.score < 20.0

def test_valuation_engine_missing_data():
    data = {
        "price": 100.0
    }
    weights = {
        "dcf": 0.35,
        "peer_relative": 0.25,
        "graham": 0.15,
        "earnings_power": 0.15,
        "asset_value": 0.10
    }
    res = run_valuation_engine("TEST", data, weights)
    assert res.confidence < 100.0
    assert len(res.missing_metrics) > 0
    assert res.fair_value == 0.0
