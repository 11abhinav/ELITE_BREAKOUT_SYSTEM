import yfinance as yf

df = yf.download("LLOYDSME.NS", interval="5m", period="5d", progress=False)
df = df.reset_index()
print(df[df['Datetime'] >= '2026-07-15'])
