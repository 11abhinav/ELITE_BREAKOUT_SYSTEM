import sys
import pandas as pd
import yfinance as yf

ticker = yf.Ticker("LLOYDSME.NS")
df = ticker.history(interval="5m", period="5d")
print(df.loc["2026-07-15 07:00:00+00:00":"2026-07-15 08:00:00+00:00", ["Open", "High", "Low", "Close"]])
