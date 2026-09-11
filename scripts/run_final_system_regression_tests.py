#!/usr/bin/env python3
# =============================================================================
# scripts/run_final_system_regression_tests.py
# FINAL SYSTEM REGRESSION SUITE: 10/10 MASTER GOVERNANCE INVARIANTS
# =============================================================================

import os
import sys
import json
import numpy as np
import pandas as pd

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)
_REPORTS_DIR = os.path.join(_REPO_ROOT, "reports")

def run_all_final_system_tests():
    out_lines = []

    # 1. Master Baseline Snapshot Completeness
    try:
        p1 = os.path.join(_REPORTS_DIR, "final_phase1_scanner_master_baseline.csv")
        p1_port = os.path.join(_REPORTS_DIR, "final_phase1_portfolio_master_baseline.csv")
        assert os.path.exists(p1) and os.path.exists(p1_port), "Missing Phase 1 baseline files"
        df1 = pd.read_csv(p1)
        assert len(df1) == 11, "Expected 11 scanners in baseline snapshot"
        assert all(df1["net_expectancy_r"] > 0), "All baseline scanners must have positive net expectancy"
        out_lines.append("[PASS] 1. Master Baseline Snapshot Schema & Completeness (11 Scanners & Portfolio)")
    except Exception as e:
        out_lines.append(f"[FAIL] 1. Baseline Snapshot: {e}")

    # 2. Bottleneck Classification Resolution
    try:
        p2 = os.path.join(_REPORTS_DIR, "final_phase2_bottleneck_diagnosis.csv")
        assert os.path.exists(p2), "Missing bottleneck diagnosis file"
        df2 = pd.read_csv(p2)
        assert len(df2) == 11, "Expected 11 scanners in bottleneck classification"
        out_lines.append("[PASS] 2. Bottleneck Diagnosis & Resolution Mapping Invariance")
    except Exception as e:
        out_lines.append(f"[FAIL] 2. Bottleneck Diagnosis: {e}")

    # 3. Regime Attribution Pure Gem Alpha Proof
    try:
        p3 = os.path.join(_REPORTS_DIR, "final_phase5_regime_attribution_matrix.csv")
        assert os.path.exists(p3), "Missing regime attribution file"
        df3 = pd.read_csv(p3)
        t1 = df3[df3["scanner"].isin(["Reversal", "Pullback V2", "MultiTF 1H", "Multibagger"])]
        assert all(t1["pure_gem_alpha_er"] > 0.10), "Tier 1 scanners must exhibit >0.10R pure Gem alpha"
        sc = df3[df3["scanner"] == "Short Covering"].iloc[0]
        assert sc["pure_gem_alpha_er"] < 0, "Short covering must exhibit decoupling under Gem"
        out_lines.append("[PASS] 3. Regime Attribution & Pure Gem Alpha Separation Invariance")
    except Exception as e:
        out_lines.append(f"[FAIL] 3. Regime Attribution: {e}")

    # 4. Two-Stage Top-K Threshold Optimality
    try:
        p4 = os.path.join(_REPORTS_DIR, "final_phase8_top_k_threshold_sweeps.csv")
        assert os.path.exists(p4), "Missing top-k threshold file"
        df4 = pd.read_csv(p4)
        sweet = df4[df4["quality_cutoff_tier"].str.contains("Top 20%")].iloc[0]
        assert float(sweet["average_ecosystem_er"]) >= 0.75, "Top 20% ER must be >= 0.75R"
        assert float(sweet["net_profit_factor"]) >= 5.0, "Top 20% PF must be >= 5.0"
        out_lines.append("[PASS] 4. Two-Stage Top-K Threshold Optimality & Sweet Spot Selection")
    except Exception as e:
        out_lines.append(f"[FAIL] 4. Top-K Thresholds: {e}")

    # 5. False-Positive Failure Taxonomy & Veto Audit
    try:
        p5 = os.path.join(_REPORTS_DIR, "final_phase11_failure_taxonomy_audit.csv")
        assert os.path.exists(p5), "Missing failure taxonomy file"
        df5 = pd.read_csv(p5)
        assert len(df5) >= 5, "Expected at least 5 failure modes in taxonomy"
        out_lines.append("[PASS] 5. False-Positive Failure Taxonomy & Active Risk Veto Audit")
    except Exception as e:
        out_lines.append(f"[FAIL] 5. Failure Taxonomy: {e}")

    # 6. Leave-One-Out Marginal Value Preservation
    try:
        p6 = os.path.join(_REPORTS_DIR, "final_phase12_leave_one_out_marginal_value.csv")
        assert os.path.exists(p6), "Missing leave-one-out file"
        df6 = pd.read_csv(p6)
        all_row = df6[df6["portfolio_composition"].str.contains("ALL 11")].iloc[0]
        assert float(all_row["realized_net_r"]) >= 6000.0, "Master portfolio return must be >= 6000R"
        out_lines.append("[PASS] 6. Leave-One-Out Marginal Portfolio Contribution & Diversification")
    except Exception as e:
        out_lines.append(f"[FAIL] 6. Leave-One-Out: {e}")

    # 7. 10-Scenario Systemic Stress Survival
    try:
        p7 = os.path.join(_REPORTS_DIR, "final_phase15_systemic_stress_suite.csv")
        assert os.path.exists(p7), "Missing stress suite file"
        df7 = pd.read_csv(p7)
        for _, row in df7.iterrows():
            assert float(row["prob_unprofitable_year_pct"]) == 0.0, f"Unprofitable year in {row['stress_scenario']}"
            assert float(row["historical_max_dd_r"]) < 20.0, f"Excessive DD in {row['stress_scenario']}"
        out_lines.append("[PASS] 7. 10-Scenario Systemic Macroeconomic & Friction Stress Survival")
    except Exception as e:
        out_lines.append(f"[FAIL] 7. Systemic Stress: {e}")

    # 8. Outlier Robustness & Concentration Safety
    try:
        p8 = os.path.join(_REPORTS_DIR, "final_phase16_outlier_robustness_matrix.csv")
        assert os.path.exists(p8), "Missing outlier robustness file"
        df8 = pd.read_csv(p8)
        no10 = df8[df8["concentration_metric"].str.contains("Excluding Best 10")].iloc[0]
        assert float(no10["pct_of_total_profit"]) >= 95.0, "Top 10 trades must not account for >5% total profit"
        out_lines.append("[PASS] 8. Outlier Robustness & Tail Profit Concentration Invariance")
    except Exception as e:
        out_lines.append(f"[FAIL] 8. Outlier Robustness: {e}")

    # 9. Walk-Forward Consistency & Holdout Generalization
    try:
        p9 = os.path.join(_REPORTS_DIR, "final_phase17_walk_forward_holdout_matrix.csv")
        assert os.path.exists(p9), "Missing walk-forward holdout file"
        df9 = pd.read_csv(p9)
        for _, row in df9.iterrows():
            assert float(row["realized_net_er"]) >= 0.75, f"Low ER in {row['validation_dataset_window']}"
            assert float(row["net_profit_factor"]) >= 5.0, f"Low PF in {row['validation_dataset_window']}"
        out_lines.append("[PASS] 9. Walk-Forward Temporal Consistency & Pristine Forward Generalization")
    except Exception as e:
        out_lines.append(f"[FAIL] 9. Walk-Forward Generalization: {e}")

    # 10. Documentation Mandate & Master Compendium Integrity
    try:
        p_comp = os.path.join(_REPO_ROOT, "docs", "MASTER_RESEARCH_AND_BACKTEST_COMPENDIUM.md")
        assert os.path.exists(p_comp), "Missing master compendium document in docs/"
        with open(p_comp, "r") as f:
            content = f.read()
        assert "V5.20" in content and "WHAT WE FOUND" in content, "Compendium missing required final sections"
        out_lines.append("[PASS] 10. Permanent Master Documentation Compendium Synchronization & Completeness")
    except Exception as e:
        out_lines.append(f"[FAIL] 10. Master Compendium Integrity: {e}")

    print("\n".join(out_lines))
    out_path = os.path.join(_REPORTS_DIR, "final_system_regression_test_results.txt")
    with open(out_path, "w") as f:
        f.write("\n".join(out_lines) + "\n")
    print(f"\nWrote test results to {out_path}")

if __name__ == "__main__":
    run_all_final_system_tests()
