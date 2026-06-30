from typing import Dict, Any
from core.models import BuyZoneResult
from core.quality_engine import safe_float
from core.audit_engine import audit_engine

def run_buy_zone_engine(symbol: str, raw_data: Dict[str, Any]) -> BuyZoneResult:
    """
    Layer 7: Buy Zone Engine
    """
    price = safe_float(raw_data.get('price'))
    sma_50 = safe_float(raw_data.get('sma_50'))
    sma_200 = safe_float(raw_data.get('sma_200'))
    atr = safe_float(raw_data.get('atr'))
    
    if price == 0.0 or sma_50 == 0.0 or sma_200 == 0.0 or atr == 0.0:
        audit_engine.log(symbol, "Buy Zone", "Warning", "Missing Technicals", "technicals", 0.0)
        return BuyZoneResult(in_buy_zone=False, reason="Missing Technicals")
        
    # Example logic: Trend must be up (Price > SMA200)
    if price < sma_200:
        audit_engine.log(symbol, "Buy Zone", "Failed", "Price < SMA 200", "trend", price)
        return BuyZoneResult(in_buy_zone=False, reason="Downtrend (Below SMA 200)")

    # Dynamic ATR Bands: Adjust based on volatility regime
    volatility_pct = atr / price
    
    # If highly volatile (>5% daily swing avg), widen the buy zone to prevent shakeouts
    if volatility_pct > 0.05:
        lower_multiplier = 1.0
        upper_multiplier = 2.0
    # If low volatility (<2%), tighten the buy zone
    elif volatility_pct < 0.02:
        lower_multiplier = 0.2
        upper_multiplier = 1.0
    else:
        lower_multiplier = 0.5
        upper_multiplier = 1.5

    # Buy zone dynamically adjusting around SMA 50
    buy_low = sma_50 - (lower_multiplier * atr)
    buy_high = sma_50 + (upper_multiplier * atr)
    
    in_zone = (buy_low <= price <= buy_high)
    
    if in_zone:
        reason = "In ATR Buy Zone near SMA 50"
        audit_engine.log(symbol, "Buy Zone", "Passed", reason, "price_vs_zone", price)
    else:
        reason = "Overextended or too far below support"
        audit_engine.log(symbol, "Buy Zone", "Warning", reason, "price_vs_zone", price)
        
    return BuyZoneResult(
        in_buy_zone=in_zone,
        buy_zone_low=round(buy_low, 2),
        buy_zone_high=round(buy_high, 2),
        reason=reason
    )
