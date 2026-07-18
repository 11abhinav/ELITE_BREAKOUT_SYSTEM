"""
Simple risk-adjusted position sizing helper used by Wealth Engine.

This is intentionally small: it maps ATR (percent) and a momentum score to
an allocation percentage and amount. It expects to be safe by default and
to avoid raising exceptions when inputs are missing.

API:
    calculate_risk_adjusted_sizing(cmp: float, atr_pct: float, momentum_score: int) -> dict

Returned dict keys:
    - Position_Pct: fraction (0.0 - 0.2) of portfolio (e.g. 0.03 means 3%)
    - Position_Amount: absolute amount in rupees (uses TOTAL_PORTFOLIO_RUPEES env or fallback)
    - Alloc_Category: string label

This avoids adding new config files and keeps behaviour deterministic.
"""
from __future__ import annotations
import os
from typing import Dict


def _get_portfolio_size() -> float:
    try:
        return float(os.environ.get("TOTAL_PORTFOLIO_RUPEES", "1000000"))
    except Exception:
        return 1_000_000.0


def calculate_risk_adjusted_sizing(cmp: float, atr_pct: float, momentum_score: int) -> Dict[str, object]:
    """Return a conservative position sizing dict.

    - cmp: current market price (unused for sizing except to compute shares elsewhere)
    - atr_pct: ATR as percentage of price (e.g. 1.5)
    - momentum_score: integer 0-100
    """
    portfolio = _get_portfolio_size()

    # Defensive defaults
    if cmp is None or cmp <= 0:
        return {"Position_Pct": 0.0, "Position_Amount": 0.0, "Alloc_Category": "SUPPRESSED"}

    # Base position fraction derived from momentum score (0-100 -> 0.5% - 6%)
    base_pct = 0.005 + (momentum_score / 100.0) * 0.055  # 0.5% -> 6%

    # Volatility adjustment: higher ATR reduces position size
    try:
        atr_adj = 1.0
        if atr_pct is None:
            atr_pct = 3.0
        if atr_pct > 6.0:
            atr_adj = 0.4
        elif atr_pct > 3.0:
            atr_adj = 0.7
        elif atr_pct > 2.0:
            atr_adj = 0.85
    except Exception:
        atr_adj = 0.8

    position_capped = (base_pct * atr_adj) >= 0.2
    final_pct = max(0.0, min(0.2, base_pct * atr_adj))

    # Allocate category
    if final_pct >= 0.04:
        cat = "CORE"
    elif final_pct >= 0.02:
        cat = "CORE-LITE"
    elif final_pct > 0.01:
        cat = "GROWTH"
    else:
        cat = "SMALL"

    position_amount = round(portfolio * final_pct, 2)

    return {
        "Position_Pct": round(final_pct, 4),
        "Position_Amount": position_amount,
        "Alloc_Category": cat,
        "position_capped": position_capped
    }

