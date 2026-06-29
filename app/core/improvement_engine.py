from typing import Dict, Any
from core.models import ImprovementResult

def detect_improvements(raw_data: Dict[str, Any]) -> ImprovementResult:
    """
    Layer 4.5: Binary Detection of Improvement
    Returns YES/NO for key trajectory indicators before scoring them.
    """
    
    # 1. Revenue Acceleration
    # Is 1Y Revenue Growth > 3Y Revenue Growth?
    rev_1y = raw_data.get("revenue_growth_1y")
    rev_3y = raw_data.get("revenue_growth_3y")
    revenue_acceleration = False
    if rev_1y is not None and rev_3y is not None:
        revenue_acceleration = rev_1y > rev_3y

    # 2. Margin Expansion
    # Is Current Margin > Historical Margin Avg?
    margin_ttm = raw_data.get("operating_margin_ttm")
    margin_3y = raw_data.get("operating_margin_3y_avg")
    margin_expansion = False
    if margin_ttm is not None and margin_3y is not None:
        margin_expansion = margin_ttm > margin_3y
        
    # 3. ROIC Improving
    # Is Current ROIC > Historical ROIC Avg?
    roic_ttm = raw_data.get("roic_ttm")
    roic_3y = raw_data.get("roic_3y_avg")
    roic_improving = False
    if roic_ttm is not None and roic_3y is not None:
        roic_improving = roic_ttm > roic_3y
        
    # 4. Debt Reducing
    # Is Current Debt/Equity < Last Year Debt/Equity?
    de_curr = raw_data.get("debt_equity_current")
    de_prev = raw_data.get("debt_equity_prev")
    debt_reducing = False
    if de_curr is not None and de_prev is not None:
        debt_reducing = de_curr < de_prev
        
    return ImprovementResult(
        revenue_acceleration=revenue_acceleration,
        margin_expansion=margin_expansion,
        roic_improving=roic_improving,
        debt_reducing=debt_reducing
    )
