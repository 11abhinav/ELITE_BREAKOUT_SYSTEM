#!/usr/bin/env python3
import os
import sys
import traceback

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, _REPO_ROOT)
sys.path.insert(0, os.path.join(_REPO_ROOT, "app"))

print("Importing test modules...", flush=True)
import tests.test_v530_eod_upgrade as t_eod
import tests.test_v540_rc1_promotions as t_prom

def run_all_tests():
    print("=" * 80, flush=True)
    print("V5.9 REGRESSION AND INVARIANT TEST SUITE", flush=True)
    print("=" * 80, flush=True)

    eod_tests = [
        ("test_eod_config_parameters_v530", t_eod.test_eod_config_parameters_v530),
        ("test_eod_52w_proximity_gate", t_eod.test_eod_52w_proximity_gate),
        ("test_eod_volume_surge_gate", t_eod.test_eod_volume_surge_gate),
        ("test_eod_base_tightness_gate", t_eod.test_eod_base_tightness_gate),
        ("test_multi_scanner_invariance_under_v530", t_eod.test_multi_scanner_invariance_under_v530),
    ]

    prom_tests = [
        ("test_multi_tf_cross_timeframe_decision_time_invariance", t_prom.test_multi_tf_cross_timeframe_decision_time_invariance),
        ("test_daily_builder_1515_forced_exit_contract", t_prom.test_daily_builder_1515_forced_exit_contract),
        ("test_reversal_deterministic_support_precedence", t_prom.test_reversal_deterministic_support_precedence),
        ("test_multi_scanner_invariants_preserved", t_prom.test_multi_scanner_invariants_preserved),
    ]

    passed = 0
    failed = 0

    for name, fn in eod_tests + prom_tests:
        try:
            print(f"Running {name}...", flush=True)
            fn()
            print(f"  [PASS] {name}", flush=True)
            passed += 1
        except Exception as e:
            print(f"  [FAIL] {name}: {e}", flush=True)
            traceback.print_exc()
            failed += 1

    print("-" * 80, flush=True)
    print(f"Test Summary: {passed} passed, {failed} failed.", flush=True)
    print("=" * 80, flush=True)
    if failed > 0:
        sys.exit(1)
    else:
        print("ALL 9 CORE REGRESSION AND INVARIANT TESTS PASSED SUCCESSFULLY.", flush=True)

if __name__ == "__main__":
    run_all_tests()
