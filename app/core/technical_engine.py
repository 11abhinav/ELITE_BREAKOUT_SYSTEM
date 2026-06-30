from typing import Dict, Any, Tuple

def evaluate_technicals(price_data: Dict[str, float]) -> Tuple[bool, float, float, str]:
    """
    Evaluates technical entry rules for Layer 7.
    Returns: (is_valid: bool, buy_zone_low: float, buy_zone_high: float, reason: str)
    """
    
    price = price_data.get("price", 0.0)
    sma_50 = price_data.get("sma_50", 0.0)
    sma_200 = price_data.get("sma_200", 0.0)
    ema_20 = price_data.get("ema_20", sma_50) # Fallback to sma50 if not provided
    atr = price_data.get("atr", 0.0)
    
    if price <= 0.0 or sma_50 <= 0.0 or sma_200 <= 0.0 or atr <= 0.0:
        return False, 0.0, 0.0, "Missing technical data"
        
    # 1. Trend Confirmation
    if price < sma_50:
        return False, 0.0, 0.0, f"Price below SMA50 ({sma_50:.0f})"
    if price < sma_200:
        return False, 0.0, 0.0, f"Price below SMA200 ({sma_200:.0f})"
        
    # 2. Extension Check (Don't chase parabolic moves)
    dist_ema20 = price - ema_20
    if dist_ema20 > 2.5 * atr:
        return False, 0.0, 0.0, f"Extended > 2.5 ATR from EMA20"
        
    # 3. ATR Buy Zone
    # The ideal buy zone is near the breakout line. We define it around the moving averages.
    buy_zone_low = sma_50
    buy_zone_high = sma_50 + (1.5 * atr)
    
    if price > buy_zone_high:
        return False, buy_zone_low, buy_zone_high, f"Price above Buy Zone High ({buy_zone_high:.1f})"
        
    return True, buy_zone_low, buy_zone_high, "Valid Technical Setup"
