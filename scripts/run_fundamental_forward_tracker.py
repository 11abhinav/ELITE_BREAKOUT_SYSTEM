#!/usr/bin/env python3
"""
FUNDAMENTAL_GEM_RECOVERY — PHASE E LIVE FORWARD TRACKER RUNNER
==============================================================
CLI script to run the monthly Phase E Forward Tracker and print a clean 10-line summary.
"""

import os
import sys

# Add repository root and app directory to python path
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
app_dir = os.path.join(project_root, "app")
for d in [project_root, app_dir]:
    if d not in sys.path:
        sys.path.insert(0, d)

from fundamental_forward_tracker import generate_forward_watchlist, append_to_tracker_ledger

def main():
    print("=" * 80)
    print("FUNDAMENTAL GEM RECOVERY — PHASE E LIVE FORWARD TRACKER")
    print("=" * 80)

    res = generate_forward_watchlist()
    append_to_tracker_ledger(res)

    print(f"\n1. AS-OF DATE        : {res['as_of_date']}")
    print(f"2. QUALIFYING STOCKS : {res['total_qualifying_candidates']} total (L1+L2+L3 EV/EBITDA pass)")
    print(f"3. WATCHLIST TOP PICKS: {res['watchlist_top_candidates']} ranked by 100-pt Score")
    print(f"4. TIER A COUNT (DISL): {res['tier_a_count']} names (Quality + Valuation + Res_DD <= 10%)")
    print(f"5. TIER B COUNT (VAL) : {res['tier_b_count']} names (Quality + Valuation Only)")
    print(f"6. TIER A SYMBOLS     : {', '.join(res['tier_a_names'][:5]) if res['tier_a_names'] else 'None'}")
    print(f"7. TIER B SYMBOLS     : {', '.join(res['tier_b_names'][:5]) if res['tier_b_names'] else 'None'}")
    print(f"8. LOG LOCATION      : research/fundamental_gem_recovery_v5/FORWARD_TRACKER_LOG.json")
    print(f"9. PRE-BUY CHECKLIST : 7 qualitative governance checks required per candidate")
    print(f"10. VERDICT STATUS   : INCONCLUSIVE_BREADTH (Backtest Halted; Live Forward Tracking Active)")
    print("=" * 80)

    print("\nTOP 10 WATCHLIST CANDIDATES FOR MANUAL PRE-BUY REVIEW:")
    print("-" * 110)
    print(f"{'Rank':<4} {'Symbol':<12} {'Tier':<7} {'Score':<6} {'Staleness':<10} {'EV/EBITDA Discount':<20} {'5Y ROCE':<10} {'Res_DD':<10}")
    print("-" * 110)

    for idx, c in enumerate(res['top_candidates'][:10], 1):
        ev_disc_str = f"{c['ev_ebitda_current']} vs {c['ev_ebitda_3y_median']} (-{c['ev_ebitda_discount_pct']}%)"
        print(f"{idx:<4} {c['symbol']:<12} {c['tier']:<7} {c['score_100']:<6.1f} {c['staleness_days']:<3}d        {ev_disc_str:<20} {c['roce_5y_avg']:<5.1f}%     {c['res_dd_pct']:<5.1f}%")

    print("-" * 110)
    print("\nMANDATORY MANUAL PRE-BUY CHECKLIST (Execute per candidate before live entry):")
    print("  [ ] 1. Promoter Pledge < 5%")
    print("  [ ] 2. Promoter Holding Stability (no sudden exit/dilution)")
    print("  [ ] 3. Auditor Clean Opinion (no qualifications or mid-term resignation)")
    print("  [ ] 4. Receivables 3Y Trend (days sales outstanding not expanding > 20%)")
    print("  [ ] 5. CWIP Ageing (< 3Y capital work-in-progress)")
    print("  [ ] 6. Related Party Transactions (< 10% of sales/purchases)")
    print("  [ ] 7. Latest Quarterly Results & Concall Management Commentary")
    print("=" * 80)

if __name__ == "__main__":
    main()
