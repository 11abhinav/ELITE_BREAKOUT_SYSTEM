import os
import sys
import pandas as pd
import yfinance as yf
from datetime import datetime, timedelta

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), 'app')))
from technical_indicators import apply_indicators
import warnings
warnings.filterwarnings("ignore")

def check_eod_bb_rejections(tickers):
    data = yf.download(tickers, period="1y", interval="1d", group_by="ticker", auto_adjust=False, prepost=False, progress=False)
    
    current_rejections = 0
    previous_rejections = 0
    total_breakouts = 0

    for ticker in tickers:
        if len(tickers) == 1:
            df = data
        else:
            df = data.get(ticker)
        if df is None or len(df.dropna()) < 150: continue
        df = df.dropna()
        
        df = apply_indicators(df, timeframe="1d")
        
        for i in range(100, len(df)):
            sub_df = df.iloc[:i+1]
            latest = sub_df.iloc[-1]
            
            # Simple breakout logic proxy
            if pd.isna(latest.get("PRIOR_20D_HIGH")): continue
            if latest["Close"] > latest["PRIOR_20D_HIGH"] and latest["Volume"] > 1.5 * sub_df["Volume"].iloc[-21:-1].mean():
                total_breakouts += 1
                
                # Check BB_WIDTH_PCTILE current vs prev
                bb_current = latest.get("BB_WIDTH_PCTILE", 0)
                bb_prev = sub_df["BB_WIDTH_PCTILE"].iloc[-2] if len(sub_df) >= 2 else 0
                
                if bb_current > 0.80:
                    current_rejections += 1
                if bb_prev > 0.80:
                    previous_rejections += 1

    print("=== EOD BB WIDTH REJECTIONS ===")
    print(f"Total Breakouts Analyzed: {total_breakouts}")
    print(f"Rejected by Current BB_WIDTH > 0.80: {current_rejections}")
    print(f"Rejected by Previous BB_WIDTH > 0.80: {previous_rejections}")


def check_reversal_sma50_rejections(tickers):
    data = yf.download(tickers, period="1y", interval="1d", group_by="ticker", auto_adjust=False, prepost=False, progress=False)
    
    total_candidates = 0
    sma50_rejections = 0
    
    for ticker in tickers:
        if len(tickers) == 1:
            df = data
        else:
            df = data.get(ticker)
        if df is None or len(df.dropna()) < 150: continue
        df = df.dropna()
        
        df = apply_indicators(df, timeframe="1d")
        
        for i in range(100, len(df)):
            sub_df = df.iloc[:i+1]
            latest = sub_df.iloc[-1]
            
            if pd.isna(latest.get("SMA50")) or pd.isna(latest.get("EMA20")) or pd.isna(latest.get("HIGH_52W")): continue
            
            close = latest["Close"]
            high_52w = latest["HIGH_52W"]
            ema20 = latest["EMA20"]
            sma50 = latest["SMA50"]
            
            drop_pct = (high_52w - close) / high_52w * 100
            if drop_pct < 20 or drop_pct > 45: continue
            
            if close < ema20: continue
            
            # MACD cross
            macd_cross = False
            for j in range(1, 11):
                if sub_df["MACD"].iloc[-j] > sub_df["MACD_SIGNAL"].iloc[-j] and sub_df["MACD"].iloc[-j-1] <= sub_df["MACD_SIGNAL"].iloc[-j-1]:
                    macd_cross = True
                    break
            if not macd_cross: continue
            
            total_candidates += 1
            
            # Check SMA50 strict gate
            pct_below_sma50 = (sma50 - close) / sma50 * 100 if sma50 > 0 else 0
            if close < sma50 and pct_below_sma50 > 3.0:
                sma50_rejections += 1

    print("\n=== REVERSAL SMA50 REJECTIONS ===")
    print(f"Total Candidates (Drop 20-45%, close > EMA20, fresh MACD cross): {total_candidates}")
    print(f"Rejected solely by strict SMA50 gate (not above, not within 3%): {sma50_rejections}")


if __name__ == "__main__":
    test_tickers = ["RELIANCE.NS", "TCS.NS", "HDFCBANK.NS", "INFY.NS", "ICICIBANK.NS", 
                    "ZOMATO.NS", "PAYTM.NS", "SUZLON.NS", "IREDA.NS", "IRFC.NS",
                    "TATASTEEL.NS", "TATAMOTORS.NS", "SBI.NS", "BAJFINANCE.NS", "ITC.NS",
                    "WIPRO.NS", "HINDUNILVR.NS", "AXISBANK.NS", "KOTAKBANK.NS", "LT.NS"]
    
    check_eod_bb_rejections(test_tickers)
    check_reversal_sma50_rejections(test_tickers)

