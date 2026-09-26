"""
scripts/simulate_12_scanner_regimes.py
=====================================
Simulates all 12 Scanner × Regime combinations under authoritative governance:
Scanners: TECHNICAL, ACCUMULATION, PULLBACK, EOD
Regimes:  BULL, SIDEWAYS, BEAR

Verifies:
1. Health display / info
2. Lifecycle state
3. Evidence-supported regime
4. Production authorization state
5. Production active state
6. Decision (ALLOW / BLOCK)
7. Reason code
8. Plain-language message
9. Persistence decision (YES / NO)
10. Dispatch decision (YES / NO)
"""

import sys
import os
import json

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)
APP_DIR = os.path.join(BASE_DIR, "app")
if APP_DIR not in sys.path:
    sys.path.insert(0, APP_DIR)

from engine.production.governance_registry import (
    get_scanner_health_regime_info,
    can_scanner_emit_production_alert,
    assert_production_alert_permitted,
    CERTIFIED_PRODUCTION_SCANNERS,
    UNDER_CERTIFICATION_SCANNERS,
    DECOMMISSIONED_SCANNERS
)
from app.database import save_alert_if_new

SCANNERS = ["TECHNICAL", "ACCUMULATION", "PULLBACK", "EOD"]
REGIMES = ["BULL", "SIDEWAYS", "BEAR"]

def run_simulation():
    results = []
    print("=" * 100)
    print("12 SCANNER × REGIME ROUTING SIMULATION BATTERY")
    print("Governance Release: TEMPORAL_REPLICATION_2026-09-26 | Algorithm Release: v6.0-prod")
    print("=" * 100)

    all_passed = True

    for scanner in SCANNERS:
        for regime in REGIMES:
            info = get_scanner_health_regime_info(scanner, current_macro_regime=regime)
            
            # 1. Routing decision
            # Under fail-closed governance, since CERTIFIED_PRODUCTION_SCANNERS is empty, decision MUST be BLOCK
            can_emit = can_scanner_emit_production_alert(scanner)
            decision = "ALLOW" if can_emit else "BLOCK"
            
            # 2. Persistence decision
            inserted, reason, alloc, shares = save_alert_if_new(
                symbol="MOCK_TEST",
                breakout_type="BREAKOUT",
                alert_time="2026-09-26 10:00:00",
                scanner=scanner,
                bayesian_regime=regime,
                entry_price=100.0,
                stop_loss=95.0
            )
            persistence = "YES" if inserted else "NO"

            # 3. Dispatch decision
            try:
                assert_production_alert_permitted(scanner)
                dispatch = "YES"
            except PermissionError:
                dispatch = "NO"

            # Invariant checks:
            # Under fail-closed, NO scanner should ALLOW, PERSIST, or DISPATCH
            if decision != "BLOCK" or persistence != "NO" or dispatch != "NO":
                print(f"FAILED INVARIANT: {scanner} × {regime} permitted action!")
                all_passed = False

            item = {
                "scanner": scanner,
                "current_regime": regime,
                "selected_variant": info.get("selected_variant", "NONE"),
                "lifecycle": info["lifecycle_state"],
                "evidence_regime": info["supported_regime"],
                "production_authorization": info["production_authorization_state"],
                "production_active_now": info["production_active_now"],
                "decision": decision,
                "reason_code": info["reason_code"],
                "plain_language_reason": info["display_message"],
                "plain_language_message": info["display_message"],
                "db_persistence": persistence,
                "persistence": persistence,
                "dispatch": dispatch
            }
            results.append(item)

            print(f"SCANNER: {scanner:<13} | REGIME: {regime:<8} | VARIANT: {item['selected_variant']:<14} | DECISION: {decision} | PERSIST: {persistence} | DISPATCH: {dispatch}")
            print(f"  Lifecycle:        {item['lifecycle']}")
            print(f"  Evidence Regime:  {item['evidence_regime']}")
            print(f"  Prod Auth State:  {item['production_authorization']}")
            print(f"  Reason Code:      {item['reason_code']}")
            print(f"  Display Message:  {item['plain_language_message']}")
            print("-" * 100)

    # Test Decommissioned Scanners as well
    print("\nDECOMMISSIONED SCANNERS SAFETY CHECK:")
    decom_scanners = [
        "SHORT_COVERING", "5M_BREAKOUT", "MOMENTUM_IGNITION",
        "MOMENTUM_THRUST_REVERSAL", "MULTI_TF", "MULTI_TF_5M",
        "TECHNICAL_INTRADAY", "REVERSAL"
    ]
    for d_sc in decom_scanners:
        d_info = get_scanner_health_regime_info(d_sc, current_macro_regime="BULL")
        can_emit = can_scanner_emit_production_alert(d_sc)
        d_inserted, d_reason, _, _ = save_alert_if_new(
            symbol="MOCK_TEST",
            breakout_type="BREAKOUT",
            alert_time="2026-09-26 10:00:00",
            scanner=d_sc,
            bayesian_regime="BULL",
            entry_price=100.0,
            stop_loss=95.0
        )
        try:
            assert_production_alert_permitted(d_sc)
            d_dispatch = "YES"
        except PermissionError:
            d_dispatch = "NO"

        print(f"DECOMMISSIONED: {d_sc:<25} | CAN_EMIT: {can_emit} | PERSIST: {'NO' if not d_inserted else 'YES'} | DISPATCH: {d_dispatch}")
        if can_emit or d_inserted or d_dispatch != "NO":
            print(f"FAILED DECOMMISSIONED GATE for {d_sc}!")
            all_passed = False

    out_file = os.path.join(BASE_DIR, "reports", "certification", "routing_simulation_12_cells.json")
    with open(out_file, "w") as f:
        json.dump(results, f, indent=2)
    print(f"\nSaved simulation results to {out_file}")

    target_dir = os.path.join(BASE_DIR, "reports", "certification", "MULTI_VARIANT_REGIME_REQUALIFICATION_2026-09-26")
    os.makedirs(target_dir, exist_ok=True)
    target_out_file = os.path.join(target_dir, "ROUTING_SIMULATION.json")
    with open(target_out_file, "w") as f:
        json.dump(results, f, indent=2)
    print(f"Saved requalification routing simulation to {target_out_file}")

    if all_passed:
        print("\nALL 12 SCENARIOS + DECOMMISSIONED SAFETY CHECKS PASSED PERFECTLY (FAIL-CLOSED)")
    else:
        print("\nSIMULATION FAILED ON INVARIANTS")
        sys.exit(1)

if __name__ == "__main__":
    run_simulation()
