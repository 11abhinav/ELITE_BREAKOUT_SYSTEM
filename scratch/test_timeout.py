import time
import yfinance as yf

# Generate 100 dummy ticker symbols that exist, e.g., Nifty 100
tickers = ["RELIANCE.NS", "TCS.NS", "HDFCBANK.NS", "ICICIBANK.NS", "BHARTIARTL.NS", "INFY.NS", "SBI.NS", "ITC.NS", "L&T.NS", "HINDUNILVR.NS",
           "AXISBANK.NS", "KOTAKBANK.NS", "BAJFINANCE.NS", "MARUTI.NS", "SUNPHARMA.NS", "TITAN.NS", "ULTRACEMCO.NS", "NTPC.NS", "TATAMOTORS.NS", "POWERGRID.NS",
           "M&M.NS", "ONGC.NS", "ASIANPAINT.NS", "COALINDIA.NS", "BAJAJFINSV.NS", "WIPRO.NS", "HCLTECH.NS", "JSWSTEEL.NS", "ADANIPORTS.NS", "TATASTEEL.NS",
           "GRASIM.NS", "HDFCLIFE.NS", "TECHM.NS", "HINDALCO.NS", "SBILIFE.NS", "BRITANNIA.NS", "CIPLA.NS", "INDUSINDBK.NS", "EICHERMOT.NS", "DRREDDY.NS",
           "DIVISLAB.NS", "APOLLOHOSP.NS", "HEROMOTOCO.NS", "BPCL.NS", "UPL.NS"]

print(f"Fetching {len(tickers)} tickers...")
start = time.time()
try:
    df = yf.download(" ".join(tickers), period="1y", interval="1d", threads=False, timeout=30)
    print(f"Success! Took {time.time() - start:.2f} seconds.")
except Exception as e:
    print(f"Failed after {time.time() - start:.2f} seconds: {e}")
