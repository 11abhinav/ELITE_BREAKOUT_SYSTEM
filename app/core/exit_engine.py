from typing import Dict, Any, Tuple
from core.models import ExitState

def evaluate_exit(position_data: Dict[str, Any], raw_metrics: Dict[str, Any], technicals: Dict[str, float]) -> Tuple[ExitState, str]:
    """
    Layer 9: Dynamic Exit Engine
    Provides granular exit states: BUY, ADD, HOLD, TRIM, SELL
    """
    
    # 1. Emergency
    auditor_flags = raw_metrics.get("auditor_flags", False)
    if auditor_flags:
        return ExitState.SELL, "Emergency: Fraud / Auditor flags"
        
    # 2. Fundamental Collapse
    roic_ttm = raw_metrics.get("roic_ttm")
    roic_entry = position_data.get("entry_roic")
    if roic_ttm is not None and roic_entry is not None:
        if roic_ttm < (roic_entry * 0.5): # Dropped by half
            return ExitState.SELL, f"Fundamental Collapse: ROIC dropped from {roic_entry} to {roic_ttm}"
            
    # 3. Fundamental Weakening (Partial Exit)
    roic_3y = raw_metrics.get("roic_3y_avg")
    if roic_ttm is not None and roic_3y is not None:
        if roic_ttm < roic_3y * 0.8: # Weakening trend but not collapsed
            return ExitState.TRIM, "Fundamental Weakening: ROIC trend is down"
            
    # 4. Technical Trend Broken
    price = technicals.get("price", 0.0)
    sma_200 = technicals.get("sma_200", 0.0)
    if price > 0 and sma_200 > 0 and price < sma_200:
        return ExitState.TRIM, "Technical: Trend Broken (Price < SMA200)"
        
    # 5. Trailing Stop Hit (Lock gains)
    atr = technicals.get("atr", 0.0)
    highest_close = position_data.get("highest_close_since_entry", price)
    trailing_stop = highest_close - (3.0 * atr)
    if price < trailing_stop:
        return ExitState.SELL, f"Trailing Stop Hit ({trailing_stop:.1f})"
        
    # If in buy zone again
    sma_50 = technicals.get("sma_50", 0.0)
    if price > sma_50 and price <= sma_50 + (1.5 * atr):
        return ExitState.ADD, "Pullback to buy zone"
        
    return ExitState.HOLD, "All conditions normal"
