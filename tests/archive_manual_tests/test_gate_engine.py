import sys
sys.path.insert(0, 'app')
from core.gate_engine import run_gates
from core.audit_engine import audit_engine
import json

test_cases = {
    "RELIANCE": {"total_equity": 500000, "promoter_pledge_pct": 0.0, "auditor_flags": False, "operating_cash_flow_ttm": 150000, "debt_equity": 0.4},
    "HDFCBANK": {"total_equity": 250000, "promoter_pledge_pct": 0.0, "auditor_flags": False, "operating_cash_flow_ttm": -50000, "debt_equity": 8.0, "is_financial": True},
    "BADSTOCK": {"total_equity": -50, "promoter_pledge_pct": 0.8, "auditor_flags": True, "operating_cash_flow_ttm": -100, "debt_equity": 5.0}
}

for sym, data in test_cases.items():
    passed, reason = run_gates(sym, data)
    print(f"[{sym}] Passed: {passed}, Reason: {reason}")

print("\nAudit Trail:")
print(audit_engine.export_trail())
