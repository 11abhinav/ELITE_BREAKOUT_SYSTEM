import numpy as np
import pandas as pd
from typing import Dict, Any, Optional

def simulate_trade_canonical(df: pd.DataFrame, t: int, entry_price: float, stop_override: Optional[float] = None, mode: str = "PATTERN", max_holding_bars: int = 25) -> Optional[Dict[str, Any]]:
    """
    Standardized canonical causal trade simulator executing on Open[t+1] with `max_holding_bars` limit.
    Enforces the strictly-tested forensic exit logic (2R/3R targets, 5-bar hold minimum for T1).
    """
    if entry_price <= 0: return None
    if t >= len(df) - 2: return None

    highs = df["High"].iloc[:t+1].values
    lows = df["Low"].iloc[:t+1].values
    closes = df["Close"].iloc[:t+1].values
    tr = np.maximum(highs[1:] - lows[1:], np.abs(highs[1:] - closes[:-1]))
    tr = np.maximum(tr, np.abs(lows[1:] - closes[:-1]))
    atr = np.mean(tr[-14:]) if len(tr) >= 14 else (entry_price * 0.025)

    if stop_override is not None:
        stop_dist = max(entry_price - stop_override, 0.035 * entry_price)
    elif mode in ["SPRING", "REVERSAL", "WYCKOFF_SPRING_TYPE_2", "HIGHER_LOW_REVERSAL"]:
        structural_low = float(np.min(lows[max(0, t-3) : t+1]))
        stop_dist = max(entry_price - structural_low, 0.035 * entry_price)
    else:
        stop_dist = max(1.8 * atr, 0.035 * entry_price)
        
    stop_dist = min(stop_dist, 0.080 * entry_price) # Clamped to [3.5%, 8.0%]

    stop_loss = round(entry_price - stop_dist, 2)
    risk_unit = entry_price - stop_loss
    if risk_unit <= 0: return None

    target_1 = round(entry_price + (2.0 * risk_unit), 2)
    target_2 = round(entry_price + (3.0 * risk_unit), 2)

    fwd_slice = df.iloc[t + 1 : min(len(df), t + 1 + max_holding_bars)]
    exit_price = entry_price
    exit_bar = len(fwd_slice)
    exit_reason = "TIME_EXPIRY"
    
    max_mfe = 0.0
    max_mae = 0.0

    fwd_lows = fwd_slice["Low"].values
    fwd_highs = fwd_slice["High"].values
    fwd_closes = fwd_slice["Close"].values

    for i in range(len(fwd_slice)):
        bar_low = float(fwd_lows[i])
        bar_high = float(fwd_highs[i])
        bar_close = float(fwd_closes[i])
        
        mfe = (bar_high - entry_price) / risk_unit
        mae = (entry_price - bar_low) / risk_unit
        if mfe > max_mfe: max_mfe = mfe
        if mae > max_mae: max_mae = mae

        if bar_low <= stop_loss:
            exit_price = stop_loss
            exit_bar = i + 1
            exit_reason = "STOP_LOSS"
            break
        elif bar_high >= target_2:
            exit_price = target_2
            exit_bar = i + 1
            exit_reason = "TARGET_2"
            break
        elif bar_high >= target_1 and i >= 5:
            exit_price = max(target_1, bar_close)
            exit_bar = i + 1
            exit_reason = "TARGET_1"
            break

    if exit_reason == "TIME_EXPIRY":
        if not fwd_slice.empty:
            exit_price = float(fwd_slice.iloc[-1]["Close"])
        else:
            exit_price = entry_price

    r_mult = (exit_price - entry_price) / risk_unit
    
    if r_mult >= 1.0:
        outcome = "WIN"
    elif r_mult <= -0.5:
        outcome = "LOSS"
    else:
        outcome = "SCRATCH"

    try:
        exit_date_str = str(fwd_slice.index[exit_bar - 1]).split(" ")[0]
    except Exception:
        exit_date_str = "UNKNOWN"

    return {
        "outcome": outcome,
        "r_multiple": round(r_mult, 2),
        "holding_bars": exit_bar,
        "pnl_pct": round(((exit_price - entry_price) / entry_price) * 100.0, 2),
        "mfe": round(max_mfe, 2),
        "mae": round(max_mae, 2),
        "exit_date": exit_date_str,
        "exit_price": round(exit_price, 2),
        "exit_reason": exit_reason
    }
