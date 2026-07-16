import sys
import os
import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import pandas as pd
import json

# Setup environment
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "app")))
from app.live_prices import get_live_prices
from app.price_provider import PriceProvider
from app.intraday import normalize_index

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)
IST = ZoneInfo("Asia/Kolkata")

def validate_intraday_pipeline(symbols):
    """Fetch data for symbols and validate intraday pipeline."""
    logger.info(f"🔍 Validating Pipeline for symbols: {symbols}")
    
    # Test 1: Fetch Live Prices (which fetches daily context)
    prices = get_live_prices(symbols)
    assert isinstance(prices, dict), "get_live_prices should return a dict"
    
    for sym in symbols:
        data = prices.get(sym)
        if data is None:
            logger.warning(f"⚠️ No live data returned for {sym}")
            continue
            
        assert isinstance(data, (float, int)), f"Expected float price for {sym}, got {type(data)}"
        assert pd.notna(data), f"NaN price for {sym}"
        logger.info(f"✅ {sym} live price: {data}")

    # Test 2: Fetch 5min Intraday Bars
    provider = PriceProvider()
    data_dict = provider.fetch_batch(symbols, period="5d", interval="5m")
    
    for sym in symbols:
        try:
            logger.info(f"Checking 5m data for {sym}...")
            df_5m = data_dict.get(sym)
            
            if df_5m is None or df_5m.empty:
                logger.warning(f"⚠️ No 5m data returned for {sym}")
                continue
                
            df_5m = normalize_index(df_5m)
                
            # Assert Timestamps are TZ-aware IST
            assert df_5m.index.tz is not None, f"{sym} 5m Index is timezone naive!"
            tz_name = str(df_5m.index.tz)
            assert "Asia/Kolkata" in tz_name, f"{sym} 5m Index timezone is {tz_name}, not Asia/Kolkata"
            
            # Print sample to verify no NaN bleeding
            last_row = df_5m.iloc[-1]
            assert pd.notna(last_row['Close']), f"NaN Close price at {df_5m.index[-1]}"
            logger.info(f"✅ {sym} 5m data: {len(df_5m)} rows. Last timestamp: {df_5m.index[-1]}")
        except Exception as e:
            logger.error(f"❌ Failed processing {sym}: {e}")
            raise

if __name__ == "__main__":
    test_symbols = ["TCS.NS", "RELIANCE.NS", "HDFCBANK.NS", "INFY.NS", "ICICIBANK.NS"]
    validate_intraday_pipeline(test_symbols)
    logger.info("🎉 Pipeline validation passed successfully.")
