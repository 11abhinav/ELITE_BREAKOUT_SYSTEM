from typing import Dict, Any, Tuple
from core.models import MetricResult

def run_gates(raw_metrics: Dict[str, Any]) -> Tuple[bool, str]:
    """
    Evaluates the business against the Layer 3 Gate Engine.
    Instantly rejects fundamentally flawed businesses.
    
    Returns:
        (passed: bool, reason: str)
    """
    
    # Negative Equity
    equity = raw_metrics.get("total_equity")
    if equity is not None and equity <= 0:
        return False, f"FAIL: Negative Equity ({equity})"

    # Promoter Pledge > 50%
    pledge = raw_metrics.get("promoter_pledge_pct")
    if pledge is not None and pledge > 50.0:
        return False, f"FAIL: High Promoter Pledge ({pledge}%)"
        
    # Auditor Flags / Fraud
    auditor_flags = raw_metrics.get("auditor_flags", False)
    if auditor_flags:
        return False, "FAIL: Auditor/Fraud Flags Detected"
        
    # OCF Negative
    ocf = raw_metrics.get("operating_cash_flow_ttm")
    if ocf is not None and ocf < 0:
        return False, f"FAIL: Negative Operating Cash Flow ({ocf})"
        
    # Extreme Debt (Exclude financials where high debt is normal)
    is_financial = raw_metrics.get("is_financial", False)
    if not is_financial:
        debt_equity = raw_metrics.get("debt_equity")
        if debt_equity is not None and debt_equity > 2.5: # Extreme debt threshold
            return False, f"FAIL: Extreme Debt to Equity ({debt_equity})"
            
    return True, "PASS"
