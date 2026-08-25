import yfinance as yf
df_adj = yf.download("TDPOWERSYS.NS", period="5d", auto_adjust=True, progress=False)
df_raw = yf.download("TDPOWERSYS.NS", period="5d", auto_adjust=False, progress=False)

print("Adjusted:")
print(df_adj['Close'])
print("\nUnadjusted:")
print(df_raw['Close'])
