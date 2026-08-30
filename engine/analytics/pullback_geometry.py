"""
Canonical PULLBACK Stop-Loss & Target Geometry Engine (v5.1.2)
Authoritative implementation of Adaptive ATR Stop Geometry & 2.5R Target for PULLBACK setups.

Mathematical Contract (Option A - Execution-Price Risk Basis):
  1. raw_atr_stop = 1.5 * atr_14 (Point-in-Time ATR14 strictly at decision timestamp)
  2. clamped_stop_pct = max(min(raw_atr_stop / entry_price, 0.060), 0.035)
  3. stop_loss = round(entry_price * (1.0 - clamped_stop_pct), 2)
  4. actual_risk = entry_price - stop_loss (determines true execution risk)
  5. target_price = round(entry_price + (2.5 * actual_risk), 2)
"""

from typing import Tuple, Dict, Any


def calculate_pullback_sl_target(entry_price: float, atr_14: float) -> Dict[str, Any]:
    """
    Computes canonical v5.1.2 stop-loss and target for PULLBACK.
    
    Args:
        entry_price: Positive execution entry price.
        atr_14: Point-in-Time 14-period Average True Range.

    Returns:
        Dict containing stop_loss, target_price, actual_risk, clamped_stop_pct, natural_rr.
    """
    if entry_price <= 0.0:
        raise ValueError(f"entry_price must be > 0, got {entry_price}")
    
    if atr_14 is None or atr_14 <= 0.0:
        # Fallback to standard median market ATR% (2.8%) if uninitialized
        atr_14 = entry_price * 0.028

    raw_atr_stop = atr_14 * 1.5
    raw_stop_pct = raw_atr_stop / entry_price
    clamped_stop_pct = max(min(raw_stop_pct, 0.060), 0.035)

    stop_loss = round(entry_price * (1.0 - clamped_stop_pct), 2)
    actual_risk = round(entry_price - stop_loss, 4)
    if actual_risk <= 0.0:
        # Micro-cap rounding safety guard
        stop_loss = round(entry_price * 0.965, 2)
        actual_risk = round(entry_price - stop_loss, 4)

    target_price = round(entry_price + (2.5 * actual_risk), 2)
    natural_rr = round((target_price - entry_price) / actual_risk, 4) if actual_risk > 0 else 2.5

    return {
        "stop_loss": stop_loss,
        "target_price": target_price,
        "actual_risk": actual_risk,
        "clamped_stop_pct": clamped_stop_pct,
        "natural_rr": natural_rr,
        "geometry_version": "v5.1.2_ADAPTIVE_ATR"
    }
