import sys
sys.path.insert(0, 'app')
from core.multibagger_pipeline import run_pipeline_for_symbol
import json
import dataclasses

test_cases = {
    "TCS": {
        "price": 4000.0, "sector": "software", "shares_outstanding": 3650.0, 
        "eps": 130.0, "book_value_per_share": 350.0, "free_cash_flow": 450000.0, 
        "ebit": 600000.0, "tt_indpe": 30.0,
        "operating_margin_ttm": 0.26, "gross_margin_stability": 0.02, "roce": 0.45, 
        "cfo_pat_ratio": 1.05, "fcf_margin": 0.20,
        "revenue_cagr_3y": 0.15, "eps_cagr_3y": 0.18, "fcf_cagr_3y": 0.16, "reinvestment_rate": 0.15,
        "debt_equity": 0.05, "interest_coverage_ratio": 50.0, "debt_yoy_growth": -0.10, "altman_z": 8.5, "current_ratio": 2.5,
        "rs_rating": 80.0, "relative_volume_10d": 1.2, "pct_from_52w_high": -0.05,
        "sma_50": 3950.0, "sma_200": 3600.0, "atr": 60.0
    }
}

for sym, data in test_cases.items():
    decision = run_pipeline_for_symbol(sym, data)
    print(json.dumps(dataclasses.asdict(decision), indent=2))
