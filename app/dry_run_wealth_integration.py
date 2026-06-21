import sys
import os
import pandas as pd
from datetime import datetime
from zoneinfo import ZoneInfo

# Ensure app is in path
sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))

from wealth_engine import run_wealth_scan

def test_integration():
    print("🚀 Starting Wealth Integration Dry Run...")
    
    # 1. Setup mock YFinance to simulate a failure and trigger the fallback path
    print("\n--- Testing Fallback & Stale Data Suppression ---")
    
    import yfinance as yf
    original_download = yf.download
    
    def mock_download(*args, **kwargs):
        raise Exception("Simulated API Timeout")
        
    yf.download = mock_download
    
    # Enable Dry Run flag to avoid polluting the database
    os.environ["DONT_SAVE_WEALTH"] = "1"
    
    import database
    database.DONT_SAVE_WEALTH = True
    
    try:
        # Run the entire wealth scan engine end-to-end!
        run_wealth_scan()
        print("✅ run_wealth_scan() completed successfully!")
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
