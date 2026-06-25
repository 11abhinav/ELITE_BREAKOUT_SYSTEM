import pytest
import pandas as pd
import numpy as np
from app.scoring_engine import check_hard_disqualifiers

def generate_mock_ticker(volume, rsi):
    # Create 25 rows of fake data
    df = pd.DataFrame({
        'Volume': [volume] * 25,
        'Close': [150.0] * 25,
        'High': [152.0] * 25,  # Reduced from 155 to make upper wick < 40% of range
        'Low': [145.0] * 25,
        'Open': [148.0] * 25,
        'RSI': [rsi] * 25,
        'SMA_200': [140.0] * 25,
        'SMA_50': [145.0] * 25,
        'SMA_20': [148.0] * 25,
        'MACD_Hist': [1.0] * 25
    })
    return df

def test_check_hard_disqualifiers_pass():
    ticker = generate_mock_ticker(150_000, 65)
    latest = ticker.iloc[-1]
    
    status, reason = check_hard_disqualifiers(
        ticker=ticker, 
        latest=latest, 
        volume_ratio=2.5, 
        symbol="RELIANCE", 
        timeframe="15m", 
        min_vol=50_000
    )
    
    # Should NOT be disqualified
    assert status is False
    assert reason is None

def test_check_hard_disqualifiers_low_volume():
    ticker = generate_mock_ticker(10_000, 65)
    latest = ticker.iloc[-1]
    
    status, reason = check_hard_disqualifiers(
        ticker=ticker, 
        latest=latest, 
        volume_ratio=2.5, 
        symbol="RELIANCE", 
        timeframe="15m", 
        min_vol=50_000
    )
    
    # SHOULD be disqualified due to volume
    assert status is True
    assert "volume" in reason.lower() or "illiquid" in reason.lower()

def test_check_hard_disqualifiers_rsi_divergence():
    # Price increases, but RSI decreases significantly
    ticker = generate_mock_ticker(150_000, 65)
    
    # 15 days ago (index -15)
    ticker.loc[10, 'Close'] = 140.0
    ticker.loc[10, 'RSI'] = 75.0
    
    # Today (index -1)
    ticker.loc[24, 'Close'] = 150.0  # Higher high (+7%)
    ticker.loc[24, 'RSI'] = 60.0     # Lower RSI (divergence)
    
    latest = ticker.iloc[-1]
    
    status, reason = check_hard_disqualifiers(
        ticker=ticker, 
        latest=latest, 
        volume_ratio=2.5, 
        symbol="RELIANCE", 
        timeframe="15m", 
        min_vol=50_000
    )
    
    # SHOULD be disqualified due to RSI divergence
    assert status is True
    assert "rsi bearish divergence" in reason.lower()
