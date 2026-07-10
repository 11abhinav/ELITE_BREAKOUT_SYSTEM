from typing import Dict, Any, Tuple
from core.audit_engine import audit_engine

def safe_float(val: Any) -> float:
    try:
        import pandas as pd
        if val is None or pd.isna(val):
            return 0.0
        return float(val)
    except (ValueError, TypeError):
        return 0.0

def run_gates(symbol: str, raw_data: Dict[str, Any]) -> Tuple[bool, str]:
    """
    Layer 1: Hard Kill Gates
    Returns (Passed, RejectionReason)
    """
    passed = True
    reason = ""

    # 0. Data Completeness
    freshness = raw_data.get("data_freshness", "LIVE")
    if freshness == "FALLBACK":
        audit_engine.log(symbol, "Kill Gates", "Failed", "Incomplete Data (Fallback)", "data_freshness", freshness)
        return False, "Incomplete Data"
        
    raw_equity = raw_data.get("total_equity")
    market_cap = raw_data.get("market_cap")
    if raw_equity is None and market_cap is None:
        audit_engine.log(symbol, "Kill Gates", "Failed", "Incomplete Data (Missing Equity & Market Cap)", "total_equity", "None")
        return False, "Incomplete Data"

    # 1. Negative Equity
    if raw_equity is not None:
        equity = safe_float(raw_equity)
        if equity <= 0:
            audit_engine.log(symbol, "Kill Gates", "Failed", "Negative Equity", "total_equity", equity)
            return False, "Negative Equity"
        else:
            audit_engine.log(symbol, "Kill Gates", "Passed", "Positive Equity", "total_equity", equity)
    else:
        audit_engine.log(symbol, "Kill Gates", "Passed", "Equity Data Missing (Fallback to Market Cap)", "total_equity", "None")

    # 2. Promoter Pledge > 50%
    pledge = safe_float(raw_data.get("promoter_pledge_pct", 0.0))
    if pledge > 0.50:
        audit_engine.log(symbol, "Kill Gates", "Failed", "Promoter Pledge > 50%", "promoter_pledge_pct", pledge)
        return False, "Promoter Pledge > 50%"
    else:
        audit_engine.log(symbol, "Kill Gates", "Passed", "Acceptable Pledge", "promoter_pledge_pct", pledge)

    # 3. Auditor Flags / Fraud
    fraud = raw_data.get("auditor_flags", False)
    if fraud:
        audit_engine.log(symbol, "Kill Gates", "Failed", "Auditor Issues / Fraud Flags", "auditor_flags", fraud)
        return False, "Auditor Issues"
    else:
        audit_engine.log(symbol, "Kill Gates", "Passed", "No Auditor Issues", "auditor_flags", fraud)

    # 4. Severe Operating Cash Flow Burn
    ocf = safe_float(raw_data.get("operating_cash_flow_ttm", 0.0))
    # We might allow early stage software companies to burn cash, but for standard gates, negative OCF is a warning/fail.
    # Let's enforce a soft threshold for now, or just log a warning unless it's extreme.
    # The prompt says "Reject negative OCF".
    is_financial = raw_data.get("is_financial", False)
    if ocf < 0 and not is_financial:
        audit_engine.log(symbol, "Kill Gates", "Failed", "Negative Operating Cash Flow", "operating_cash_flow_ttm", ocf)
        return False, "Negative Operating Cash Flow"
    else:
        audit_engine.log(symbol, "Kill Gates", "Passed", "Positive OCF or Financial", "operating_cash_flow_ttm", ocf)

    # 5. Extreme Debt
    debt_equity = safe_float(raw_data.get("debt_equity", 0.0))
    if debt_equity > 3.0 and not is_financial:
        audit_engine.log(symbol, "Kill Gates", "Failed", "Extreme Debt/Equity > 3.0", "debt_equity", debt_equity)
        return False, "Extreme Debt"
    else:
        audit_engine.log(symbol, "Kill Gates", "Passed", "Acceptable Debt", "debt_equity", debt_equity)

    return passed, reason
