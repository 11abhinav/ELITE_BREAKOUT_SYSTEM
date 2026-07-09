import sys
sys.path.insert(0, 'app')
from core.financial_strength_engine import run_financial_strength_engine
from core.audit_engine import audit_engine

test_cases = {
    "RELIANCE": {"debt_equity": 0.3, "interest_coverage_ratio": 12.0, "debt_yoy_growth": -0.05, "altman_z": 3.2, "current_ratio": 2.1},
    "HDFCBANK": {"debt_equity": 8.0, "is_financial": True},
    "POORSTOCK": {"debt_equity": 1.5, "interest_coverage_ratio": 1.5, "debt_yoy_growth": 0.20, "altman_z": 1.5, "current_ratio": 0.8}
}

for sym, data in test_cases.items():
    res = run_financial_strength_engine(sym, data)
    print(f"[{sym}] Score: {res.score}, Confidence: {res.confidence}, Reasons: {res.reasons}")
