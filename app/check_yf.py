import yfinance as yf

df = yf.download("LLOYDSME.NS", interval="5m", period="5d", progress=False)
if df.empty:
    df = yf.download("LLOYDSME.BO", interval="5m", period="5d", progress=False)

print(df.tail(10))
