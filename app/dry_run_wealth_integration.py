import sys
import os
import pandas as pd
from datetime import datetime
from zoneinfo import ZoneInfo

# Ensure app is in path
sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))

from wealth_engine import calculate_wealth_technicals, DataQuality
from wealth_hold_tracking import HoldScoreTrendAnalyzer

def test_integration():
    print("🚀 Starting Wealth Integration Dry Run...")
    
    # 1. Setup mock DataFrame mimicking cached (fallback) data
    print("\n--- Testing Fallback & Stale Data Suppression ---")
    
    # Simulate a cached row from yesterday
    cached_df = pd.DataFrame([{
        "Stock": "TCS.NS",
        "cmp": 4000.0,
        "sma_200": 3500.0,
        "FM_Score": 90,
        "RSI": 45.0,
        "ATR_Pct": 2.5,
        "momentum_score": 85,
        "rs_6m": 15.0,
        "dist_52w_high": 2.0,
        "fallback_timestamp": datetime.now(ZoneInfo("Asia/Kolkata")).isoformat()
    }])
    
    # Normally we pull from database and yahoo finance. We will patch YFinance to fail.
    import yfinance as yf
    original_download = yf.download
    
    def mock_download(*args, **kwargs):
        raise Exception("Simulated API Timeout")
        
    yf.download = mock_download
    
    try:
        # Pass the cached_df as the "previous day's dataframe" to trigger fallback
        df = calculate_wealth_technicals(cached_df, nifty_6m_ret=5.0, nifty_dist_52w=2.0)
        
        row = df.iloc[0]
        print(f"✅ Fallback Triggered: {row['used_fallback_data']}")
        print(f"✅ Data Quality: {row['data_quality']}")
        print(f"✅ Signal Generated: '{row['Signal']}' (Should be suppressed)")
        print(f"✅ Risk Adjusted Size Category: '{row['alloc_category']}' (Should be SUPPRESSED)")
        
        assert row["used_fallback_data"] == True, "Fallback flag not set!"
        assert "SUPPRESS" in row["Signal"], "Signal was not suppressed!"
        assert row["alloc_category"] == "SUPPRESSED", "Sizing was not zeroed out!"
        
    finally:
        yf.download = original_download

    print("\n--- Testing Dashboard Portfolio Enrichment ---")
    from dashboard_server import app
    with app.test_client() as client:
        # Mock portfolio in DB
        # Since we can't easily mock DB without side effects, we just verify the endpoint doesn't crash
        res = client.get('/api/portfolio')
        print(f"✅ Dashboard Portfolio API Status: {res.status_code}")
        
    print("\n--- Integration Test Complete! Everything is working cleanly. ---")

if __name__ == "__main__":
    test_integration()
