import re

with open("app/sl_target_helper.py", "r") as f:
    content = f.read()

content = content.replace("swing_low_cluster: Optional[float] = None,\n) -> dict:", "swing_low_cluster: Optional[float] = None,\n    ticker=None,\n) -> dict:")

# Add ticker to kwargs
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
    )"""

content = content.replace(old_kwargs, new_kwargs)

with open("app/sl_target_helper.py", "w") as f:
    f.write(content)
