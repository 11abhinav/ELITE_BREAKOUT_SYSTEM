import sys
sys.path.insert(0, 'app')
from core.valuation_engine import run_valuation_engine
from core.audit_engine import audit_engine
import yaml

with open('app/core/config/v5_weights.yaml', 'r') as f:
    config = yaml.safe_load(f)

test_cases = {
    "RELIANCE": {"price": 3000, "shares_outstanding": 6700, "eps": 120.0, "book_value_per_share": 1500.0, "free_cash_flow": 800000.0, "ebit": 1000000.0, "tt_indpe": 25.0},
    "MISSINGDATA": {"price": 100, "eps": 10.0, "book_value_per_share": 50.0} # Missing shares, FCF, EBIT, indpe
}

for sym, data in test_cases.items():
    res = run_valuation_engine(sym, data, config['valuation_weights'])
    print(f"[{sym}] Fair Value: {res.fair_value}, MoS: {res.margin_of_safety}%, Score: {res.score}, Confidence: {res.confidence}")
    print(f"  Models: DCF={res.dcf_value}, Peer={res.peer_relative_value}, Graham={res.graham_value}, EPV={res.epv_value}, Asset={res.asset_value}")
