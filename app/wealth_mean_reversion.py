import pandas as pd

def calculate_mean_reversion_opportunity(cmp: float, sma_200: float, rsi_14: float) -> dict:
    """
    Detects severe oversold conditions on strong fundamental stocks.
    """
    if not all([cmp, sma_200, rsi_14]):
        return {"is_reversion": False, "discount_pct": 0.0}
        
    discount = ((sma_200 - cmp) / sma_200) * 100
    
    # Deep discount (>30%) and oversold (RSI < 30)
    if discount > 30 and rsi_14 < 30:
        return {
            "is_reversion": True,
            "discount_pct": discount
        }
        
    return {"is_reversion": False, "discount_pct": discount}

def get_mean_reversion_signal(r: pd.Series) -> tuple[str, str]:
    """
    Evaluates if the stock triggers a mean reversion BUY.
    """
    cmp = r.get("cmp", 0) or 0
    sma = r.get("sma_200", 0) or 0
    rsi = r.get("RSI", 50.0)
    score = r.get("FM_Score", 0)
    
    # Only for fundamentally Elite stocks
    if score < 85:
        return "", ""
        
    opp = calculate_mean_reversion_opportunity(cmp, sma, rsi)
    if opp["is_reversion"]:
        return "BUY", f"Mean Reversion — {opp['discount_pct']:.1f}% Discount"
        
    return "", ""
