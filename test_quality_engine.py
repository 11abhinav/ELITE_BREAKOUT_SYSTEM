import sys
sys.path.insert(0, 'app')
from core.quality_engine import run_quality_engine
from core.audit_engine import audit_engine
import yaml

with open('app/core/config/v5_weights.yaml', 'r') as f:
    config = yaml.safe_load(f)

test_cases = {
    "RELIANCE": {"operating_margin_ttm": 0.18, "gross_margin_stability": 0.03, "roce": 0.16, "cfo_pat_ratio": 1.1, "fcf_margin": 0.10},
    "MISSINGDATA": {"operating_margin_ttm": 0.18}
}

for sym, data in test_cases.items():
    res = run_quality_engine(sym, data, config['quality_weights'])
    print(f"[{sym}] Score: {res.score}, Confidence: {res.confidence}, Reasons: {res.reasons}, Missing: {res.missing_metrics}")

print("\nAudit Trail:")
print(audit_engine.export_trail())
