import pytest
from app.core.technical_engine import run_buy_zone_engine

def test_buy_zone_engine_in_zone():
    data = {
        "price": 102.0,
        "sma_50": 100.0,
        "sma_200": 80.0,
        "atr": 5.0
    }
    res = run_buy_zone_engine("TEST", data)
    assert res.in_buy_zone is True
    assert "In ATR Buy Zone near SMA 50" in res.reason

def test_buy_zone_engine_extended():
    data = {
        "price": 120.0,
        "sma_50": 100.0,
        "sma_200": 80.0,
        "atr": 5.0
    }
    res = run_buy_zone_engine("TEST", data)
    assert res.in_buy_zone is False
    assert "Overextended" in res.reason

def test_buy_zone_engine_below_sma():
    data = {
        "price": 90.0,
        "sma_50": 100.0,
        "sma_200": 95.0, # Below SMA 200 fails
        "atr": 5.0
    }
    res = run_buy_zone_engine("TEST", data)
    assert res.in_buy_zone is False
    assert "Downtrend" in res.reason

def test_buy_zone_engine_missing_data():
    data = {
        "price": 100.0
    }
    res = run_buy_zone_engine("TEST", data)
    assert res.in_buy_zone is False
    assert "Missing Technicals" in res.reason
