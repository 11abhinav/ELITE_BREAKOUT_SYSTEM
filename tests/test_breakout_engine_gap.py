import os
import sys
import pandas as pd

# Ensure app directory is in path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'app')))
from breakout_engine import detect_breakouts

def test_classic_breakout_detection():
    # Simulate a classic breakout candle:
    # Resistance (prev_high) is at 100.
    # Candle opens at 99, drops to 98 (low), surges and closes at 105 (high is 106).
    # This is a solid 5% breakout over resistance.
    # Previous engine would reject this because candle_low (98) is NOT > 99.7.
    
    data = []
    # Create 25 bars of history for the rolling window.
    for i in range(25):
        data.append({
            "Open": 95,
            "High": 98 if i < 24 else 106, 
            "Low": 94 if i < 24 else 98,
            "Close": 96 if i < 24 else 105,
            "Volume": 1000 if i < 24 else 5000,
            "BASE_WIDTH": 1.0,
            "OBV_TREND": 1
        })
    # Set bar 10 to be the "prev_high" of 100 to simulate the resistance level
    data[10]["High"] = 100
    
    df = pd.DataFrame(data)
    
    signals = detect_breakouts(df, timeframe="1d")
    
    # Verify that a breakout signal was detected
    assert len(signals) > 0, "Expected a breakout signal to be detected"
    assert "52W Breakout" in signals or "Daily Breakout" in signals, "Expected a standard breakout signal"
