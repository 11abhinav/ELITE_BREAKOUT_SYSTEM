import sys
sys.path.insert(0, 'app')
from core.growth_engine import run_growth_engine
from core.audit_engine import audit_engine

test_cases = {
    "RELIANCE": {"revenue_cagr_3y": 0.20, "eps_cagr_3y": 0.25, "fcf_cagr_3y": 0.15, "reinvestment_rate": 0.50},
    "SLOWGROWTH": {"revenue_cagr_3y": 0.08, "eps_cagr_3y": 0.05, "fcf_cagr_3y": -0.05, "reinvestment_rate": 0.10}
}

for sym, data in test_cases.items():
    res = run_growth_engine(sym, data)
    print(f"[{sym}] Score: {res.score}, Confidence: {res.confidence}, Reasons: {res.reasons}")
