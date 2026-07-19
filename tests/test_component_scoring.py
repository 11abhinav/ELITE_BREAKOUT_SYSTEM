import pytest
import pandas as pd
from app.scoring_engine import calculate_score
from tests.factories import make_price_history

@pytest.fixture
def base_history():
    # Build a healthy 100-day price history
    df = make_price_history("TEST.NS") \
        .with_base_price(100.0) \
        .build()
        
    # Add technicals
    df["Close"] = 101.5
    df["RSI"] = 65.0
    df["Volume"] = 100_000
    df["EMA20"] = 98.0
    df["SMA50"] = 95.0
    df["SMA200"] = 90.0
    df["ADX"] = 35.0
    df["MACD"] = 2.0
    df["MACD_SIGNAL"] = 1.0
    df["HIGH_52W"] = 105.0
    df["BASE_WIDTH"] = 10.0
    df["BB_UPPER"] = 102.0
    return df

@pytest.fixture
def latest_row(base_history):
    return base_history.iloc[-1].to_dict()

def test_scoring_deterministic_happy_path(base_history, latest_row):
    """
    Input: Fixed indicators showing a strong breakout.
    Expected: Deterministic, high score.
    Decision/Reason: Score should evaluate trend, volume, and signals.
    """
    score, version, weights = calculate_score(
        category="High Momentum",
        breakout_count=2,
        rsi=65.0,
        volume_ratio=3.5,
        breakout_signals={"52W_High": 10.0, "Volume_Spike": 5.0},
        ticker=base_history,
        latest=latest_row,
        symbol="TEST.NS",
        timeframe="1d",
        atr_val=2.5,
        delivery_pct=65.0
    )
    
    # Input was valid, output must be consistent
    assert score > 0, "Valid breakout should produce a positive score"
    assert score > 50, "Strong setup should score highly"
    assert version == "v1"

def test_scoring_below_threshold_rejection(base_history, latest_row):
    """
    Input: Setup with ADX < threshold (trend too weak).
    Expected: Rejected (score = 0).
    Decision/Reason: Hard disqualifier triggered.
    """
    # Mutate the history to have terrible ADX
    base_history["ADX"] = 10.0
    latest_row["ADX"] = 10.0
    
    score, version, weights = calculate_score(
        category="High Momentum",
        breakout_count=1,
        rsi=65.0,
        volume_ratio=3.0,
        ticker=base_history,
        latest=latest_row,
        timeframe="1d"
    )
    
    assert score == 0, "ADX below 25 on 1d should trigger hard disqualifier"

def test_scoring_maximum_boundary(base_history, latest_row):
    """
    Input: Absolutely perfect setup with all bonuses.
    Expected: Score is capped at 100.
    """
    # Maximize all bonuses
    latest_row["RSI"] = 65.0 # Sweet spot
    latest_row["HIGH_52W"] = 101.0
    latest_row["BASE_WIDTH"] = 2.0
    
    score, version, weights = calculate_score(
        category="High Momentum",
        breakout_count=5,
        rsi=65.0,
        volume_ratio=10.0,
        breakout_signals={"52W_High": 12.0, "Monthly": 12.0, "Volume": 12.0},
        ticker=base_history,
        latest=latest_row,
        timeframe="1d",
        atr_val=1.5, # 1.0 to 2.0 ATR move
        delivery_pct=80.0
    )
    
    assert score == 100, "Perfect setup should be capped exactly at 100"

def test_scoring_minimum_boundary(base_history, latest_row):
    """
    Input: Marginal setup with multiple penalties.
    Expected: Score can drop significantly but never goes below 0 after disqualifiers.
    """
    latest_row["RSI"] = 80.0  # Overbought penalty
    latest_row["Close"] = 110.0  # Extended above SMA50 penalty
    base_history.loc[base_history.index[-1], "Close"] = 110.0
    base_history["BASE_WIDTH"] = 50.0  # Choppy penalty
    
    # We still need to pass hard disqualifiers to see the penalty math
    # BB_UPPER condition requires volume conviction
    latest_row["BB_UPPER"] = 108.0 
    volume_ratio = 2.5 # Passes BB_UPPER check on 1d
    
    score, version, weights = calculate_score(
        category="Unknown",
        breakout_count=0,
        rsi=80.0,
        volume_ratio=volume_ratio,
        breakout_signals={},
        ticker=base_history,
        latest=latest_row,
        timeframe="1d"
    )
    
    assert score >= 0, "Score should never drop below 0"
    assert score < 50, "Penalized setup should have a low score"
