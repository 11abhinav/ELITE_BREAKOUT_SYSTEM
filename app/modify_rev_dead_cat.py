import re

with open("app/sl_target_helper.py", "r") as f:
    content = f.read()

old_code = """    # Reversal Dead Cat Filter (Volume expansion)
    import pandas as pd
    if not is_rejected:
        # Check volume from locals if ticker was passed, though compute_reversal doesn't receive ticker directly!
        pass # Wait, we need to add ticker to _compute_reversal"""

new_code = """    # Reversal Dead Cat Filter (Volume expansion)
    import pandas as pd
    if not is_rejected and ticker is not None and "Volume" in ticker.columns and len(ticker) > 20:
        latest_vol = ticker["Volume"].iloc[-1]
        avg_vol = ticker["Volume"].iloc[-21:-1].mean()
        if avg_vol > 0 and (latest_vol / avg_vol) < 1.5:
            is_rejected = True
            rejection_reason = f"Dead cat bounce (vol ratio {round(latest_vol/avg_vol, 2)} < 1.5)"
"""
content = content.replace(old_code, new_code)

# Reversal SL should be wider: SL = swing_low - max(1.5 * ATR, 2%)
old_sl_rev = """    atr_base, sl_atr_buf, sl_pct_buf, min_rr, max_sl_atr = _MODE_CONFIG["REVERSAL"]

    # Volatility-scaled buffer (beaten stocks are volatile)
    support, sup_label = _pick_support(
        entry, _safe(swing_low), _safe(s1), _safe(swing_low_raw), _safe(s2),
        swing_low_cluster=swing_low_cluster
    )

    if support is not None:
        raw_sl, sl_method = _sl_from_support(entry, support, eff_atr, sl_atr_buf, sl_pct_buf, max_sl_atr, sup_label)
    else:
        # No pivot swing — use recent candle low (the reversal trigger bar's low)
        raw_sl    = entry - max(sl_atr_buf * eff_atr, sl_pct_buf * entry)
        sl_method = f"Below entry by buffer ₹{round(entry - raw_sl, 2)} (no prior swing low found)\""""

new_sl_rev = """    atr_base, sl_atr_buf, sl_pct_buf, min_rr, max_sl_atr = _MODE_CONFIG["REVERSAL"]

    # Volatility-scaled buffer (beaten stocks are volatile)
    # User feedback: Reversal SL must be wider -> max(1.5 * ATR, 2% price)
    sl_atr_buf = max(sl_atr_buf, 1.5)
    sl_pct_buf = max(sl_pct_buf, 0.02)
    
    support, sup_label = _pick_support(
        entry, _safe(swing_low), _safe(s1), _safe(swing_low_raw), _safe(s2),
        swing_low_cluster=swing_low_cluster
    )

    if support is not None:
        raw_sl, sl_method = _sl_from_support(entry, support, eff_atr, sl_atr_buf, sl_pct_buf, max_sl_atr, sup_label)
    else:
        # No pivot swing — use recent candle low (the reversal trigger bar's low)
        raw_sl    = entry - max(sl_atr_buf * eff_atr, sl_pct_buf * entry)
        sl_method = f"Below entry by buffer ₹{round(entry - raw_sl, 2)} (no prior swing low found)\""""

content = content.replace(old_sl_rev, new_sl_rev)

with open("app/sl_target_helper.py", "w") as f:
    f.write(content)
