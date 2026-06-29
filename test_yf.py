import yfinance as yf
ticker = yf.Ticker("RELIANCE.NS")
print(ticker.info.get('sector'))
