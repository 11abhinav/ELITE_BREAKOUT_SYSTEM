import yfinance as yf
df = yf.download(["INVALID1.NS", "INVALID2.NS"], period="5d", interval="1d", progress=False, group_by="ticker", auto_adjust=True)
print(df.columns)
print(type(df))
