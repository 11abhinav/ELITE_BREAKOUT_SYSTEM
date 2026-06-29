import time
import yfinance as yf

symbols = ["WABAG.NS", "UNITDSPR.NS", "RRKABEL.NS", "NETWEB.NS", "HUDCO.NS"] * 6 # 30 symbols

start = time.time()
for sym in symbols:
    try:
        t = yf.Ticker(sym)
        price = t.fast_info.last_price
    except Exception:
        pass
end = time.time()
print(f"Time taken for {len(symbols)} symbols: {end - start:.2f} seconds")
