import os
import sys
import json
import time
import pandas as pd
from datetime import datetime, timedelta

# Ensure app is in path
sys.path.append(os.path.abspath('app'))

from data_provider import get_fetcher
from fundamentals_cache import fetch_single_piotroski

SNAPSHOT_DIR = "tests/fixtures/market_snapshot_v1"

CURATED_SYMBOLS = [
    # 5 Large Caps
    "RELIANCE.NS", "HDFCBANK.NS", "INFY.NS", "TCS.NS", "ICICIBANK.NS",
    # 5 Recent IPOs / Limited History
    "IREDA.NS", "JIOFIN.NS", "TATATECH.NS", "DOMS.NS", "AADHARHFC.NS",
    # 5 Penny Stocks
    "SUZLON.NS", "YESBANK.NS", "IDEA.NS", "SOUTHBANK.NS", "GTLINFRA.NS",
    # 5 High Volatility / Adani
    "ADANIENT.NS", "AWL.NS", "PAYTM.NS", "ZOMATO.NS", "RAILTEL.NS",
    # 5 Known BSE Fallbacks / Low Volume
    "YASHHV.BO", "SME.BO", "BOMDYEING.NS", "HINDMOTORS.NS", "TINPLATE.NS",
    # 5 Missing/Dead Data Cases
    "FAKE_SYMBOL123.NS", "DEAD_STOCK.NS", "DELISTED.BO", "INVALID_SYM", "UNKNOWN.NS",
    # 20 Other mixed bag for breakouts and reversals
    "HAL.NS", "MAZDOCK.NS", "BHEL.NS", "BEL.NS", "RVNL.NS", 
    "IRFC.NS", "TVSMOTOR.NS", "TATAMOTORS.NS", "M&M.NS", "BAJFINANCE.NS",
    "CDSL.NS", "BSE.NS", "ANGELONE.NS", "MCX.NS", "IEX.NS",
    "TATASTEEL.NS", "HINDALCO.NS", "VEDL.NS", "JSWSTEEL.NS", "SAIL.NS"
]

def round_dataframe(df):
    """Normalize floating point values."""
    if df is None or df.empty:
        return df
    
    # Prices to 4 decimals
    price_cols = ["Open", "High", "Low", "Close", "Adj Close"]
    for col in price_cols:
        if col in df.columns:
            df[col] = df[col].round(4)
            
    # Volume to int
    if "Volume" in df.columns:
        df["Volume"] = df["Volume"].fillna(0).astype(int)
        
    return df

def round_fundamentals(funds):
    """Normalize fundamentals dictionary."""
    if not funds or funds.get("failed"):
        return funds
        
    for k, v in funds.items():
        if isinstance(v, float):
            # Normalizing everything to 2 or 4 decimals based on type
            if "Margin" in k or "%" in k:
                funds[k] = round(v, 2)
            else:
                funds[k] = round(v, 4)
    return funds

def build_snapshot():
    os.makedirs(os.path.join(SNAPSHOT_DIR, "ohlcv", "1d"), exist_ok=True)
    os.makedirs(os.path.join(SNAPSHOT_DIR, "ohlcv", "15m"), exist_ok=True)
    os.makedirs(os.path.join(SNAPSHOT_DIR, "fundamentals"), exist_ok=True)
    
    fetcher = get_fetcher()
    
    print(f"Building V1 Snapshot for {len(CURATED_SYMBOLS)} symbols...")
    
    # Fetch 1D Data
    print("Fetching 1D OHLCV (1y)...")
    res_1d = fetcher.get_batch_ohlcv(CURATED_SYMBOLS, interval="1d", period="1y")
    
    # Fetch 15m Data
    print("Fetching 15m OHLCV (60d)...")
    res_15m = fetcher.get_batch_ohlcv(CURATED_SYMBOLS, interval="15m", period="60d")
    
    for sym in CURATED_SYMBOLS:
        # Save OHLCV 1d
        df_1d = res_1d.get(sym)
        if df_1d is not None and not df_1d.empty:
            df_1d = round_dataframe(df_1d)
            # Safe filename
            safe_sym = sym.replace(":", "_")
            df_1d.to_parquet(os.path.join(SNAPSHOT_DIR, "ohlcv", "1d", f"{safe_sym}.parquet"))
            
        # Save OHLCV 15m
        df_15m = res_15m.get(sym)
        if df_15m is not None and not df_15m.empty:
            df_15m = round_dataframe(df_15m)
            safe_sym = sym.replace(":", "_")
            df_15m.to_parquet(os.path.join(SNAPSHOT_DIR, "ohlcv", "15m", f"{safe_sym}.parquet"))
            
        # Save Fundamentals
        print(f"Fetching fundamentals for {sym}...")
        # Fix for naked symbol requirement in fetch_single_piotroski
        naked_sym = sym.replace(".NS", "").replace(".BO", "")
        funds = fetch_single_piotroski(naked_sym)
        
        if funds:
            funds = round_fundamentals(funds)
            safe_sym = sym.replace(":", "_")
            with open(os.path.join(SNAPSHOT_DIR, "fundamentals", f"{safe_sym}.json"), "w") as f:
                json.dump(funds, f, indent=2)
                
        time.sleep(0.5) # Avoid rapid hammering during snapshot building
        
    print(f"✅ V1 Snapshot successfully frozen in {SNAPSHOT_DIR}")

if __name__ == "__main__":
    build_snapshot()
