import os
import sys
from datetime import date
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)
sys.path.insert(0, os.path.join(BASE_DIR, "app"))

from scripts.rebuild_pit_from_exchange import compute_canonical_symbol_row

rec = compute_canonical_symbol_row("ARIS", date(2026, 10, 4))
print("ARIS record results:")
for k in ["symbol", "cmp", "shares_outstanding_m", "market_cap", "cash_and_equivalents", "total_debt", "enterprise_value", "ebitda", "current_ev_ebitda", "provenance_status", "is_structural_ineligible"]:
    print(f"  {k}: {rec.get(k)}")
