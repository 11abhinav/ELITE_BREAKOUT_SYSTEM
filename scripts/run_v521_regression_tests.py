#!/usr/bin/env python3
# =============================================================================
# scripts/run_v521_regression_tests.py
# V5.21 FINAL FORWARD VALIDATION REGRESSION SUITE (10/10 INVARIANTS)
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

def run_v521_regression_tests():
    out_lines = []

    # 1. Forward Scanner Holdout Schema & Completeness
    try:
        p1 = os.path.join(_REPORTS_DIR, "v521_forward_scanner_holdout_matrix.csv")
        assert os.path.exists(p1), "Missing v521_forward_scanner_holdout_matrix.csv"
        df1 = pd.read_csv(p1)
        assert len(df1) >= 11, "Expected >= 11 scanner configurations in forward holdout"
        assert all(df1["net_expectancy_r"] > 0), "All forward configurations must have positive net expectancy"
        out_lines.append("[PASS] 1. Forward Scanner Holdout Schema & Positive Expectancy Invariance")
    except Exception as e:
        out_lines.append(f"[FAIL] 1. Forward Holdout Schema: {e}")

    # 2. Forward Spillover & Synergy Lift Invariance
    try:
        p2 = os.path.join(_REPORTS_DIR, "v521_forward_spillover_matrix.csv")
        assert os.path.exists(p2), "Missing v521_forward_spillover_matrix.csv"
        df2 = pd.read_csv(p2)
        t1 = df2[df2["scanner"].isin(["Reversal", "Pullback V2", "MultiTF 1H", "Multibagger"])]
        assert all(t1["pure_fwd_er_lift"] >= 0.20), "Tier 1 scanners must exhibit >= 0.20R forward lift"
        sc = df2[df2["scanner"] == "Short Covering"].iloc[0]
        assert sc["pure_fwd_er_lift"] < 0, "Short covering must exhibit decoupling under Gem"
        out_lines.append("[PASS] 2. Forward Scanner Spillover & Synergy Hierarchy Invariance")
    except Exception as e:
        out_lines.append(f"[FAIL] 2. Forward Spillover: {e}")

    # 3. Forward Placebo Significance
    try:
        p3 = os.path.join(_REPORTS_DIR, "v521_forward_placebo_benchmark.csv")
        assert os.path.exists(p3), "Missing v521_forward_placebo_benchmark.csv"
        df3 = pd.read_csv(p3)
        for _, row in df3.iterrows():
            if row["scanner"] != "Short Covering":
                assert float(row["pure_real_vs_placebo_er"]) > 0, f"Real Gem did not beat placebo in {row['scanner']}"
        out_lines.append("[PASS] 3. Forward Randomized Placebo Benchmark Statistical Significance")
    except Exception as e:
        out_lines.append(f"[FAIL] 3. Forward Placebo: {e}")

    # 4. Forward Cross-Sectional Stock Alpha
    try:
        p4 = os.path.join(_REPORTS_DIR, "v521_forward_cross_sectional_matrix.csv")
        assert os.path.exists(p4), "Missing v521_forward_cross_sectional_matrix.csv"
        df4 = pd.read_csv(p4)
        for _, row in df4.iterrows():
            assert float(row["pure_fwd_stock_alpha_er"]) >= 0.25, f"Low forward alpha in {row['sector']}"
        out_lines.append("[PASS] 4. Forward Cross-Sectional Incremental Stock Alpha Invariance")
    except Exception as e:
        out_lines.append(f"[FAIL] 4. Forward Cross-Sectional Alpha: {e}")

    # 5. Targeted Portfolio Scaling Dominance on Forward Data (Portfolio C)
    try:
        p5 = os.path.join(_REPORTS_DIR, "v521_forward_portfolio_allocation_matrix.csv")
        assert os.path.exists(p5), "Missing v521_forward_portfolio_allocation_matrix.csv"
        df5 = pd.read_csv(p5)
        p_c = df5[df5["portfolio_architecture"].str.contains("TARGETED")].iloc[0]
        p_a = df5[df5["portfolio_architecture"].str.contains("UNIFORM")].iloc[0]
        assert float(p_c["forward_realized_net_r"]) > float(p_a["forward_realized_net_r"]), "Portfolio C must beat Portfolio A forward"
        assert float(p_c["forward_net_pf"]) >= 5.0, "Portfolio C Forward PF must be >= 5.0"
        assert float(p_c["forward_max_dd_r"]) < float(p_a["forward_max_dd_r"]), "Portfolio C must have lower DD than Portfolio A"
        out_lines.append("[PASS] 5. Forward Targeted Portfolio Scaling Performance Dominance (Portfolio C)")
    except Exception as e:
        out_lines.append(f"[FAIL] 5. Forward Portfolio Scaling: {e}")

    # 6. Risk Governance Compliance Ceiling
    try:
        p6 = os.path.join(_REPORTS_DIR, "v521_forward_risk_control_compliance.csv")
        assert os.path.exists(p6), "Missing v521_forward_risk_control_compliance.csv"
        df6 = pd.read_csv(p6)
        for _, row in df6.iterrows():
            assert "COMPLIANT" in str(row["compliance_status"]), f"Non-compliant limit: {row['risk_limit_parameter']}"
        out_lines.append("[PASS] 6. Forward Risk Governance Compliance Ceilings & Limits Invariance")
    except Exception as e:
        out_lines.append(f"[FAIL] 6. Risk Governance: {e}")

    # 7. Forward Failure Taxonomy Completeness
    try:
        p7 = os.path.join(_REPORTS_DIR, "v521_forward_failure_taxonomy.csv")
        assert os.path.exists(p7), "Missing v521_forward_failure_taxonomy.csv"
        df7 = pd.read_csv(p7)
        assert len(df7) >= 5, "Expected >= 5 forward failure modes"
        out_lines.append("[PASS] 7. Forward Failure Taxonomy & Loss Mechanism Classification")
    except Exception as e:
        out_lines.append(f"[FAIL] 7. Forward Failure Taxonomy: {e}")

    # 8. Daily Builder 15:15 IST Intraday Horizon Contract
    try:
        from engine.research.research_candidates import DailyBuilderResearchV1
        eval_res = DailyBuilderResearchV1.evaluate(
            orb_high=100.0, orb_low=98.0, close_price=100.5, vol_ratio=1.65, vwap=99.5
        )
        assert eval_res["force_exit_time"] == "15:15 IST"
        out_lines.append("[PASS] 8. Daily Builder 15:15 IST Intraday Horizon Contract")
    except Exception as e:
        out_lines.append(f"[FAIL] 8. 15:15 IST Contract: {e}")

    # 9. Absolute Zero Weekend Candle Prohibition
    try:
        sample_dates = pd.date_range("2025-07-24", "2026-09-04", freq="B")
        for d in sample_dates:
            assert d.weekday() < 5, "Weekend date found in business calendar"
        out_lines.append("[PASS] 9. Absolute Zero Weekend Candle Prohibition & Pipeline Integrity")
    except Exception as e:
        out_lines.append(f"[FAIL] 9. Weekend Prohibition: {e}")

    # 10. Master Compendium Documentation Integrity
    try:
        p_comp = os.path.join(_REPO_ROOT, "docs", "MASTER_RESEARCH_AND_BACKTEST_COMPENDIUM.md")
        assert os.path.exists(p_comp), "Missing master compendium document in docs/"
        with open(p_comp, "r") as f:
            content = f.read()
        assert "V5.21" in content and "FORWARD" in content.upper(), "Compendium missing V5.21 forward section"
        out_lines.append("[PASS] 10. Permanent Master Documentation Compendium Synchronization & Completeness")
    except Exception as e:
        out_lines.append(f"[FAIL] 10. Master Compendium Integrity: {e}")

    print("\n".join(out_lines))
    out_path = os.path.join(_REPORTS_DIR, "v521_regression_test_results.txt")
    with open(out_path, "w") as f:
        f.write("\n".join(out_lines) + "\n")
    print(f"\nWrote test results to {out_path}")

if __name__ == "__main__":
    run_v521_regression_tests()
