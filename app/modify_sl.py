import re

with open("app/sl_target_helper.py", "r") as f:
    content = f.read()

# 1. Replace ADX buffer scaling with ATR volatility scale
old_adx_atr = """def _adx_atr_scale(adx: Optional[float], atr_pct: Optional[float], base: float) -> float:
    \"\"\"
    Scale ATR multiplier by trend strength (ADX) and volatility regime (ATR%).
    Stronger trend → slightly wider SL to survive pullbacks without stopping out.
    High volatility → wider SL (stock moves more per day).

    v5 UPGRADE: ADX > 35 now gets 30% wider buffer (up from 20% at >40).
    Rationale: trending stocks with ADX 35-40 get the deepest operator stop-hunts
    because the trend draws in the most retail SL clusters.
    \"\"\"
    m = base
    if adx is not None:
        if adx > 40:   m *= 1.30   # v5: widened from 1.20 → 1.30 (deep pullback protection)
        elif adx > 35: m *= 1.20   # v5: NEW tier — strong trend, frequent stop-hunts
        elif adx > 30: m *= 1.10
        elif adx < 20: m *= 0.85   # choppy — tighter
    if atr_pct is not None:
        if atr_pct > 4.0:   m *= 1.20
        elif atr_pct > 2.5: m *= 1.10
        elif atr_pct < 1.0: m *= 0.90
    return round(m, 3)"""

new_atr_scale = """def _atr_volatility_scale(atr_pct: Optional[float], base: float) -> float:
    \"\"\"
    Scale ATR multiplier purely by volatility regime (ATR%).
    High volatility → wider SL (stock moves more per day).
    \"\"\"
    m = base
    if atr_pct is not None:
        if atr_pct > 6.0:   m *= 1.30
        elif atr_pct > 4.0: m *= 1.20
        elif atr_pct > 2.5: m *= 1.10
        elif atr_pct < 1.0: m *= 0.90
    return round(m, 3)"""

content = content.replace(old_adx_atr, new_atr_scale)
content = content.replace("_adx_atr_scale(_safe(adx), _safe(atr_pct), atr_base)", "_atr_volatility_scale(_safe(atr_pct), atr_base)")


# 2. Volatility-adaptive targets
old_cap_target = """def _cap_target(
    target: float,
    entry: float,
    eff_atr: float,
    timeframe: str,
) -> float:
    \"\"\"
    v5 NEW: Cap target at MAX_TARGET_ATR × ATR from entry.
    Prevents unrealistic targets that the stock has no chance of reaching.
    \"\"\"
    max_atr_mult = MAX_TARGET_ATR.get(timeframe, 12.0)
    max_target   = entry + max_atr_mult * eff_atr
    return min(target, max_target)"""

new_cap_target = """def _cap_target(
    target: float,
    entry: float,
    eff_atr: float,
    timeframe: str,
    atr_pct: Optional[float] = None,
) -> float:
    \"\"\"
    Adaptive target cap based on volatility.
    \"\"\"
    max_atr_mult = MAX_TARGET_ATR.get(timeframe, 12.0)
    if atr_pct is not None:
        if atr_pct > 4.0:
            max_atr_mult = min(max_atr_mult, 6.0)
        elif atr_pct < 2.0:
            max_atr_mult = max(max_atr_mult, 10.0)
    max_target = entry + max_atr_mult * eff_atr
    return min(target, max_target)"""

content = content.replace(old_cap_target, new_cap_target)

# Change usages of _cap_target to include atr_pct
content = content.replace('_cap_target(t1_raw, entry, eff_atr, "1d")', '_cap_target(t1_raw, entry, eff_atr, "1d", _safe(atr_pct))')
content = content.replace('_cap_target(r2_v, entry, eff_atr, "1d")', '_cap_target(r2_v, entry, eff_atr, "1d", _safe(atr_pct))')
content = content.replace('_cap_target(entry + 3.5 * risk, entry, eff_atr, "1d")', '_cap_target(entry + 3.5 * risk, entry, eff_atr, "1d", _safe(atr_pct))')
content = content.replace('_cap_target(entry + 5.0 * risk, entry, eff_atr, "1d")', '_cap_target(entry + 5.0 * risk, entry, eff_atr, "1d", _safe(atr_pct))')
content = content.replace('_cap_target(t1_raw, entry, eff_atr, "15m")', '_cap_target(t1_raw, entry, eff_atr, "15m", _safe(atr_pct))')
content = content.replace('_cap_target(r2_v, entry, eff_atr, "15m")', '_cap_target(r2_v, entry, eff_atr, "15m", _safe(atr_pct))')
content = content.replace('_cap_target(entry + 2.5 * risk, entry, eff_atr, "15m")', '_cap_target(entry + 2.5 * risk, entry, eff_atr, "15m", _safe(atr_pct))')
content = content.replace('_cap_target(t1_raw, entry, eff_atr, "1h")', '_cap_target(t1_raw, entry, eff_atr, "1h", _safe(atr_pct))')
content = content.replace('_cap_target(r2_v, entry, eff_atr, "1h")', '_cap_target(r2_v, entry, eff_atr, "1h", _safe(atr_pct))')
content = content.replace('_cap_target(entry + 3.0 * risk, entry, eff_atr, "1h")', '_cap_target(entry + 3.0 * risk, entry, eff_atr, "1h", _safe(atr_pct))')
content = content.replace('_cap_target(sma50_v, entry, eff_atr, "1d")', '_cap_target(sma50_v, entry, eff_atr, "1d", _safe(atr_pct))')
content = content.replace('_cap_target(r1_v, entry, eff_atr, "1d")', '_cap_target(r1_v, entry, eff_atr, "1d", _safe(atr_pct))')

with open("app/sl_target_helper.py", "w") as f:
    f.write(content)
