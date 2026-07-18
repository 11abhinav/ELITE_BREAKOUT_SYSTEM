import sys

with open("app/multibagger.py", "r") as f:
    content = f.read()

# Fix get_ohlcv usage
old_nifty = """    try:
        nifty_df = get_fetcher().get_ohlcv("^NSEI", period="1y", interval="1d")
        if nifty_df is not None and not nifty_df.empty:"""

new_nifty = """    try:
        nifty_md = get_fetcher().get_ohlcv("^NSEI", period="1y", interval="1d")
        nifty_df = nifty_md.dataframe if nifty_md else None
        if nifty_df is not None and not nifty_df.empty:"""

# Fix get_batch_ohlcv usage
old_batch = """    batch_res = fetcher.get_batch_ohlcv(symbols, period="1y", interval="1d", caller="multibagger")
    for sym in symbols:
        df = batch_res.get(sym)
        if df is None or hasattr(df, 'empty') and df.empty or getattr(df, 'name', '') in ['RATE_LIMIT', 'NETWORK_ERROR']:"""

new_batch = """    batch_res = fetcher.get_batch_ohlcv(symbols, period="1y", interval="1d", caller="multibagger")
    for sym in symbols:
        md = batch_res.get(sym)
        df = md.dataframe if md else None
        if df is None or (hasattr(df, 'empty') and df.empty):"""

content = content.replace(old_nifty, new_nifty)
content = content.replace(old_batch, new_batch)

with open("app/multibagger.py", "w") as f:
    f.write(content)
print("Patched multibagger.py")
