import sys

with open('app/sl_target_helper.py', 'r') as f:
    content = f.read()

# Fix the signature
old_sig = """    timeframe:      Optional[str]   = None,
    ticker:         Optional[pd.DataFrame] = None,
) -> dict:"""
new_sig = """    timeframe:      Optional[str]   = None,
    ticker:         Optional[pd.DataFrame] = None,
    **kwargs_extra
) -> dict:"""

content = content.replace(old_sig, new_sig)

# Fix the kwargs assignment
old_kwargs = """    kwargs = dict(
        entry=entry_price, eff_atr=eff_atr,
        adx=adx, rsi=rsi, macd_hist=macd_hist, atr_pct=atr_pct,
        swing_low=swing_low, swing_high=swing_high,
        bb_upper=bb_upper, bb_lower=bb_lower,
        s1=s1, s2=s2, r1=r1, r2=r2,
        swing_low_raw=swing_low_raw, swing_high_raw=swing_high_raw,
        swing_low_cluster=swing_low_cluster,
    )"""

new_kwargs = """    kwargs = dict(
        entry=entry_price, eff_atr=eff_atr,
        adx=adx, rsi=rsi, macd_hist=macd_hist, atr_pct=atr_pct,
        swing_low=swing_low, swing_high=swing_high,
        bb_upper=bb_upper, bb_lower=bb_lower,
        s1=s1, s2=s2, r1=r1, r2=r2,
        swing_low_raw=swing_low_raw, swing_high_raw=swing_high_raw,
        swing_low_cluster=swing_low_cluster,
        ticker=ticker,
    )
    kwargs.update(kwargs_extra)"""

content = content.replace(old_kwargs, new_kwargs)

with open('app/sl_target_helper.py', 'w') as f:
    f.write(content)
